"""Advisory Laya judge - the eval-first placement of a local, calibrated decision model.

The Jev-loop blueprint's staged adoption puts a decision model in the EVALUATION role FIRST, because
evaluation is the one placement where the model's answer does not change what the agent does. So this
runs Laya as an ADVISORY judge: at the end of a run it scores each check's output with the same
output-failure Noul the spike measured, records its answer as a Decision (decider="laya") ALONGSIDE
the deterministic ground truth, and appends the (ground_truth, prediction, confidence) pair to an
advisory log. It never gates, blocks, or changes a single loop action.

Over real runs that log becomes a real labeled corpus - Newton's OWN outputs, labeled by the
deterministic result - to measure Laya's agreement and drift, and later to calibrate it before it is
ever trusted to act. Optional + lazy: Laya (torch/transformers, ~808MB) loads only when advisory
judging is switched on (NEWTON_LOOP_ADVISORY_JUDGE=1) and is installed; Newton runs identically
without it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .decision import NOUL, Decision

_FAILURE_Q = (
    "Does this command, build, or test output indicate that it FAILED - an error, a traceback, a "
    "crash, or failing tests - as opposed to succeeding (tests passing, a clean build, a warning "
    "that is not fatal)?"
)


class LayaJudge:
    """A process-wide Laya judge that loads the model in the BACKGROUND and never blocks a run.

    The model load is heavy (tens of seconds), so it must not sit in the loop's critical path - an
    advisory judge that delays the user's result is a broken advisory judge. So the first construction
    kicks a background thread to load the model; until it's ready, `judge_failure` returns None and the
    run proceeds untouched. Once loaded, the cached agent scores every later check in ~sub-second. Every
    path fails soft: not-installed, still-loading, or a call error all return None."""

    _lock = threading.Lock()
    _agent: Any = None
    _unavailable: str | None = None
    _loading = False

    def __init__(self, model: str = "convaiinnovations/laya") -> None:
        self.model = model
        self.start_loading()

    def start_loading(self) -> None:
        """Kick a one-time background load if it hasn't started. Non-blocking."""
        with LayaJudge._lock:
            if (LayaJudge._agent is not None or LayaJudge._unavailable is not None
                    or LayaJudge._loading):
                return
            LayaJudge._loading = True
        threading.Thread(target=self._load, name="laya-load", daemon=True).start()

    def _load(self) -> None:
        try:
            import laya  # type: ignore
            agent = laya.load(self.model)
            with LayaJudge._lock:
                LayaJudge._agent = agent
        except Exception as e:                        # ImportError or a load/runtime failure
            with LayaJudge._lock:
                LayaJudge._unavailable = str(e)
        finally:
            with LayaJudge._lock:
                LayaJudge._loading = False

    @property
    def available(self) -> bool:
        """True only once the background load has finished successfully — never triggers a blocking
        load, so it's safe to poll in the hot path."""
        return LayaJudge._agent is not None

    def judge_failure(self, text: str) -> Decision | None:
        """Score one check output as a failure-or-not Noul. Returns a Laya `Decision`, or None when
        the model isn't loaded yet (still loading / not installed) or the call fails - never blocks."""
        if not text or not self.available:
            return None
        try:
            res = LayaJudge._agent.predict(
                text, {"failed": {"type": "noul", "instructions": _FAILURE_Q}})
            ans = res["answers"]["failed"]
            p = float(ans["noul"])
            conf = float(ans.get("confidence", max(p, 1.0 - p)))
        except Exception:
            return None
        return Decision(name="advisory_output_check", kind=NOUL,
                        question="Is this check output a failure?",
                        answer="failure" if p >= 0.5 else "ok",
                        confidence=conf, reason=f"P(failure)={p:.3f}", decider="laya")


def append_advisory(path: Path, row: dict[str, Any]) -> None:
    """Append one (ground_truth, prediction, confidence) record to the advisory JSONL log. Best-effort
    - a logging failure must never break a run."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**row, "at": time.time()}) + "\n")
    except OSError:
        pass


def read_advisory(path: Path) -> list[dict]:
    """All advisory records, oldest first. Missing/corrupt lines are skipped, never fatal."""
    rows: list[dict] = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows
