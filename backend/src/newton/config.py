"""Newton configuration — every default points at the local machine.

The engine model must support tool-calling for the agent loop (e.g. qwen2.5-coder);
a smaller completion-only model (gemma3:4b) is fine for plain context-chat.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    # aisuite provider:model strings. Both resolve to local Ollama.
    # Default is the only model guaranteed installed; the prompt-based tool protocol
    # (see agent.py) means the loop works here without native tool-calling. For sharper
    # multi-file work, `ollama pull qwen2.5-coder:7b` and set NEWTON_AGENT_MODEL.
    agent_model: str = os.environ.get("NEWTON_AGENT_MODEL", "ollama:gemma3:4b")
    chat_model: str = os.environ.get("NEWTON_CHAT_MODEL", "ollama:gemma3:4b")
    # Local vision model for understanding attached images (llava). Used by the intake path.
    vision_model: str = os.environ.get("NEWTON_VISION_MODEL", "llava")
    # Where the project lives; the agent is sandboxed to this root.
    project_root: Path = Path(os.environ.get("NEWTON_PROJECT", ".")).resolve()
    # The Claude-Code-style context file loaded first, every session.
    context_file: str = os.environ.get("NEWTON_CONTEXT", "NEWTON.md")
    temperature: float = float(os.environ.get("NEWTON_TEMP", "0.1"))
    max_turns: int = int(os.environ.get("NEWTON_MAX_TURNS", "16"))
    # Ollama endpoint (aisuite's ollama provider honours this env var).
    ollama_url: str = os.environ.get("OLLAMA_API_URL", "http://localhost:11434")

    def __post_init__(self) -> None:
        # Make sure aisuite's ollama provider sees the endpoint.
        os.environ.setdefault("OLLAMA_API_URL", self.ollama_url)


def load_settings(project_root: str | os.PathLike | None = None) -> Settings:
    s = Settings()
    if project_root is not None:
        s.project_root = Path(project_root).resolve()
    return s
