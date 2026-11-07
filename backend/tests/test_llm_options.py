"""Ollama request options: num_ctx + temperature always; num_gpu only when NEWTON_NUM_GPU is set."""

from __future__ import annotations

from newton import llm


def test_default_has_no_num_gpu(monkeypatch):
    monkeypatch.setattr(llm, "_NUM_GPU", "")
    opts = llm._ollama_options(0.3)
    assert opts == {"num_ctx": llm._NUM_CTX, "temperature": 0.3}


def test_num_gpu_is_passed_when_set(monkeypatch):
    monkeypatch.setattr(llm, "_NUM_GPU", "99")
    assert llm._ollama_options(0.1)["num_gpu"] == 99


def test_garbage_num_gpu_is_ignored(monkeypatch):
    monkeypatch.setattr(llm, "_NUM_GPU", "all")
    assert "num_gpu" not in llm._ollama_options(0.1)


def test_complete_sends_options(monkeypatch):
    sent = {}

    class _C:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    sent.update(kw)
                    return None

    monkeypatch.setattr(llm, "_client", _C)
    monkeypatch.setattr(llm, "_NUM_GPU", "99")
    monkeypatch.setattr("newton.savings.record_response", lambda *a, **k: None)
    llm.complete("ollama:gemma3:4b", [{"role": "user", "content": "hi"}])
    assert sent["options"]["num_gpu"] == 99
