"""Tests for the Project Conductor's dependency-driven backlog logic (no model calls)."""

from __future__ import annotations

from newton.config import load_settings
from newton.project.conductor import ProjectConductor
from newton.project.state import BLOCKED, DONE, FAILED, ProjectState, Task


def _pc():
    return ProjectConductor(load_settings("."), emit=lambda *a: None, approve=lambda *a: True)


def _state():
    return ProjectState(goal="build it", tasks=[
        Task(id="t1", title="base", goal="make base"),
        Task(id="t2", title="uses base", goal="use base", depends_on=["t1"]),
    ])


def test_ready_picks_task_without_deps_first():
    s = _state()
    assert s.ready().id == "t1"

def test_dependent_becomes_ready_after_dep_done():
    s = _state()
    s.by_id("t1").status = DONE
    assert s.ready().id == "t2"

def test_dependent_blocks_when_dep_failed():
    s = _state()
    s.by_id("t1").status = FAILED
    assert s.ready() is None            # t2 cannot run
    assert s.by_id("t2").status == BLOCKED

def test_counts_and_remaining():
    s = _state()
    s.by_id("t1").status = DONE
    assert s.remaining() == 1           # only t2 left
    assert s.counts()[DONE] == 1

def test_save_load_roundtrip(tmp_path):
    s = _state()
    s.by_id("t1").status = DONE
    p = tmp_path / ".newton" / "project.json"
    s.save(p)
    back = ProjectState.load(p)
    assert back.by_id("t1").status == DONE
    assert back.by_id("t2").depends_on == ["t1"]


# --- decomposition-quality guard ---

def test_refine_merges_same_file_tasks():
    # The exact over-decomposition we saw: 5 slivers across 2 files.
    tasks = [
        Task(id="t1", title="add", goal="add add()", file="calc.py"),
        Task(id="t2", title="mul", goal="add multiply()", file="calc.py", depends_on=["t1"]),
        Task(id="t3", title="import", goal="import them", file="main.py", depends_on=["t2"]),
        Task(id="t4", title="call add", goal="call add", file="main.py", depends_on=["t3"]),
        Task(id="t5", title="call mul", goal="call multiply", file="main.py", depends_on=["t4"]),
    ]
    out = _pc()._refine_backlog(tasks)
    assert [t.file for t in out] == ["calc.py", "main.py"]        # one task per file
    calc, main = out
    assert calc.depends_on == []                                  # intra-file deps gone
    assert main.depends_on == [calc.id]                           # cross-file dep remapped
    assert "add()" in calc.goal and "multiply()" in calc.goal     # sub-goals combined

def test_refine_drops_forward_and_dangling_deps():
    tasks = [
        Task(id="a", title="x", goal="x", file="a.py", depends_on=["b", "zzz"]),
        Task(id="b", title="y", goal="y", file="b.py"),
    ]
    out = _pc()._refine_backlog(tasks)
    a = next(t for t in out if t.file == "a.py")
    assert a.depends_on == []                                     # forward + dangling both dropped (DAG)

def test_refine_keeps_solo_tasks_separate():
    tasks = [Task(id="t1", title="a", goal="a", file=""), Task(id="t2", title="b", goal="b", file="")]
    out = _pc()._refine_backlog(tasks)
    assert len(out) == 2


# --- architecture-first planning (blueprint → dependency-ordered file tasks) ---

def test_order_components_puts_dependencies_first():
    comps = [
        {"id": "c3", "name": "api", "layer": "api", "files": ["api.py"], "depends_on": ["c2"]},
        {"id": "c2", "name": "models", "layer": "db", "files": ["models.py"], "depends_on": ["c1"]},
        {"id": "c1", "name": "config", "layer": "config", "files": ["config.py"]},
    ]
    ordered = [c["id"] for c in _pc()._order_components(comps)]
    assert ordered == ["c1", "c2", "c3"]                 # topo order despite input order

def test_order_components_tolerates_cycles_and_unknown_edges():
    comps = [
        {"id": "a", "name": "a", "files": ["a.py"], "depends_on": ["b", "ghost"]},
        {"id": "b", "name": "b", "files": ["b.py"], "depends_on": ["a"]},   # cycle a<->b
    ]
    ordered = [c["id"] for c in _pc()._order_components(comps)]
    assert set(ordered) == {"a", "b"} and len(ordered) == 2      # never drops/loops

def test_expand_makes_one_task_per_file_tagged_by_component():
    bp = {"stack": "Python + FastAPI", "components": [
        {"id": "c1", "name": "config", "layer": "config", "files": ["app/config.py"]},
        {"id": "c2", "name": "models", "layer": "db", "files": ["app/models.py", "app/db.py"],
         "depends_on": ["c1"]},
    ]}
    tasks = _pc()._expand("build an API", bp)
    assert [t.file for t in tasks] == ["app/config.py", "app/models.py", "app/db.py"]
    assert [t.component for t in tasks] == ["config", "models", "models"]
    # models' first file depends on config's last file; its second file depends on its first.
    cfg, m1, m2 = tasks
    assert cfg.depends_on == []
    assert cfg.id in m1.depends_on                       # cross-component edge
    assert m1.id in m2.depends_on                        # intra-component sequential edge
    assert "Stack: Python + FastAPI" in m1.goal          # architectural context carried in

def test_expand_respects_the_task_cap():
    files = [f"f{i}.py" for i in range(100)]
    bp = {"stack": "x", "components": [{"id": "c1", "name": "big", "layer": "logic",
          "files": files[:ProjectConductor.MAX_FILES_PER_COMPONENT]}]}
    # many components, each at the per-component cap, should still stop at MAX_TASKS overall.
    bp["components"] = [{"id": f"c{i}", "name": f"c{i}", "layer": "logic",
                        "files": [f"c{i}_{j}.py" for j in range(ProjectConductor.MAX_FILES_PER_COMPONENT)]}
                        for i in range(20)]
    tasks = _pc()._expand("big", bp)
    assert len(tasks) <= ProjectConductor.MAX_TASKS


# --- blueprint normalization (salvage a weak model's directory-tree drift) ---

def test_normalize_passes_through_our_shape():
    bp = {"components": [{"id": "c1", "name": "x", "files": ["x.py"]}]}
    out = _pc()._normalize_blueprint(bp)
    assert out["components"] == bp["components"] and "stack" in out

def test_normalize_salvages_directory_structure_shape():
    # The exact drift observed from qwen2.5-coder:7b: a nested directory tree, no components[].
    raw = {
        "description": "A task API.",
        "architecture": {
            "technology_stack": {"backend": "Python (Flask)", "database": "SQLite"},
            "directory_structure": {
                "root": "App",                                  # string alias — ignored
                "backend/app": {"__init__.py": "init", "config.py": "settings"},
                "backend/auth": {"authentication.py": "login", "session.py": "sessions"},
            },
        },
    }
    out = _pc()._normalize_blueprint(raw)
    assert out is not None
    names = [c["name"] for c in out["components"]]
    assert "app" in names and "auth" in names               # one component per real dir
    files = [f for c in out["components"] for f in c["files"]]
    assert "backend/app/config.py" in files                  # flattened to real paths
    assert "backend/auth/session.py" in files
    assert "Python (Flask)" in out["stack"]                   # stack pulled from the map

def test_normalize_returns_none_when_nothing_salvageable():
    assert _pc()._normalize_blueprint({"foo": "bar"}) is None
    assert _pc()._normalize_blueprint("not a dict") is None


# --- integration verification (does the whole assembled project run) ---

def _pc_at(root):
    return ProjectConductor(load_settings(str(root)), emit=lambda *a: None, approve=lambda *a: True)

def _state_with(files):
    return ProjectState(goal="", tasks=[Task(id=f"t{i}", title="", goal="", file=f)
                                        for i, f in enumerate(files, 1)])

def test_integration_passes_on_working_multifile_project(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "main.py").write_text("from calc import add\nif __name__ == '__main__':\n    print(add(2, 3))\n")
    ok, _ = _pc_at(tmp_path)._integration_verify(_state_with(["calc.py", "main.py"]))
    assert ok

def test_integration_fails_when_project_crashes(tmp_path):
    # main imports something that doesn't exist — per-file checks wouldn't catch the wiring.
    (tmp_path / "main.py").write_text("from missing_mod import x\nif __name__ == '__main__':\n    print(x)\n")
    ok, msg = _pc_at(tmp_path)._integration_verify(_state_with(["main.py"]))
    assert not ok and "crashed" in msg

def test_integration_checks_expected_output(tmp_path):
    (tmp_path / "main.py").write_text("if __name__ == '__main__':\n    print('NOPE')\n")
    state = ProjectState(goal="it should print 'DONE'", tasks=[Task(id="t", title="", goal="", file="main.py")])
    ok, _ = _pc_at(tmp_path)._integration_verify(state)
    assert not ok                                    # ran clean but wrong output

def test_integration_skips_when_no_entrypoint(tmp_path):
    (tmp_path / "lib.py").write_text("def f():\n    return 1\n")   # no __main__ guard
    ok, msg = _pc_at(tmp_path)._integration_verify(_state_with(["lib.py"]))
    assert ok and "entrypoint" in msg

def test_find_entry_prefers_shallowest_named_script(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x = 1\n")
    (tmp_path / "run.py").write_text("y = 2\n")
    state = _state_with(["app/main.py", "run.py"])
    assert _pc_at(tmp_path)._find_entry(state) == "run.py"     # root-level beats nested

def test_find_entry_uses_main_guard_when_no_named_script(tmp_path):
    (tmp_path / "lib.py").write_text("def f():\n    return 1\n")
    (tmp_path / "start.py").write_text("if __name__ == '__main__':\n    print('go')\n")
    state = _state_with(["lib.py", "start.py"])
    assert _pc_at(tmp_path)._find_entry(state) == "start.py"

def test_real_run_skipped_without_requirements(tmp_path, monkeypatch):
    # No requirements.txt → the dispatcher must take the light path, never touch a venv.
    (tmp_path / "main.py").write_text("print('ok')\n")
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_real_run", lambda *a: (_ for _ in ()).throw(AssertionError("should not run")))
    ok, _ = pc._integration_verify(_state_with(["main.py"]))
    assert ok

def test_real_run_disabled_by_env(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "main.py").write_text("print('ok')\n")
    monkeypatch.setenv("NEWTON_RUN_BUILD", "0")               # opt out of the heavy real run
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_real_run", lambda *a: (_ for _ in ()).throw(AssertionError("should not run")))
    ok, _ = pc._integration_verify(_state_with(["main.py"]))
    assert ok

def test_real_run_falls_back_when_env_setup_fails(tmp_path, monkeypatch):
    # requirements present but the venv can't be built → fall back to the light check, not a failure.
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "main.py").write_text("print('ok')\n")
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_ensure_project_venv", lambda root: None)
    ok, msg = pc._integration_verify(_state_with(["main.py"]))
    assert ok                                                 # light path ran main.py cleanly

# --- full-stack: backend real-run + frontend npm build, combined ---

def test_find_frontend_dir(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")
    assert _pc_at(tmp_path)._find_frontend_dir() == tmp_path / "frontend"

def test_find_frontend_dir_none_without_package_json(tmp_path):
    assert _pc_at(tmp_path)._find_frontend_dir() is None

def test_verify_combines_backend_and_frontend_pass(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "frontend").mkdir(); (tmp_path / "frontend" / "package.json").write_text("{}")
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_real_run", lambda s: (True, "test suite passes"))
    monkeypatch.setattr(pc, "_frontend_build", lambda d: (True, "frontend builds"))
    ok, msg = pc._integration_verify(_state_with(["app/main.py"]))
    assert ok and "backend" in msg and "frontend" in msg

def test_verify_fails_if_frontend_build_fails(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    (tmp_path / "frontend").mkdir(); (tmp_path / "frontend" / "package.json").write_text("{}")
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_real_run", lambda s: (True, "test suite passes"))
    monkeypatch.setattr(pc, "_frontend_build", lambda d: (False, "the frontend build failed: TS2345"))
    ok, msg = pc._integration_verify(_state_with(["app/main.py"]))
    assert not ok and "frontend build failed" in msg

def test_verify_frontend_only_project(tmp_path, monkeypatch):
    # No requirements.txt → backend skipped; a frontend/ alone still gets built.
    (tmp_path / "frontend").mkdir(); (tmp_path / "frontend" / "package.json").write_text("{}")
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_frontend_build", lambda d: (True, "frontend builds"))
    ok, msg = pc._integration_verify(_state_with([]))
    assert ok and "frontend" in msg

def test_verify_frontend_skip_falls_back(tmp_path, monkeypatch):
    # npm unavailable (frontend_build returns None) and no backend → light check runs.
    (tmp_path / "frontend").mkdir(); (tmp_path / "frontend" / "package.json").write_text("{}")
    (tmp_path / "main.py").write_text("print('ok')\n")
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_frontend_build", lambda d: None)
    ok, _ = pc._integration_verify(_state_with(["main.py"]))
    assert ok                                          # fell back to the light entrypoint check


# --- integration self-correction (feed the failure back to fix it) ---

def test_culprit_files_from_tsc_error():
    state = _state_with(["frontend/src/App.tsx", "frontend/src/api.ts"])
    err = "frontend/src/App.tsx(12,5): error TS2345: Argument of type 'number' is not assignable."
    assert _pc()._culprit_files(err, state) == ["frontend/src/App.tsx"]

def test_culprit_files_from_pytest_traceback_prefers_app_over_test():
    state = _state_with(["app/models.py", "tests/test_api.py"])
    err = 'tests/test_api.py::test_user FAILED\n  File "app/models.py", line 10, in User\n  NameError'
    got = _pc()._culprit_files(err, state)
    assert got[0] == "app/models.py"                 # the bug's in the code, not the test

def test_culprit_files_matches_by_basename():
    state = _state_with(["app/routers/tasks.py"])
    err = "ImportError in tasks.py: cannot import name 'get_db'"
    assert _pc()._culprit_files(err, state) == ["app/routers/tasks.py"]

def test_culprit_files_none_when_no_known_file():
    state = _state_with(["app/main.py"])
    assert _pc()._culprit_files("some generic error with no file path", state) == []

def test_integration_fix_runs_task_for_culprit(tmp_path, monkeypatch):
    state = _state_with(["frontend/src/App.tsx"])
    pc = _pc_at(tmp_path)
    calls = []
    monkeypatch.setattr(pc, "_run_task", lambda goal: (calls.append(goal) or (True, "fixed")))
    assert pc._integration_fix(state, "frontend/src/App.tsx(1,1): error TS2304")
    assert len(calls) == 1 and "App.tsx" in calls[0] and "TS2304" in calls[0]

def test_integration_fix_noop_without_culprit(tmp_path, monkeypatch):
    state = _state_with(["app/main.py"])
    pc = _pc_at(tmp_path)
    monkeypatch.setattr(pc, "_run_task", lambda goal: (_ for _ in ()).throw(AssertionError("no run")))
    assert pc._integration_fix(state, "opaque error") is False


def test_integration_runs_conventional_main_without_guard(tmp_path):
    # main.py with top-level code and NO __main__ guard is still an entrypoint by name.
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "main.py").write_text("from calc import add\nprint(add(2, 3))\n")
    ok, _ = _pc_at(tmp_path)._integration_verify(_state_with(["calc.py", "main.py"]))
    assert ok
