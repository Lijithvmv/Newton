"""Newton's loop engine — the differentiator.

The spike proved the seam: a small local model reliably executes an ATOMIC, explicit step, but
cannot self-manage a COMPOUND goal (OpenWorker's raw loop rambled for 151s and produced nothing on
the same task this engine will complete). This engine is the layer that closes that gap.

It is a durable Plan → Execute → Verify → Replan loop over a dependency DAG (the pattern the loop-
and graph-engineering literature converges on): decompose the goal into atomic steps, run each
ready step through a reliable atomic executor with exactly the right context, verify it, and
checkpoint after every step so a run that takes hours can be interrupted and resumed. Quality comes
from iteration + right-context over unbounded time — the local model's advantages (free tokens,
unlimited time) turned into Claude-class output on tasks a naive prompt can't do.
"""

from .bestofn import Attempt, BestOfN, BestOfNResult
from .engine import ContextShaper, LoopEngine, LoopResult, RetrievalShaper
from .execute import NativeStepExecutor, StepExecutor, StepResult
from .explore import ExploreEngine, ExploreResult
from .state import DONE, FAILED, PENDING, RUNNING, LoopState, Step

__all__ = [
    "LoopEngine", "LoopResult", "LoopState", "Step",
    "BestOfN", "BestOfNResult", "Attempt",
    "ExploreEngine", "ExploreResult",
    "ContextShaper", "RetrievalShaper",
    "StepExecutor", "NativeStepExecutor", "StepResult",
    "PENDING", "RUNNING", "DONE", "FAILED",
]
