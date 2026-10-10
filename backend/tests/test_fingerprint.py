"""The eval fingerprint records which code, model weights and settings produced a result."""

from __future__ import annotations

import newton.eval.fingerprint as fp


def test_fingerprint_records_code_and_settings(monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_PARALLEL", "2")
    monkeypatch.setattr(fp, "_ollama", lambda path: {"version": "9.9"} if path == "/api/version"
                        else {"models": [{"name": "gemma3:4b", "digest": "abc123"}]})
    f = fp.fingerprint("ollama:gemma3:4b")
    assert f["newton_commit"] and len(f["newton_commit"]) >= 7          # real git state of this repo
    assert isinstance(f["newton_dirty"], bool)
    assert f["model_digest"] == "abc123" and f["ollama_version"] == "9.9"
    assert f["newton_env"]["NEWTON_LOOP_PARALLEL"] == "2"


def test_fails_soft_without_ollama(monkeypatch):
    monkeypatch.setattr(fp, "_ollama", lambda path: None)
    f = fp.fingerprint("ollama:gemma3:4b")
    assert f["model_digest"] is None and f["ollama_version"] is None


def test_comparable_needs_same_weights_and_code():
    a = {"newton_commit": "x", "newton_dirty": False, "model_digest": "d1", "num_ctx": 16384, "newton_env": {}}
    assert fp.comparable(a, dict(a))
    assert not fp.comparable(a, {**a, "model_digest": "d2"})
    assert not fp.comparable(a, {**a, "newton_dirty": True})
