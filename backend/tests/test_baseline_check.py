"""Baseline-replay check validation: a check that also passes on the pre-change code can't verify a run.

Reproduces the real corpus false-PASS shape (cli-shout-flag: the plan's only check was `python greet.py`,
which exits 0 before the change too) with real files and real commands; only the model is faked."""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.baseline import is_check_file
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import NativeStepExecutor, StepResult
from newton.loop.state import RUN, WRITE, Step

_ONE = Effort("test", max_attempts=1, repair_cycles=0, replan_cycles=0,
              best_of_n=1, retrieval_k=3, budget_chars=6000)
_ORIGINAL = "print('hello')\n"
_UNCHANGED_BUILD = "print('hello')\n"                     # the model "built" nothing useful
_REAL_BUILD = "import sys\nprint('HELLO' if '--shout' in sys.argv else 'hello')\n"


class _Exec(NativeStepExecutor):
    """Writes canned code for WRITE steps; runs RUN steps for real (inherited run_in)."""
    def __init__(self, root, code):
        super().__init__(root, "unused-model")
        self.code = code
    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            return self.run_in(step, self.root)
        (self.root / step.file).write_text(self.code, encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def _run(tmp_path, monkeypatch, code, check_cmd, extra_files=None, flag="1"):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setenv("NEWTON_LOOP_BASELINE_CHECK", flag)
    (tmp_path / "greet.py").write_text(_ORIGINAL, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        (tmp_path / rel).write_text(content, encoding="utf-8")
    plan = [Step(id="s1", goal="add --shout", kind=WRITE, file="greet.py"),
            Step(id="c1", goal="check", kind=RUN, command=check_cmd, depends_on=["s1"])]
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: plan)
    events = []
    eng = LoopEngine(load_settings(tmp_path), executor=_Exec(tmp_path, code), effort=_ONE,
                     emit=lambda ch, p: events.append((ch, p)))
    eng.run("add a --shout flag")
    verdict = next(p for ch, p in events if ch == "verdict")
    base = next((p for ch, p in events if ch == "baseline"), None)
    return verdict, base


def test_vacuous_check_cannot_verify(tmp_path, monkeypatch):
    verdict, base = _run(tmp_path, monkeypatch, _UNCHANGED_BUILD, "{py} greet.py")
    assert base["label_without"] != "WOULD BLOCK"        # before: rated verified (the false PASS)
    assert verdict["level"] == "block" and "unverified" in verdict["reason"]
    assert base["vacuous"] == ["c1"]


def test_real_check_still_verifies(tmp_path, monkeypatch):
    test = ("import subprocess, sys\n"
            "def test_shout():\n"
            "    out = subprocess.run([sys.executable, 'greet.py', '--shout'], capture_output=True, text=True).stdout\n"
            "    assert out.strip() == 'HELLO'\n")
    verdict, base = _run(tmp_path, monkeypatch, _REAL_BUILD, "{py} -m pytest -q test_greet.py",
                         extra_files={"test_greet.py": test})
    assert verdict["level"] != "block" and base["vacuous"] == []


def test_flag_off_changes_nothing(tmp_path, monkeypatch):
    verdict, base = _run(tmp_path, monkeypatch, _UNCHANGED_BUILD, "{py} greet.py", flag="0")
    assert base is None and verdict["level"] != "block"
    assert not (tmp_path / ".newton" / "baseline").exists()


def test_is_check_file():
    assert is_check_file("test_x.py") and is_check_file("pkg/x_test.py") and is_check_file("tests/a.py")
    assert is_check_file("check.py") and not is_check_file("greet.py") and not is_check_file("pkg/core.py")
