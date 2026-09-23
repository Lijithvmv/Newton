"""Newton's tool belt — the hands of the agent.

Every tool is a deterministic Python function sandboxed to the project root. Writes
and shell commands never run silently: the agent loop routes them through an approval
gate before calling. Tools return plain strings — the observation the model reads next.

The model does not call these via native tool-calling (many local models lack it).
Instead it emits a JSON action block; `agent.py` parses it and dispatches here. That
keeps the loop working on any Ollama model, gemma3:4b included.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .proc import run_shell

# Actions that change the world or run code. The loop asks before executing these.
MUTATING = {"write_file", "edit_file", "run"}


class ToolError(Exception):
    """Raised for a bad tool call; the message is fed back to the model as an observation."""


@dataclass
class Tool:
    name: str
    description: str
    args: str  # human-readable arg spec, shown to the model
    func: Callable[..., str]


def _safe_path(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root`` and refuse anything that escapes the sandbox."""
    if not rel:
        raise ToolError("path is required")
    p = (root / rel).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise ToolError(f"path '{rel}' escapes the project root; refused") from None
    return p


class ToolBelt:
    """Binds the tool functions to one project root and exposes a registry."""

    def __init__(self, root: Path, *, max_read_chars: int = 20_000) -> None:
        self.root = root
        self.max_read_chars = max_read_chars
        self._tools: dict[str, Tool] = {}
        self._register_all()

    # --- registry -------------------------------------------------------

    def _add(self, name: str, description: str, args: str, func: Callable[..., str]) -> None:
        self._tools[name] = Tool(name, description, args, func)

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"unknown tool '{name}'. Available: {', '.join(self._tools)}")
        return self._tools[name]

    def catalog(self) -> str:
        """One-line-per-tool spec injected into the system prompt."""
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.name}({t.args}) — {t.description}")
        return "\n".join(lines)

    # --- tool implementations ------------------------------------------

    def _register_all(self) -> None:
        self._add(
            "read_file", "Read a text file's contents.", "path",
            self.read_file,
        )
        self._add(
            "write_file", "Create or overwrite a file with the given content.", "path, content",
            self.write_file,
        )
        self._add(
            "edit_file",
            "Replace the first exact occurrence of `old` with `new` in a file.",
            "path, old, new",
            self.edit_file,
        )
        self._add(
            "list_dir", "List files and folders under a directory (relative to root).", "path",
            self.list_dir,
        )
        self._add(
            "grep", "Search files under a directory for a substring (case-insensitive).",
            "pattern, path",
            self.grep,
        )
        self._add(
            "run", "Run a shell command in the project root and capture its output.", "cmd",
            self.run,
        )
        self._add(
            "finish", "End the task and deliver the final answer to the user.", "message",
            self.finish,
        )

    def read_file(self, path: str = "", **_: object) -> str:
        p = _safe_path(self.root, path)
        if not p.is_file():
            raise ToolError(f"no such file: {path}")
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > self.max_read_chars:
            text = text[: self.max_read_chars] + f"\n... [truncated at {self.max_read_chars} chars]"
        return text or "[empty file]"

    def write_file(self, path: str = "", content: str = "", **_: object) -> str:
        p = _safe_path(self.root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.is_file()
        p.write_text(content, encoding="utf-8")
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(content)} chars)."

    def edit_file(self, path: str = "", old: str = "", new: str = "", **_: object) -> str:
        p = _safe_path(self.root, path)
        if not p.is_file():
            raise ToolError(f"no such file: {path}")
        text = p.read_text(encoding="utf-8")
        if old not in text:
            raise ToolError(f"`old` text not found in {path}; read the file and match exactly")
        if text.count(old) > 1:
            raise ToolError(f"`old` matches {text.count(old)} times in {path}; make it unique")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}."

    def list_dir(self, path: str = ".", **_: object) -> str:
        p = _safe_path(self.root, path or ".")
        if not p.is_dir():
            raise ToolError(f"not a directory: {path}")
        skip = {".venv", "node_modules", "__pycache__", ".git", "vendor"}
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if child.name in skip:
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
        return "\n".join(entries) or "[empty directory]"

    def grep(self, pattern: str = "", path: str = ".", **_: object) -> str:
        if not pattern:
            raise ToolError("pattern is required")
        root = _safe_path(self.root, path or ".")
        needle = pattern.lower()
        skip = {".venv", "node_modules", "__pycache__", ".git", "vendor"}
        hits: list[str] = []
        files = [root] if root.is_file() else root.rglob("*")
        for f in files:
            if not f.is_file() or any(part in skip for part in f.parts):
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if needle in line.lower():
                        rel = f.relative_to(self.root)
                        hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 100:
                            hits.append("... [more matches truncated]")
                            return "\n".join(hits)
            except OSError:
                continue
        return "\n".join(hits) or f"no matches for '{pattern}'"

    def run(self, cmd: str = "", **_: object) -> str:
        if not cmd:
            raise ToolError("cmd is required")
        try:
            proc = run_shell(cmd, cwd=self.root, timeout=180)
        except subprocess.TimeoutExpired:
            return "[command timed out after 180s]"
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip() or "[no output]"
        if len(out) > self.max_read_chars:
            out = out[: self.max_read_chars] + "\n... [output truncated]"
        return f"[exit {proc.returncode}]\n{out}"

    def finish(self, message: str = "", **_: object) -> str:
        return message or "Done."
