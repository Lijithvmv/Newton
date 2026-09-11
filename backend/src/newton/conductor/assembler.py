"""The Context Assembler — fills the window, one stage at a time.

Every stage calls `assemble()` with its goal and the specific sources it needs. The
assembler packs them into a token budget, most-important-first, and returns both the
model messages AND a list of ContextBlocks — the exact provenance the Context Inspector
rail renders in the UI. Retrieval happens *per stage*, re-aimed at the current goal;
this is what separates Newton from retrieve-once RAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .state import WorkingState, est_tokens

Kind = Literal["system", "goal", "state", "project", "code", "wiki", "skill", "memory", "plan", "error"]


@dataclass
class ContextBlock:
    kind: Kind
    label: str
    text: str
    pinned: bool = False           # pinned blocks are never dropped to fit budget
    tokens: int = 0

    def __post_init__(self) -> None:
        self.tokens = est_tokens(self.text)


@dataclass
class Assembled:
    messages: list[dict[str, str]]
    blocks: list[ContextBlock]     # what actually made it into the window (for the inspector)
    dropped: list[ContextBlock] = field(default_factory=list)
    used_tokens: int = 0
    budget: int = 0


class ContextAssembler:
    def __init__(self, state: WorkingState, *, budget_tokens: int = 6000, state_budget: int = 500):
        self.state = state
        self.budget = budget_tokens
        self.state_budget = state_budget

    def assemble(
        self,
        *,
        system: str,
        stage_goal: str,
        instruction: str,
        sources: list[ContextBlock],
    ) -> Assembled:
        """Build the window for one stage.

        Order of priority (pinned first, then by declared order): system + stage goal +
        distilled working-state are always in; `sources` fill the remaining budget and the
        overflow is reported as `dropped` so the UI can show what was left out.
        """
        pinned: list[ContextBlock] = [
            ContextBlock("system", "Newton · instructions + tools", system, pinned=True),
            ContextBlock("goal", "This stage's goal", stage_goal, pinned=True),
            ContextBlock("state", "Working state (distilled)", self.state.summary(self.state_budget), pinned=True),
        ]

        used = sum(b.tokens for b in pinned)
        kept: list[ContextBlock] = []
        dropped: list[ContextBlock] = []
        for b in sources:
            if used + b.tokens <= self.budget or b.pinned:
                kept.append(b)
                used += b.tokens
            else:
                dropped.append(b)

        # Compose the actual chat messages. System carries the durable context; the user
        # message carries this stage's goal, the retrieved slices, and the concrete ask.
        context_text = "\n\n".join(f"## {b.label}\n{b.text}" for b in pinned[1:] + kept)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{context_text}\n\n## Your task now\n{instruction}"},
        ]

        return Assembled(
            messages=messages,
            blocks=pinned + kept,
            dropped=dropped,
            used_tokens=used,
            budget=self.budget,
        )
