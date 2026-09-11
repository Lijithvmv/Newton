"""Wiki — a curated knowledge base of markdown pages under `<project>/wiki/`.

Thin wrapper over RepoIndex: the wiki directory is indexed and searched exactly like the
codebase (markdown chunked by heading, hybrid BM25 + semantic, mtime-cached), so relevant
knowledge surfaces by meaning. Also supports authoring — list / read / write pages — so it
can be curated by hand (`newton-wiki`) or, later, maintained by the agent itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..index import RepoIndex


class Wiki:
    def __init__(self, project_root: Path, embedder=None) -> None:
        self.dir = Path(project_root) / "wiki"
        self.embedder = embedder
        self._index: RepoIndex | None = None

    # --- authoring -----------------------------------------------------

    def pages(self) -> list[str]:
        return sorted(p.name for p in self.dir.glob("*.md")) if self.dir.is_dir() else []

    def read(self, name: str) -> str:
        p = self.dir / self._filename(name)
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def write(self, name: str, content: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / self._filename(name)
        p.write_text(content.rstrip() + "\n", encoding="utf-8")
        self._index = None            # invalidate so the next search re-indexes
        return p

    @staticmethod
    def _filename(name: str) -> str:
        name = name.strip()
        if not name.endswith(".md"):
            name = re.sub(r"[^\w-]+", "-", name.lower()).strip("-") + ".md"
        return name

    # --- retrieval -----------------------------------------------------

    def search(self, query: str, k: int = 2) -> list[tuple[str, str]]:
        """Relevant page sections for a query. Returns (page:section, text) pairs."""
        if not self.dir.is_dir() or not self.pages():
            return []
        if self._index is None:
            self._index = RepoIndex(self.dir, embedder=self.embedder).build()
        hits = self._index.search(query, k)
        return [(f"{c.path}:{c.name}", c.text) for c, _score in hits]
