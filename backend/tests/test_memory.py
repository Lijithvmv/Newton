"""Tests for cross-session memory — semantic recall, recency fallback, threshold."""

from __future__ import annotations

from newton.memory import Memory


class _Fake:
    """Deterministic: anything about 'auth' points one way, everything else the other."""
    def available(self): return True
    def embed(self, texts):
        return [[1.0, 0.0] if "auth" in t.lower() else [0.0, 1.0] for t in texts]


def test_add_stores_embedding_and_roundtrips(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    m.add("built the auth flow", request="add auth", files=["auth.py"])
    items = m.all()
    assert len(items) == 1
    assert items[0].embedding is not None and items[0].files == ["auth.py"]

def test_semantic_recall_returns_the_relevant_item(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    m.add("built the authentication login flow", request="add auth")
    m.add("wrote a math helper add function", request="add math")
    rec = m.recall("how did we handle auth", k=2)
    assert len(rec) == 1 and "authentication" in rec[0].text

def test_threshold_filters_irrelevant(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    m.add("wrote a math helper")                       # embeds away from 'auth'
    assert m.recall("auth login work", k=2) == []      # cosine below threshold → nothing

def test_recency_fallback_without_embedder(tmp_path):
    m = Memory(tmp_path / "mem.jsonl")                 # no embedder
    for t in ("first", "second", "third"):
        m.add(t)
    assert [r.text for r in m.recall("anything", k=2)] == ["second", "third"]

def test_recall_empty(tmp_path):
    assert Memory(tmp_path / "mem.jsonl", embedder=_Fake()).recall("x") == []


# --- salience: don't remember near-duplicates ---

def test_salience_skips_near_duplicate(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    first = m.add("built the auth flow", files=["auth.py"])
    dup = m.add("built the auth flow again", files=["auth.py"])   # same embedding class
    assert first is not None and dup is None
    assert len(m.all()) == 1

def test_distinct_memories_both_kept(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    assert m.add("built the auth flow", files=["auth.py"]) is not None
    assert m.add("wrote a math helper", files=["math.py"]) is not None   # different embedding
    assert len(m.all()) == 2


# --- graph recall: by file / symbol, not just meaning ---

def test_touching_matches_file_and_symbol(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    m.add("added slugify", files=["textutil.py"], symbols=["slugify"])
    assert m.touching(["textutil.py"]) and m.touching(["slugify"])
    assert not m.touching(["nope.py"])

def test_graph_recall_surfaces_by_file_over_meaning(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Fake())
    m.add("wrote a math helper", files=["math.py"], symbols=["add"])   # embeds away from 'auth'
    m.add("built the auth flow", files=["auth.py"])                    # embeds toward 'auth'
    hits = m.recall("auth related query", k=2, files=["math.py"])
    assert hits[0].files == ["math.py"]                # graph edge prioritized over meaning
    assert any("auth" in h.text for h in hits)         # semantic hit still included
