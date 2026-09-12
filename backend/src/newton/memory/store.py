"""Memory — a small, local memory over .newton/memory.jsonl with salience and graph recall.

Each entry is one JSON line: a summary `text`, the `request`, the `files` and `symbols` it
touched (the graph edges), a timestamp, and — when an embedding model is available — the
text's embedding. Two properties beyond plain semantic recall:

  * salience — a near-duplicate of an existing memory is not stored, so memory stays lean;
  * graph recall — a query can be scoped to files/symbols, so "what have we done to X?"
    returns the memories that actually touched X, not just ones that sound similar;
  * provenance — every item records its `origin` (verified | user | agent | import | tool); recall
    weights similarity by trust, so a fact distilled from untrusted tool output can't resurface with
    the authority of one the operator confirmed (relevance hygiene *and* prompt-injection defence).
    `verify()` promotes a memory to `verified` (top trust) — the operator confirming a fact;
  * decay — recall also weights by an Ebbinghaus half-life, so fresh work outranks equally-similar
    stale work over Newton's long, unbounded runs — without ever deleting anything.

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

# --- provenance: where a memory came from, and how far we trust it -------
#
# Memory is written from inside the loop (the Remember step), so a fact distilled from raw tool
# output would otherwise be recalled with the SAME weight as one the user confirmed — which is both
# a relevance problem and a prompt-injection hole (a malicious line in tool output could resurface
# as if it were an instruction). Every item carries an immutable `origin`; recall multiplies the
# similarity by a trust weight, so untrusted origins must clear a higher relevance bar to surface.
TRUST_WEIGHTS = {
    "verified": 1.3,  # the operator EXPLICITLY confirmed this fact (promoted via verify()) — top trust
    "user": 1.0,      # a human-attended run (the operator was in the loop and approved the work)
    "agent": 0.9,     # Newton's own distilled conclusion (the default; unattended runs; legacy lines)
    "import": 0.75,   # ingested external document
    "tool": 0.5,      # raw command / tool output — untrusted; may carry injected text
}
DEFAULT_ORIGIN = "agent"

# --- decay: memory of a long-running agent must stay lean AND recent -----
#
# Newton's edge is unlimited-time runs, so .newton/memory.jsonl accumulates heavily. Nothing is
# deleted, but recall weights an item by an Ebbinghaus-style half-life: a memory's pull halves
# every HALF_LIFE_DAYS, so fresh work outranks equally-similar stale work without any pruning.
HALF_LIFE_DAYS = 30.0


def trust_weight(origin: str) -> float:
    """Trust multiplier for an origin; unknown origins fall back to the agent default."""
    return TRUST_WEIGHTS.get(origin, TRUST_WEIGHTS[DEFAULT_ORIGIN])


def recency_weight(ts: str, now: datetime | None = None) -> float:
    """Ebbinghaus decay in [0, 1]: 1.0 for a just-written memory, halving every HALF_LIFE_DAYS.
    A missing or unparseable timestamp is treated as no decay (weight 1.0) — never penalised."""
    if not ts:
        return 1.0
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return 1.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - t).total_seconds() / 86400.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


@dataclass
class MemoryItem:
    text: str
    request: str = ""
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    kind: str = "task"
    origin: str = DEFAULT_ORIGIN            # provenance: user | agent | tool | import
    ts: str = ""
    embedding: list[float] | None = None

    def to_json(self) -> dict[str, Any]:
        return {"ts": self.ts, "kind": self.kind, "origin": self.origin, "text": self.text,
                "request": self.request, "files": self.files, "symbols": self.symbols,
                "embedding": self.embedding}


class Memory:
    def __init__(self, path: Path, embedder=None) -> None:
        self.path = Path(path)
        self.embedder = embedder

    # --- write (with salience) -----------------------------------------

    def add(self, text: str, *, request: str = "", files: list[str] | None = None,
            symbols: list[str] | None = None, kind: str = "task",
            origin: str = DEFAULT_ORIGIN) -> MemoryItem | None:
        """Store a memory — unless it is a near-duplicate of one we already hold (salience).
        `origin` records provenance (user | agent | tool | import) and governs recall trust.
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
                          kind=kind, origin=origin,
                          ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          embedding=embedding)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_json()) + "\n")
        return item

    # --- verify (promote to top trust) ---------------------------------

    def verify(self, match: str) -> MemoryItem | None:
        """Promote the memory that best matches `match` to `origin='verified'` — the operator
        explicitly confirming a fact, so it recalls above any unconfirmed memory. Semantic match
        when an embedder is available, else a substring match on the text. Rewrites the store in
        place; returns the promoted item, or None if nothing matched. Idempotent."""
        items = self.all()
        if not items:
            return None
        target: MemoryItem | None = None
        if self.embedder is not None and self.embedder.available():
            qv = self.embedder.embed([match])[0]
            scored = sorted(((cosine(qv, it.embedding), it) for it in items if it.embedding),
                            key=lambda x: x[0], reverse=True)
            if scored and scored[0][0] >= RECALL_THRESHOLD:
                target = scored[0][1]
        if target is None:                                   # fallback: literal substring match
            m = match.lower()
            target = next((it for it in items if m in it.text.lower()), None)
        if target is None:
            return None
        target.origin = "verified"
        self._rewrite(items)
        return target

    def _rewrite(self, items: list[MemoryItem]) -> None:
        """Atomically rewrite the whole store (used by verify — add() only ever appends)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(it.to_json()) + "\n" for it in items), encoding="utf-8")
        tmp.replace(self.path)

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
                kind=d.get("kind", "task"), origin=d.get("origin", DEFAULT_ORIGIN),
                ts=d.get("ts", ""), embedding=d.get("embedding"),
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
                    now = datetime.now(timezone.utc)
                    scored: list[tuple[float, MemoryItem]] = []
                    for it in items:
                        if not it.embedding:
                            continue
                        sim = cosine(qv, it.embedding)
                        if sim < RECALL_THRESHOLD:          # relevance gate on RAW similarity
                            continue
                        # Rank survivors by trust- and recency-weighted similarity: an untrusted
                        # (e.g. tool-origin) or stale memory must be far more similar to outrank a
                        # trusted, recent one — injection defence and lean recall in one score.
                        weight = sim * trust_weight(it.origin) * recency_weight(it.ts, now)
                        scored.append((weight, it))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    sem_hits = [it for _, it in scored]
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
