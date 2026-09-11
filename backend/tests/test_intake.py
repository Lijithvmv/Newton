"""Tests for document intake's deterministic core.

The text-passthrough and ingestion paths need no external converter, so they're tested
directly. The MarkItDown-backed binary path is tested only for its *contract* — a clear,
actionable error when the optional dependency is absent — not real PDF/DOCX parsing.
"""

from __future__ import annotations

import importlib.util

import pytest

from newton.intake.converter import (
    SUPPORTED_EXTS,
    IntakeError,
    convert_to_markdown,
    ingest,
)

_HAS_MARKITDOWN = importlib.util.find_spec("markitdown") is not None


def test_supported_exts_cover_text_and_office():
    for ext in (".md", ".txt", ".csv", ".pdf", ".docx", ".pptx", ".xlsx", ".html"):
        assert ext in SUPPORTED_EXTS


def test_text_passthrough_needs_no_dependency(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("Hello **world**\nsecond line\n", encoding="utf-8")
    out = convert_to_markdown(src)
    assert "Hello **world**" in out and "second line" in out


def test_ingest_writes_markdown_with_frontmatter(tmp_path):
    (tmp_path / "spec.md").write_text("# Spec\nrequirements here\n", encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    result = ingest(root, tmp_path / "spec.md")
    assert result.dest == "raw/spec.md"
    written = (root / "raw" / "spec.md").read_text(encoding="utf-8")
    assert written.startswith("---\nsource: spec.md")
    assert "ingested:" in written
    assert "requirements here" in written


def test_ingest_slugs_messy_names(tmp_path):
    (tmp_path / "My Big Report (v2).txt").write_text("body\n", encoding="utf-8")
    root = tmp_path / "p"
    root.mkdir()
    result = ingest(root, tmp_path / "My Big Report (v2).txt")
    assert result.dest == "raw/my-big-report-v2.md"


def test_ingest_rejects_missing_file(tmp_path):
    with pytest.raises(IntakeError, match="no such file"):
        ingest(tmp_path, tmp_path / "nope.txt")


def test_ingest_rejects_unsupported_type(tmp_path):
    (tmp_path / "thing.zzz").write_text("x", encoding="utf-8")
    with pytest.raises(IntakeError, match="unsupported file type"):
        ingest(tmp_path, tmp_path / "thing.zzz")


@pytest.mark.skipif(_HAS_MARKITDOWN, reason="MarkItDown is installed; the missing-dep path can't trigger")
def test_binary_without_markitdown_gives_actionable_error(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 not really a pdf")
    with pytest.raises(IntakeError, match="pip install"):
        convert_to_markdown(src)


# --- images → local vision model (mocked; no Ollama needed) ---

def test_image_exts_are_supported():
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        assert ext in SUPPORTED_EXTS

def test_image_routed_to_vision_and_wrapped_as_markdown(tmp_path, monkeypatch):
    import newton.vision as vision
    monkeypatch.setattr(vision, "describe_image", lambda p, **k: "A green login button on a dark form.")
    img = tmp_path / "screenshot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake bytes")
    out = convert_to_markdown(img)
    assert "# Image: screenshot.png" in out
    assert "green login button" in out

def test_image_ingest_writes_searchable_note(tmp_path, monkeypatch):
    import newton.vision as vision
    monkeypatch.setattr(vision, "describe_image", lambda p, **k: "Architecture diagram: API talks to DB.")
    (tmp_path / "diagram.png").write_bytes(b"\x89PNG fake")
    root = tmp_path / "proj"; root.mkdir()
    result = ingest(root, tmp_path / "diagram.png")
    assert result.dest == "raw/diagram.md"
    written = (root / "raw" / "diagram.md").read_text(encoding="utf-8")
    assert "Architecture diagram" in written and written.startswith("---\nsource: diagram.png")

def test_image_vision_failure_is_actionable(tmp_path, monkeypatch):
    import newton.vision as vision
    def _boom(p, **k):
        raise vision.VisionError("llava not available")
    monkeypatch.setattr(vision, "describe_image", _boom)
    (tmp_path / "x.png").write_bytes(b"\x89PNG fake")
    with pytest.raises(IntakeError, match="could not read image"):
        convert_to_markdown(tmp_path / "x.png")

def test_vision_model_name_strips_ollama_prefix():
    from newton.vision import _model_name
    assert _model_name("ollama:llava") == "llava"
    assert _model_name("llava") == "llava"
