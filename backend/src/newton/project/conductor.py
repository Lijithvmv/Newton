"""ProjectConductor — decompose a project goal, then delegate tasks to the Task Conductor.

Flow: Decompose (one bounded model call → a dependency-ordered backlog, human-approved) →
for each ready task, run a fresh Task Conductor with that task's goal (it does the staged
Understand→…→Remember with whole-repo retrieval that now sees files earlier tasks created)
→ distill the result into project state → repeat until the backlog is drained.
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..conductor.pipeline import Conductor, derive_expect
from ..conductor.state import extract_json
from ..config import Settings
from ..context import project_tree
from ..llm import complete
from ..scaffolds import ScaffoldLibrary
from ..tools import ToolBelt
from .state import BLOCKED, DONE, FAILED, PENDING, RUNNING, ProjectState, Task

Emit = Callable[[str, Any], None]
Approve = Callable[[str, dict], bool]

DECOMPOSE_SYSTEM = (
    "You are Newton's project planner. You break a software goal into a small, ordered set "
    "of concrete, file-level build tasks that a junior coder can each do in one sitting. "
    "Output ONLY JSON."
)

BLUEPRINT_SYSTEM = (
    "You are Newton's software architect. Given a project goal you design a realistic, "
    "buildable architecture that a small local model can implement one file at a time. You "
    "choose a conventional stack, lay out a real directory structure, and break the system "
    "into components across the layers a production application actually has: configuration, "
    "data/models, authentication & sessions, user management, business logic, API, frontend, "
    "and tests/CI. You explicitly account for the cross-cutting concerns every serious app "
    "needs — session management, error handling, user management, configuration, logging, "
    "automated tests, and CI/CD — by making each the responsibility of a named component. "
    "Prefer proven, boring, conventional choices over novelty. Output ONLY JSON."
)

# Layers we recognise, in a sensible default build order (dependencies first).
_LAYER_ORDER = {
    "config": 0, "infra": 0, "db": 1, "models": 1, "data": 1, "auth": 2, "session": 2,
    "user": 2, "logic": 3, "service": 3, "core": 3, "api": 4, "backend": 4, "frontend": 5,
    "ui": 5, "tests": 6, "test": 6, "ci": 7, "cicd": 7, "ops": 7,
}

# A filled example in the exact target shape — weak local models copy a concrete example far
# more reliably than they follow an abstract schema. Deliberately a different domain so the
# model adapts the structure rather than the content.
_BLUEPRINT_EXAMPLE = """{
  "stack": "Python + FastAPI + SQLite (SQLAlchemy), pytest, GitHub Actions",
  "summary": "A URL shortener with user accounts. FastAPI serves the API, SQLAlchemy models persist to SQLite, sessions authenticate users, and a middleware handles errors uniformly.",
  "components": [
    {"id": "c1", "name": "config", "layer": "config", "concern": "configuration & settings",
     "files": ["app/config.py"], "depends_on": []},
    {"id": "c2", "name": "database", "layer": "db", "concern": "persistence & models",
     "files": ["app/db.py", "app/models.py"], "depends_on": ["c1"]},
    {"id": "c3", "name": "auth", "layer": "auth", "concern": "session management & login",
     "files": ["app/auth.py", "app/sessions.py"], "depends_on": ["c2"]},
    {"id": "c4", "name": "errors", "layer": "logic", "concern": "error handling",
     "files": ["app/errors.py"], "depends_on": ["c1"]},
    {"id": "c5", "name": "api", "layer": "api", "concern": "shorten & redirect endpoints",
     "files": ["app/routes.py", "app/main.py"], "depends_on": ["c2", "c3", "c4"]},
    {"id": "c6", "name": "tests", "layer": "tests", "concern": "automated tests",
     "files": ["tests/test_api.py"], "depends_on": ["c5"]},
    {"id": "c7", "name": "ci", "layer": "ci", "concern": "CI/CD",
     "files": [".github/workflows/ci.yml"], "depends_on": ["c6"]}
  ]
}"""


# Third-party packages our scaffolded stacks pull in — a missing one at integration time means
# "deps not installed in Newton's venv", not a code bug, so the runtime check is skipped not failed.
_EXTERNAL_DEPS = {
    "fastapi", "starlette", "uvicorn", "sqlalchemy", "pydantic", "httpx", "pytest",
    "jinja2", "aiofiles", "passlib", "jose", "bcrypt", "alembic", "flask", "django",
}


def _infer_layer(dirname: str) -> str:
    """Best-effort layer name from a directory name, for salvaged blueprints."""
    d = dirname.lower()
    for key in _LAYER_ORDER:
        if key in d:
            return key
    return "logic"


@dataclass
class ProjectResult:
    ok: bool
    answer: str
    state: ProjectState | None = None
    tasks_done: int = 0
    tasks_failed: int = 0


class ProjectConductor:
    MAX_TASKS = 40            # a real application is many files, not a handful
    MAX_FILES_PER_COMPONENT = 8

    def __init__(self, settings: Settings, *, emit: Emit, approve: Approve) -> None:
        self.s = settings
        self.emit = emit
        self.approve = approve
        self.model = settings.agent_model
        self.scaffolds = ScaffoldLibrary()

    # --- decomposition -------------------------------------------------

    def _decompose(self, goal: str) -> list[Task]:
        tree = project_tree(self.s.project_root)
        instruction = (
            f"Project goal:\n{goal}\n\n"
            f"Existing project files:\n{tree}\n\n"
            "Break this into 2 to 6 concrete build tasks. Output JSON:\n"
            '{"tasks": [{"id": "t1", "title": "<short>", "file": "<the one file this task '
            'creates or edits>", "goal": "<a self-contained instruction: what to implement '
            'in that file, precise enough to build and verify>", "depends_on": ["<ids>"]}]}\n'
            "Order matters: a task may depend on files created by earlier tasks. Keep each "
            "task to a single file. Prefer fewer, larger-value tasks. Output ONLY the JSON."
        )
        resp = complete(self.model, [
            {"role": "system", "content": DECOMPOSE_SYSTEM},
            {"role": "user", "content": instruction},
        ], temperature=self.s.temperature)
        data = extract_json(resp.choices[0].message.content or "")
        raw = data.get("tasks") if isinstance(data, dict) else None
        if not raw or not isinstance(raw, list):
            return []
        tasks: list[Task] = []
        for i, t in enumerate(raw[: self.MAX_TASKS], 1):
            if not isinstance(t, dict):
                continue
            tasks.append(Task(
                id=str(t.get("id") or f"t{i}"),
                title=str(t.get("title") or f"Task {i}"),
                goal=str(t.get("goal") or t.get("title") or ""),
                file=str(t.get("file") or ""),
                depends_on=[str(d) for d in (t.get("depends_on") or []) if d],
            ))
        return [t for t in tasks if t.goal]

    # --- decomposition-quality guard (system repairs the model's plan) --

    def _refine_backlog(self, tasks: list[Task]) -> list[Task]:
        """Weak models over-decompose — five slivers across two files where two clean
        tasks would do. Deterministically repair the plan: collapse multiple tasks that
        target the same file into one, remap dependencies, and keep only backward edges so
        the result is always an acyclic, one-file-one-task backlog."""
        if not tasks:
            return tasks

        # Group in first-seen order; tasks with no file each stand alone.
        groups: dict[str, list[Task]] = {}
        order: list[str] = []
        for t in tasks:
            key = t.file or f"__solo_{t.id}"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(t)

        remap: dict[str, str] = {}
        merged: list[Task] = []
        for i, key in enumerate(order, 1):
            grp = groups[key]
            new_id = f"t{i}"
            for t in grp:
                remap[t.id] = new_id
            if len(grp) == 1:
                t = grp[0]
                t.id = new_id
                merged.append(t)
            else:
                file = grp[0].file
                deps: list[str] = []
                for t in grp:
                    deps.extend(t.depends_on)
                merged.append(Task(
                    id=new_id,
                    title=f"Build {file}" if file else grp[0].title,
                    goal=" ".join(t.goal for t in grp),   # combined — whole-file regen handles it
                    file=file,
                    depends_on=deps,
                ))

        # Remap dependencies; drop self/dangling and any forward edge (guarantees a DAG).
        pos = {t.id: idx for idx, t in enumerate(merged)}
        for t in merged:
            clean: list[str] = []
            for d in t.depends_on:
                nd = remap.get(d, d)
                if nd in pos and pos[nd] < pos[t.id] and nd not in clean:
                    clean.append(nd)
            t.depends_on = clean

        if len(merged) < len(tasks):
            self.emit("note", f"Refined backlog: {len(tasks)} model tasks → {len(merged)} "
                              f"(one task per file).")
        return merged

    # --- architecture-first planning -----------------------------------

    def _blueprint(self, goal: str) -> dict | None:
        """Design an architecture before writing any file: a stack, a component breakdown
        across real layers, and the cross-cutting concerns a production app needs. Returns
        the parsed blueprint, or None if the model didn't produce a usable one (the caller
        then falls back to the flat single-file decomposition)."""
        tree = project_tree(self.s.project_root)
        instruction = (
            f"Project goal:\n{goal}\n\n"
            f"Existing project files:\n{tree}\n\n"
            "Design the architecture. Output JSON with EXACTLY these top-level keys: "
            '"stack", "summary", "components". "components" is a flat list — one object per '
            "component, each with id, name, layer, concern, files (a flat list of relative "
            "paths), and depends_on. Do NOT nest a directory tree; list files as flat paths.\n\n"
            "Here is the exact shape to follow, for a DIFFERENT project (a URL shortener) — "
            "copy the structure, not the content:\n"
            + _BLUEPRINT_EXAMPLE + "\n\n"
            "Now design the architecture for the goal above, in that exact JSON shape. Cover "
            "session management, error handling, user management, configuration, logging, "
            "tests, and CI/CD as components whenever the goal implies an app that needs them. "
            "Put all backend source under a SINGLE top-level package directory (e.g. `app/`) so "
            "imports stay consistent — like the example. If the goal implies a user interface, add "
            "a frontend component: a React + Vite + TypeScript app under a top-level `frontend/` "
            "directory (frontend/package.json, frontend/index.html, frontend/vite.config.ts, "
            "frontend/tsconfig.json, frontend/src/main.tsx, frontend/src/App.tsx, frontend/src/api.ts). "
            "Order components so dependencies come first. Output ONLY the JSON."
        )
        # Structured output — pin temperature low so a weak model follows the shape.
        resp = complete(self.model, [
            {"role": "system", "content": BLUEPRINT_SYSTEM},
            {"role": "user", "content": instruction},
        ], temperature=0.0)
        data = self._normalize_blueprint(extract_json(resp.choices[0].message.content or ""))
        if not data:
            return None
        # Keep only well-formed components that actually name files.
        clean = [c for c in data["components"] if isinstance(c, dict) and (c.get("files") or [])]
        if not clean:
            return None
        data["components"] = self._order_components(clean)
        return data

    def _normalize_blueprint(self, data: Any) -> dict | None:
        """Weak models drift from the schema — they nest a directory tree under
        'architecture.directory_structure' instead of emitting a flat 'components' list. Accept
        our shape as-is; otherwise salvage components from a directory-tree shape so a good plan
        isn't thrown away over formatting. Returns a normalized {stack, summary, components}."""
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("components"), list) and data["components"]:
            data.setdefault("stack", "")
            data.setdefault("summary", "")
            return data

        arch = data.get("architecture") if isinstance(data.get("architecture"), dict) else data

        # Stack — from a technology_stack map or a plain string, wherever it sits.
        stack = ""
        ts = arch.get("technology_stack") or arch.get("tech_stack") or arch.get("stack") \
            or data.get("stack")
        if isinstance(ts, dict):
            stack = ", ".join(str(v) for v in ts.values() if isinstance(v, str))
        elif isinstance(ts, str):
            stack = ts

        # Find a directory-tree-shaped map and flatten it into one component per top dir.
        tree = None
        for key in ("directory_structure", "structure", "layout", "tree", "files"):
            v = arch.get(key) if isinstance(arch, dict) else None
            if isinstance(v, dict):
                tree = v
                break
        comps: list[dict] = []
        if isinstance(tree, dict):
            cid = 0
            for dirname, val in tree.items():
                if not isinstance(val, dict):
                    continue                        # a string alias/description for a dir
                files = self._flatten_files(val, f"{dirname.rstrip('/')}/")
                if files:
                    cid += 1
                    comps.append({"id": f"c{cid}", "name": dirname.rstrip("/").split("/")[-1] or dirname,
                                  "layer": _infer_layer(dirname), "concern": "",
                                  "files": files, "depends_on": []})
        if not comps:
            return None
        summary = data.get("description") or (arch.get("description") if isinstance(arch, dict) else "") or ""
        return {"stack": stack, "summary": str(summary), "components": comps}

    def _flatten_files(self, node: dict, prefix: str) -> list[str]:
        """Recursively pull file paths out of a nested directory-tree map (keys with an
        extension are files; dict values are subdirectories)."""
        out: list[str] = []
        for k, v in node.items():
            if isinstance(v, dict):
                out.extend(self._flatten_files(v, f"{prefix}{k.rstrip('/')}/"))
            elif "." in k and not k.startswith("."):
                out.append(f"{prefix}{k}")
        return out[: self.MAX_FILES_PER_COMPONENT]

    def _order_components(self, comps: list[dict]) -> list[dict]:
        """Topologically sort components so dependencies build first; within a tie, sort by
        layer order, then by given order. Cycles/unknown edges are ignored (never fatal)."""
        by_id: dict[str, dict] = {}
        order_in: list[str] = []
        for i, c in enumerate(comps):
            cid = str(c.get("id") or f"c{i+1}")
            c["id"] = cid
            by_id.setdefault(cid, c)
            order_in.append(cid)

        indeg = {cid: 0 for cid in order_in}
        edges: dict[str, list[str]] = {cid: [] for cid in order_in}
        for cid in order_in:
            for d in (by_id[cid].get("depends_on") or []):
                d = str(d)
                if d in by_id and d != cid:
                    edges[d].append(cid)
                    indeg[cid] += 1

        def rank(cid: str) -> tuple[int, int]:
            layer = str(by_id[cid].get("layer") or "").lower()
            return (_LAYER_ORDER.get(layer, 3), order_in.index(cid))

        ready = sorted([cid for cid in order_in if indeg[cid] == 0], key=rank)
        out: list[str] = []
        seen: set[str] = set()
        while ready:
            cid = ready.pop(0)
            if cid in seen:
                continue
            seen.add(cid)
            out.append(cid)
            newly = []
            for nxt in edges[cid]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    newly.append(nxt)
            if newly:
                ready = sorted(ready + newly, key=rank)
        for cid in order_in:            # anything left (a cycle) — append in input order
            if cid not in seen:
                out.append(cid)
        return [by_id[cid] for cid in out]

    def _expand(self, goal: str, blueprint: dict) -> list[Task]:
        """Turn an ordered component breakdown into dependency-ordered, one-file build tasks.
        A file depends on the last file of each component it depends on (so those files exist
        when it builds) and on the previous file in its own component (so the component grows
        coherently). Every task carries the architectural context the Task Conductor needs."""
        stack = str(blueprint.get("stack") or "")
        comps = blueprint.get("components") or []
        files_of: dict[str, list[str]] = {
            str(c.get("id")): [str(f) for f in (c.get("files") or []) if f] for c in comps
        }
        tasks: list[Task] = []
        comp_last: dict[str, str] = {}
        n = 0
        for c in comps:
            cid = str(c.get("id"))
            name = str(c.get("name") or cid)
            layer = str(c.get("layer") or "")
            concern = str(c.get("concern") or "")
            files = files_of.get(cid, [])[: self.MAX_FILES_PER_COMPONENT]
            dep_ids = [str(d) for d in (c.get("depends_on") or []) if str(d) in comp_last]
            dep_last = [comp_last[d] for d in dep_ids]
            dep_files = sorted({f for d in dep_ids for f in files_of.get(d, [])})
            prev = None
            for f in files:
                n += 1
                if n > self.MAX_TASKS:
                    break
                deps = list(dep_last)
                if prev:
                    deps.append(prev)
                goal_text = (
                    f"Build the file `{f}` — part of the '{name}' component ({layer} layer"
                    + (f", responsible for {concern}" if concern else "") + ").\n"
                    f"Project goal: {goal}\n"
                    f"Stack: {stack}\n"
                    f"Files in this component: {', '.join(files)}.\n"
                    + (f"Already-built files it can rely on: {', '.join(dep_files)}.\n" if dep_files else "")
                    + f"Implement only what belongs in `{f}`, consistent with the rest of the "
                    "component and the project stack. Follow conventional, production-quality "
                    f"patterns for the {layer or 'application'} layer."
                )
                tasks.append(Task(id=f"t{n}", title=f"{name}: {f}", goal=goal_text,
                                  file=f, component=name, depends_on=deps))
                prev = f"t{n}"
            if prev:
                comp_last[cid] = prev
            if n > self.MAX_TASKS:
                break
        return tasks

    # --- run -----------------------------------------------------------

    def _run_task(self, goal_text: str) -> tuple[bool, str]:
        """Delegate one task to a fresh Task Conductor. Returns (ok, summary)."""
        try:
            # Build sub-tasks don't each learn a skill — a whole project would spawn dozens.
            result = Conductor(self.s, emit=self.emit, approve=self.approve,
                               learn_skills=False).run(goal_text)
            return result.ok, result.answer
        except Exception as e:
            return False, f"task crashed: {e}"

    def _find_entry(self, state: ProjectState) -> str | None:
        """A runnable entrypoint: a conventionally-named script (anywhere, shallowest first) or
        the first file with an explicit __main__ guard."""
        root = self.s.project_root
        cands = [t.file.replace("\\", "/") for t in state.tasks
                 if t.file and t.file.endswith(".py") and (root / t.file).is_file()]
        named = [f for f in cands if f.rsplit("/", 1)[-1] in ("main.py", "app.py", "run.py", "cli.py")]
        if named:
            return sorted(named, key=lambda f: (f.count("/"), f))[0]
        for f in cands:
            try:
                if "__main__" in (root / f).read_text(encoding="utf-8", errors="ignore"):
                    return f
            except OSError:
                continue
        return None

    def _integration_verify(self, state: ProjectState) -> tuple[bool, str]:
        """Verify the assembled project runs as a whole. A Python backend (requirements.txt) is
        installed into an isolated venv and RUN for real (its test suite, else its entrypoint); a
        Node frontend (package.json) has its deps installed and is BUILT. Both must pass. With
        nothing installable, fall back to a lightweight import/run check in Newton's interpreter."""
        root = self.s.project_root
        run_enabled = os.getenv("NEWTON_RUN_BUILD", "1") != "0"
        results: list[tuple[str, tuple[bool, str]]] = []

        if run_enabled and (root / "requirements.txt").is_file():
            backend = self._real_run(state)
            if backend is not None:
                results.append(("backend", backend))

        fe_dir = self._find_frontend_dir()
        if run_enabled and fe_dir is not None:
            frontend = self._frontend_build(fe_dir)
            if frontend is not None:
                results.append(("frontend", frontend))

        if not results:
            return self._light_verify(state)
        ok = all(r[0] for _, r in results)
        msg = "; ".join(f"{name}: {r[1]}" for name, r in results)
        return ok, msg

    def _find_frontend_dir(self) -> Path | None:
        """The Node project's directory — a `frontend/package.json`, or one at the project root."""
        root = self.s.project_root
        for cand in (root / "frontend", root):
            if (cand / "package.json").is_file():
                return cand
        return None

    def _frontend_build(self, fe_dir) -> tuple[bool, str] | None:
        """Install the frontend's npm dependencies and build it (tsc + vite). Returns a genuine
        (ok, msg) — deps are present, so a failure is a real build/type error — or None if npm
        isn't available or the install couldn't complete (→ skipped, never fails the build)."""
        belt = ToolBelt(fe_dir)
        if not belt.run(cmd="npm --version").startswith("[exit 0]"):
            self.emit("note", "Integration: npm not available — frontend build skipped.")
            return None
        self.emit("note", "Installing the frontend's dependencies (npm)…")
        if not belt.run(cmd="npm install --no-audit --no-fund").startswith("[exit 0]"):
            self.emit("note", "Integration: frontend dependency install didn't complete — skipped.")
            return None
        self.emit("note", "Building the frontend…")
        out = belt.run(cmd="npm run build")
        body = out.split("]", 1)[-1].strip()
        if out.startswith("[exit 0]"):
            self.emit("verify", "Integration PASS — frontend deps installed, build succeeded.")
            return True, "frontend builds"
        self.emit("verify", f"Integration FAIL — frontend build failed: {body[:200]}")
        return False, f"the frontend build failed: {body[:120]}"

    # --- integration self-correction -----------------------------------

    def _culprit_files(self, error: str, state: ProjectState) -> list[str]:
        """The project file(s) an integration error points at — the file to fix. Matches path-like
        tokens in the error (a pytest traceback frame, a `App.tsx(12,5)` tsc error) against the
        real task files. Prefers non-test files (the bug is usually in the code, not its test)."""
        known = {t.file.replace("\\", "/") for t in state.tasks if t.file}
        by_base: dict[str, list[str]] = {}
        for k in known:
            by_base.setdefault(k.rsplit("/", 1)[-1], []).append(k)
        found: list[str] = []
        for m in re.finditer(r"([\w./\\-]+\.(?:tsx?|jsx?|py))", error):
            tok = m.group(1).replace("\\", "/")
            base = tok.rsplit("/", 1)[-1]
            for cand in ([tok] if tok in known else []) or by_base.get(base, []):
                if cand not in found:
                    found.append(cand)
        found.sort(key=lambda k: ("test" in k.rsplit("/", 1)[-1], k))
        return found[:2]

    def _integration_fix(self, state: ProjectState, error: str) -> bool:
        """One repair pass on an integration failure: send the error and the culprit file to a
        Task Conductor to fix. Returns True if a fix was attempted (so the caller re-verifies)."""
        culprits = self._culprit_files(error, state)
        if not culprits:
            self.emit("note", "Integration failed, but the error names no project file to fix.")
            return False
        self.emit("note", f"Integration failed — fixing {', '.join(culprits)} from the error.")
        attempted = False
        for f in culprits:
            goal = (f"The assembled project fails its integration check. Fix the file `{f}` so the "
                    f"project builds and its tests pass.\n\nError:\n{error}\n\n(work in file: {f})")
            ok, _ = self._run_task(goal)
            attempted = attempted or ok
        return attempted

    def _ensure_project_venv(self, root) -> Path | None:
        """Create (or reuse) an isolated .venv inside the project. Returns its python, or None if
        the environment can't be built (offline, no venv module) — the caller then falls back."""
        venv = root / ".venv"
        sub = "Scripts" if os.name == "nt" else "bin"
        py = venv / sub / ("python.exe" if os.name == "nt" else "python")
        if py.is_file():
            return py
        self.emit("note", "Setting up an isolated environment for the project…")
        out = ToolBelt(root).run(cmd=f'"{sys.executable}" -m venv .venv')
        return py if out.startswith("[exit 0]") and py.is_file() else None

    def _real_run(self, state: ProjectState) -> tuple[bool, str] | None:
        """Install the project's dependencies into its own venv and run it for real. Returns a
        genuine (ok, msg) — deps are present, so a failure is a real bug — or None if the
        environment couldn't be prepared (→ fall back to the light check, never fail on setup)."""
        root = self.s.project_root
        py = self._ensure_project_venv(root)
        if py is None:
            self.emit("note", "Integration: couldn't create a project environment — running a light check instead.")
            return None
        belt = ToolBelt(root)
        self.emit("note", "Installing the project's dependencies…")
        inst = belt.run(cmd=f'"{py}" -m pip install --disable-pip-version-check -q -r requirements.txt')
        if not inst.startswith("[exit 0]"):
            self.emit("note", "Integration: dependency install didn't complete — running a light check instead.")
            return None

        # A test suite is the truest integration check (it exercises the app); else run the entrypoint.
        has_tests = (root / "tests").is_dir() or any(
            t.file.rsplit("/", 1)[-1].startswith("test_") for t in state.tasks if t.file)
        if has_tests:
            self.emit("note", "Running the project's test suite…")
            out = belt.run(cmd=f'"{py}" -m pytest -q')
            body = out.split("]", 1)[-1].strip()
            if out.startswith("[exit 0]"):
                self.emit("verify", "Integration PASS — dependencies installed, test suite green.")
                return True, "dependencies installed; test suite passes"
            # pytest exits 5 when it collected no tests — not a failure of the build.
            if "[exit 5]" in out or "no tests ran" in body.lower():
                self.emit("note", "Integration: deps installed, app imports, but no tests collected.")
                return True, "dependencies installed; app builds (no tests collected)"
            self.emit("verify", f"Integration FAIL — tests failed: {body[:200]}")
            return False, f"the project's tests failed: {body[:120]}"

        entry = self._find_entry(state)
        if entry is None:
            self.emit("note", "Integration: deps installed but no test suite or entrypoint to run.")
            return True, "dependencies installed; nothing runnable to exercise"
        out = belt.run(cmd=f'"{py}" "{entry}"')
        body = out.split("]", 1)[-1].strip()
        if out.startswith("[exit 0]"):
            self.emit("verify", f"Integration PASS — dependencies installed, {entry} runs.")
            return True, f"dependencies installed; {entry} runs cleanly"
        self.emit("verify", f"Integration FAIL — running {entry}: {body[:200]}")
        return False, f"the assembled project crashed running {entry}: {body[:120]}"

    def _light_verify(self, state: ProjectState) -> tuple[bool, str]:
        """Run the entrypoint in Newton's own interpreter — no dependency install. A missing
        third-party import is an environment gap (skip), a missing local module is a real bug."""
        entry = self._find_entry(state)
        if entry is None:
            self.emit("note", "Integration: no runnable entrypoint — skipped.")
            return True, "no entrypoint to run"
        expect = derive_expect(state.goal)
        out = ToolBelt(self.s.project_root).run(cmd=f'"{sys.executable}" "{entry}"')
        body = out.split("]", 1)[-1].strip()
        if not out.startswith("[exit 0]"):
            missing = re.search(r"No module named ['\"]([\w.]+)['\"]", body)
            if missing and missing.group(1).split(".")[0] in _EXTERNAL_DEPS:
                self.emit("note", f"Integration: {entry} needs its dependencies installed "
                                  f"(`pip install -r requirements.txt`) — runtime check skipped.")
                return True, "runtime check skipped (dependencies not installed)"
            self.emit("verify", f"Integration FAIL — running {entry}: {body[:200]}")
            return False, f"the assembled project crashed running {entry}: {body[:120]}"
        if expect and expect not in body:
            self.emit("verify", f"Integration FAIL — {entry} ran but expected '{expect}', got: {body[:120]}")
            return False, f"{entry} ran but did not produce '{expect}'"
        self.emit("verify", f"Integration PASS — {entry} runs" + (f", found '{expect}'" if expect else ""))
        return True, f"{entry} runs cleanly"

    # --- templates-first scaffolding -----------------------------------

    def _scaffold_context(self, tasks: list[Task]) -> tuple[str, str]:
        """Infer the root package and the entrypoint module from the planned files so template
        imports resolve. `app/config.py` → root package 'app'; `app/main.py` → 'app.main'."""
        py = [t.file.replace("\\", "/") for t in tasks if t.file.endswith(".py")]
        app_files = [f for f in py if not f.rsplit("/", 1)[-1].startswith("test_") and "tests/" not in f]
        top_segs = [f.split("/")[0] for f in app_files if "/" in f]
        root_pkg = Counter(top_segs).most_common(1)[0][0] if top_segs else ""
        mains = [f for f in py if f.rsplit("/", 1)[-1] in ("main.py", "app.py")]
        if mains:
            main_mod = mains[0][:-3].replace("/", ".")
        else:
            main_mod = f"{root_pkg}.main" if root_pkg else "main"
        return root_pkg, main_mod

    def _apply_scaffolds(self, state: ProjectState, stack: str, goal: str) -> None:
        """Write template-backed files before the build loop. Complete boilerplate is written and
        its task marked done (the model is skipped); skeletons are written as a starting point and
        their task goal notes that the file already exists to be completed, not created."""
        if not self.scaffolds.stack_matches(stack):
            return
        root = self.s.project_root
        root_pkg, main_mod = self._scaffold_context(state.tasks)
        app_name = (goal.strip().split(".")[0][:48] or "App")
        laid_complete = laid_skeleton = 0

        for task in state.tasks:
            if not task.file:
                continue
            sc = self.scaffolds.match(task.file, stack=stack, root_pkg=root_pkg,
                                      main_mod=main_mod, app_name=app_name)
            if sc is None:
                continue
            path = root / task.file
            try:
                if path.is_file() and path.read_text(encoding="utf-8", errors="ignore").strip():
                    continue                                    # never clobber real content
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(sc.text, encoding="utf-8")
            except OSError:
                continue
            if sc.complete:
                task.status = DONE
                task.summary = "scaffolded from template"
                laid_complete += 1
                self.emit("task", {"id": task.id, "title": task.title, "status": "done",
                                   "file": task.file, "summary": "scaffolded from template"})
            else:
                task.goal += (f"\n\nA scaffold already exists at `{task.file}` — COMPLETE it, do not "
                              f"start from scratch. {sc.note}")
                laid_skeleton += 1

        # A dependency manifest is boilerplate too — add one if the plan didn't include it.
        if not any(t.file.rsplit("/", 1)[-1] in ("requirements.txt", "pyproject.toml") for t in state.tasks):
            sc = self.scaffolds.match("requirements.txt", stack=stack)
            if sc and not (root / "requirements.txt").exists():
                try:
                    (root / "requirements.txt").write_text(sc.text, encoding="utf-8")
                    laid_complete += 1
                except OSError:
                    pass

        if laid_complete or laid_skeleton:
            self.emit("note", f"Templates-first: laid down {laid_complete} ready file(s) and "
                              f"{laid_skeleton} skeleton(s) — the model only fills domain logic.")

    def run(self, goal: str) -> ProjectResult:
        # Architecture-first: design the whole system, then expand it into file tasks. If the
        # architect model doesn't produce a usable blueprint, fall back to the flat plan.
        self.emit("stage", "Blueprint")
        blueprint = self._blueprint(goal)
        stack = ""
        if blueprint:
            stack = str(blueprint.get("stack", ""))
            tasks = self._expand(goal, blueprint)
            comps = blueprint.get("components") or []
            self.emit("blueprint", {
                "stack": blueprint.get("stack", ""),
                "summary": blueprint.get("summary", ""),
                "components": [{"name": c.get("name", ""), "layer": c.get("layer", ""),
                                "concern": c.get("concern", ""),
                                "files": [str(f) for f in (c.get("files") or [])]}
                               for c in comps],
            })
            self.emit("note", f"Designed {len(comps)} component(s) → {len(tasks)} build task(s).")
        else:
            self.emit("stage", "Decompose")
            tasks = self._refine_backlog(self._decompose(goal))

        if not tasks:
            self.emit("halt", "could not turn the goal into a buildable plan")
            return ProjectResult(False, "Planning failed.")
        state = ProjectState(goal=goal, tasks=tasks)
        self.emit("backlog", [{"id": t.id, "title": t.title, "file": t.file,
                               "component": t.component, "depends_on": t.depends_on}
                              for t in tasks])

        if not self.approve("backlog", {"tasks": [t.title for t in tasks]}):
            return ProjectResult(False, "Backlog rejected by user.", state)

        # Templates-first: lay proven boilerplate down deterministically before the model runs,
        # so cross-cutting concerns (config, db, sessions, errors, CI) are correct by construction
        # and the model only fills domain logic.
        self._apply_scaffolds(state, stack, goal)

        # Execute the backlog in dependency order, one Task Conductor per task.
        retried: set[str] = set()
        while state.remaining() > 0:
            task = state.ready()
            if task is None:
                break  # only blocked tasks remain
            task.status = RUNNING
            self.emit("task", {"id": task.id, "title": task.title, "status": "start",
                               "file": task.file})

            goal_text = task.goal + (f" (work in file: {task.file})" if task.file else "")
            ok, summary = self._run_task(goal_text)

            # Replanning-lite: a failed task gets one retry with the failure as context
            # before it's allowed to block its dependents.
            if not ok and task.id not in retried:
                retried.add(task.id)
                self.emit("note", f"Task {task.id} failed — retrying once with the failure as context.")
                ok, summary = self._run_task(
                    f"{goal_text}\n\nA previous attempt failed: {summary}. Fix that specifically."
                )

            task.status = DONE if ok else FAILED
            task.summary = summary.splitlines()[0][:200] if summary else ""
            state.log.append(f"{task.id} {'done' if ok else 'FAILED'}: {task.title}")
            self.emit("task", {"id": task.id, "title": task.title,
                               "status": "done" if ok else "failed", "summary": task.summary})

        # Anything still pending had a failed/blocked dependency.
        for t in state.tasks:
            if t.status in (PENDING, RUNNING):
                t.status = BLOCKED

        c = state.counts()
        # Integration verification — only meaningful once every task built cleanly.
        integ_ok, integ_msg = True, ""
        if c[FAILED] == 0 and c[BLOCKED] == 0:
            self.emit("stage", "Integrate")
            integ_ok, integ_msg = self._integration_verify(state)
            # Close the loop: if the assembled project fails to run/build, feed the error back to
            # a Task Conductor to fix the culprit file(s), then verify once more.
            if not integ_ok and self._integration_fix(state, integ_msg):
                self.emit("stage", "Integrate")
                integ_ok, integ_msg = self._integration_verify(state)

        state.save(self.s.project_root / ".newton" / "project.json")
        ok = c[FAILED] == 0 and c[BLOCKED] == 0 and integ_ok
        answer = (f"Project '{goal[:60]}' — {c[DONE]} done, {c[FAILED]} failed, "
                  f"{c[BLOCKED]} blocked across {len(state.tasks)} tasks."
                  + (f" Integration: {integ_msg}." if integ_msg else ""))
        self.emit("project", {"ok": ok, "answer": answer, "counts": c})
        return ProjectResult(ok, answer, state, c[DONE], c[FAILED])
