"""NEWTON_LOOP_REAL_CHECK: trust the plan's check only when a real test exists.

Reproduces the two measured wrong-verdict shapes (clean eval 2026-10-11, 8 of 8 wrong verdicts) with
real files and real commands; only the model is faked:
  * false PASS — the plan's only check is a bare `python greet.py`, which can't fail;
  * false FAIL — the plan runs `pytest` but never writes a test, so it can't pass."""

from __future__ import annotations

import pytest

from newton.config import load_settings
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import NativeStepExecutor, StepResult
from newton.loop.state import RUN, WRITE, Step

_EFFORT = Effort("test", max_attempts=1, repair_cycles=2, replan_cycles=0,
                 best_of_n=1, retrieval_k=3, budget_chars=6000)
_BROKEN = "print('hello')\n"                                                   # ignores --shout
_WORKING = "import sys\nprint('HELLO' if '--shout' in sys.argv else 'hello')\n"
_GOAL_TEST = ("import subprocess, sys\n"
              "def test_shout():\n"
              "    out = subprocess.run([sys.executable, 'greet.py', '--shout'], capture_output=True, text=True).stdout\n"
              "    assert out.strip() == 'HELLO'\n")


class _Exec(NativeStepExecutor):
    """Fake model for WRITE steps (greet.py: `codes` in order, one per write); RUN steps run for real."""
    def __init__(self, root, codes):
        super().__init__(root, "unused-model")
        self.codes, self.writes = list(codes), 0
    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            return self._run(step)
        if step.file == "greet.py":
            code = self.codes[min(self.writes, len(self.codes) - 1)]
            self.writes += 1
        else:
            code = _GOAL_TEST                                    # the goal-grounded test Newton writes
        (self.root / step.file).write_text(code, encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def _run(tmp_path, monkeypatch, codes, check_cmd, flag):
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "1")
    monkeypatch.setenv("NEWTON_LOOP_REAL_CHECK", flag)
    (tmp_path / "greet.py").write_text("print('hello')\n", encoding="utf-8")
    plan = [Step(id="s1", goal="add --shout", kind=WRITE, file="greet.py"),
            Step(id="c1", goal="check", kind=RUN, command=check_cmd, depends_on=["s1"])]
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: plan)
    monkeypatch.setattr("newton.loop.engine.LoopEngine._replan", lambda *a, **k: False)
    events = []
    LoopEngine(load_settings(tmp_path), executor=_Exec(tmp_path, codes), effort=_EFFORT,
               emit=lambda ch, p: events.append((ch, p))).run("add a --shout flag to greet.py")
    verdict = next(p for ch, p in events if ch == "verdict")
    shout = (tmp_path / "greet.py").read_text(encoding="utf-8") == _WORKING
    return verdict["level"], shout


def test_bare_script_check_false_pass_reproduces_without_flag(tmp_path, monkeypatch):
    level, shout = _run(tmp_path, monkeypatch, [_BROKEN], "{py} greet.py", flag="0")
    assert level == "pass" and not shout                      # verified PASS on broken code


def test_bare_script_check_gets_a_real_test_with_flag(tmp_path, monkeypatch):
    level, shout = _run(tmp_path, monkeypatch, [_BROKEN], "{py} greet.py", flag="1")
    assert level == "block" and not shout                     # broken code is no longer called verified


@pytest.mark.xfail(strict=True, reason=(
    "Known gap (2026-10-11): a test that runs the program via subprocess names only the TEST file in "
    "its traceback, so repair blames test_generated.py instead of greet.py, stalls, and escalates. "
    "The verdict is honest (WOULD BLOCK); routing the fix to the right file is a separate change."))
def test_bare_script_check_with_flag_repairs_to_working_code(tmp_path, monkeypatch):
    level, shout = _run(tmp_path, monkeypatch, [_BROKEN, _WORKING], "{py} greet.py", flag="1")
    assert level != "block" and shout                          # the real test drove a repair that works


def test_pytest_without_tests_false_fail_reproduces_without_flag(tmp_path, monkeypatch):
    level, shout = _run(tmp_path, monkeypatch, [_WORKING], "{py} -m pytest -q", flag="0")
    assert level == "block" and shout                         # working code blocked


def test_pytest_without_tests_gets_a_real_test_with_flag(tmp_path, monkeypatch):
    level, shout = _run(tmp_path, monkeypatch, [_WORKING], "{py} -m pytest -q", flag="1")
    assert level != "block" and shout                          # working code verified by a real test


def test_existing_project_tests_are_trusted(tmp_path, monkeypatch):
    (tmp_path / "test_greet.py").write_text(_GOAL_TEST, encoding="utf-8")
    level, shout = _run(tmp_path, monkeypatch, [_WORKING], "{py} -m pytest -q", flag="1")
    assert level != "block" and not (tmp_path / "test_generated.py").exists()
