"""Durable run history — records survive a restart, and an interrupted Build is detected as resumable.

Covers the persistence core the History view + Overview feed rely on: a run is logged when it starts
and updated when it ends, the log is read newest-first and capped, a corrupt file is tolerated (never
breaks a run), and `_build_resumable` recognises a checkpoint with unfinished steps."""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.state import DONE, PENDING, LoopState, Step
from newton.runlog import RunLog


def test_add_then_update_is_readable_newest_first(tmp_path):
    log = RunLog(tmp_path / "runs.json")
    log.add({"id": "a", "task": "first", "status": "running", "started": 1.0})
    log.add({"id": "b", "task": "second", "status": "running", "started": 2.0})
    log.update("a", status="done", ok=True)
    rows = log.recent()
    assert [r["id"] for r in rows] == ["b", "a"]          # newest first
    assert rows[1]["status"] == "done" and rows[1]["ok"] is True

def test_add_replaces_same_id(tmp_path):
    log = RunLog(tmp_path / "runs.json")
    log.add({"id": "a", "task": "v1"})
    log.add({"id": "a", "task": "v2"})
    rows = log.recent()
    assert len(rows) == 1 and rows[0]["task"] == "v2"

def test_history_survives_a_new_instance(tmp_path):
    RunLog(tmp_path / "runs.json").add({"id": "a", "task": "kept", "status": "done"})
    assert RunLog(tmp_path / "runs.json").recent()[0]["task"] == "kept"   # a fresh process still sees it

def test_cap_keeps_the_newest(tmp_path):
    log = RunLog(tmp_path / "runs.json", cap=3)
    for i in range(6):
        log.add({"id": str(i), "started": float(i)})
    rows = log.recent()
    assert [r["id"] for r in rows] == ["5", "4", "3"]     # oldest three dropped

def test_corrupt_file_is_tolerated(tmp_path):
    p = tmp_path / "runs.json"
    p.write_text("{ this is not json", encoding="utf-8")
    log = RunLog(p)
    assert log.recent() == []                             # never raises on a broken log
    log.add({"id": "a"})                                  # and can recover by writing fresh
    assert log.recent()[0]["id"] == "a"

def test_update_missing_id_is_a_noop(tmp_path):
    log = RunLog(tmp_path / "runs.json")
    log.add({"id": "a", "status": "running"})
    log.update("ghost", status="done")
    assert log.recent()[0]["status"] == "running"


# --- resumable detection: an interrupted Build (unfinished checkpoint) is offered a resume ---

def _checkpoint(tmp_path, statuses):
    st = LoopState(goal="g", steps=[Step(id=f"s{i}", goal="", status=s) for i, s in enumerate(statuses)])
    cp = tmp_path / ".newton" / "loop.json"
    cp.parent.mkdir(parents=True, exist_ok=True)
    st.save(cp)
    return cp

def test_build_resumable(tmp_path, monkeypatch):
    from newton.api import server
    # No checkpoint → not resumable.
    assert server._build_resumable(str(tmp_path)) is False
    # A checkpoint with an unfinished step → resumable.
    _checkpoint(tmp_path, [DONE, PENDING])
    monkeypatch.setattr(server, "load_settings", lambda p: load_settings(tmp_path))
    assert server._build_resumable(str(tmp_path)) is True
    # A fully-done checkpoint → nothing to resume.
    _checkpoint(tmp_path, [DONE, DONE])
    assert server._build_resumable(str(tmp_path)) is False
