"""Atomic step executors — the reliable primitive the spike validated.

A WRITE step is executed by asking the model for exactly ONE file's content, given the shaped
context, and writing it. A RUN step executes one command. This is the operation a small local model
does reliably (spike D48); the intelligence is in the loop that assembles the context and sequences
these, not in the primitive.

`StepExecutor` is a protocol so a stronger executor (e.g. driving OpenWorker's TurnEngine, which the
spike proved runs atomic steps on Ollama) can drop in without changing the loop.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..conductor.state import extract_code
from ..llm import complete
from ..proc import run_shell
from .output_check import error_shaped
from .state import RUN, Step

STEP_SYSTEM = (
    "You are a coding assistant doing ONE atomic step. Do exactly what is asked, nothing more. "
    "When asked for a file's content, reply with only that file's complete content in one code block."
)


@dataclass
class StepResult:
    ok: bool
    detail: str = ""


class StepExecutor(Protocol):
    def execute(self, step: Step, context: str, *, temperature: float = 0.1) -> StepResult: ...


class NativeStepExecutor:
    """Executes atomic steps with Newton's own model call + filesystem — no external engine."""

    def __init__(self, root: Path, model: str, *, timeout: int = 120) -> None:
        self.root = Path(root)
        self.model = model
        self.timeout = timeout

    def execute(self, step: Step, context: str, *, temperature: float = 0.1) -> StepResult:
        if step.kind == RUN:
            return self._run(step)
        return self._write(step, context, temperature)

    def _write(self, step: Step, context: str, temperature: float = 0.1) -> StepResult:
        if not step.file:
            return StepResult(False, "write step has no target file")
        messages = [
            {"role": "system", "content": STEP_SYSTEM},
            {"role": "user", "content":
                f"{context}\n\n## Your one step\n{step.goal}\n\n"
                f"Reply with ONLY the complete content of `{step.file}` in one code block."},
        ]
        try:
            resp = complete(self.model, messages, temperature=temperature)
            code = extract_code(resp.choices[0].message.content or "")
        except Exception as e:
            return StepResult(False, f"model call failed: {e}")
        if not code.strip():
            return StepResult(False, "model produced no file content")
        path = self.root / step.file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding="utf-8")
        except OSError as e:
            return StepResult(False, f"could not write {step.file}: {e}")
        return StepResult(True, f"wrote {step.file} ({len(code)} chars)")

    def _run(self, step: Step) -> StepResult:
        if not step.command:
            return StepResult(False, "run step has no command")
        cmd = _normalize_command(step.command).replace("{py}", f'"{sys.executable}"')
        try:
            r = run_shell(cmd, cwd=self.root, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return StepResult(False, f"command timed out: {step.command}")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode == 0:
            # A command can exit 0 and still have failed (a wrapper swallows the code, a script prints
            # a traceback but returns 0, a server returns an error page). This cheap verifier — from
            # toolgrad's output validator — catches the unambiguous cases; it's conservative so a valid
            # exit-0 command is never failed by mistake.
            if error_shaped(out):
                return StepResult(False, f"command exited 0 but its output looks like a failure: "
                                         f"{_extract_failure(out)}")
            return StepResult(True, f"command ok: {out[:160]}")
        # pytest exit 5 = "no tests collected" — NOT a test failure and NOT a code bug: the test file
        # has no `def test_*` functions (a weak model often writes bare module-level asserts). Flag it
        # distinctly so repair fixes the TEST file, instead of blaming the code and replanning forever.
        if r.returncode == 5 and "pytest" in cmd.lower():
            return StepResult(False, f"NO_TESTS_COLLECTED (pytest exit 5): the test file defines no "
                                     f"`def test_*` functions, so pytest ran nothing. {out[:200]}")
        return StepResult(False, f"command failed (exit {r.returncode}): {_extract_failure(out)}")


_BARE_PY = re.compile(r"^\s*(pytest|python3|python)\b(.*)$", re.DOTALL)


def _normalize_command(command: str) -> str:
    """Rewrite a leading BARE Python/pytest invocation to the `{py}` convention.

    The decompose prompt asks for `{py} -m pytest`, but the weak local model sometimes emits a plain
    `pytest ...` or `python ...`; on Windows that shell command is 'not recognized' (pytest/python
    aren't on PATH) and the check falsely FAILS — a real build gets marked broken and burns repair
    cycles. The system owns the plan, so it deterministically fixes what the model got wrong (like the
    import-repair): map the leading executable to `{py}` (the resolved interpreter, which has pytest).
    Commands already using `{py}`, an absolute/quoted interpreter, or a non-Python tool are untouched."""
    if "{py}" in command:
        return command
    m = _BARE_PY.match(command)
    if not m:
        return command
    exe, rest = m.group(1), m.group(2)
    return ("{py} -m pytest" if exe == "pytest" else "{py}") + rest


def _extract_failure(out: str, limit: int = 500) -> str:
    """Pull the ACTIONABLE part of a failed test run for the repair loop.

    A bare `pytest -q` failure starts with progress dots (`..F.`) and only names the failing test +
    assertion at the END (`E   ...`, `FAILED file::test - message`). Truncating from the top fed the
    repair those useless dots, so the model kept regenerating identical code. Instead, surface the
    lines that say WHAT failed — the `FAILED ...` summary and the `E ...`/assert lines — so the model
    gets a targeted signal (e.g. 'DID NOT RAISE ValueError'). Falls back to the tail, where pytest
    prints its summary, when nothing matches."""
    picked: list[str] = []
    for ln in out.splitlines():
        s = ln.strip()
        if ln.startswith("FAILED ") or ln.startswith("ERROR ") or s.startswith("E ") \
                or s.startswith("assert ") or s.endswith("Error") or ": error:" in s:
            picked.append(s)
    text = "\n".join(dict.fromkeys(picked)) if picked else out[-limit:]   # dedupe, keep order
    return text[:limit]
