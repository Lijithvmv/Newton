"""Config fingerprint for eval results — so results from different days are only compared when they
ran the same thing.

Newton's measurements run for hours and across days; without a record of WHICH code, model weights
and settings produced a row, a later comparison can silently mix versions (it happened: a 54-run
measurement spanned two days and several commits). Every eval row should carry this fingerprint, and
two runs are comparable only when the fields that matter match.

Read-only and local: it reads git state, environment variables and the local Ollama API
(localhost only). Every field fails soft to None.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[4]          # …/backend/src/newton/eval → repo root
_OLLAMA = "http://localhost:11434"


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=_REPO, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _ollama(path: str) -> Any:
    try:
        with urllib.request.urlopen(_OLLAMA + path, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def model_digest(model: str) -> str | None:
    """The local Ollama digest of `model` ("ollama:gemma3:4b" or "gemma3:4b") — identifies the exact
    weights, so a re-pulled or re-imported model under the same name is not mistaken for the old one."""
    name = model.split("ollama:", 1)[-1]
    tags = _ollama("/api/tags") or {}
    for m in tags.get("models", []):
        if m.get("name") in (name, f"{name}:latest") or m.get("model") in (name, f"{name}:latest"):
            return m.get("digest")
    return None


def fingerprint(model: str) -> dict[str, Any]:
    """Everything that decides what an eval row measured."""
    status = _git("status", "--porcelain", "--untracked-files=no")
    version = _ollama("/api/version") or {}
    try:
        from ..llm import _NUM_CTX
    except Exception:
        _NUM_CTX = None
    return {
        "newton_commit": _git("rev-parse", "--short", "HEAD"),
        "newton_branch": _git("branch", "--show-current"),
        "newton_dirty": None if status is None else bool(status),
        "model": model,
        "model_digest": model_digest(model),
        "ollama_version": version.get("version"),
        "num_ctx": _NUM_CTX,
        "newton_env": {k: v for k, v in sorted(os.environ.items()) if k.startswith("NEWTON_")},
        "python": platform.python_version(),
    }


# Fields that must match for two eval rows to be compared (env and python matter too, but these
# are the ones that silently drift).
COMPARABLE_KEYS = ("newton_commit", "newton_dirty", "model_digest", "num_ctx", "newton_env")


def comparable(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in COMPARABLE_KEYS)
