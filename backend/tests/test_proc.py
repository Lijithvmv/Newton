"""run_shell: a timeout must hold even when the command spawns a child that keeps the pipes open."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from newton.proc import run_shell

_PY = f'"{sys.executable}"'


def test_timeout_kills_the_whole_tree(tmp_path):
    # The shell starts python, which starts a grandchild that inherits stdout and sleeps — the shape
    # of a generated `python app.py` server. Plain subprocess.run(..., timeout) hangs on this.
    child = ("import subprocess,sys,time; "
             "subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); time.sleep(120)")
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        run_shell(f'{_PY} -c "{child}"', cwd=tmp_path, timeout=2)
    assert time.time() - t0 < 30


def test_normal_command_returns_output_and_code(tmp_path):
    r = run_shell(f'{_PY} -c "import sys; print(42); sys.exit(3)"', cwd=tmp_path, timeout=30)
    assert r.returncode == 3 and r.stdout.strip() == "42"
