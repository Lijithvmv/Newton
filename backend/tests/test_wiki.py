"""Tests for the LLM-wiki — authoring and retrieval of curated knowledge pages."""

from __future__ import annotations

from newton.wiki import Wiki


def test_write_read_and_list(tmp_path):
    w = Wiki(tmp_path)
    w.write("CLI Flag Pattern", "# CLI flags\nUse argparse.\n")
    assert "cli-flag-pattern.md" in w.pages()
    assert "argparse" in w.read("cli-flag-pattern")
    assert "argparse" in w.read("CLI Flag Pattern")     # title resolves to the slug too

def test_search_finds_the_relevant_page(tmp_path):
    w = Wiki(tmp_path)                                    # no embedder → BM25
    w.write("cli-flags", "# Adding CLI flags\nUse argparse.add_argument to add a flag.\n")
    w.write("json-output", "# JSON output\nUse json.dumps for structured output.\n")
    hits = w.search("how do I add an argparse flag", k=1)
    assert hits and "cli-flags" in hits[0][0]

def test_search_empty_when_no_wiki(tmp_path):
    assert Wiki(tmp_path / "empty").search("anything") == []

def test_write_invalidates_index(tmp_path):
    w = Wiki(tmp_path)
    w.write("a", "# A\napple pattern\n")
    assert w.search("apple", k=1)
    w.write("b", "# B\nbanana convention\n")               # should be searchable immediately
    hits = w.search("banana", k=1)
    assert hits and "b.md" in hits[0][0]
