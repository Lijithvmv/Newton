"""Project context — the markdown Newton reads first, every session.

This is the core Claude-Code pattern: a per-project NEWTON.md plus an optional docs/
tree (PRD, architecture, decisions, status) give the agent a durable, human-editable
memory of the project. The agent reads these for context and can update them as work
progresses. No embeddings required — just disciplined markdown.
"""

from __future__ import annotations

from pathlib import Path

# Docs we fold into context automatically if present, in priority order.
_DOC_NAMES = ("PRD.md", "ARCHITECTURE.md", "DECISIONS.md", "STATUS.md")


def load_context(root: Path, context_file: str) -> str:
    """Assemble the project context blob from NEWTON.md and any docs/ files."""
    parts: list[str] = []

    ctx = root / context_file
    if ctx.is_file():
        parts.append(f"### {context_file}\n{ctx.read_text(encoding='utf-8', errors='replace')}")

    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for name in _DOC_NAMES:
            f = docs_dir / name
            if f.is_file():
                parts.append(f"### docs/{name}\n{f.read_text(encoding='utf-8', errors='replace')}")

    if not parts:
        return (
            f"(No {context_file} found in this project yet. You may create one with "
            "write_file to record project context for future sessions.)"
        )
    return "\n\n".join(parts)


def project_tree(root: Path, max_entries: int = 60) -> str:
    """A shallow file listing so the agent knows the lay of the land without a tool call."""
    skip = {".venv", "node_modules", "__pycache__", ".git", "vendor", ".pytest_cache"}
    lines: list[str] = []
    for p in sorted(root.rglob("*")):
        if any(part in skip for part in p.relative_to(root).parts):
            continue
        depth = len(p.relative_to(root).parts) - 1
        if depth > 2:
            continue
        lines.append("  " * depth + p.name + ("/" if p.is_dir() else ""))
        if len(lines) >= max_entries:
            lines.append("... [tree truncated]")
            break
    return "\n".join(lines)
