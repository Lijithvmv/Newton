"""Laya is pinned to one exact revision and resolved offline from the local cache.

An unpinned load re-resolves "main" on the Hub every time (a network call carrying the user's IP) and
can silently swap weights under a measured baseline. These tests pin the contract without touching
the network: a cached revision resolves with local_files_only; only a first run fetches, and always
that exact revision."""

from __future__ import annotations

import re

import huggingface_hub

from newton.loop import judge
from newton.loop.judge import LAYA_REVISION, resolve_laya_path


def test_revision_is_a_full_commit_sha_not_a_branch():
    assert re.fullmatch(r"[0-9a-f]{40}", LAYA_REVISION)   # never "main" — a moving target


def test_cached_revision_resolves_offline_with_one_call(monkeypatch):
    calls = []
    def fake(repo, **kw):
        calls.append(kw)
        return "/cache/laya/snap"
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    assert resolve_laya_path() == "/cache/laya/snap"
    assert len(calls) == 1 and calls[0]["local_files_only"] is True   # no network on a cache hit
    assert calls[0]["revision"] == LAYA_REVISION
    assert "model.safetensors" in calls[0]["allow_patterns"]           # only the files Laya needs


def test_first_run_fetches_exactly_the_pinned_revision(monkeypatch):
    calls = []
    def fake(repo, **kw):
        calls.append(kw)
        if kw.get("local_files_only"):
            raise FileNotFoundError("not cached yet")
        return "/cache/laya/snap"
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    assert resolve_laya_path() == "/cache/laya/snap"
    assert [c.get("local_files_only", False) for c in calls] == [True, False]
    assert all(c["revision"] == LAYA_REVISION for c in calls)          # fallback is still pinned


def test_local_directory_is_used_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resolve")))
    assert resolve_laya_path(str(tmp_path)) == str(tmp_path)


def test_judge_loads_through_the_pinned_resolver():
    import inspect
    assert "resolve_laya_path(self.model)" in inspect.getsource(judge.LayaJudge._load)
