"""Evidence — what a finished run actually PROVED, bound to its verdict.

A verdict that only says PASS / NEEDS ATTENTION / WOULD BLOCK asks the user to trust a label. Newton's
whole bet is trustworthy output from a weak local model, so a result should carry its proof: the
verification CHECKS that ran (the tests, the whole-app route probe) with their captured outcome, and
a content FINGERPRINT of every file the run produced — so a later step, the History view, or the user
can trust the result without re-running it. This records what the existing gates already established;
it is the durable artifact of verification, not a new capability claim.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .state import DONE, RUN, LoopState


@dataclass
class Check:
    """A verification step that ran and what it concluded — the RUN steps (tests, integration probe)."""
    id: str
    goal: str
    passed: bool
    detail: str = ""            # captured outcome — the actual proof line(s), or the failure


@dataclass
class Artifact:
    """A file the run produced, fingerprinted so the result is verifiable without re-running it."""
    file: str
    sha256: str
    bytes: int


@dataclass
class Evidence:
    verified: bool                                  # every step done AND every check passed
    checks: list[Check] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    @property
    def checks_passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    def summary(self) -> dict:
        """A compact form for the run log / History — counts only, no large detail blobs."""
        return {"verified": self.verified, "checks": len(self.checks),
                "checks_passed": self.checks_passed, "artifacts": len(self.artifacts)}

    def as_dict(self) -> dict:
        return asdict(self)


def _sha256(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def collect_evidence(state: LoopState, root: Path) -> Evidence:
    """Build the evidence record from a finished loop state: the checks that ran (RUN steps) with
    their outcome, and a sha256 fingerprint of each produced file that exists on disk. Best-effort on
    the hashes — a file that vanished is simply omitted, never an error."""
    checks = [Check(id=s.id, goal=s.goal or s.command or s.id,
                    passed=s.status == DONE, detail=(s.result or "")[:600])
              for s in state.steps if s.kind == RUN]
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for s in state.steps:
        rel = s.file.replace("\\", "/")
        if s.kind == RUN or not rel or s.status != DONE or rel in seen:
            continue
        p = root / s.file
        try:
            if p.is_file():
                digest, n = _sha256(p)
                artifacts.append(Artifact(file=rel, sha256=digest, bytes=n))
                seen.add(rel)
        except OSError:
            continue
    verified = state.all_done() and all(c.passed for c in checks)
    return Evidence(verified=verified, checks=checks, artifacts=artifacts)
