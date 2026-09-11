"""Durable queue state — the batch of jobs, checkpointed after every job so it resumes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


@dataclass
class Job:
    id: str
    goal: str
    project: str = ""                    # optional per-job project dir; blank = the queue's project
    status: str = PENDING
    result: str = ""


@dataclass
class QueueState:
    jobs: list[Job] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def next_pending(self) -> Job | None:
        return next((j for j in self.jobs if j.status == PENDING), None)

    def remaining(self) -> int:
        return sum(1 for j in self.jobs if j.status in (PENDING, RUNNING))

    def counts(self) -> dict[str, int]:
        c = {DONE: 0, FAILED: 0, PENDING: 0, RUNNING: 0}
        for j in self.jobs:
            c[j.status] = c.get(j.status, 0) + 1
        return c

    # --- durability -----------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"jobs": [asdict(j) for j in self.jobs], "log": self.log},
                                  indent=2), encoding="utf-8")
        tmp.replace(path)                # atomic — a crash mid-save can't corrupt the checkpoint

    @classmethod
    def load(cls, path: Path) -> QueueState:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(jobs=[Job(**j) for j in d["jobs"]], log=d.get("log", []))

    @classmethod
    def from_jobs_file(cls, path: Path) -> QueueState:
        """Parse a jobs file: one job per line. A line is either a plain goal, or a JSON object
        {"goal": ..., "project": ..., "id": ...}. Blank lines and #-comments are skipped."""
        jobs: list[Job] = []
        for _i, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            goal, project, jid = line, "", f"j{len(jobs) + 1}"
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    goal = str(obj.get("goal") or "").strip()
                    project = str(obj.get("project") or "")
                    jid = str(obj.get("id") or jid)
                except json.JSONDecodeError:
                    goal = line
            if goal:
                jobs.append(Job(id=jid, goal=goal, project=project))
        return cls(jobs=jobs)
