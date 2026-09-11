"""Tests for the scaffold library and its templates-first integration into Build."""

from __future__ import annotations

import ast

from newton.config import load_settings
from newton.project.conductor import ProjectConductor
from newton.project.state import DONE, PENDING, ProjectState, Task
from newton.scaffolds import ScaffoldLibrary


def _lib():
    return ScaffoldLibrary()


# --- stack gating ---

def test_stack_matches_fastapi_only():
    lib = _lib()
    assert lib.stack_matches("Python + FastAPI + SQLite (SQLAlchemy)")
    assert not lib.stack_matches("Node + Express + Postgres")
    assert not lib.stack_matches("")

def test_unknown_stack_matches_nothing():
    assert _lib().match("config.py", stack="Go + Gin") is None


# --- template matching ---

def test_complete_boilerplate_is_marked_complete():
    lib = _lib()
    for f in ("config.py", "db.py", "database.py", "errors.py", "sessions.py",
              "requirements.txt", "Dockerfile"):
        sc = lib.match(f, stack="FastAPI")
        assert sc is not None and sc.complete, f

def test_ci_workflow_matches():
    sc = _lib().match(".github/workflows/ci.yml", stack="FastAPI")
    assert sc is not None and sc.complete and "pytest" in sc.text

def test_domain_files_are_skeletons_to_complete():
    lib = _lib()
    for f in ("models.py", "main.py", "app.py", "auth.py", "app/routers/tasks.py", "tests/test_api.py"):
        sc = lib.match(f, stack="FastAPI")
        assert sc is not None and not sc.complete and sc.note, f


# --- rendering ---

def test_pkg_prefix_substituted_from_root_package():
    with_pkg = _lib().match("app/db.py", stack="FastAPI", root_pkg="app")
    assert "from app.config import get_settings" in with_pkg.text
    flat = _lib().match("db.py", stack="FastAPI", root_pkg="")
    assert "from config import get_settings" in flat.text

def test_tests_use_main_module_path():
    sc = _lib().match("tests/test_api.py", stack="FastAPI", main_mod="app.main")
    assert "from app.main import app" in sc.text

def test_all_python_templates_are_valid_syntax():
    lib = _lib()
    for f in ("config.py", "db.py", "errors.py", "sessions.py", "auth.py", "models.py",
              "main.py", "routes.py", "tests/test_x.py"):
        sc = lib.match(f, stack="FastAPI", root_pkg="app", main_mod="app.main")
        ast.parse(sc.text)                              # raises SyntaxError if malformed


# --- Build integration ---

def _pc(root):
    return ProjectConductor(load_settings(str(root)), emit=lambda *a: None, approve=lambda *a: True)

def test_scaffold_context_infers_package_and_entrypoint():
    tasks = [Task(id="t1", title="", goal="", file="app/config.py"),
             Task(id="t2", title="", goal="", file="app/main.py"),
             Task(id="t3", title="", goal="", file="tests/test_api.py")]
    root_pkg, main_mod = _pc(".")._scaffold_context(tasks)
    assert root_pkg == "app" and main_mod == "app.main"

def test_apply_scaffolds_writes_complete_and_marks_done(tmp_path):
    state = ProjectState(goal="A task API with sessions", tasks=[
        Task(id="t1", title="config", goal="build config", file="app/config.py"),
        Task(id="t2", title="models", goal="build models", file="app/models.py"),
        Task(id="t3", title="ci", goal="build ci", file=".github/workflows/ci.yml"),
    ])
    _pc(tmp_path)._apply_scaffolds(state, "Python + FastAPI + SQLite", "A task API with sessions")

    # Complete boilerplate is written and its task is done (model skipped).
    assert (tmp_path / "app/config.py").read_text().strip()
    assert state.by_id("t1").status == DONE
    assert state.by_id("t3").status == DONE               # CI workflow
    # A skeleton is written but stays pending, with a "complete it" note added to the goal.
    assert (tmp_path / "app/models.py").read_text().strip()
    assert state.by_id("t2").status == PENDING
    assert "COMPLETE it" in state.by_id("t2").goal
    # A dependency manifest is added even though the plan didn't list one.
    assert (tmp_path / "requirements.txt").is_file()
    # Package inferred as 'app' → the db/config wiring resolves.
    assert "from app.config import get_settings" not in (tmp_path / "app/config.py").read_text()  # config has no self-import

def test_apply_scaffolds_noop_for_unknown_stack(tmp_path):
    state = ProjectState(goal="x", tasks=[Task(id="t1", title="", goal="", file="app/config.py")])
    _pc(tmp_path)._apply_scaffolds(state, "Node + Express", "x")
    assert not (tmp_path / "app/config.py").exists()      # nothing laid down
    assert state.by_id("t1").status == PENDING

# --- React + Vite frontend stack (pairs with FastAPI) ---

def test_stack_gating_backend_and_frontend():
    lib = _lib()
    fs = "Python + FastAPI backend, React + Vite + TypeScript frontend, SQLite"
    assert lib.matches_backend(fs) and lib.matches_frontend(fs) and lib.stack_matches(fs)
    assert lib.matches_backend("FastAPI") and not lib.matches_frontend("FastAPI")
    assert lib.matches_frontend("React + Vite") and not lib.matches_backend("React + Vite")

def test_frontend_config_files_are_complete():
    lib = _lib()
    fs = "FastAPI + React"
    for f in ("frontend/package.json", "frontend/index.html", "frontend/vite.config.ts",
              "frontend/tsconfig.json", "frontend/src/main.tsx"):
        sc = lib.match(f, stack=fs)
        assert sc is not None and sc.complete, f

def test_frontend_app_and_api_are_skeletons():
    lib = _lib()
    for f in ("frontend/src/App.tsx", "frontend/src/api.ts"):
        sc = lib.match(f, stack="FastAPI + React")
        assert sc is not None and not sc.complete and sc.note, f

def test_frontend_only_matched_when_stack_wants_a_frontend():
    # A .tsx with no frontend in the stack shouldn't scaffold (backend-only build).
    assert _lib().match("frontend/src/App.tsx", stack="Python + FastAPI") is None

def test_frontend_package_json_is_valid_and_slugged():
    import json as _json
    sc = _lib().match("frontend/package.json", stack="React + Vite", app_name="Task Tracker")
    data = _json.loads(sc.text)                       # valid JSON
    assert data["name"] == "task-tracker-frontend"    # app_name slugged
    assert "react" in data["dependencies"] and "vite" in data["devDependencies"]

def test_backend_files_still_match_in_a_fullstack_stack():
    # With React in the stack, a backend .py file must still route to the backend template.
    sc = _lib().match("app/config.py", stack="FastAPI + React", root_pkg="app")
    assert sc is not None and sc.complete and "get_settings" in sc.text

def test_apply_scaffolds_lays_down_both_backend_and_frontend(tmp_path):
    state = ProjectState(goal="A task app", tasks=[
        Task(id="t1", title="", goal="", file="app/config.py"),
        Task(id="t2", title="", goal="", file="app/main.py"),
        Task(id="t3", title="", goal="", file="frontend/package.json"),
        Task(id="t4", title="", goal="", file="frontend/src/App.tsx"),
        Task(id="t5", title="", goal="", file="frontend/src/main.tsx"),
    ])
    _pc(tmp_path)._apply_scaffolds(state, "Python + FastAPI, React + Vite frontend", "A task app")
    # Backend config: complete → done. Frontend package.json + main.tsx: complete → done.
    assert (tmp_path / "app/config.py").read_text().strip() and state.by_id("t1").status == DONE
    assert (tmp_path / "frontend/package.json").is_file() and state.by_id("t3").status == DONE
    assert state.by_id("t5").status == DONE
    # Skeletons stay pending for the model.
    assert (tmp_path / "frontend/src/App.tsx").is_file() and state.by_id("t4").status == PENDING


def test_apply_scaffolds_never_clobbers_existing_content(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/config.py").write_text("# my real config\n")
    state = ProjectState(goal="x", tasks=[Task(id="t1", title="", goal="", file="app/config.py")])
    _pc(tmp_path)._apply_scaffolds(state, "FastAPI", "x")
    assert (tmp_path / "app/config.py").read_text() == "# my real config\n"
    assert state.by_id("t1").status == PENDING            # not overwritten, not marked done
