"""Typed decision records - the loop's bounded forks are logged, with confidence, into evidence.

The Jev-loop discipline (keep the probabilities): a run should carry an auditable trail of the
decisions it took - which file to fix, whether to keep repairing, the final verdict - each typed and
with a confidence, so a bad run shows whether a fork was confident-and-wrong or close-and-unlucky.
This proves the seam without a model: the deciders are heuristics today, but the record is in place
so a calibrated model (Laya) can later fill the same shape unchanged."""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.decision import CHOICE, NOUL, Decision
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import StepResult
from newton.loop.state import RUN, WRITE, Step

_ISOLATED = Effort("test", max_attempts=1, repair_cycles=2, replan_cycles=0,
                   best_of_n=1, retrieval_k=3, budget_chars=6000)


def test_decision_as_dict_is_stable_and_rounds_confidence():
    d = Decision(name="verdict", kind=NOUL, question="q", answer="PASS", confidence=0.123456,
                 reason="why", decider="heuristic")
    out = d.as_dict()
    assert out["confidence"] == 0.1235                       # rounded, JSON-stable
    assert out["name"] == "verdict" and out["decider"] == "heuristic"


class _FixableExec:
    """The check fails once (naming the code file), then passes after a repair - so a run records a
    repair_culprit decision AND a verdict decision."""
    def __init__(self, root):
        self.root = root
        self.code_writes = self.check_runs = 0

    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            self.check_runs += 1
            ok = self.check_runs >= 2
            return StepResult(ok, "ok" if ok
                              else "FAILED tests/test_x.py - logic.py:3 AssertionError: bad")
        self.code_writes += 1
        (self.root / step.file).write_text("x = 1\n", encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def test_run_records_culprit_and_verdict_decisions_into_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: [
        Step(id="logic", goal="", kind=WRITE, file="logic.py"),
        Step(id="check", goal="", kind=RUN, command="pytest", depends_on=["logic"]),
    ])
    res = LoopEngine(load_settings(tmp_path), executor=_FixableExec(tmp_path), effort=_ISOLATED).run("build")
    assert res.ok
    decs = res.evidence.as_dict()["decisions"]
    by_name = {d["name"]: d for d in decs}
    assert "repair_culprit" in by_name and "verdict" in by_name
    # the failure named logic.py directly -> high-confidence culprit pick, recorded as a Choice
    culprit = by_name["repair_culprit"]
    assert culprit["kind"] == CHOICE and culprit["answer"] == "logic.py"
    assert culprit["confidence"] == 0.9 and culprit["decider"] == "heuristic"
    assert res.evidence.summary()["decisions"] == len(decs)


def test_stalled_repair_records_a_no_progress_decision(tmp_path, monkeypatch):
    """When a file can't be fixed (same error recurs), the escalation is itself a logged decision."""
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setenv("NEWTON_LOOP_REPAIR_PROGRESS", "1")
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: [
        Step(id="logic", goal="", kind=WRITE, file="logic.py"),
        Step(id="check", goal="", kind=RUN, command="pytest", depends_on=["logic"]),
    ])

    class _Stuck:
        def __init__(self, root):
            self.root = root
        def execute(self, step, context, *, temperature: float = 0.1, **kw):
            if step.kind == RUN:
                return StepResult(False, "E AssertionError: expected 3 got 2\n  logic.py:5: in run")
            (self.root / step.file).write_text("def run():\n    return 2\n", encoding="utf-8")
            return StepResult(True, f"wrote {step.file}")

    res = LoopEngine(load_settings(tmp_path), executor=_Stuck(tmp_path), effort=_ISOLATED).run("build")
    names = [d["name"] for d in res.evidence.as_dict()["decisions"]]
    assert "repair_progress" in names                        # the escalation was logged as a decision
