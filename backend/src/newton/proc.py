"""Run a shell command with a timeout that actually holds.

`subprocess.run(cmd, shell=True, capture_output=True, timeout=...)` is not safe for commands that
spawn children: on timeout it kills only the SHELL, and a grandchild (a dev server, a watcher) keeps
the output pipes open, so the final `communicate()` blocks forever. A generated `python app.py` RUN
step hung the loop exactly this way (Windows, 2026-09-24). This helper runs the command in its own
process group and, on timeout, kills the whole TREE before collecting output — so a timeout is a
timeout, which unattended runs depend on.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass


def run_shell(cmd: str, *, cwd: str | Path, timeout: float) -> subprocess.CompletedProcess:
    """Like `subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)`, but
    a timeout kills the whole process tree. Raises `subprocess.TimeoutExpired` on timeout."""
    kw: dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                else {"start_new_session": True})
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, **kw)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=5)
        except Exception:
            pass                                  # output of a killed command isn't needed
        raise subprocess.TimeoutExpired(cmd, timeout) from None
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)
