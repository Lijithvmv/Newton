"""Durable run history — a small append/update JSON log so past runs survive a server restart.

The `RunManager` keeps live runs in memory (for the SSE stream and gate coordination); that is lost
when the server stops. This persists a lightweight record per run — what was asked, in which
mode/project, at what effort, and how it ended — so the Overview activity feed and the History view
show real history across restarts, and an interrupted Build can be found and resumed. It is runtime
state (gitignored under `.newton/`), never product source; writes are atomic and capped so the file
can't grow without bound or be left half-written by an interrupted process.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


class RunLog:
    """A capped, newest-last JSON list of run records at `path`. Thread-safe; every read tolerates a
    missing or corrupt file by returning an empty history (a broken log must never break a run)."""

    def __init__(self, path: Path, cap: int = 200) -> None:
        self.path = Path(path)
        self.cap = cap
        self._lock = threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rows[-self.cap:], f)
            os.replace(tmp, self.path)                 # atomic — never a half-written history
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def add(self, rec: dict[str, Any]) -> None:
        """Insert a record (replacing any existing one with the same id)."""
        with self._lock:
            rows = [r for r in self._read() if r.get("id") != rec.get("id")]
            rows.append(rec)
            self._write(rows)

    def update(self, run_id: str, **changes: Any) -> None:
        """Merge `changes` into the record with `run_id` (no-op if it isn't there)."""
        with self._lock:
            rows = self._read()
            for r in rows:
                if r.get("id") == run_id:
                    r.update(changes)
                    self._write(rows)
                    return

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """The most recent records, newest first."""
        return list(reversed(self._read()))[:limit]
