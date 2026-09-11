"""Project-level state — the backlog the Project Conductor holds and the task level never sees.

This is the "large context" at the project altitude: every task, its dependencies, and its
status. Readiness is dependency-driven — a task runs only once its prerequisites are done,
and is blocked if any prerequisite failed. This structured backlog is what lets a whole
project proceed without any single model call needing to hold the whole thing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Task lifecycle.
PENDING, RUNNING, DONE, FAILED, BLOCKED = "pending", "running", "done", "failed", "blocked"


@dataclass
class Task:
    id: str
    title: str
    goal: str                       # a self-contained instruction for a Task Conductor
    file: str = ""                  # the file this task centers on, if any
    component: str = ""             # the architecture component this file belongs to
    depends_on: list[str] = field(default_factory=list)
    status: str = PENDING
    summary: str = ""


@dataclass
class ProjectState:
    goal: str
    tasks: list[Task] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def by_id(self, tid: str) -> Task | None:
        return next((t for t in self.tasks if t.id == tid), None)

    def ready(self) -> Task | None:
        """The next pending task whose dependencies are all done. Blocks tasks whose deps
        have failed/blocked so we never run something built on broken ground."""
        for t in self.tasks:
            if t.status != PENDING:
                continue
            deps = [self.by_id(d) for d in t.depends_on]
            if any(d is not None and d.status in (FAILED, BLOCKED) for d in deps):
                t.status = BLOCKED
                self.log.append(f"{t.id} blocked: a dependency failed")
                continue
            if all(d is not None and d.status == DONE for d in deps if d is not None) and \
               all(self.by_id(d) is not None for d in t.depends_on):
                return t
            if not t.depends_on:
                return t
        return None

    def remaining(self) -> int:
        return sum(1 for t in self.tasks if t.status in (PENDING, RUNNING))

    def counts(self) -> dict[str, int]:
        c = {DONE: 0, FAILED: 0, BLOCKED: 0, PENDING: 0, RUNNING: 0}
        for t in self.tasks:
            c[t.status] = c.get(t.status, 0) + 1
        return c

    def save(self, path: Path) -> None:
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps({"goal": self.goal, "tasks": [asdict(t) for t in self.tasks],
                                    "log": self.log}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ProjectState:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(goal=d["goal"], tasks=[Task(**t) for t in d["tasks"]], log=d.get("log", []))
