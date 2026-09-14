"""Effort ladder — model-free proof that the compute dial is real and wired into the loop.

No Ollama here: we assert the ladder's shape, that `normal` is the historical no-op, and that the
LoopEngine actually spends the level's attempt budget (a scripted failing executor is called exactly
`max_attempts` times, and a passing one stops after one). The pass-rate-vs-compute claim itself is
measured separately with a real model via `newton-eval --curve`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from newton.config import load_settings
from newton.loop import ORDER, LoopEngine, effort
from newton.loop.engine import MAX_ATTEMPTS, REPAIR_CYCLES, REPLAN_CYCLES, ContextShaper
from newton.loop.execute import StepResult
from newton.loop.state import WRITE, LoopState, Step


def test_normal_is_the_historical_no_op():
    """`normal` must equal the constants the engine shipped with — so the default change nothing."""
    e = effort("normal")
    assert (e.max_attempts, e.repair_cycles, e.replan_cycles) == (MAX_ATTEMPTS, REPAIR_CYCLES, REPLAN_CYCLES)
    assert e.best_of_n == 1                      # the historical in-place single run


def test_ladder_is_monotonic():
    """Every rung must spend >= the previous on EVERY axis — the curve's meaning depends on it."""
    rungs = [effort(name) for name in ORDER]
    for lo, hi in zip(rungs, rungs[1:]):
        assert hi.max_attempts >= lo.max_attempts
        assert hi.repair_cycles >= lo.repair_cycles
        assert hi.replan_cycles >= lo.replan_cycles
        assert hi.best_of_n >= lo.best_of_n
        assert hi.retrieval_k >= lo.retrieval_k
        assert hi.budget_chars >= lo.budget_chars
    # and strictly increasing overall (quick spends less than max somewhere on every axis)
    assert effort("max").max_attempts > effort("quick").max_attempts


def test_unknown_and_missing_resolve_to_normal():
    assert effort("nonsense") is effort("normal")
    assert effort(None) is effort("normal")


def test_env_override(monkeypatch):
    monkeypatch.setenv("NEWTON_EFFORT", "thorough")
    assert effort().name == "thorough"


class _ScriptedExecutor:
    """Counts execute() calls; returns a fixed result. Lets us prove the engine honours the budget
    without any model."""

    def __init__(self, ok: bool):
        self.ok = ok
        self.calls = 0

    def execute(self, step, context, *, temperature: float = 0.1) -> StepResult:
        self.calls += 1
        return StepResult(self.ok, "ok" if self.ok else "nope")


def _engine(root: Path, level: str, executor):
    settings = load_settings(str(root))
    # Plain ContextShaper (no RepoIndex build) keeps the test fast and model-free.
    return LoopEngine(settings, executor=executor, shaper=ContextShaper(root), effort=effort(level))


def test_engine_spends_the_attempt_budget_on_failure():
    """A step whose executor always fails is retried exactly `max_attempts` times — proving higher
    effort really does buy more attempts on the SAME step."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for level in ("quick", "thorough", "max"):
            ex = _ScriptedExecutor(ok=False)
            eng = _engine(root, level, ex)
            step = Step(id="s1", goal="write a thing", kind=WRITE, file="notes.txt")
            eng._run_step(step, LoopState(goal="g", steps=[step]))
            assert ex.calls == effort(level).max_attempts, level
            assert step.status == "failed"


def test_engine_stops_early_when_a_step_verifies():
    """A passing non-.py write verifies immediately, so even at max effort it runs once — compute is
    spent to REACH quality, not wasted once quality is reached."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ex = _ScriptedExecutor(ok=True)
        eng = _engine(root, "max", ex)
        step = Step(id="s1", goal="write notes", kind=WRITE, file="notes.txt")
        eng._run_step(step, LoopState(goal="g", steps=[step]))
        assert ex.calls == 1
        assert step.status == "done"


def test_effort_scales_the_retrieval_window():
    """Higher effort widens the context window + retrieved-file count the shaper is built with."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        quick = LoopEngine(load_settings(str(root)), effort=effort("quick"))
        mx = LoopEngine(load_settings(str(root)), effort=effort("max"))
        assert mx.shaper.budget_chars > quick.shaper.budget_chars
        assert mx.shaper.k > quick.shaper.k
