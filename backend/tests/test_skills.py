"""Tests for the Skills store — the deterministic parts (frontmatter, filenames, CRUD, search).

No model calls. The Conductor's use of a retrieved skill is model-driven and not asserted here.
"""

from __future__ import annotations

from newton.skills.store import (
    Skills,
    build_skill_md,
    parse_frontmatter,
    parse_learned_skill,
)


def test_parse_frontmatter_basic():
    fm, body = parse_frontmatter("---\nname: Foo\ndescription: does foo\n---\n\n# Foo\nbody")
    assert fm["name"] == "Foo" and fm["description"] == "does foo"
    assert body.startswith("# Foo")


def test_parse_frontmatter_absent():
    fm, body = parse_frontmatter("# Just a heading\ntext")
    assert fm == {} and body.startswith("# Just")


def test_filename_slug():
    assert Skills._filename("Add a CLI flag") == "add-a-cli-flag.md"
    assert Skills._filename("already.md") == "already.md"


# --- building a SKILL.md ---

def test_build_skill_md_from_steps():
    md = build_skill_md("add-cli-flag", "adding a --flag to a script", ["Add argparse arg", "Thread it in"])
    fm, body = parse_frontmatter(md)
    assert fm["name"] == "add-cli-flag" and fm["description"] == "adding a --flag to a script"
    assert "## When to use" in body and "## Procedure" in body
    assert "1. Add argparse arg" in body and "2. Thread it in" in body

def test_build_skill_md_marks_learned():
    md = build_skill_md("x", "when x", ["a", "b"], learned=True)
    fm, _ = parse_frontmatter(md)
    assert fm.get("source") == "learned"

def test_build_skill_md_accepts_freeform_body():
    md = build_skill_md("x", "when x", "## Steps\ndo the thing")
    assert "## Steps\ndo the thing" in md


# --- parsing a learned-skill model response ---

def test_parse_learned_skill_valid():
    got = parse_learned_skill({"skill": True, "name": "add-route", "description": "new API route",
                               "steps": ["define handler", "register it"]})
    assert got == ("add-route", "new API route", ["define handler", "register it"])

def test_parse_learned_skill_declined_or_thin():
    assert parse_learned_skill({"skill": False}) is None            # model declined
    assert parse_learned_skill({"skill": True, "name": "x", "description": "d", "steps": ["only one"]}) is None
    assert parse_learned_skill({"skill": True, "name": "", "description": "d", "steps": ["a", "b"]}) is None
    assert parse_learned_skill("not a dict") is None


# --- dedup: don't relearn a skill we already have ---

def test_would_duplicate_same_slug(tmp_path):
    sk = Skills(tmp_path)
    sk.write("add-cli-flag", build_skill_md("add-cli-flag", "adding a CLI flag", ["a", "b"]))
    assert sk.would_duplicate("Add CLI Flag", "something") == "add-cli-flag"   # same slug

def test_would_duplicate_high_overlap(tmp_path):
    sk = Skills(tmp_path)
    sk.write("add-endpoint", build_skill_md("add-endpoint", "adding a REST API endpoint route", ["a", "b"]))
    # A differently-named skill describing the same trigger is a near-duplicate.
    assert sk.would_duplicate("new-route", "adding a REST API endpoint route") == "add-endpoint"

def test_would_duplicate_none_for_distinct(tmp_path):
    sk = Skills(tmp_path)
    sk.write("add-endpoint", build_skill_md("add-endpoint", "adding a REST API endpoint", ["a", "b"]))
    assert sk.would_duplicate("write-migration", "writing a database migration script") is None


def test_write_read_list_meta(tmp_path):
    s = Skills(tmp_path)
    s.write("My Skill", "---\nname: My Skill\ndescription: do a thing\n---\n\nsteps here")
    assert "my-skill.md" in s.files()
    assert "steps here" in s.read("my-skill")
    metas = s.list()
    assert len(metas) == 1
    assert metas[0].name == "My Skill" and metas[0].description == "do a thing"


def test_search_returns_full_skill_by_relevance(tmp_path):
    s = Skills(tmp_path)  # no embedder → pure BM25
    s.write("cli-flag",
            "---\nname: CLI flag\ndescription: add a command line flag\n---\n\nFind argparse and add_argument.")
    s.write("db-thing",
            "---\nname: DB\ndescription: unrelated\n---\n\nContent about database migrations and sql.")
    hits = s.search("add a command line flag to the script", k=1)
    assert len(hits) == 1
    assert hits[0][0] == "cli-flag.md"
    assert "argparse" in hits[0][1]      # the WHOLE skill body, not just the matched chunk


def test_search_empty_when_no_skills(tmp_path):
    assert Skills(tmp_path).search("anything") == []


def test_search_returns_nothing_below_floor(tmp_path):
    # An unrelated task must load NO skill — a wrong (pinned) skill is worse than none.
    s = Skills(tmp_path)  # no embedder → keyword overlap
    s.write("cli-flag",
            "---\nname: CLI flag\ndescription: add a command line flag to a python script\n---\n\nbody")
    assert s.search("investigate the flaky network timeout in the deploy pipeline") == []
