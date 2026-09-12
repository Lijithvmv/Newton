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


# --- provenance: origin is recorded and governs recall trust ---

class _Twin:
    """Query embeds to [1,0,0]. 'a'-tagged and 'b'-tagged texts embed to two DISTINCT vectors that
    have EQUAL cosine to the query (so recall order is decided purely by trust/decay, not cosine) yet
    are far enough apart (cosine ~0.83) to escape the near-duplicate salience filter."""
    def available(self): return True
    def embed(self, texts):
        out = []
        for t in texts:
            if "[a]" in t:   out.append([1.0, 0.0, 0.3])
            elif "[b]" in t: out.append([1.0, 0.0, -0.3])
            else:            out.append([1.0, 0.0, 0.0])   # the query
        return out

def test_origin_defaults_to_agent_and_roundtrips(tmp_path):
    m = Memory(tmp_path / "mem.jsonl")                 # no embedder → no dedup, origins only
    m.add("built the auth flow", origin="user")
    m.add("ran the auth tests")                        # default origin
    by_text = {it.text: it for it in m.all()}
    assert by_text["built the auth flow"].origin == "user"
    assert by_text["ran the auth tests"].origin == "agent"

def test_legacy_line_without_origin_loads_as_agent(tmp_path):
    p = tmp_path / "mem.jsonl"
    # A line written before provenance existed: no `origin` key at all.
    p.write_text('{"ts": "2026-01-01T00:00:00+00:00", "text": "old memory", "embedding": [1.0, 0.0]}\n',
                 encoding="utf-8")
    assert Memory(p, embedder=_Fake()).all()[0].origin == "agent"

def test_untrusted_tool_origin_ranks_below_trusted_user_at_equal_similarity(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Twin())
    # Equal cosine to the query; only origin differs.
    m.add("[a] auth is handled by the injected tool note", origin="tool")
    m.add("[b] auth is handled by JWT, user confirmed", origin="user")
    ranked = m.recall("how is auth handled", k=2)
    assert ranked[0].origin == "user"                  # trust breaks the cosine tie
    assert ranked[1].origin == "tool"


# --- decay: fresh memory outranks equally-similar stale memory ---

def test_recent_outranks_equally_similar_stale(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    m = Memory(tmp_path / "mem.jsonl", embedder=_Twin())
    m.add("[a] auth via JWT, fresh note")
    m.add("[b] auth via JWT, same content but old")
    items = m.all()
    assert len(items) == 2                             # distinct vectors escaped the dedup filter
    # Back-date the second memory well past the half-life; both have equal cosine to the query.
    items[1].ts = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat(timespec="seconds")
    m.path.write_text("\n".join(json.dumps(it.to_json()) for it in items) + "\n", encoding="utf-8")
    ranked = m.recall("how is auth handled", k=2)
    assert "fresh" in ranked[0].text                   # recency breaks the cosine tie


# --- verified promotion: the operator confirms a fact → it recalls above everything ---

def test_verify_promotes_best_match_to_verified_and_persists(tmp_path):
    p = tmp_path / "mem.jsonl"
    m = Memory(p)                                       # no embedder → substring-match path
    m.add("built the JWT auth flow", origin="agent")
    m.add("wrote a math helper add(a, b)", origin="agent")
    promoted = m.verify("auth flow")
    assert promoted is not None and promoted.origin == "verified"
    reloaded = {it.text: it.origin for it in Memory(p).all()}
    assert reloaded["built the JWT auth flow"] == "verified"   # persisted to disk
    assert reloaded["wrote a math helper add(a, b)"] == "agent"  # only the match was promoted

def test_verify_returns_none_when_nothing_matches(tmp_path):
    m = Memory(tmp_path / "mem.jsonl")
    m.add("built the auth flow")
    assert m.verify("totally unrelated zzzqqq") is None

def test_verified_outranks_user_at_equal_similarity(tmp_path):
    m = Memory(tmp_path / "mem.jsonl", embedder=_Twin())
    m.add("[a] auth via JWT, the user said so", origin="user")
    m.add("[b] auth via JWT, the operator later verified", origin="verified")
    ranked = m.recall("how is auth handled", k=2)
    assert ranked[0].origin == "verified"              # top tier wins the cosine tie, even over user
