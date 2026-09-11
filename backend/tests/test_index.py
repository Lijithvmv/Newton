"""Tests for the whole-repo context layer — chunking, code graph, and BM25 search."""

from __future__ import annotations

from newton.index.chunker import chunk_file
from newton.index.embeddings import cosine
from newton.index.graph import CodeGraph, analyze_python
from newton.index.store import RepoIndex, tokenize

# --- tokenizer: NL queries must reach code symbols ---

def test_tokenize_splits_identifiers():
    toks = tokenize("ContextAssembler")
    assert "context" in toks and "assembler" in toks and "contextassembler" in toks

def test_tokenize_snake_case():
    assert set(tokenize("count_words")) >= {"count", "words", "count_words"}


# --- chunker ---

def test_chunk_python_by_symbol():
    src = "import os\n\ndef foo():\n    return 1\n\nclass Bar:\n    pass\n"
    chunks = chunk_file("m.py", src)
    kinds = {(c.kind, c.name) for c in chunks}
    assert ("function", "foo") in kinds and ("class", "Bar") in kinds
    assert any(c.kind == "module" for c in chunks)  # header with the import

def test_chunk_markdown_by_heading():
    chunks = chunk_file("d.md", "# Title\nintro\n\n## Section\nbody\n")
    names = {c.name for c in chunks}
    assert "Title" in names and "Section" in names


# --- code graph ---

def test_graph_where_defined_and_dependents():
    g = CodeGraph()
    g.add(analyze_python("pkg/util.py", "def helper():\n    return 1\n"))
    g.add(analyze_python("pkg/app.py", "from pkg.util import helper\n\ndef run():\n    return helper()\n"))
    g.finalize()
    assert g.where_defined("helper") == ["pkg/util.py"]
    assert "pkg/app.py" in g.dependents("pkg/util.py")

def test_graph_resolves_relative_import():
    g = CodeGraph()
    g.add(analyze_python("pkg/util.py", "def helper():\n    return 1\n"))
    g.add(analyze_python("pkg/app.py", "from .util import helper\n"))
    g.finalize()
    assert "pkg/app.py" in g.dependents("pkg/util.py")


# --- PageRank centrality over the import graph ---

def test_pagerank_favours_widely_imported_file():
    g = CodeGraph()
    g.add(analyze_python("core.py", "def base():\n    return 1\n"))
    for n in ("a", "b", "c"):
        g.add(analyze_python(f"{n}.py", "from core import base\n"))
    g.finalize()
    pr = g.pagerank()
    assert pr["core.py"] == max(pr.values())          # imported by all → most central

def test_pagerank_personalization_biases_focus():
    g = CodeGraph()
    g.add(analyze_python("core.py", "def base():\n    return 1\n"))
    g.add(analyze_python("a.py", "from core import base\n"))
    g.finalize()
    base = g.pagerank()
    biased = g.pagerank({"a.py": 1.0})
    assert biased["a.py"] > base["a.py"]              # teleporting to a leaf raises it

def test_pagerank_empty_graph_is_safe():
    assert CodeGraph().pagerank() == {}

def test_pagerank_is_memoized():
    g = CodeGraph()
    g.add(analyze_python("core.py", "def base():\n    return 1\n"))
    g.finalize()
    assert g.pagerank() is g.pagerank()               # same object → cached

def test_centrality_breaks_ties_toward_central_file(tmp_path):
    (tmp_path / "core.py").write_text("def base():\n    return 1\n")
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.py").write_text(f"from core import base\n\ndef {n}fn():\n    return base()\n")
    (tmp_path / "leaf.py").write_text("def solo():\n    return 2\n")
    idx = RepoIndex(tmp_path).build()
    def cidx(path):
        return next(i for i, c in enumerate(idx.chunks) if c.path == path and c.kind != "module")
    ci, li = cidx("core.py"), cidx("leaf.py")
    # Equal relevance in → the central file (core.py, imported by three) comes out first.
    ranked = idx._apply_centrality([(li, 1.0), (ci, 1.0)], None)
    assert ranked[0][0] == ci

def test_centrality_never_overrides_relevance(tmp_path):
    (tmp_path / "core.py").write_text("def base():\n    return 1\n")
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.py").write_text("from core import base\n")
    (tmp_path / "leaf.py").write_text("def solo():\n    return 2\n")
    idx = RepoIndex(tmp_path).build()
    def cidx(path):
        return next(i for i, c in enumerate(idx.chunks) if c.path == path and c.kind != "module")
    ci, li = cidx("core.py"), cidx("leaf.py")
    # A clearly more-relevant leaf still beats the central file (weight is only 0.30).
    ranked = idx._apply_centrality([(li, 1.0), (ci, 0.5)], None)
    assert ranked[0][0] == li


# --- search over an in-memory repo ---

def test_search_finds_relevant_chunk(tmp_path):
    (tmp_path / "auth.py").write_text("def login(user, password):\n    return verify(user, password)\n")
    (tmp_path / "math_ops.py").write_text("def add(a, b):\n    return a + b\n")
    idx = RepoIndex(tmp_path).build()
    top = idx.search("how does user login and password verification work", k=1)
    assert top and top[0][0].path == "auth.py"

def test_index_save_load_roundtrip(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    idx = RepoIndex(tmp_path).build()
    p = idx.save()
    reloaded = RepoIndex.load(p)
    assert reloaded.where_defined("alpha") == ["a.py"]
    assert reloaded.search("alpha", k=1)


# --- semantic re-rank (hybrid retrieval) ---

def test_cosine():
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([], [1, 2]) == 0.0

class _FakeEmbedder:
    """Deterministic: texts mentioning 'beta' (and the query marker) point one way."""
    def available(self): return True
    def embed(self, texts):
        return [[1.0, 0.0] if ("beta" in t or "MEANING" in t) else [0.0, 1.0] for t in texts]

def test_semantic_rerank_orders_by_meaning(tmp_path):
    # Both chunks tie on BM25 (share run+task); the embedder should surface the 'beta' one.
    (tmp_path / "a.py").write_text("def run():\n    return 'alpha task'\n")
    (tmp_path / "b.py").write_text("def run():\n    return 'beta task'\n")
    idx = RepoIndex(tmp_path, embedder=_FakeEmbedder()).build()
    top = idx.search("MEANING run task", k=2)
    assert top[0][0].path == "b.py"

def test_semantic_rerank_falls_back_on_error(tmp_path):
    class _Boom:
        def available(self): return True
        def embed(self, texts): raise RuntimeError("ollama down")
    (tmp_path / "a.py").write_text("def run():\n    return 1\n")
    idx = RepoIndex(tmp_path, embedder=_Boom()).build()
    assert idx.search("run", k=1)                     # no crash → pure BM25

def test_no_embedder_is_pure_bm25(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    return 1\n")
    idx = RepoIndex(tmp_path).build()                 # embedder=None
    assert idx.search("run", k=1)


# --- mtime-cached index ---

def test_cache_reused_when_unchanged(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    RepoIndex(tmp_path).build()                        # first build writes the cache
    idx = RepoIndex(tmp_path).build()                  # second build loads it
    assert idx.loaded_from_cache
    assert idx.where_defined("alpha") == ["a.py"]

def test_cache_invalidated_on_change(tmp_path):
    import os
    import time
    f = tmp_path / "a.py"
    f.write_text("def alpha():\n    return 1\n")
    RepoIndex(tmp_path).build()
    f.write_text("def beta():\n    return 2\n")
    os.utime(f, (time.time() + 10, time.time() + 10))  # force a new mtime
    idx = RepoIndex(tmp_path).build()
    assert not idx.loaded_from_cache
    assert idx.where_defined("beta") == ["a.py"] and not idx.where_defined("alpha")

class _Counting:
    def __init__(self): self.calls = 0
    def available(self): return True
    def embed(self, texts): self.calls += len(texts); return [[1.0, 0.0] for _ in texts]

def test_embedding_cache_persists_across_instances(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    return 'x'\n")
    e1 = _Counting()
    RepoIndex(tmp_path, embedder=e1).build().search("run", k=1)
    assert e1.calls >= 2                                # query + chunk embedded, then persisted
    e2 = _Counting()
    RepoIndex(tmp_path, embedder=e2).build().search("run", k=1)
    assert e2.calls == 1                                # chunk vector loaded from cache; only query
