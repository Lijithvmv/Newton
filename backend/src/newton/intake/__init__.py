"""Intake — turn any document into indexed local markdown.

Drop a PDF, DOCX, PPTX, XLSX, HTML (etc.) at Newton and it becomes a markdown file under
`raw/`, which the RepoIndex then picks up automatically — so external documents enrich
search, tasks, and reports just like code does.

Text formats convert natively (no dependency). Binary/office formats use MarkItDown
(Microsoft, MIT), installed on demand via the optional `intake` extra so the core stays
dependency-light (consistent with D6/D18: don't pull heavy deps into the base product).
"""

from .converter import (
    SUPPORTED_EXTS,
    IntakeError,
    IntakeResult,
    convert_to_markdown,
    ingest,
)

__all__ = [
    "SUPPORTED_EXTS",
    "IntakeError",
    "IntakeResult",
    "convert_to_markdown",
    "ingest",
]
