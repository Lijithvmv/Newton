"""Local text embeddings via Ollama — the semantic half of hybrid retrieval.

BM25 is lexical: it needs shared words. Embeddings capture meaning, so a natural-language
goal can match code that shares no keywords. This wraps Ollama's embeddings endpoint and,
critically, degrades gracefully: if no embedding model is installed (or Ollama is down),
`available()` returns False and retrieval stays pure BM25 — no hard dependency.
"""

from __future__ import annotations

import math
import os

import httpx

DEFAULT_MODEL = os.environ.get("NEWTON_EMBED_MODEL", "nomic-embed-text")
OLLAMA = os.environ.get("OLLAMA_API_URL", "http://localhost:11434")


class Embedder:
    def __init__(self, model: str = DEFAULT_MODEL, url: str = OLLAMA, timeout: float = 60.0) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._available: bool | None = None

    def available(self) -> bool:
        """True if the model can actually embed. Checked once, cached; never raises."""
        if self._available is None:
            try:
                v = self.embed(["ping"])
                self._available = bool(v and v[0])
            except Exception:
                self._available = False
        return self._available

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text. One request per text (simple, and the candidate sets are small)."""
        out: list[list[float]] = []
        with httpx.Client(timeout=self.timeout) as c:
            for t in texts:
                r = c.post(f"{self.url}/api/embeddings", json={"model": self.model, "prompt": t})
                r.raise_for_status()
                out.append(r.json()["embedding"])
        return out


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
