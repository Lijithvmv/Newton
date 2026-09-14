"""Effort — how much free local compute the loop is allowed to spend to reach verified quality.

Newton's edge is that tokens are free and time is unlimited, so it can pay for quality with COMPUTE
in a way a metered cloud model structurally cannot: retry more, retrieve wider, re-plan more, and run
more whole-task attempts, keeping only what verifies. Until now those knobs were timid module
constants (3 attempts, 2 repairs, 2 replans, 1 attempt). This turns them into a single dial with a
few named levels, so the SAME model can be pushed harder — and, critically, so the eval can sweep the
dial and MEASURE pass-rate as a function of compute spent (`newton-eval --curve`). If pass-rate climbs
with effort, that curve is the empirical statement of the moat; if it's flat, we've found the ceiling
honestly. Either way it's measured, not asserted (Rule 4).

`normal` is exactly today's behaviour (3/2/2/1, k=4, 8000 chars) so nothing regresses and the
already-measured results (D46/D49/D50) still hold at that level.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Effort:
    """One rung of the effort ladder. Every field is a compute knob the loop already honours."""

    name: str
    max_attempts: int      # per-step best-of-N by temperature (engine._run_step)
    repair_cycles: int     # test-failure-driven code fixes (engine.run)
    replan_cycles: int     # whole-plan re-decompositions (engine.run)
    best_of_n: int         # isolated whole-task attempts, verified selection (BestOfN)
    retrieval_k: int       # existing files retrieved into each step's window (RetrievalShaper)
    budget_chars: int      # per-step context window budget (RetrievalShaper)

    @property
    def rank(self) -> int:
        return ORDER.index(self.name) if self.name in ORDER else 1


# The ladder. Each rung spends strictly more compute than the last on every axis (monotonic — the
# eval curve depends on this). `normal` == the historical constants, so it is a no-op change by default.
LEVELS: dict[str, Effort] = {
    "quick":    Effort("quick",    max_attempts=2, repair_cycles=1, replan_cycles=1,
                       best_of_n=1, retrieval_k=3, budget_chars=6000),
    "normal":   Effort("normal",   max_attempts=3, repair_cycles=2, replan_cycles=2,
                       best_of_n=1, retrieval_k=4, budget_chars=8000),
    "thorough": Effort("thorough", max_attempts=5, repair_cycles=3, replan_cycles=3,
                       best_of_n=3, retrieval_k=6, budget_chars=12000),
    "max":      Effort("max",      max_attempts=8, repair_cycles=4, replan_cycles=4,
                       best_of_n=5, retrieval_k=8, budget_chars=16000),
}

ORDER = ["quick", "normal", "thorough", "max"]
DEFAULT = "normal"


def effort(name: str | None = None) -> Effort:
    """Resolve an effort level by name, falling back to `$NEWTON_EFFORT`, then `normal`. An unknown
    name is treated as `normal` rather than raising — a bad env var must never break a run."""
    key = (name or os.environ.get("NEWTON_EFFORT") or DEFAULT).strip().lower()
    return LEVELS.get(key, LEVELS[DEFAULT])
