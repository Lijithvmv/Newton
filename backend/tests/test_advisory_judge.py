"""The advisory Laya judge - eval-first placement that NEVER changes what the loop does.

Proves the safe-placement contract: with an advisory judge, each check is scored ALONGSIDE the
deterministic ground truth, recorded as a laya-decider Decision, and logged for cross-run measurement
- but the run's result is identical to a run without it. The judge is injected (a fake), so the tests
are fast and offline; the real Laya path is exercised separately by a live run."""

from __future__ import annotations

from newton.config import load_settings
from newton.eval.advisory_report import report
from newton.loop.decision import NOUL, Decision
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import StepResult
from newton.loop.judge import read_advisory
from newton.loop.state import RUN, WRITE, Step

_ISOLATED = Effort("test", max_attempts=1, repair_cycles=1, replan_cycles=0,
                   best_of_n=1, retrieval_k=3, budget_chars=6000)


class _FakeJudge:
    """A deterministic stand-in for Laya: predicts 'failure' iff the output contains FAIL."""
    def judge_failure(self, text: str) -> Decision:
        pred = "failure" if "FAIL" in text.upper() else "ok"
        return Decision(name="advisory_output_check", kind=NOUL, question="failure?",
                        answer=pred, confidence=0.8, reason="fake", decider="laya")


def _plan():
    return [Step(id="logic", goal="", kind=WRITE, file="logic.py"),
            Step(id="check", goal="", kind=RUN, command="pytest", depends_on=["logic"])]


class _OkExec:
    def __init__(self, root):
        self.root = root
    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            return StepResult(True, "3 passed")
        (self.root / step.file).write_text("x = 1\n", encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def test_advisory_judge_records_and_logs_without_acting(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: _plan())
    eng = LoopEngine(load_settings(tmp_path), executor=_OkExec(tmp_path), effort=_ISOLATED,
                     judge=_FakeJudge())
    res = eng.run("build")
    assert res.ok                                        # advisory changed nothing about the outcome
    laya = [d for d in res.evidence.as_dict()["decisions"] if d["decider"] == "laya"]
    assert len(laya) == 1 and laya[0]["answer"] == "ok"  # "3 passed" judged not-a-failure
    rows = read_advisory(tmp_path / ".newton" / "advisory.jsonl")
    assert len(rows) == 1
    assert rows[0]["truth_failure"] is False and rows[0]["pred_failure"] is False
    assert rows[0]["correct"] is True                    # agreed with the deterministic result


def test_no_judge_and_disabled_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.delenv("NEWTON_LOOP_ADVISORY_JUDGE", raising=False)
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: _plan())
    eng = LoopEngine(load_settings(tmp_path), executor=_OkExec(tmp_path), effort=_ISOLATED)
    res = eng.run("build")
    assert not any(d["decider"] == "laya" for d in res.evidence.as_dict()["decisions"])
    assert not (tmp_path / ".newton" / "advisory.jsonl").exists()   # nothing logged when off


def test_report_computes_agreement_from_rows(capsys):
    rows = [
        {"truth_failure": True, "pred_failure": True, "confidence": 0.95, "correct": True},
        {"truth_failure": False, "pred_failure": False, "confidence": 0.9, "correct": True},
        {"truth_failure": True, "pred_failure": False, "confidence": 0.55, "correct": False},
    ]
    out = report(rows)
    assert out["n"] == 3
    assert abs(out["agreement"] - 2 / 3) < 1e-9
    assert out["recall"] == 0.5                          # 1 of 2 real failures caught
    assert "agreement" in capsys.readouterr().out


class _FlakyCheckExec(_OkExec):
    """The check fails on its first attempt, then passes — the intermediate failure a finalize-only
    corpus would never see (survivorship bias)."""
    def __init__(self, root):
        super().__init__(root)
        self.runs = 0
    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            self.runs += 1
            return (StepResult(False, "=== 1 FAILED, 2 passed ===") if self.runs == 1
                    else StepResult(True, "3 passed"))
        return super().execute(step, context, temperature=temperature)


def test_collector_captures_intermediate_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: _plan())
    retry = Effort("test", max_attempts=2, repair_cycles=0, replan_cycles=0,
                   best_of_n=1, retrieval_k=3, budget_chars=6000)
    eng = LoopEngine(load_settings(tmp_path), executor=_FlakyCheckExec(tmp_path), effort=retry,
                     judge=_FakeJudge())
    assert eng.run("build").ok
    rows = read_advisory(tmp_path / ".newton" / "advisory.jsonl")
    assert [r["truth_failure"] for r in rows] == [True, False]   # the failed attempt is in the corpus
    assert all(r["correct"] for r in rows)
    assert len({r["run_id"] for r in rows}) == 1 and rows[0]["command"] == "pytest"


def test_collector_dedupes_and_never_blocks(tmp_path):
    import threading
    import time

    from newton.loop.advisory_collector import AdvisoryCollector

    gate = threading.Event()

    class _SlowJudge(_FakeJudge):
        def judge_failure(self, text):
            gate.wait(5)                                  # simulate ~0.5s Laya inference
            return super().judge_failure(text)

    c = AdvisoryCollector(_SlowJudge(), tmp_path / "a.jsonl")
    t0 = time.perf_counter()
    assert c.record("r", 1, "pytest", "1 FAILED", False) is True
    assert c.record("r", 2, "pytest", "1 FAILED", False) is False   # identical output -> skipped
    assert c.record("r", 3, "pytest", "3 passed", True) is True
    assert time.perf_counter() - t0 < 0.05                # hot path didn't wait on the judge
    gate.set()
    c.shutdown()
    assert len(read_advisory(tmp_path / "a.jsonl")) == 2
    assert not c._worker.is_alive()
