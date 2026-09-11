"""Tests for the job queue — parsing, durability, batch run, and resume (no model needed)."""

from __future__ import annotations

from newton.config import load_settings
from newton.queue import DONE, JobQueue, QueueState
from newton.queue.state import Job


class _Res:
    def __init__(self, ok, answer="ok"):
        self.ok, self.answer = ok, answer


class _FakeEngine:
    """Records goals it 'built'; ok unless the goal contains 'FAIL'."""
    calls: list = []

    def __init__(self, settings):
        pass

    def run(self, goal):
        _FakeEngine.calls.append(goal)
        return _Res("FAIL" not in goal)


def _factory():
    _FakeEngine.calls = []
    return _FakeEngine


# --- parsing ---

def test_from_jobs_file_parses_lines_and_json(tmp_path):
    f = tmp_path / "jobs.txt"
    f.write_text("# a comment\nadd a login page\n\n"
                 '{"goal": "add a logout button", "id": "x", "project": "web"}\n', encoding="utf-8")
    st = QueueState.from_jobs_file(f)
    assert [j.goal for j in st.jobs] == ["add a login page", "add a logout button"]
    assert st.jobs[1].id == "x" and st.jobs[1].project == "web"

def test_checkpoint_roundtrip(tmp_path):
    st = QueueState(jobs=[Job(id="j1", goal="a", status=DONE), Job(id="j2", goal="b")])
    p = tmp_path / "queue.json"
    st.save(p)
    back = QueueState.load(p)
    assert [(j.id, j.status) for j in back.jobs] == [("j1", "done"), ("j2", "pending")]


# --- batch run + durability ---

def test_queue_runs_all_jobs_and_checkpoints(tmp_path):
    st = QueueState(jobs=[Job(id="j1", goal="a"), Job(id="j2", goal="b")])
    q = JobQueue(load_settings(tmp_path), loop_factory=_factory())
    res = q.run(st)
    assert res.ok and res.done == 2 and _FakeEngine.calls == ["a", "b"]
    assert (tmp_path / ".newton" / "queue.json").is_file()          # batch checkpointed

def test_failed_job_marks_batch_incomplete(tmp_path):
    st = QueueState(jobs=[Job(id="j1", goal="a"), Job(id="j2", goal="b FAIL")])
    res = JobQueue(load_settings(tmp_path), loop_factory=_factory()).run(st)
    assert not res.ok and res.done == 1 and res.failed == 1


# --- resume (the flight-story capability) ---

def test_resume_skips_done_jobs(tmp_path):
    st = QueueState(jobs=[Job(id="j1", goal="a", status=DONE), Job(id="j2", goal="b")])
    st.save(tmp_path / ".newton" / "queue.json")
    res = JobQueue(load_settings(tmp_path), loop_factory=_factory()).run(resume=True)
    assert res.ok and _FakeEngine.calls == ["b"]                    # j1 not re-run

def test_interrupted_running_job_restarts_on_resume(tmp_path):
    st = QueueState(jobs=[Job(id="j1", goal="a", status="running"), Job(id="j2", goal="b")])
    st.save(tmp_path / ".newton" / "queue.json")
    res = JobQueue(load_settings(tmp_path), loop_factory=_factory()).run(resume=True)
    assert res.ok and _FakeEngine.calls == ["a", "b"]              # interrupted j1 restarts
