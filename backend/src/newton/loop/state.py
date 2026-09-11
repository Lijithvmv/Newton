"""Durable loop state — the DAG of atomic steps, checkpointed after every step.

This is what makes a long run resumable: the whole plan and every step's status live in one JSON
file that is rewritten after each step completes. An interrupted run reloads it and continues from
the first not-done step — never repeating finished work (idempotent resume, the durable-execution
pattern from the loop-engineering literature: journal the plan, replay only what's unfinished).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Step lifecycle.
PENDING, RUNNING, DONE, FAILED, BLOCKED = "pending", "running", "done", "failed", "blocked"

# Step kinds: a `write` step produces a file's content; a `run` step executes a command (e.g. tests).
WRITE, RUN = "write", "run"


@dataclass
class Step:
    id: str
    goal: str                                    # a single, explicit, atomic instruction
    kind: str = WRITE                            # WRITE (produce a file) | RUN (execute a command)
    file: str = ""                               # target file for a WRITE step
    command: str = ""                            # command for a RUN step ({py} → the interpreter)
    depends_on: list[str] = field(default_factory=list)
    status: str = PENDING
    attempts: int = 0
    result: str = ""                             # last outcome/detail (for context + audit)


@dataclass
class LoopState:
    goal: str
    steps: list[Step] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def by_id(self, sid: str) -> Step | None:
        return next((s for s in self.steps if s.id == sid), None)

    def ready(self) -> Step | None:
        """The next pending step whose dependencies are all done. Blocks a step whose dependency
        failed so we never build on broken ground."""
        for s in self.steps:
            if s.status != PENDING:
                continue
            deps = [self.by_id(d) for d in s.depends_on]
            if any(d is not None and d.status in (FAILED, BLOCKED) for d in deps):
                s.status = BLOCKED
                self.log.append(f"{s.id} blocked: a dependency failed")
                continue
            if all(d is not None and d.status == DONE for d in deps):
                return s
        return None

    def ready_batch(self, n: int) -> list[Step]:
        """Up to n ready steps that are SAFE to run concurrently. Applies the same block-on-failed-
        dependency rule as ready(). A wave is either WRITE steps on DISTINCT files (independent by the
        DAG, so their order and their file writes don't collide), or a single RUN step on its own — a
        check runs pytest over the whole tree, so it must never overlap a WRITE that could be mid-flight.
        n<=1 returns exactly what ready() would (one step), preserving sequential behaviour."""
        ready: list[Step] = []
        for s in self.steps:
            if s.status != PENDING:
                continue
            deps = [self.by_id(d) for d in s.depends_on]
            if any(d is not None and d.status in (FAILED, BLOCKED) for d in deps):
                s.status = BLOCKED
                self.log.append(f"{s.id} blocked: a dependency failed")
                continue
            if all(d is not None and d.status == DONE for d in deps):
                ready.append(s)
        if not ready:
            return []
        if n <= 1 or ready[0].kind == RUN:
            return [ready[0]]
        wave: list[Step] = []
        seen: set[str] = set()
        for s in ready:
            if s.kind == RUN:
                continue                          # checks never share a wave — they read everything
            f = (s.file or "").replace("\\", "/")
            if f and f in seen:
                continue                          # never two steps writing the same file at once
            seen.add(f)
            wave.append(s)
            if len(wave) >= n:
                break
        return wave or [ready[0]]

    def remaining(self) -> int:
        return sum(1 for s in self.steps if s.status in (PENDING, RUNNING))

    def all_done(self) -> bool:
        return bool(self.steps) and all(s.status == DONE for s in self.steps)

    def counts(self) -> dict[str, int]:
        c = {DONE: 0, FAILED: 0, BLOCKED: 0, PENDING: 0, RUNNING: 0}
        for s in self.steps:
            c[s.status] = c.get(s.status, 0) + 1
        return c

    def upstream_outputs(self, step: Step) -> list[Step]:
        """The completed dependency steps whose results feed this step's context (context
        propagation — a step sees what the steps it depends on already produced)."""
        return [d for d in (self.by_id(i) for i in step.depends_on) if d is not None and d.status == DONE]

    # --- durability -----------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "goal": self.goal,
            "steps": [asdict(s) for s in self.steps],
            "log": self.log,
        }, indent=2), encoding="utf-8")
        tmp.replace(path)                        # atomic write so a crash mid-save can't corrupt it

    @classmethod
    def load(cls, path: Path) -> LoopState:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(goal=d["goal"], steps=[Step(**s) for s in d["steps"]], log=d.get("log", []))
