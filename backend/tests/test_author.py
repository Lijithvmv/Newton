"""Tests for the Author Conductor's deterministic core.

No model calls: these lock the document skeletons, the slug/output-path derivation, and
the markdown composition. The generative section prose is the model's job.
"""

from __future__ import annotations

from newton.author.conductor import (
    AUTHOR_SPECS,
    compose_doc,
    output_path,
)


def test_author_specs_integrity():
    prefixes = set()
    for name, spec in AUTHOR_SPECS.items():
        assert spec.title and spec.prefix
        assert spec.prefix not in prefixes, "doc prefixes must be unique"
        prefixes.add(spec.prefix)
        assert spec.sections, f"{name} has no sections"
        for title, instruction in spec.sections:
            assert title and instruction


def test_expected_doc_types_present():
    assert {"prd", "architecture", "brainstorm", "design"} <= set(AUTHOR_SPECS)


def test_output_path_slugs_topic():
    assert output_path("prd", "Dark Mode Toggle") == "docs/prd-dark-mode-toggle.md"
    assert output_path("brainstorm", "ideas for onboarding!!") == "docs/brainstorm-ideas-for-onboarding.md"


def test_output_path_truncates_long_topic():
    path = output_path("design", "x" * 200)
    stem = path[len("docs/design-"):-len(".md")]
    assert len(stem) <= 60


def test_compose_doc_structure():
    spec = AUTHOR_SPECS["prd"]
    out = compose_doc(spec, "Dark Mode", "ollama:qwen2.5-coder:7b",
                      [("Problem", "Users want dark mode."), ("Goals", "- ship a toggle")])
    assert out.startswith("# Product Requirements: Dark Mode")
    assert "Drafted by Newton" in out
    assert "## Problem\n\nUsers want dark mode." in out
    assert "## Goals\n\n- ship a toggle" in out
