"""Atomic step executors — the reliable primitive the spike validated.

A WRITE step is executed by asking the model for exactly ONE file's content, given the shaped
context, and writing it. A RUN step executes one command. This is the operation a small local model
does reliably (spike D48); the intelligence is in the loop that assembles the context and sequences
these, not in the primitive.

`StepExecutor` is a protocol so a stronger executor (e.g. driving OpenWorker's TurnEngine, which the
spike proved runs atomic steps on Ollama) can drop in without changing the loop.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..conductor.state import extract_code
from ..llm import complete
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
        cmd = step.command.replace("{py}", f'"{sys.executable}"')
        try:
            r = subprocess.run(cmd, shell=True, cwd=self.root, capture_output=True,
                               text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return StepResult(False, f"command timed out: {step.command}")
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode == 0:
            return StepResult(True, f"command ok: {out[:160]}")
        # pytest exit 5 = "no tests collected" — NOT a test failure and NOT a code bug: the test file
        # has no `def test_*` functions (a weak model often writes bare module-level asserts). Flag it
        # distinctly so repair fixes the TEST file, instead of blaming the code and replanning forever.
        if r.returncode == 5 and "pytest" in cmd.lower():
            return StepResult(False, f"NO_TESTS_COLLECTED (pytest exit 5): the test file defines no "
                                     f"`def test_*` functions, so pytest ran nothing. {out[:200]}")
        return StepResult(False, f"command failed (exit {r.returncode}): {out[:300]}")
