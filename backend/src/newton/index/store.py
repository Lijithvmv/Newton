"""RepoIndex — the searchable whole-repo context.

Walks a repository, chunks every file, analyzes Python into the code graph, and builds a
pure-Python BM25 index over the chunks. BM25 is lexical, but the tokenizer splits
identifiers (ContextAssembler -> context, assembler) so a natural-language goal matches
code symbols. No embeddings, no services — and the interface is ready for a semantic
re-ranker (Ollama embeddings) to slot in later as a second stage.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from .chunker import Chunk, chunk_file
from .graph import CodeGraph, analyze_python

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")
_SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", "vendor", ".pytest_cache", ".newton"}
# How much a file's structural centrality can lift its relevance score (mild — relevance leads).
_CENTRALITY_WEIGHT = 0.30
_TEXT_EXT = {".py", ".md", ".markdown", ".txt", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml", ".js", ".ts"}


def _split_ident(tok: str) -> list[str]:
    parts: list[str] = []
    for piece in tok.split("_"):
        parts.extend(_CAMEL.findall(piece) or [piece])
    return [p.lower() for p in parts if p]


def tokenize(text: str) -> list[str]:
    """Lowercased words plus identifier sub-tokens, so NL queries hit code symbols."""
    out: list[str] = []
    for m in _WORD.findall(text):
        low = m.lower()
        out.append(low)
        sub = _split_ident(m)
        if len(sub) > 1:
            out.extend(sub)
    return out


class RepoIndex:
    def __init__(self, root: Path, *, k1: float = 1.5, b: float = 0.75, embedder=None) -> None:
        self.root = Path(root).resolve()
        self.k1, self.b = k1, b
        self.embedder = embedder          # optional semantic re-ranker; None = pure BM25
        self.chunks: list[Chunk] = []
        self.graph = CodeGraph()
        self._tf: list[Counter] = []
        self._len: list[int] = []
        self._df: Counter = Counter()
        self._avg = 0.0
        self._emb: dict[str, list[float]] = {}   # content-hash → embedding (survives edits)
        self._mtimes: dict[str, float] = {}
        self.loaded_from_cache = False

    # --- build (mtime-cached) ------------------------------------------

    @property
    def _cache_path(self) -> Path:
        return self.root / ".newton" / "index.json"

    def _scan_mtimes(self) -> dict[str, float]:
        """Modification times of every indexable file — the cache freshness key. Cheap (stat only)."""
        out: dict[str, float] = {}
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _TEXT_EXT:
                continue
            rel = path.relative_to(self.root)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            out[rel.as_posix()] = path.stat().st_mtime
        return out

    def build(self) -> RepoIndex:
        self._mtimes = self._scan_mtimes()
        cached = None
        if self._cache_path.is_file():
            try:
                cached = json.loads(self._cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        # Carry embeddings forward regardless of freshness — unchanged code keeps its vector.
        if cached:
            self._emb = {k: v for k, v in cached.get("embeddings", {}).items()}

        if cached and cached.get("mtimes") == self._mtimes:
            self.chunks = [Chunk(**c) for c in cached["chunks"]]
            self.graph = CodeGraph.from_dict(cached["graph"])
            self._index()
            self.loaded_from_cache = True
            return self

        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if not path.is_file() or path.suffix.lower() not in _TEXT_EXT:
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(self.root).parts):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            self.chunks.extend(chunk_file(rel, source))
            if path.suffix == ".py":
                self.graph.add(analyze_python(rel, source))
        self.graph.finalize()
        self._index()
        self._write_cache()
        return self

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha1(text[:1200].encode("utf-8", "ignore")).hexdigest()

    def _write_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(exist_ok=True, parents=True)
            self._cache_path.write_text(json.dumps({
                "root": str(self.root),
                "mtimes": self._mtimes,
                "chunks": [c.__dict__ for c in self.chunks],
                "graph": self.graph.to_dict(),
                "embeddings": self._emb,
            }), encoding="utf-8")
        except OSError:
            pass

    def _doc_tokens(self, c: Chunk) -> list[str]:
        # Boost the symbol name and path — a search for a function name should find it.
        return tokenize(f"{c.name} {c.name} {c.path} {c.kind} {c.text}")

    def _index(self) -> None:
        self._tf = [Counter(self._doc_tokens(c)) for c in self.chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        self._df = Counter()
        for tf in self._tf:
            self._df.update(tf.keys())

    # --- search --------------------------------------------------------

    def _bm25(self, query: str) -> list[tuple[int, float]]:
        """Lexical scores per chunk index, sorted desc, positives only."""
        q = tokenize(query)
        n = len(self.chunks)
        if not q or n == 0:
            return []
        scores = [0.0] * n
        for term in set(q):
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self._len[i] / (self._avg or 1))
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        return [(i, scores[i]) for i in ranked if scores[i] > 0]

    def search(self, query: str, k: int = 8, focus: list[str] | None = None) -> list[tuple[Chunk, float]]:
        """Hybrid retrieval in three stages: BM25 finds lexical candidates; an embedder (if
        available) re-ranks them by semantic similarity; then a PageRank centrality prior over
        the import graph lifts structurally-important files among the relevant ones. Relevance
        always leads — centrality only reorders comparably-relevant hits and never surfaces an
        irrelevant file. `focus` (e.g. the task's target file) personalizes the centrality walk."""
        bm = self._bm25(query)
        if not bm:
            return []
        pool = bm[: max(k * 3, 15)]
        relevance = pool
        if self.embedder is not None:
            reranked = self._semantic_rerank(query, pool)
            if reranked is not None:
                relevance = reranked
        ranked = self._apply_centrality(relevance, focus)
        return [(self.chunks[i], s) for i, s in ranked[:k]]

    def _apply_centrality(self, relevance: list[tuple[int, float]],
                          focus: list[str] | None) -> list[tuple[int, float]]:
        """Blend a file's PageRank into its relevance score: final = rel_norm · (1 + w·pr_norm),
        both normalized within the candidate pool. With no import edges (a markdown-only repo)
        PageRank is uniform, so the blend is a no-op and ordering is unchanged."""
        if not relevance:
            return relevance
        pr = self.graph.pagerank(self._focus_weights(focus))
        ranked = sorted(relevance, key=lambda x: x[1], reverse=True)
        if not pr:
            return ranked
        max_rel = max(s for _, s in relevance) or 1.0
        pool_paths = {self.chunks[i].path for i, _ in relevance}
        max_pr = max((pr.get(p, 0.0) for p in pool_paths), default=0.0) or 1.0
        blended = []
        for i, s in relevance:
            rel_norm = s / max_rel
            pr_norm = pr.get(self.chunks[i].path, 0.0) / max_pr
            blended.append((i, rel_norm * (1 + _CENTRALITY_WEIGHT * pr_norm)))
        blended.sort(key=lambda x: x[1], reverse=True)
        return blended

    def _focus_weights(self, focus: list[str] | None) -> dict[str, float] | None:
        """Map focus file paths to a personalization vector, keeping only real graph nodes."""
        if not focus:
            return None
        weights: dict[str, float] = {}
        for f in focus:
            rel = f.replace("\\", "/")
            if rel in self.graph.files:
                weights[rel] = 1.0
        return weights or None

    def _semantic_rerank(self, query: str, candidates: list[tuple[int, float]]):
        """Blend normalized BM25 with cosine of query↔chunk embeddings. Chunk vectors come
        from the content-hashed cache; only cache-miss chunks are embedded (and persisted), so
        unchanged code is never re-embedded. Returns None (→ pure BM25) on any failure."""
        from .embeddings import cosine

        try:
            if not self.embedder.available():
                return None
            qv = self.embedder.embed([query])[0]
        except Exception:
            return None

        misses = [i for i, _ in candidates if self._hash(self.chunks[i].text) not in self._emb]
        if misses:
            try:
                vecs = self.embedder.embed([self.chunks[i].text[:1200] for i in misses])
            except Exception:
                return None
            for i, v in zip(misses, vecs):
                self._emb[self._hash(self.chunks[i].text)] = v
            self._write_cache()   # persist newly embedded chunks

        max_bm = max(s for _, s in candidates) or 1.0
        blended = []
        for i, bm_score in candidates:
            cv = self._emb.get(self._hash(self.chunks[i].text), [])
            blended.append((i, 0.5 * (bm_score / max_bm) + 0.5 * cosine(qv, cv)))
        blended.sort(key=lambda x: x[1], reverse=True)
        return blended

    # --- graph passthrough --------------------------------------------

    def where_defined(self, name: str) -> list[str]:
        return self.graph.where_defined(name)

    def dependents(self, path: str) -> list[str]:
        return self.graph.dependents(path)

    def imports_of(self, path: str) -> list[str]:
        return self.graph.imports_of(path)

    def stats(self) -> dict:
        return {"files": len({c.path for c in self.chunks}), "chunks": len(self.chunks),
                "symbols": len(self.graph.symbol_index)}

    # --- persistence ---------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        path = path or self._cache_path
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps({
            "root": str(self.root),
            "mtimes": self._mtimes or self._scan_mtimes(),
            "chunks": [c.__dict__ for c in self.chunks],
            "graph": self.graph.to_dict(),
            "embeddings": self._emb,
        }), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> RepoIndex:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        idx = cls(Path(data["root"]))
        idx.chunks = [Chunk(**c) for c in data["chunks"]]
        idx.graph = CodeGraph.from_dict(data["graph"])
        idx._emb = data.get("embeddings", {})
        idx._mtimes = data.get("mtimes", {})
        idx._index()
        return idx
