"""Red acceptance test — a check the code's author never wrote, proven able to fail.

Measured on Newton's eval (2026-10-11, 54 runs): all 8 wrong verdicts came from a check Newton wrote
for itself — a bare script run that couldn't fail, or `pytest` with no test that couldn't pass.
Forcing a self-written test (2026-10-12) traded that for badly written tests. Outside evidence agrees:
ExecCritic (arXiv:2609.09133) found tests from a weak model LOWER SWE-bench Verified 61.2% → 57.3%,
and qualifies + freezes tests before a repair agent may use them.

So, before any implementation step (when the project folder still IS the pre-change code):
  1. a separate model call that sees only the goal and the file names writes test_acceptance.py;
  2. it is run at once and must FAIL by assertion or a missing name (red) — a test that already passes,
     collects nothing or doesn't parse is discarded and rewritten;
  3. once qualified it is frozen: the engine restores it if anything changes it.
Opt-in (NEWTON_LOOP_RED_ACCEPT=1) until measured.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..conductor.state import extract_code
from ..proc import run_shell

FILE = "test_acceptance.py"
COMMAND = "{py} -B -m pytest -q " + FILE
_TRIES = 3

_SYSTEM = (
    "You write ONE pytest file that checks whether a coding task has been done. You have NOT seen the "
    "implementation and must not guess at its internals: test only the behaviour the task states, using "
    "the exact names and examples it gives. Rules: every assertion lives inside a `def test_...():` "
    "function; test a command-line program by running it as a subprocess with explicit arguments, e.g. "
    "subprocess.run([sys.executable, 'greet.py', '--shout'], capture_output=True, text=True), and check "
    "its output — never call its main() directly; import functions from the project's modules by the names "
    "the task gives. Output only the complete file in one ```python block.")


@dataclass
class Acceptance:
    qualified: bool
    content: str = ""
    attempts: int = 0
    reason: str = ""


def classify(exit_code: int, output: str) -> tuple[bool, str]:
    """(is_valid_red, why) for a run of the new test against the pre-change code."""
    if exit_code == 0:
        return False, "it already passes on the code before the change, so it can't test the change"
    if exit_code == 5:
        return False, "pytest collected no tests: put every assertion in a def test_...() function"
    if any(k in output for k in ("SyntaxError", "IndentationError", "TabError")) and FILE in output:
        return False, "the test file itself does not parse"
    if exit_code in (1, 2):
        return True, "fails on the pre-change code"
    return False, f"unexpected pytest exit {exit_code}"


def _run_test(root: Path, timeout: int = 120) -> tuple[int, str]:
    cmd = COMMAND.replace("{py}", f'"{sys.executable}"')
    try:
        r = run_shell(cmd, cwd=root, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))
    except subprocess.TimeoutExpired:
        return -1, "timed out"


def prepare(root: Path, goal: str, tree: str, model: str, complete) -> Acceptance:
    """Write and qualify the acceptance test in `root` (which must still be the pre-change code).
    `complete` is the model call (injected so tests stay offline). Leaves no file if it never qualifies."""
    path = Path(root) / FILE
    feedback, why, attempt = "", "no attempt", 0
    for attempt in range(1, _TRIES + 1):
        user = (f"Task:\n{goal}\n\nFiles in the project (names only):\n{tree}\n\n"
                f"Write {FILE} for this task.{feedback}")
        try:
            resp = complete(model, [{"role": "system", "content": _SYSTEM},
                                    {"role": "user", "content": user}], temperature=0.2)
            code = extract_code(resp.choices[0].message.content or "")
        except Exception as e:                                   # model unavailable: no test, honestly
            why = f"model call failed: {e!r}"[:200]
            break
        if not code.strip():
            why, feedback = "empty test", "\nYour last reply contained no code."
            continue
        path.write_text(code, encoding="utf-8")
        exit_code, out = _run_test(root)
        ok, why = classify(exit_code, out)
        if ok:
            return Acceptance(True, code, attempt, why)
        feedback = f"\nYour previous version was rejected: {why}. Write it again."
    path.unlink(missing_ok=True)
    return Acceptance(False, "", attempt, why)
