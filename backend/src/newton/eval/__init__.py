"""Newton's evaluation harness — the instrument that measures whether Newton's core bet pays off.

Newton's whole thesis is that a *staged, verified pipeline* makes a weak local model produce more
trustworthy work than handing it the task naively. That claim has to be measured, not asserted:
this harness runs two arms — **baseline** (one naive prompt) and **newton** (the full staged
Conductor) — on the SAME model over objectively-checkable tasks, N times each (models are
stochastic), and reports the pass-rate delta. If Newton doesn't beat the baseline, we learn that
here instead of shipping faith.
"""

from .harness import ArmResult, report, run_baseline, run_newton, run_suite
from .tasks import SEED_TASKS, EvalTask

__all__ = ["EvalTask", "SEED_TASKS", "ArmResult", "run_suite", "run_baseline", "run_newton", "report"]
