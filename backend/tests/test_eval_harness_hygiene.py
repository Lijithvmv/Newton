"""Eval hygiene: the agent never sees the grader, and graders never leave servers running.

Measured 2026-10-12: with check.py in the project, Newton's generated tests imported it (6 of 8 false
FAILs) or copied its assertions; and the urlshort grader left 9 app servers listening on 0.0.0.0
because killing a venv launcher doesn't kill the real interpreter it started."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from newton.eval.harness import task_inputs
from newton.eval.tasks import HARD_TASKS, LARGE_TASKS, REAL_TASKS, SEED_TASKS

ALL = SEED_TASKS + HARD_TASKS + LARGE_TASKS + REAL_TASKS


def test_grader_is_never_a_task_input():
    for t in ALL:
        given = task_inputs(t)
        assert not any(rel.rsplit("/", 1)[-1] == "check.py" for rel in given), t.id
        for rel in t.files:                                  # task-provided tests stay visible
            if rel.rsplit("/", 1)[-1].startswith("test_"):
                assert rel in given, (t.id, rel)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_urlshort_grader_kills_the_whole_server_tree(tmp_path):
    check = next(t for t in REAL_TASKS if t.id == "urlshort").files["check.py"]
    (tmp_path / "check.py").write_text(check, encoding="utf-8")
    # An app that never serves but starts a long-lived child — the orphan shape we found.
    (tmp_path / "app.py").write_text(
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "open('child.pid', 'w').write(str(c.pid))\n"
        "time.sleep(120)\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "check.py"], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0                                   # the app never served: the grade fails
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(20):
        if not _alive(pid):
            break
        time.sleep(0.5)
    else:
        pytest.fail(f"grader left the server's child process {pid} running")
