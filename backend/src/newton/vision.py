"""Local image understanding via a vision model (llava) on Ollama.

Ollama's native chat API accepts base64 images alongside a prompt — something aisuite's
OpenAI-shaped path doesn't cleanly expose for the local provider — so we call Ollama
directly here. Kept dependency-light (httpx is already a dependency) and fully local: an
attached screenshot, diagram, or photo becomes a searchable project note.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx

_OLLAMA = os.environ.get("OLLAMA_API_URL", "http://localhost:11434")
DEFAULT_VISION_MODEL = os.environ.get("NEWTON_VISION_MODEL", "llava")

_DEFAULT_PROMPT = (
    "Describe this image in detail for a searchable project note. If it contains a diagram, "
    "UI mockup, chart, or text, transcribe the text and explain the structure and the "
    "relationships between elements. Be concise and factual — no preamble."
)


class VisionError(Exception):
    """A user-facing image-understanding problem (bad file, model missing, model error)."""


def _model_name(model: str) -> str:
    """Strip an aisuite 'ollama:' prefix — Ollama's native API wants the bare model name."""
    return model.split(":", 1)[1] if model.startswith("ollama:") else model


def available(model: str = DEFAULT_VISION_MODEL) -> bool:
    """True if the vision model is installed in Ollama (so callers can degrade gracefully)."""
    name = _model_name(model)
    try:
        r = httpx.get(f"{_OLLAMA}/api/tags", timeout=5)
        tags = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return False
    return any(t == name or t.startswith(name + ":") for t in tags)


def describe_image(path: str | Path, *, model: str = DEFAULT_VISION_MODEL,
                   prompt: str | None = None, timeout: int = 300) -> str:
    """Return a factual description/transcription of an image via the local vision model."""
    p = Path(path)
    if not p.is_file():
        raise VisionError(f"no such image: {p}")
    try:
        b64 = base64.b64encode(p.read_bytes()).decode()
    except OSError as e:
        raise VisionError(f"could not read {p.name}: {e}") from e

    body = {
        "model": _model_name(model),
        "messages": [{"role": "user", "content": prompt or _DEFAULT_PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    try:
        r = httpx.post(f"{_OLLAMA}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        # A missing model comes back as 404 with a helpful body — surface it plainly.
        raise VisionError(
            f"vision model '{_model_name(model)}' not available (pull it: "
            f"`ollama pull {_model_name(model)}`) — {e}"
        ) from e
    except Exception as e:
        raise VisionError(f"vision model call failed: {e}") from e

    text = (data.get("message") or {}).get("content", "").strip()
    if not text:
        raise VisionError("the vision model returned no description")
    return text
