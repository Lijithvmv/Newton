"""The loop surfaces its BUDGET — so the user sees a run is bounded, not open-ended (the quota
trust win). The engine emits a `budget` event at the start of a run carrying the effort caps."""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import StepResult
from newton.loop.state import RUN, WRITE, Step

_E = Effort("thorough", max_attempts=5, repair_cycles=3, replan_cycles=3,
            best_of_n=3, retrieval_k=6, budget_chars=12000)


class _OkExec:
    def __init__(self, root):
        self.root = root

    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            return StepResult(True, "ok")
        (self.root / step.file).write_text("x = 1\n", encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def test_run_emits_the_effort_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: [Step(id="m", goal="", kind=WRITE, file="m.py")])
    events: list[tuple[str, object]] = []
    LoopEngine(load_settings(tmp_path), executor=_OkExec(tmp_path), effort=_E,
               emit=lambda ch, p: events.append((ch, p))).run("build m")
    budget = next((p for ch, p in events if ch == "budget"), None)
    assert budget is not None                              # the ceiling is surfaced, not hidden
    assert budget == {"effort": "thorough", "attempts": 5, "repairs": 3, "replans": 3}
