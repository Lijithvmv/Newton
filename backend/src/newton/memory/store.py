"""Memory — a small, local memory over .newton/memory.jsonl with salience and graph recall.

Each entry is one JSON line: a summary `text`, the `request`, the `files` and `symbols` it
touched (the graph edges), a timestamp, and — when an embedding model is available — the
text's embedding. Two properties beyond plain semantic recall:

  * salience — a near-duplicate of an existing memory is not stored, so memory stays lean;
  * graph recall — a query can be scoped to files/symbols, so "what have we done to X?"
    returns the memories that actually touched X, not just ones that sound similar.

Reuses Newton's own embedder rather than pulling in Mem0/Chroma — fully local.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..index.embeddings import cosine

RECALL_THRESHOLD = 0.55   # cosine below this is "not relevant enough" for semantic recall
DEDUP_THRESHOLD = 0.92    # cosine at/above this means "we already remember essentially this"


@dataclass
class MemoryItem:
    text: str
    request: str = ""
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    kind: str = "task"
    ts: str = ""
    embedding: list[float] | None = None

    def to_json(self) -> dict[str, Any]:
        return {"ts": self.ts, "kind": self.kind, "text": self.text, "request": self.request,
                "files": self.files, "symbols": self.symbols, "embedding": self.embedding}


class Memory:
    def __init__(self, path: Path, embedder=None) -> None:
        self.path = Path(path)
        self.embedder = embedder

    # --- write (with salience) -----------------------------------------

    def add(self, text: str, *, request: str = "", files: list[str] | None = None,
            symbols: list[str] | None = None, kind: str = "task") -> MemoryItem | None:
        """Store a memory — unless it is a near-duplicate of one we already hold (salience).
        Returns the stored item, or None if it was skipped as redundant."""
        embedding = None
        if self.embedder is not None:
            try:
                if self.embedder.available():
                    embedding = self.embedder.embed([text])[0]
            except Exception:
                embedding = None
        # Salience: don't remember what we essentially already remember.
        if embedding is not None:
            for it in self.all():
                if it.embedding and cosine(embedding, it.embedding) >= DEDUP_THRESHOLD:
                    return None
        item = MemoryItem(text=text, request=request, files=files or [], symbols=symbols or [],
                          kind=kind, ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          embedding=embedding)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_json()) + "\n")
        return item

    # --- read ----------------------------------------------------------

    def all(self) -> list[MemoryItem]:
        if not self.path.is_file():
            return []
        items: list[MemoryItem] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            items.append(MemoryItem(
                text=d.get("text") or d.get("request", ""), request=d.get("request", ""),
                files=d.get("files", []), symbols=d.get("symbols", []),
                kind=d.get("kind", "task"), ts=d.get("ts", ""), embedding=d.get("embedding"),
            ))
        return items

    def touching(self, entities: list[str]) -> list[MemoryItem]:
        """Graph recall: memories that touched any of these files/symbols (newest first)."""
        want = {e for e in entities if e}
        if not want:
            return []
        hits = [it for it in self.all() if want & (set(it.files) | set(it.symbols))]
        return list(reversed(hits))

    def recall(self, query: str, k: int = 3, files: list[str] | None = None) -> list[MemoryItem]:
        """Relevant past items for this task: memories that touched the same files/symbols
        (graph) come first, then semantically-similar ones, merged and capped at k."""
        items = self.all()
        if not items:
            return []

        graph_hits = self.touching(files or [])

        sem_hits: list[MemoryItem] = []
        semantic_ok = False
        if self.embedder is not None:
            try:
                if self.embedder.available():
                    semantic_ok = True
                    qv = self.embedder.embed([query])[0]
                    scored = [(cosine(qv, it.embedding), it) for it in items if it.embedding]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    sem_hits = [it for s, it in scored if s >= RECALL_THRESHOLD]
            except Exception:
                semantic_ok = False
        if not semantic_ok and not graph_hits:
            return items[-k:]                     # no way to rank → most recent

        merged: list[MemoryItem] = []
        seen: set[str] = set()
        for it in graph_hits + sem_hits:          # graph edges take priority
            key = f"{it.ts}|{it.text[:24]}"
            if key not in seen:
                seen.add(key)
                merged.append(it)
        return merged[:k]
