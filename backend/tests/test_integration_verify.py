"""Integration verification in the LoopEngine — the whole-app load check.

Per-file verify + the self-generated unit test can all pass while the ASSEMBLED app won't import.
These tests prove the final gate: detect the entrypoint, import it for real, and feed a load failure
into the repair loop. The RUN step is executed for real (subprocess), so a genuine cross-file/module
error is caught — but the WRITE steps are scripted, so no model is needed."""

from __future__ import annotations

import subprocess
import sys

from newton.config import load_settings
from newton.loop.engine import LoopEngine
from newton.loop.execute import StepResult
from newton.loop.state import DONE, RUN, WRITE, LoopState, Step


class _WireExec:
    """WRITE steps write scripted content (a list per file, consumed in order — so a file can be
    written buggy first, then fixed on repair). RUN steps run for real, so the import smoke check
    actually executes."""
    def __init__(self, root, writes):
        self.root = root
        self.writes = writes
        self.ran: list[str] = []

    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            self.ran.append(step.command)
            r = subprocess.run(step.command, shell=True, cwd=self.root,
                               capture_output=True, text=True)
            return StepResult(r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[:300])
        content = self.writes[step.file].pop(0)
        (self.root / step.file).write_text(content, encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


# --- entrypoint detection ---

def _done(file, txt, tmp_path):
    p = tmp_path / file
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt, encoding="utf-8")
    return Step(id=file, goal="", kind=WRITE, file=file, status=DONE)

def test_detect_entrypoint_prefers_the_app_wiring_file(tmp_path):
    eng = LoopEngine(load_settings(tmp_path))
    state = LoopState(goal="g", steps=[
        _done("helper.py", "x = 1\n", tmp_path),
        _done("app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n", tmp_path),
    ])
    assert eng._detect_entrypoint(state) == "app.main"       # the FastAPI file, not the helper

def test_detect_entrypoint_none_when_nothing_looks_like_an_app(tmp_path):
    eng = LoopEngine(load_settings(tmp_path))
    state = LoopState(goal="g", steps=[_done("util.py", "def f():\n    return 1\n", tmp_path)])
    assert eng._detect_entrypoint(state) is None             # a lone util isn't an entrypoint


# --- dependency readiness ---

def test_project_python_not_ready_without_requirements(tmp_path):
    eng = LoopEngine(load_settings(tmp_path))
    py, ready = eng._project_python()
    assert py == sys.executable and ready is False           # can't guarantee deps → don't cry wolf


# --- the gate: a broken assembled app is caught and repaired ---

def test_integration_check_catches_a_load_failure_then_repair_fixes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")          # isolate: only the integration gate runs
    monkeypatch.setattr(LoopEngine, "_project_python", lambda self, extra=(): (sys.executable, True))
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: [Step(id="main", goal="", kind=WRITE, file="main.py")])
    # main.py imports fine as text but FAILS at import (NameError) — exactly what per-file parse
    # verify misses; the repair rewrites it correct.
    ex = _WireExec(tmp_path, {"main.py": ["result = undefined_name\n", "result = 1\n"]})
    res = LoopEngine(load_settings(tmp_path), executor=ex).run("build main")
    assert res.ok                                            # the app loads after repair
    assert any(s.id == "i_run" and s.status == DONE for s in res.state.steps)
    assert ex.ran and "import main" in ex.ran[-1]            # the whole-app import really ran
    assert (tmp_path / "main.py").read_text() == "result = 1\n"

def test_integration_check_off_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr(LoopEngine, "_project_python", lambda self, extra=(): (sys.executable, True))
    eng = LoopEngine(load_settings(tmp_path))
    state = LoopState(goal="g", steps=[_done("main.py", "app = 1\n", tmp_path)])
    eng._ensure_integration_check(state)
    assert not any(s.id == "i_run" for s in state.steps)     # disabled → no check added


# --- the route probe: a web app that LOADS but a request 500s is caught (the request-time gap) ---

def test_entry_is_asgi_true_only_for_a_web_app(tmp_path):
    eng = LoopEngine(load_settings(tmp_path))
    web = LoopState(goal="g", steps=[_done("main.py", "from fastapi import FastAPI\napp = FastAPI()\n", tmp_path)])
    plain = LoopState(goal="g", steps=[_done("calc.py", "def add(a, b):\n    return a + b\n", tmp_path)])
    assert eng._entry_is_asgi(web) is True
    assert eng._entry_is_asgi(plain) is False

_BAD_APP = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n\n"
    "@app.get('/')\n"
    "def root():\n"
    "    return {'value': undefined_name}\n"        # imports fine; raises only when the route is hit
)
_GOOD_APP = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n\n"
    "@app.get('/')\n"
    "def root():\n"
    "    return {'value': 1}\n"
)

def test_route_probe_catches_a_500_then_repair_fixes_it(tmp_path, monkeypatch):
    """The core gap: every file passes its own verify AND the app imports cleanly, but a GET request
    500s (a handler bug / contract mismatch). The probe drives the assembled app and catches it, then
    the repair — routed to main.py by the self-reported culprit — rewrites the handler correct."""
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setattr(LoopEngine, "_project_python", lambda self, extra=(): (sys.executable, True))
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: [Step(id="main", goal="", kind=WRITE, file="main.py")])
    ex = _WireExec(tmp_path, {"main.py": [_BAD_APP, _GOOD_APP]})
    res = LoopEngine(load_settings(tmp_path), executor=ex).run("build a web app")
    assert res.ok                                            # the app serves cleanly after repair
    assert any(s.id == "i_run" and s.status == DONE for s in res.state.steps)
    assert ex.ran and ".newton_integration.py" in ex.ran[-1]  # the ROUTE PROBE ran, not a plain import
    assert (tmp_path / "main.py").read_text() == _GOOD_APP
    assert not (tmp_path / ".newton_integration.py").exists()  # the probe artifact is cleaned up

def test_route_probe_passes_a_clean_web_app_without_crying_wolf(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setattr(LoopEngine, "_project_python", lambda self, extra=(): (sys.executable, True))
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: [Step(id="main", goal="", kind=WRITE, file="main.py")])
    ex = _WireExec(tmp_path, {"main.py": [_GOOD_APP]})       # only ever written once — no repair needed
    res = LoopEngine(load_settings(tmp_path), executor=ex).run("build a web app")
    assert res.ok
    assert ex.ran and ".newton_integration.py" in ex.ran[-1]
    assert ex.writes["main.py"] == []                        # written exactly once (probe didn't false-fail)
