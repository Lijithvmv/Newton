"""Typed decision records - the loop's bounded forks, made auditable.

The Jev-loop blueprint's design rules 4-5: keep thresholds in CODE, and KEEP THE PROBABILITIES - log
every bounded decision with its answer, confidence, and reason, so a run that went wrong shows whether
a fork was 'confident and wrong' or 'close and unlucky'. Newton already makes these forks (which file
is the culprit? is the repair making progress? what's the verdict?), but as inline heuristics whose
reasoning vanished after the run.

This records them as typed `Decision`s that flow into the run's evidence. Today the deciders are
heuristics, so their `confidence` is coarse and `decider` is "heuristic" - which the record states
honestly. The point is the SEAM: when a calibrated local model (Laya) later takes the judge fork, its
answer + real confidence flow through this same record to the log and UI with no further plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

CHOICE, SCORE, NOUL = "choice", "score", "noul"


@dataclass
class Decision:
    """One bounded fork the loop took. `kind` is the question shape (choice/score/noul); `answer` is
    the chosen value; `confidence` in [0,1] is how sure the decider was (coarse for a heuristic, real
    for a calibrated model); `options` is the distribution when there was one; `decider` names who
    decided so a heuristic call is distinguishable from a model call in the audit trail."""
    name: str                       # e.g. "repair_culprit", "repair_progress", "verdict"
    kind: str                       # CHOICE | SCORE | NOUL
    question: str                   # the bounded question, in words
    answer: str                     # the chosen option / label / yes|no
    confidence: float               # [0,1]
    reason: str = ""                # why - the signal the decision rode on
    decider: str = "heuristic"      # "heuristic" now; "laya" etc. later
    options: dict[str, float] = field(default_factory=dict)   # distribution, when applicable

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "question": self.question,
                "answer": self.answer, "confidence": round(self.confidence, 4),
                "reason": self.reason, "decider": self.decider, "options": self.options}


class Decider(Protocol):
    """A swappable source for a bounded decision. Newton's forks call heuristics today; a calibrated
    local model (Laya) can implement this same shape later and slot into the judge fork unchanged."""
    name: str

    def decide(self, question: str, state: str) -> Decision: ...
