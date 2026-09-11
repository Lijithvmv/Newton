"""Document → markdown conversion, and ingestion into the project's `raw/` folder.

Two tiers, on purpose:
  - text formats (.md/.txt/.csv/.json/…) are read directly — zero dependencies;
  - office/binary formats (.pdf/.docx/.pptx/.xlsx/.html/…) use MarkItDown, imported
    lazily so the base install never needs it. A missing MarkItDown yields a clear,
    actionable error, never a stack trace.

Ingested files land in `<project>/raw/<slug>.md` with provenance frontmatter. `raw/` is
not skipped by the RepoIndex, so an ingested document is searchable on the next build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Formats we can read as text with no external dependency.
_TEXT_PASSTHROUGH = {".md", ".markdown", ".txt", ".text", ".csv", ".tsv", ".json", ".log", ".rst"}

# Formats that require MarkItDown to convert.
_MARKITDOWN_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt",
    ".html", ".htm", ".epub", ".xml", ".rtf", ".odt",
}

# Images — understood by a local vision model (llava) and turned into a searchable note.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

SUPPORTED_EXTS = _TEXT_PASSTHROUGH | _MARKITDOWN_EXTS | _IMAGE_EXTS


class IntakeError(Exception):
    """A user-facing intake problem (unsupported type, missing converter, bad path)."""


@dataclass
class IntakeResult:
    source: str        # the original file path
    dest: str          # project-relative path of the written markdown
    chars: int         # size of the converted markdown body


def _slug(stem: str) -> str:
    s = re.sub(r"[^\w-]+", "-", stem.lower()).strip("-")
    return s or "document"


def convert_to_markdown(path: Path) -> str:
    """Return the markdown text for a document. Raises IntakeError with guidance if a
    binary format is given but MarkItDown isn't installed."""
    ext = path.suffix.lower()
    if ext in _TEXT_PASSTHROUGH:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext in _IMAGE_EXTS:
        # A local vision model reads the image into a factual description we can index.
        from ..vision import VisionError, describe_image
        try:
            desc = describe_image(path)
        except VisionError as e:
            raise IntakeError(f"could not read image {path.name}: {e}") from e
        return f"# Image: {path.name}\n\n{desc}\n"
    if ext in _MARKITDOWN_EXTS:
        try:
            from markitdown import MarkItDown
        except ImportError as e:
            raise IntakeError(
                f"Converting '{ext}' files needs MarkItDown, which isn't installed. "
                "Install the intake extra:  pip install -e './backend[intake]'"
            ) from e
        try:
            result = MarkItDown().convert(str(path))
        except Exception as e:  # MarkItDown raises various backend-specific errors
            raise IntakeError(f"could not convert {path.name}: {e}") from e
        text = getattr(result, "text_content", None) or getattr(result, "markdown", "")
        if not text.strip():
            raise IntakeError(f"{path.name} converted to empty markdown (nothing extractable).")
        return text
    raise IntakeError(
        f"unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTS))}"
    )


def ingest(root: Path, src: str | Path, *, dest_dir: str = "raw") -> IntakeResult:
    """Convert `src` to markdown and write it under `<root>/<dest_dir>/`. Absolute source
    paths are allowed (documents usually live outside the project); the destination is
    always inside the project so the index can reach it."""
    root = Path(root).resolve()
    src = Path(src).expanduser()
    if not src.is_file():
        raise IntakeError(f"no such file: {src}")
    if src.suffix.lower() not in SUPPORTED_EXTS:
        raise IntakeError(
            f"unsupported file type '{src.suffix}'. Supported: {', '.join(sorted(SUPPORTED_EXTS))}"
        )

    body = convert_to_markdown(src).strip()
    dest_rel = f"{dest_dir}/{_slug(src.stem)}.md"
    dest = root / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = (
        f"---\nsource: {src.name}\ningested: {date.today().isoformat()}\n"
        f"origin: {src}\n---\n\n"
    )
    dest.write_text(frontmatter + body + "\n", encoding="utf-8")
    return IntakeResult(source=str(src), dest=dest_rel, chars=len(body))
