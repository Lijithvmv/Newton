"""Confidence routing — the loop stops re-fixing a file it can't crack, and escalates instead.

Newton's loop makes bounded decisions (which file is the culprit, repair vs. replan) as hard binary
branches with no notion of how sure it is. These tests cover the first uncertainty signal: a
trajectory-derived 'is this repair making progress?' If a fix for a file is followed by the SAME
error, another repair has low odds — so the loop escalates (re-plan) instead of burning its whole
repair budget re-fixing the same file (the thrash the ledger diagnostic showed). The signal is
proven with a MODEL-FREE scripted executor, and the win is a NUMBER (fewer wasted rewrites), not an
assertion (Rule 4)."""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.decide import error_signature
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import StepResult
from newton.loop.state import RUN, WRITE, Step

# repair twice, never replan/self-test/integration — isolate the repair-progress behaviour cleanly.
_ISOLATED = Effort("test", max_attempts=1, repair_cycles=2, replan_cycles=0,
                   best_of_n=1, retrieval_k=3, budget_chars=6000)


# --- the signature: same bug hashes equal, different bug does not, path/line noise ignored ---

def test_error_signature_is_stable_across_volatile_noise():
    a = error_signature('E   AssertionError: expected 3\n  File "logic.py", line 5, in run')
    b = error_signature('E   AssertionError: expected 3\n  File "logic.py", line 9, in run')  # diff line
    assert a == b                                          # same bug → same signature

def test_error_signature_separates_genuinely_different_errors():
    assert error_signature("E   AssertionError: nope") != error_signature("E   TypeError: bad arg")


# --- the routing: a file the model can't fix is repaired ONCE, then the loop escalates ---

class _StuckExec:
    """A file the model can never fix: every WRITE writes the same (parseable) buggy code, and the
    RUN check always fails with the SAME error naming that file. Counts how many times each happens."""
    def __init__(self, root, code_file: str):
        self.root, self.code_file = root, code_file
        self.writes = self.runs = 0

    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            self.runs += 1
            return StepResult(False, f"E   AssertionError: expected 3 got 2\n  {self.code_file}:5: in run")
        self.writes += 1
        (self.root / step.file).write_text("def run():\n    return 2\n", encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def _run_stuck(tmp_path, monkeypatch, *, progress: bool):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setenv("NEWTON_LOOP_REPAIR_PROGRESS", "1" if progress else "0")
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: [
        Step(id="logic", goal="", kind=WRITE, file="logic.py"),
        Step(id="check", goal="", kind=RUN, command="run the check", depends_on=["logic"]),
    ])
    ex = _StuckExec(tmp_path, "logic.py")
    res = LoopEngine(load_settings(tmp_path), executor=ex, effort=_ISOLATED).run("build logic")
    return res, ex


def test_repair_progress_stops_the_thrash(tmp_path, monkeypatch):
    """WITH the signal: the file is written once, repaired once, then the recurring error is detected
    and the loop escalates — so the second repair cycle is NOT spent re-fixing the same file."""
    res, ex = _run_stuck(tmp_path, monkeypatch, progress=True)
    assert not res.ok                                     # honestly unresolved (the model can't fix it)
    assert ex.writes == 2                                 # initial build + exactly ONE repair, then stop
    assert any("repair-stalled" in ln for ln in res.state.log)

def test_without_the_signal_the_loop_burns_the_whole_repair_budget(tmp_path, monkeypatch):
    """Baseline (signal off): the loop re-fixes the same uncrackable file for EVERY repair cycle —
    3 writes for 2 cycles — and never flags the stall. This is the wasted compute the signal saves."""
    res, ex = _run_stuck(tmp_path, monkeypatch, progress=False)
    assert not res.ok
    assert ex.writes == 3                                 # initial build + 2 repairs (both wasted)
    assert not any("repair-stalled" in ln for ln in res.state.log)
