"""Background advisory collector - capture INTERMEDIATE check outcomes without slowing the loop.

Survivorship bias: at finalize a run's checks have mostly passed (the failure got repaired, or attempts
ran out), so a finalize-only corpus is nearly all 'ok'. The real signal - the broken imports, failing
asserts, tracebacks that got patched two turns later - lives in the intermediate iterations. Capturing
those naturally balances the corpus without synthesizing fake broken builds.

But Laya inference is ~0.5s, and running it synchronously on every intermediate check would slow the
repair loop. So this is a producer-consumer: `record()` is a fire-and-forget `put_nowait` (<0.05ms) on
the hot path; a daemon worker does the ~0.5s judgment and the disk append OUT OF BAND. Identical
outputs within a run are de-duplicated (SHA-256) so a repeated failure doesn't flood the corpus, and
the queue is bounded so a runaway build can't bloat memory.

Newton-native (not a generic router): it judges with `LayaJudge` (the real Laya shape), takes ground
truth from Newton's DETERMINISTIC StepResult (which already folds in `error_shaped` + pytest exit-5),
and writes the same `advisory.jsonl` schema `calibrate.py`/`advisory_report.py` read. Fail-soft
throughout - a collector error never touches the run.
"""

from __future__ import annotations

import hashlib
import queue
import threading
from pathlib import Path
from typing import Any

from .judge import append_advisory


class AdvisoryCollector:
    """One background worker per run. `record()` is non-blocking; `shutdown()` drains the backlog and
    stops the worker (so no thread lingers between runs)."""

    def __init__(self, judge: Any, output_path: Path, max_queue: int = 500) -> None:
        self.judge = judge
        self.output_path = Path(output_path)
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self.decisions: list[Any] = []                 # Laya Decisions produced this run (for evidence)
        self._worker = threading.Thread(target=self._run, name="advisory-collector", daemon=True)
        self._worker.start()

    def record(self, run_id: str, iteration: int, command: str, output: str, ok: bool) -> bool:
        """Fire-and-forget from the hot path (<0.05ms). De-dupes identical (command, output, result)
        within the run, and drops silently if the bounded queue is full."""
        if not output:
            return False
        h = hashlib.sha256(f"{command}|{output}|{ok}".encode("utf-8", "ignore")).hexdigest()
        with self._lock:
            if h in self._seen:
                return False
            self._seen.add(h)
        try:
            self._q.put_nowait((run_id, iteration, command, output, ok))
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is None:                       # shutdown sentinel
                    break
                self._evaluate(item)
            except Exception:
                pass                                    # advisory must never break anything
            finally:
                self._q.task_done()

    def _evaluate(self, item: tuple) -> None:
        run_id, iteration, command, output, ok = item
        d = self.judge.judge_failure(output)
        if d is None:                                  # model not loaded / unavailable -> skip cleanly
            return
        truth_failure = not ok                         # Newton's deterministic verdict is the label
        pred_failure = d.answer == "failure"
        p_failure = d.confidence if pred_failure else 1.0 - d.confidence
        d.reason += f" | {command or 'check'} #{iteration} truth={'failure' if truth_failure else 'ok'}"
        with self._lock:
            self.decisions.append(d)
        append_advisory(self.output_path, {
            "run_id": run_id, "iteration": iteration, "command": (command or "")[:120],
            "truth_failure": truth_failure, "pred_failure": pred_failure,
            "p_failure": round(p_failure, 4), "confidence": d.confidence,
            "correct": pred_failure == truth_failure})

    def drain(self) -> None:
        """Block until every queued judgment has been processed and written."""
        self._q.join()

    def shutdown(self) -> None:
        """Drain, then stop the worker (called at run end so no thread lingers)."""
        try:
            self._q.join()
            self._q.put_nowait(None)
        except Exception:
            pass
        self._worker.join(timeout=5.0)
