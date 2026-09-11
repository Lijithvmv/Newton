"""JobQueue — run a batch of goals unattended, one at a time, checkpointed after every job.

Each job runs through the loop engine (its own decompose→build→verify→checkpoint). The queue adds
the batch-level durability: after each job finishes, the whole queue's state is written, so an
interrupted run resumes from the next unfinished job — never repeating completed ones.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, load_settings
from .state import DONE, FAILED, PENDING, RUNNING, QueueState


@dataclass
class QueueResult:
    ok: bool
    done: int
    failed: int
    state: QueueState


class JobQueue:
    def __init__(self, settings: Settings, *, emit: Callable[[str, Any], None] | None = None,
                 checkpoint: str = "queue.json",
                 loop_factory: Callable[[Settings], Any] | None = None) -> None:
        self.s = settings
        self.emit = emit or (lambda *a: None)
        self.checkpoint_path = Path(settings.project_root) / ".newton" / checkpoint
        self.loop_factory = loop_factory        # injectable engine builder (tests pass a fake)

    def _engine(self, settings: Settings):
        if self.loop_factory is not None:
            return self.loop_factory(settings)
        from ..loop import LoopEngine
        return LoopEngine(settings, emit=self.emit)   # forward loop events so per-job progress shows

    def run(self, state: QueueState | None = None, *, resume: bool = False) -> QueueResult:
        if resume and self.checkpoint_path.is_file():
            state = QueueState.load(self.checkpoint_path)
            self.emit("note", f"Resuming — {state.counts()[DONE]} of {len(state.jobs)} jobs already done.")
        if state is None:
            return QueueResult(False, 0, 0, QueueState())

        for j in state.jobs:                     # an interrupted (RUNNING) job restarts from scratch
            if j.status == RUNNING:
                j.status = PENDING
        state.save(self.checkpoint_path)

        while (job := state.next_pending()) is not None:
            job.status = RUNNING
            self.emit("job", {"id": job.id, "goal": job.goal, "status": "start",
                              "index": [j.id for j in state.jobs].index(job.id) + 1, "total": len(state.jobs)})
            settings = load_settings(job.project or str(self.s.project_root))
            settings.agent_model = self.s.agent_model
            try:
                result = self._engine(settings).run(job.goal)
                ok, answer = result.ok, result.answer
            except Exception as e:
                ok, answer = False, f"job crashed: {e}"
            job.status = DONE if ok else FAILED
            job.result = (answer or "")[:200]
            state.log.append(f"{job.id} {job.status}: {job.result}")
            state.save(self.checkpoint_path)     # checkpoint after EVERY job → whole batch resumable
            self.emit("job", {"id": job.id, "status": job.status, "detail": job.result})

        c = state.counts()
        ok = c[FAILED] == 0 and c[PENDING] == 0
        self.emit("queue", {"ok": ok, "done": c[DONE], "failed": c[FAILED], "total": len(state.jobs)})
        return QueueResult(ok, c[DONE], c[FAILED], state)
