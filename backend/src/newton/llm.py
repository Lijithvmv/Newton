"""Thin wrapper over aisuite — the only place we talk to the model.

aisuite gives a provider-agnostic, OpenAI-shaped API. We use it against the local
Ollama provider, but the same code would target any provider by changing the model
string — so 'give it to Claude to review' stays a deliberate, manual choice, never
wired into the loop.
"""

from __future__ import annotations

import os
from typing import Any

import aisuite as ai

# Local models on modest hardware are legitimately slow — a 7B model's first token can
# take well over aisuite's 30s default, especially under load (e.g. a model pull running).
# A generous timeout is the correct default for a local-first product.
_TIMEOUT = int(os.environ.get("NEWTON_LLM_TIMEOUT", "600"))
_client = ai.Client({"ollama": {"timeout": _TIMEOUT}})

# Ollama defaults to a TINY context window (~2–4k tokens) regardless of the model's real capacity
# (qwen2.5-coder handles 32k), silently truncating everything the context-engine assembles — the
# capability ceiling behind "we can't hold 60k tokens like the llama.cpp setup". Lift it explicitly.
# Bigger num_ctx costs RAM (KV cache) and first-token latency; local has time to spare. Configurable.
_NUM_CTX = int(os.environ.get("NEWTON_NUM_CTX", "16384"))


def complete(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.1,
) -> Any:
    """One model call. Returns the raw aisuite (OpenAI-shaped) response.

    We deliberately do NOT use aisuite's automatic tool execution (max_turns): the
    agent loop drives tools itself so every write/shell action passes an approval gate.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    # For Ollama, sampling and context size live in `options` (top-level temperature is ignored),
    # so set num_ctx AND temperature there — otherwise we run truncated and at the default temp.
    if str(model).startswith("ollama") and _NUM_CTX > 0:
        kwargs["options"] = {"num_ctx": _NUM_CTX, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
    resp = _client.chat.completions.create(**kwargs)
    # Tally what this would have cost on the cloud (no-op until configured; never raises).
    from . import savings
    savings.record_response(resp, messages)
    return resp
