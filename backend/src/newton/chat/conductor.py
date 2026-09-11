"""ChatConductor — answer a question about the project, grounded in retrieved context.

The same philosophy as the other conductors, minus the editing: the system gathers the
relevant context (repo index + memory + wiki), and the local model only phrases an answer
from it. It never edits files or invents APIs the project doesn't have.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..index import RepoIndex
from ..index.embeddings import Embedder
from ..llm import complete
from ..memory import Memory
from ..wiki import Wiki

CHAT_SYSTEM = (
    "You are Newton, a local coding assistant. Answer the user's question about THIS project "
    "using the provided context (code, notes, memory, conventions). Be concise and concrete, "
    "and cite the files or symbols you're drawing on. If the context doesn't cover the answer, "
    "say so plainly — never invent files, functions, or APIs the project doesn't have."
)


@dataclass
class ChatResult:
    ok: bool
    answer: str


class ChatConductor:
    def __init__(self, settings: Settings, *, emit: Callable[[str, Any], None],
                 approve: Callable[[str, dict], bool]) -> None:
        self.s = settings
        self.emit = emit
        self.embedder = Embedder()

    def run(self, question: str) -> ChatResult:
        self.emit("stage", "Search")
        blocks: list[str] = []
        labels: list[str] = []

        try:
            idx = RepoIndex(self.s.project_root, embedder=self.embedder).build()
            for c, _ in idx.search(question, k=6):
                blocks.append(f"### {c.path} · {c.name}\n```\n{c.text[:1200]}\n```")
                labels.append(("code", f"{c.path}:{c.name}"))
        except Exception:
            pass
        try:
            mem = Memory(self.s.project_root / ".newton" / "memory.jsonl", embedder=self.embedder)
            for m in mem.recall(question, k=2):
                blocks.append(f"### past work (memory)\n{m.text}")
                labels.append(("memory", "past task"))
        except Exception:
            pass
        try:
            for ref, text in Wiki(self.s.project_root, embedder=self.embedder).search(question, k=2):
                blocks.append(f"### wiki · {ref}\n{text}")
                labels.append(("wiki", ref))
        except Exception:
            pass

        self.emit("note", f"Searched the project — {len(blocks)} relevant piece(s) of context.")
        self.emit("context", {
            "stage": "Answer",
            "used": sum(len(b) // 4 for b in blocks),
            "budget": 6000,
            "blocks": [(k, lbl, len(b) // 4, False) for (k, lbl), b in zip(labels, blocks)],
            "dropped": [],
        })

        self.emit("stage", "Answer")
        context = "\n\n".join(blocks) or "(no matching context found in this project)"
        messages = [
            {"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": f"Project context:\n{context}\n\nQuestion: {question}"},
        ]
        try:
            resp = complete(self.s.agent_model, messages, temperature=0.2)
            answer = (resp.choices[0].message.content or "").strip() or "(no answer)"
        except Exception as e:
            return ChatResult(False, f"model error: {e}")

        self.emit("answer", answer)
        return ChatResult(True, answer)
