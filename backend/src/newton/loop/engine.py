"""LoopEngine — the durable Plan → Execute → Verify → Replan loop.

Decompose the goal into atomic steps, then for each ready step: shape exactly the right context,
run the atomic executor, verify, and (on failure) retry with the error fed back — up to a cap. State
is checkpointed after EVERY step, so a run that takes hours can be killed and resumed from where it
stopped, never repeating finished work. This is the engine that turns a small model's reliable
atomic steps into a finished compound result — the thing a naive prompt and a raw autonomous loop
both fail at.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..conductor.verify import python_parses
from ..config import Settings
from ..context import project_tree
from ..index.graph import _module_of
from ..tools import ToolBelt
from .decide import error_signature
from .decision import CHOICE, NOUL, SCORE, Decision
from .decompose import decompose
from .effort import Effort
from .effort import effort as _resolve_effort
from .evidence import Evidence, collect_evidence
from .execute import NativeStepExecutor, StepExecutor, StepResult
from .judge import LayaJudge, append_advisory
from .state import BLOCKED, DONE, FAILED, PENDING, RUN, RUNNING, WRITE, LoopState, Step

# The historical caps, kept as the definition of the `normal` effort level (see loop/effort.py).
# They are no longer read directly — the LoopEngine takes its budgets from `self.effort`, so the SAME
# model can be pushed harder (thorough/max) or lighter (quick) without editing the control flow.
MAX_ATTEMPTS = 3        # per-step best-of-N by temperature
REPAIR_CYCLES = 2       # failing-check → code-fix cycles (bounded so an unsolvable task can't loop forever)
REPLAN_CYCLES = 2       # whole-plan re-decompositions (the "Replan" of Plan→Execute→Verify→Replan)

# Third-party packages a generated file may import that aren't installed in Newton's venv — a
# missing one is an environment gap (deferred to a real run), NOT a code bug to retry on. A missing
# LOCAL/hallucinated module (e.g. `project.pricing` when the real one is `store.pricing`) is a bug.
_EXTERNAL_DEPS = {
    "fastapi", "starlette", "uvicorn", "sqlalchemy", "pydantic", "httpx", "pytest",
    "jinja2", "aiofiles", "passlib", "jose", "bcrypt", "alembic", "flask", "django", "numpy",
    "pandas", "requests", "aiohttp", "redis", "click", "rich", "typer", "yaml", "dotenv",
}

# Generated probe for the whole-app integration check on an ASGI (FastAPI/Starlette) app. A plain
# `import entry` proves the app LOADS; it says nothing about whether a request works. This drives the
# assembled app through an in-process test client and GETs every no-argument route, failing on any
# server error (5xx or a handler that raises) — the request-time class of bug that broke the first
# real full-stack run (a frontend/backend contract mismatch that every per-file verify passed). It
# self-reports `culprit: <file>` from the traceback so the repair loop can target the handler, and
# skips quietly (exit 0) when there's no ASGI app or no test client, so it never cries wolf.
_INTEGRATION_PROBE = r'''"""Newton whole-app integration probe (generated — safe to delete)."""
import importlib, os, re, sys, traceback

ENTRY = "__ENTRY__"
ROOT = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.basename(os.path.abspath(__file__))


def _culprit(text):
    """The deepest project .py file named in a traceback — the handler to fix (not stdlib/deps)."""
    for fn in reversed(re.findall(r'File "([^"]+\.py)"', text)):
        ab = os.path.abspath(fn)
        norm = ab.replace("\\", "/")
        if ab.startswith(ROOT) and "site-packages" not in norm and os.path.basename(ab) != PROBE:
            return os.path.relpath(ab, ROOT).replace("\\", "/")
    return "?"


try:
    mod = importlib.import_module(ENTRY)
except Exception:
    tb = traceback.format_exc()
    print("FAILED import %s -- culprit: %s" % (ENTRY, _culprit(tb)))
    print(tb)
    sys.exit(1)

try:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
except Exception as exc:
    print("integration: no test client available (%s) -- load-only, ok" % exc)
    sys.exit(0)

app = None
for _name in dir(mod):
    _obj = getattr(mod, _name, None)
    if isinstance(_obj, Starlette):
        app = _obj
        break
if app is None:
    print("integration: no ASGI app object on %s -- load-only, ok" % ENTRY)
    sys.exit(0)

skip = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
paths = []
for route in getattr(app, "routes", []):
    methods = getattr(route, "methods", None) or set()
    path = getattr(route, "path", "") or ""
    if "GET" in methods and "{" not in path and path not in skip:
        paths.append(path)

fails = []
try:
    with TestClient(app) as client:
        for p in paths:
            try:
                resp = client.get(p)
                if resp.status_code >= 500:
                    fails.append("FAILED GET %s -> HTTP %d\n%s" % (p, resp.status_code, (resp.text or "")[:600]))
            except Exception:
                tb = traceback.format_exc()
                fails.append("FAILED GET %s raised -- culprit: %s\n%s" % (p, _culprit(tb), tb))
except Exception:
    tb = traceback.format_exc()
    print("FAILED app startup -- culprit: %s" % _culprit(tb))
    print(tb)
    sys.exit(1)

if fails:
    print("INTEGRATION: %d route(s) returned a server error:" % len(fails))
    for f in fails:
        print(f)
    sys.exit(1)
print("integration ok: probed %d GET route(s), no server errors" % len(paths))
'''


@dataclass
class LoopResult:
    ok: bool
    answer: str
    state: LoopState | None = None
    evidence: Evidence | None = None       # what the run proved — checks that ran + file fingerprints


class ContextShaper:
    """The context-engineering core: assemble exactly what THIS step needs into a small budget.

    Parts are tagged PRIORITY (the goal, the target file's current content, the prior error — kept
    verbatim) or REFERENCE (what upstream steps built, retrieved code — compactible). When the
    assembled window overflows the budget, reference context is SUMMARISED (compaction) rather than
    blindly clipped, so a long, many-step loop keeps the essential facts instead of losing the tail.
    """

    def __init__(self, root: Path, *, budget_chars: int = 6000, per_file: int = 1800,
                 summarize: Callable[[str, int], str] | None = None) -> None:
        self.root = Path(root)
        self.budget_chars = budget_chars
        self.per_file = per_file
        self.summarize = summarize          # (text, max_chars) -> compacted text; None = clip

    def _read(self, rel: str) -> str:
        p = self.root / rel
        try:
            return p.read_text(encoding="utf-8", errors="ignore")[: self.per_file] if p.is_file() else ""
        except OSError:
            return ""

    def parts(self, state: LoopState, step: Step, *, error: str | None = None) -> list[tuple[bool, str]]:
        """Return (is_priority, text) parts. Priority parts are never compacted."""
        out: list[tuple[bool, str]] = [(True, f"Overall goal: {state.goal}")]
        for dep in state.upstream_outputs(step):
            if dep.file:
                content = self._read(dep.file)
                if content:
                    out.append((False, f"Already built `{dep.file}`:\n```\n{content}\n```"))
        if step.file:
            current = self._read(step.file)
            if current:
                out.append((True, f"Current `{step.file}` (edit it):\n```\n{current}\n```"))
        if error:
            out.append((True, f"Your previous attempt failed: {error}\nFix that specifically."))
        return out

    def shape(self, state: LoopState, step: Step, *, error: str | None = None) -> str:
        tagged = self.parts(state, step, error=error)
        head = "\n\n".join(t for p, t in tagged if p)
        ref = "\n\n".join(t for p, t in tagged if not p)
        if not ref:
            return head[: self.budget_chars]
        if len(head) + len(ref) + 2 <= self.budget_chars:
            return f"{head}\n\n{ref}"
        room = max(self.budget_chars - len(head) - 4, 600)      # leave the priority head intact
        return f"{head}\n\n{self._compact(ref, room)}"[: self.budget_chars]

    def _compact(self, text: str, room: int) -> str:
        """Fit reference context into `room` chars — SUMMARISE it if a summarizer is wired, else
        fall back to a clip. Never fails: a bad or missing summary just clips."""
        if len(text) <= room:
            return text
        if self.summarize is not None:
            try:
                s = self.summarize(text, room)
                if s and s.strip():
                    return s.strip()[: room]
            except Exception:
                pass
        return text[: room]


class RetrievalShaper(ContextShaper):
    """The context-engine for LARGE repos: beyond upstream deps + target, RETRIEVE the most relevant
    existing files for the step (RepoIndex — BM25 + semantic + PageRank centrality), so the small
    window holds the right context even when the repo far exceeds it. This is what lets the loop
    find a symbol defined in a file the planner never named."""

    def __init__(self, root: Path, *, budget_chars: int = 8000, per_file: int = 1400, k: int = 4,
                 summarize: Callable[[str, int], str] | None = None) -> None:
        super().__init__(root, budget_chars=budget_chars, per_file=per_file, summarize=summarize)
        self.k = k
        self._index = None

    def _idx(self):
        if self._index is None:
            from ..index import RepoIndex
            from ..index.embeddings import Embedder
            self._index = RepoIndex(self.root, embedder=Embedder()).build()
        return self._index

    def _importable_symbols(self, rel: str) -> list[str]:
        """Public top-level names a file exports — functions, classes, AND module constants — so we
        can hand the model exact, copy-paste import lines instead of hoping it infers them."""
        import ast
        try:
            tree = ast.parse((self.root / rel).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            return []
        names: list[str] = []
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not n.name.startswith("_"):
                    names.append(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and not t.id.startswith("_"):
                        names.append(t.id)
        return list(dict.fromkeys(names))

    def _import_menu(self, paths: list[str]) -> str:
        lines = []
        for rel in paths:
            if rel.endswith(".py"):
                syms = self._importable_symbols(rel)
                if syms:
                    lines.append(f"  from {_module_of(rel)} import {', '.join(syms)}")
        if not lines:
            return ""
        return ("Available imports in THIS project — use these EXACT module paths and names, and do "
                "NOT invent module names or symbols:\n" + "\n".join(lines))

    def parts(self, state: LoopState, step: Step, *, error: str | None = None) -> list[tuple[bool, str]]:
        base = super().parts(state, step, error=error)          # tagged (is_priority, text)
        seen = {step.file} | {d.file for d in state.upstream_outputs(step) if d.file}
        retrieved: list[tuple[bool, str]] = []
        hit_paths: list[str] = []
        try:
            idx = self._idx()
            focus = [step.file] if step.file else None
            for chunk, _ in idx.search(f"{state.goal} {step.goal} {step.file}", k=self.k, focus=focus):
                if chunk.path in seen:
                    continue
                seen.add(chunk.path)
                hit_paths.append(chunk.path)
                # Hand the model the IMPORT-READY module name AND everything the module defines — so a
                # constant retrieved from one chunk doesn't hide the function defined in another chunk
                # of the same file (small models otherwise hallucinate a module for the missing symbol).
                if chunk.path.endswith(".py"):
                    fi = idx.graph.files.get(chunk.path)
                    defs = ", ".join(fi.defs) if fi and fi.defs else ""
                    head = f"Relevant existing code — import it as module `{_module_of(chunk.path)}` (file {chunk.path}"
                    head += f"; this module also defines: {defs})" if defs else ")"
                    head += ":"
                else:
                    head = f"Relevant `{chunk.ref}` (existing code):"
                # Retrieved code is REFERENCE context (compactible when the window overflows).
                retrieved.append((False, f"{head}\n```\n{chunk.text[: self.per_file]}\n```"))
        except Exception:
            pass
        # An explicit import menu (PRIORITY — never compacted): exact import lines from the retrieved
        # files + the files this step depends on. Small models follow this menu instead of inventing
        # module names from the goal's prose (`from pricing import DISCOUNT_RATE`).
        menu_paths = hit_paths + [d.file for d in state.upstream_outputs(step) if d.file]
        menu = self._import_menu(menu_paths)
        head_extra = [(True, menu)] if menu else []
        return base[:1] + head_extra + retrieved + base[1:]


class LoopEngine:
    def __init__(self, settings: Settings, *, executor: StepExecutor | None = None,
                 shaper: ContextShaper | None = None,
                 emit: Callable[[str, Any], None] | None = None,
                 effort: Effort | None = None,
                 judge: Any = None,
                 checkpoint: str = "loop.json") -> None:
        self.s = settings
        self.root = Path(settings.project_root)
        self.emit = emit or (lambda *a: None)
        self._judge = judge                            # optional advisory judge (Laya); injected in tests
        # How much free local compute this run may spend to reach verified quality. `normal` == the
        # historical constants, so the default is a no-op; higher levels push the SAME model harder
        # (more attempts / wider retrieval / more replans), the lever that only free-token, unlimited-
        # time local execution can pull. See loop/effort.py.
        self.effort = effort or _resolve_effort()
        self.executor = executor or NativeStepExecutor(self.root, settings.agent_model)
        # Retrieval is the product-correct default: the context-engine always pulls the relevant
        # existing files into each step's window (essential on large repos; harmless on small ones),
        # with compaction wired so a long, many-step run stays inside the window. Its width scales
        # with effort — more compute buys a wider window and more retrieved files.
        self.shaper = shaper or RetrievalShaper(
            self.root, budget_chars=self.effort.budget_chars, k=self.effort.retrieval_k,
            summarize=self._make_summarizer())
        self.checkpoint_path = self.root / ".newton" / checkpoint
        self._pending_error: dict[str, str] = {}      # step id → error to inject into its next context
        self._extra_temp: dict[str, float] = {}       # step id → added temperature (rises each repair)
        self._repair_history: list[tuple[str, str]] = []  # (culprit id, error signature) already tried —
        #                                                   feeds repair-progress routing (loop/decide.py)
        self._decisions: list[Decision] = []          # the run's bounded forks, logged into evidence
        # Parallel step execution: independent ready steps (distinct files, no dep path between them)
        # run concurrently. Default 1 (sequential, unchanged). It's real work overlap — the model
        # calls are blocking HTTP so threads release the GIL — but the actual speedup depends on the
        # backend: a single Ollama instance with OLLAMA_NUM_PARALLEL=1 serialises generation, so the
        # win comes from parallel slots (num_parallel>1 batches on the GPU) and from a step's pytest/
        # subprocess overlapping another's generation. Opt in via NEWTON_LOOP_PARALLEL; measure per box.
        self.parallel = max(1, int(os.environ.get("NEWTON_LOOP_PARALLEL", "1")))
        self._state_lock = threading.Lock()           # guards checkpoint saves + status writes in a wave

    def _make_summarizer(self) -> Callable[[str, int], str]:
        """A model-backed compactor: squeeze reference context down to the facts a coding step needs
        (module paths, signatures, constant values). Used only when the window overflows."""
        model = self.s.agent_model

        def summarize(text: str, room: int) -> str:
            from ..llm import complete
            resp = complete(model, [
                {"role": "system", "content":
                    "Compress the code/context below to only the facts needed to write ONE coding "
                    "step: module import paths, function/class signatures, and constant names+values. "
                    "Terse bullet list, no prose, no explanation."},
                {"role": "user", "content": text[:16000]},
            ], temperature=0)
            return (resp.choices[0].message.content or "").strip()

        return summarize

    def run(self, goal: str, *, resume: bool = False) -> LoopResult:
        self._decisions = []                           # fresh audit trail of bounded forks per run
        state = self._load_or_plan(goal, resume)
        if state is None:
            return LoopResult(False, "could not decompose the goal into steps")

        # Surface the loop's budget so the user sees the run is BOUNDED, not open-ended — the caps
        # that would otherwise be hidden module constants (loopx's "quota" idea). Progress (N of M
        # steps) the UI derives from the plan + step events; this makes the ceiling explicit.
        self.emit("budget", {"effort": self.effort.name, "attempts": self.effort.max_attempts,
                             "repairs": self.effort.repair_cycles, "replans": self.effort.replan_cycles})
        self._drive(state)
        # Self-generated verification: if the plan built code but planned no check, write a
        # goal-grounded test and run it — so a step's LOGIC is validated even when the model forgot
        # to test. A failing generated test then feeds the normal repair.
        self._ensure_verification(state, goal)
        # Test-driven self-correction: if a check (a RUN step, e.g. the tests) failed, feed the
        # failure back to fix the CODE it exercises and re-run the check. Free compute + unlimited
        # time on local hardware make this iteration the way to lift a weak model's logic quality —
        # it turns "tests failed, give up" into "tests failed, fix the code, try again".
        for _ in range(self.effort.repair_cycles):
            if not self._repair_from_test_failure(state):
                break
            self._drive(state)

        # Replanning: if the loop is still stuck (a step failed/blocked that fixing one file didn't
        # resolve), the PLAN itself was wrong — re-decompose the remaining work given what exists and
        # what went wrong, and drive the new plan (+ its own repair cycles).
        for gen in range(1, self.effort.replan_cycles + 1):
            if state.all_done() or not self._replan(state, goal, gen):
                break
            self._drive(state)
            for _ in range(self.effort.repair_cycles):
                if not self._repair_from_test_failure(state):
                    break
                self._drive(state)

        # Integration verification: per-file verify + the unit test can all pass while the ASSEMBLED
        # app won't even load. As a final gate, import the entrypoint with its real deps and let a
        # load failure feed the repair loop — closing the wiring gap the Scene Script Studio E2E
        # (D63) exposed, where 14/14 steps were "done" but the app didn't run.
        self._ensure_integration_check(state)

        return self._finalize(state)

    def _drive(self, state: LoopState) -> None:
        """Run every ready step to completion, checkpointing after each. Independent ready steps run
        in a wave of up to `self.parallel` at once; dependents wait for their upstreams as before."""
        while not state.all_done() and state.remaining() > 0:
            wave = state.ready_batch(self.parallel)
            if not wave:
                break
            if len(wave) == 1:
                self._run_step(wave[0], state)        # sequential path — identical to before
            else:
                self._run_wave(wave, state)

    def _run_wave(self, wave: list[Step], state: LoopState) -> None:
        """Execute an independent set of steps concurrently. Each still checkpoints when it finishes
        (under a lock), so an interrupted wave resumes cleanly: done steps stay DONE, in-flight steps
        are reset to PENDING on resume and simply redone."""
        self.emit("note", f"Building {len(wave)} independent steps in parallel…")
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = [pool.submit(self._run_step, step, state) for step in wave]
            for f in as_completed(futures):
                f.result()                            # surface any worker exception

    def _run_step(self, step: Step, state: LoopState) -> None:
        """Drive ONE step to DONE/FAILED: best-of-N attempts, import repair, verify, checkpoint.
        Thread-safe — the only shared mutations (log append, checkpoint save, the completion emit)
        are taken under a lock, and each step owns a distinct file, so a wave can run this in parallel."""
        step.status = RUNNING
        self.emit("step", {"id": step.id, "goal": step.goal, "kind": step.kind,
                           "file": step.file, "status": "start"})

        error: str | None = self._pending_error.pop(step.id, None)
        res = StepResult(False, "")
        for attempt in range(self.effort.max_attempts):
            step.attempts += 1
            # Best-of-N: each attempt samples a genuinely different candidate (temperature rises)
            # so the loop EXPLORES instead of repeating the same broken solution; the verify (and
            # a later test) is the selector. Free local compute makes the extra tries cheap.
            temperature = min(0.1 + 0.3 * attempt + self._extra_temp.get(step.id, 0.0), 0.9)
            context = self.shaper.shape(state, step, error=error)
            res = self.executor.execute(step, context, temperature=temperature)
            if res.ok and step.kind != RUN and step.file.endswith(".py"):
                # Deterministic safety net: fix intra-project imports the model got wrong before
                # verifying — the system knows where every symbol lives, so imports resolve.
                if self._repair_imports(step.file):
                    self.emit("note", f"Repaired imports in {step.file}")
            if res.ok:
                v = self._verify(step)
                if v.ok:
                    break
                res = StepResult(False, v.detail)
            error = res.detail

        step.status = DONE if res.ok else FAILED
        step.result = res.detail[:400]      # keep enough of a test failure for the repair to be targeted
        with self._state_lock:
            state.log.append(f"{step.id} {step.status}: {step.result}")
            state.save(self.checkpoint_path)          # checkpoint after EVERY step → resumable
        self.emit("step", {"id": step.id, "status": step.status, "detail": step.result})

    def _repair_from_test_failure(self, state: LoopState) -> bool:
        """A failed RUN step (a check/tests) means the CODE is wrong, not the command — retrying the
        command is useless. Find the code step whose file the error blames, reset it (and the check)
        to pending with the failure as context, and let the loop re-do them. True if repair was set up."""
        failed = next((s for s in state.steps if s.kind == RUN and s.status == FAILED), None)
        if failed is None:
            return False
        # "No tests collected" isn't a code bug — the TEST file is malformed (no `def test_*`). Route
        # the fix to the test file, not the code, or the loop blames the code and replans forever.
        if "NO_TESTS_COLLECTED" in (failed.result or ""):
            culprit = self._test_step(state)
            if culprit is None:
                return False
            self.emit("note", f"The check collected no tests — rewriting {culprit.file} with real "
                              f"`def test_*` functions and re-running it.")
            self._pending_error[culprit.id] = (
                "The test run collected NO tests. pytest only runs functions named `test_*` — the "
                "file has bare module-level asserts instead. Rewrite it so every assertion lives "
                "inside a `def test_...():` function, then it will run.")
            culprit_conf, culprit_why = 0.95, "the test file defines no def test_* functions"
        else:
            culprit = self._culprit_step(failed.result, state)
            if culprit is None:
                return False
            self.emit("note", f"A check failed — fixing {culprit.file or culprit.id} and re-running it.")
            msg = f"A later check failed with this error — fix the code so it passes:\n{failed.result}"
            blast = self._blast_radius(culprit.file) if culprit.file else ""
            if blast:
                self.emit("note", f"Fixing {culprit.file} — {blast.count(chr(10))} file(s) depend on it; "
                                  f"keeping their interface.")
                msg += "\n\n" + blast
            self._pending_error[culprit.id] = msg
            # Structural confidence in the culprit pick: high when the failure names the file directly,
            # lower when it was inferred (e.g. via the test's imports). Coarse, but honest and auditable.
            base = (culprit.file or "").replace("\\", "/").rsplit("/", 1)[-1]
            named = bool(base) and base in (failed.result or "")
            culprit_conf, culprit_why = ((0.9, "named directly in the failure") if named
                                         else (0.5, "inferred (not named directly in the failure)"))
        # --- Confidence routing (the local, honest "decision + confidence"): don't keep spending
        # repairs on a fix that isn't working. If we have ALREADY tried to fix THIS file for THIS same
        # error and it came back unchanged, another repair (even a hotter best-of-N candidate) has low
        # odds — so escalate: return False and let run() fall through to REPLAN instead of burning the
        # budget re-fixing a file we can't crack (the thrash the ledger diagnostic showed). The signal
        # is trajectory-derived, needs no calibrated model probability, and only ever makes the loop
        # MORE cautious. Kill-switch: NEWTON_LOOP_REPAIR_PROGRESS=0.
        sig = error_signature(failed.result)
        if os.getenv("NEWTON_LOOP_REPAIR_PROGRESS", "1") != "0" \
                and (culprit.id, sig) in self._repair_history:
            self._pending_error.pop(culprit.id, None)
            self.emit("note", f"Repair isn't making progress on {culprit.file or culprit.id} — the "
                              f"same error persists after a fix. Escalating (re-plan) instead of "
                              f"re-fixing it again.")
            state.log.append(f"repair-stalled {culprit.id}: {sig[:80]}")
            self._decisions.append(Decision(
                name="repair_progress", kind=NOUL,
                question="Is another repair on this file likely to help?",
                answer="no — escalate", confidence=1.0,
                reason=f"the same error recurred on {culprit.file or culprit.id} after a fix"))
            state.save(self.checkpoint_path)
            return False
        self._repair_history.append((culprit.id, sig))
        self._decisions.append(Decision(
            name="repair_culprit", kind=CHOICE,
            question="Which file should be fixed for this failure?",
            answer=culprit.file or culprit.id, confidence=culprit_conf, reason=culprit_why))
        culprit.status = PENDING
        culprit.result = ""
        # Each repair explores a MORE different fix (best-of-N over cycles), not the same broken one.
        self._extra_temp[culprit.id] = self._extra_temp.get(culprit.id, 0.0) + 0.3
        failed.status = PENDING
        for s in state.steps:                          # unblock what the failure had blocked
            if s.status == BLOCKED:
                s.status = PENDING
        state.save(self.checkpoint_path)
        return True

    def _test_step(self, state: LoopState) -> Step | None:
        """The WRITE step for a test file (`test_*.py`) — what to fix when a check collected no tests."""
        for s in state.steps:
            if s.kind != RUN and s.file.endswith(".py") \
                    and s.file.replace("\\", "/").rsplit("/", 1)[-1].startswith("test_"):
                return s
        return None

    def _culprit_step(self, error: str, state: LoopState) -> Step | None:
        """The code (WRITE .py) step to fix for a failing check. Prefers a non-test production file the
        error names directly. But a BEHAVIOURAL assertion (`assert x.transfer(...)` / `DID NOT RAISE`)
        names only the TEST file in its traceback, even though the bug is in the production code the
        test imports. In that case, route the fix to the production module the test imports — NOT the
        test (the test is the spec; rewriting it just games the check and the loop stalls forever, as
        the ledger diagnostic showed)."""
        code = {s.file.replace("\\", "/"): s for s in state.steps
                if s.kind != RUN and s.file.endswith(".py")}
        mod_to_step = {_module_of(p): s for p, s in code.items()}
        named_tests: list[str] = []
        fallback = None
        for m in re.finditer(r"([\w./\\-]+\.py)", error or ""):
            tok = m.group(1).replace("\\", "/")
            base = tok.rsplit("/", 1)[-1]
            for path, step in code.items():
                if path == tok or path.rsplit("/", 1)[-1] == base:
                    if not base.startswith("test_"):
                        return step                       # a production file is named — fix it
                    fallback = fallback or step
            if base.startswith("test_"):
                named_tests.append(tok)
        # No production file named — map the failing test to the production module(s) it imports.
        for t in named_tests:
            for mod in self._imports_of_test(t):
                if mod in mod_to_step:
                    return mod_to_step[mod]
        return fallback

    def _imports_of_test(self, test_rel: str) -> list[str]:
        """Project modules a test file imports (`from service import Ledger` → 'service'), so a
        behavioural failure can be routed to the code that implements the behaviour. Reads the file
        from disk (the test may be a seeded fixture, not a planned step); best-effort."""
        import ast
        p = self.root / test_rel
        if not p.is_file():
            hits = list(self.root.rglob(test_rel.rsplit("/", 1)[-1]))
            if not hits:
                return []
            p = hits[0]
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            return []
        mods: list[str] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.append(n.module)
            elif isinstance(n, ast.Import):
                mods.extend(a.name for a in n.names)
        return list(dict.fromkeys(mods))

    def _code_graph(self):
        """A fresh import/def graph over the project's Python files — cheap to rebuild (repairs are
        rare and a build's tree is small) and independent of the shaper, so blast-radius works whatever
        context strategy is in use."""
        from ..index.graph import CodeGraph, analyze_python
        skip = {".venv", "venv", "node_modules", "__pycache__", ".git", ".newton", "vendor"}
        g = CodeGraph()
        for py in self.root.rglob("*.py"):
            if any(p in skip for p in py.parts):
                continue
            rel = py.relative_to(self.root).as_posix()
            try:
                g.add(analyze_python(rel, py.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
        g.finalize()
        return g

    def _imported_names_from(self, dep_rel: str, target_mod: str) -> list[str]:
        """The names `dep_rel` imports FROM module `target_mod` (`from service import Ledger` → ['Ledger'])
        — the exact interface a fix to the target must not break."""
        import ast

        from ..index.graph import CodeGraph
        try:
            tree = ast.parse((self.root / dep_rel).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            return []
        importer_mod = _module_of(dep_rel.replace("\\", "/"))
        names: list[str] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                resolved = CodeGraph._resolve(importer_mod, "." * n.level + (n.module or ""))
                if resolved == target_mod or resolved.startswith(target_mod + "."):
                    names.extend(a.name for a in n.names)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == target_mod:
                        names.append(a.name.rsplit(".", 1)[-1])
        return list(dict.fromkeys(names))

    def _dependents(self, rel: str) -> list[str]:
        """PRODUCTION files that import `rel`'s module (test files excluded) — the blast radius.
        Best-effort: an empty list on any graph failure."""
        rel = rel.replace("\\", "/")
        try:
            return [d for d in self._code_graph().dependents(rel)
                    if not d.rsplit("/", 1)[-1].startswith("test_")]
        except Exception:
            return []

    def _blast_radius(self, rel: str) -> str:
        """The PRODUCTION files that depend on `rel` (import its module), and the names they use from
        it — surfaced into a repair so a fix to a shared file keeps its callers working instead of
        silently breaking them (the D63 cross-file-consistency failure). Empty when nothing depends on
        it. Test files are excluded: the point is protecting OTHER code, and the failing test's own
        interface is already carried by the error message."""
        rel = rel.replace("\\", "/")
        deps = self._dependents(rel)
        if not deps:
            return ""
        target_mod = _module_of(rel)
        lines = []
        for dep in deps[:6]:                              # cap so the small window isn't flooded
            syms = self._imported_names_from(dep, target_mod)
            lines.append(f"  - {dep}" + (f" (uses: {', '.join(syms)})" if syms else ""))
        return (f"Blast radius — these files import `{target_mod}` (in {rel}). Your fix MUST keep the "
                f"names they rely on working (do not rename or remove them; change only the "
                f"implementation):\n" + "\n".join(lines))

    def _ensure_verification(self, state: LoopState, goal: str) -> None:
        """If the plan produced code but NO check, generate a goal-grounded test and run it — so a
        step's LOGIC is validated even when the model didn't plan a test. Conservative on purpose: a
        model-authored test can be imperfect, so it's added ONLY when there's no real test, is
        grounded in the goal's own examples, and a failing one feeds the normal repair. Off with
        NEWTON_LOOP_SELFTEST=0."""
        if os.getenv("NEWTON_LOOP_SELFTEST", "1") == "0":
            return
        if any(s.kind == RUN for s in state.steps):
            return                                   # the model already planned a check — trust it
        code = [s for s in state.steps if s.kind != RUN and s.file.endswith(".py")
                and not s.file.rsplit("/", 1)[-1].startswith("test_") and s.status == DONE]
        if not code:
            return
        self.emit("note", "No test in the plan — writing a check to verify the work.")
        test_step = Step(
            id="v_test", kind=WRITE, file="test_generated.py",
            goal=(f"Write pytest tests that verify this goal is met: {goal}\n"
                  "Import from the project's modules (see the available imports) and assert their "
                  "behaviour matches the goal — use the EXACT examples the goal gives. Test only what "
                  "the goal clearly specifies; do not invent requirements. Every assertion MUST live "
                  "inside a `def test_...():` function — pytest runs functions named test_*, NOT bare "
                  "module-level asserts."))
        run_step = Step(id="v_run", kind=RUN, command='{py} -B -m pytest -q test_generated.py',
                        goal="run the generated check", depends_on=["v_test"])
        state.steps += [test_step, run_step]
        state.save(self.checkpoint_path)
        self.emit("plan", [{"id": s.id, "goal": s.goal, "kind": s.kind, "file": s.file,
                            "depends_on": s.depends_on} for s in (test_step, run_step)])
        self._drive(state)

    def _ensure_integration_check(self, state: LoopState) -> None:
        """Whole-app load check — the gap the per-file verify and the unit test both miss. Each file
        can pass its own verify while the ASSEMBLED app won't even import (a cross-file import that
        doesn't resolve, a module-load-time error like a StaticFiles dir that isn't there, a symbol
        used but never imported). After the build, import the app's entrypoint in a subprocess with
        its real dependencies installed; a failure that names a project file feeds the normal repair.

        For a web app it goes further than a smoke load: it PROBES the assembled app — drives it
        through an in-process test client and GETs every no-argument route, failing on a 5xx or a
        handler that raises. That catches the request-time class of bug (a frontend/backend contract
        mismatch, a handler that crashes on a basic request) that a per-file verify and a plain
        import both pass — the exact gap the first real full-stack run hit. For a non-web app it
        falls back to the plain load check. Either way a failure that names a project file feeds the
        normal repair. Needs a requirements.txt so deps are real (else a missing package would look
        like a bug); without one we skip rather than cry wolf. Off with NEWTON_LOOP_INTEGRATION=0."""
        if os.getenv("NEWTON_LOOP_INTEGRATION", "1") == "0":
            return
        existing = next((s for s in state.steps if s.id == "i_run"), None)
        if existing is not None and existing.status == DONE:
            return                                       # already passed (e.g. on resume)
        entry = self._detect_entrypoint(state)
        if not entry:
            return
        asgi = self._entry_is_asgi(state)
        py, ready = self._project_python(extra=("httpx",) if asgi else ())
        if not ready:
            self.emit("note", "Skipping the whole-app check — couldn't prepare its dependencies.")
            return
        probe = self.root / ".newton_integration.py"
        try:
            if asgi:
                probe.write_text(self._integration_probe(entry), encoding="utf-8")
                command = f'"{py}" -B ".newton_integration.py"'
                goal = f"probe {entry}'s routes — confirm the assembled app serves without errors"
                self.emit("note", "Checking the whole app serves its routes without errors…")
            else:
                command = f'"{py}" -B -c "import {entry}"'
                goal = f"import {entry} — confirm the assembled app loads"
                self.emit("note", "Checking the whole app loads together…")
            step = existing
            if step is None:
                step = Step(id="i_run", kind=RUN, command=command, goal=goal)
                state.steps.append(step)
            else:                                        # resumed mid-gate — refresh and re-run
                step.command, step.goal, step.status = command, goal, PENDING
            state.save(self.checkpoint_path)
            self.emit("plan", [{"id": step.id, "goal": step.goal, "kind": step.kind,
                                "file": step.file, "depends_on": step.depends_on}])
            self._run_step(step, state)
            for _ in range(self.effort.repair_cycles):   # a failure fixes the file it blames
                if not self._repair_from_test_failure(state):
                    break
                self._drive(state)
        finally:
            try:
                probe.unlink()                           # the probe is a check artifact, not output
            except OSError:
                pass

    def _detect_entrypoint(self, state: LoopState) -> str | None:
        """The module to import as the app's entrypoint — the file most likely to wire the app
        together (main.py/app.py, a `__main__` guard, or a FastAPI/Flask app object). Returns its
        dotted import path, or None when nothing looks like an entrypoint."""
        best, best_score = None, 0
        for s in state.steps:
            if s.status != DONE or s.kind == RUN or not s.file.endswith(".py"):
                continue
            base = s.file.replace("\\", "/").rsplit("/", 1)[-1]
            if base.startswith("test_"):
                continue
            try:
                txt = (self.root / s.file).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                txt = ""
            score = (3 if base in ("main.py", "app.py") else 0)
            score += 2 if "__main__" in txt else 0
            score += 2 if ("FastAPI(" in txt or "Flask(" in txt) else 0
            if score > best_score:
                best, best_score = s, score
        if best is None or best_score == 0:
            return None
        return _module_of(best.file.replace("\\", "/"))

    def _project_python(self, extra: tuple[str, ...] = ()) -> tuple[str, bool]:
        """(python to run the check, deps-ready). With a requirements.txt, build/reuse a project
        .venv and install into it so third-party imports resolve — then a failed import is a REAL
        bug, not a missing package. Without one, we can't guarantee deps, so report not-ready.
        `extra` names packages the CHECK itself needs (e.g. `httpx` for the route probe's test
        client) that the app may not list — installed on top of the app's own requirements."""
        req = self.root / "requirements.txt"
        if not req.is_file():
            return sys.executable, False
        venv = self.root / ".venv"
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        try:
            if not py.exists():
                subprocess.run([sys.executable, "-m", "venv", str(venv)],
                               capture_output=True, timeout=180, check=True)
            subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(req)],
                           capture_output=True, timeout=600, check=True)
            if extra:
                subprocess.run([str(py), "-m", "pip", "install", "-q", *extra],
                               capture_output=True, timeout=300, check=True)
        except (subprocess.SubprocessError, OSError):
            return sys.executable, False
        return str(py), True

    def _entry_is_asgi(self, state: LoopState) -> bool:
        """Does the build declare a FastAPI/Starlette app? If so the whole-app check should PROBE its
        routes (does a request work?), not merely import it (does it load?). Cheap text scan of the
        built Python files — an APIRouter or a FastAPI/Starlette constructor is the tell."""
        for s in state.steps:
            if s.status == DONE and s.kind != RUN and s.file.endswith(".py"):
                try:
                    t = (self.root / s.file).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "FastAPI(" in t or "Starlette(" in t or "APIRouter(" in t:
                    return True
        return False

    @staticmethod
    def _integration_probe(entry: str) -> str:
        """The generated route-probe script for `entry`'s ASGI app (see `_INTEGRATION_PROBE`)."""
        return _INTEGRATION_PROBE.replace("__ENTRY__", entry)

    @staticmethod
    def _reid(steps: list[Step], prefix: str) -> list[Step]:
        """Prefix a fresh plan's step ids (and internal deps) so they can't collide with steps
        already recorded in the state."""
        idmap = {s.id: f"{prefix}{s.id}" for s in steps}
        for s in steps:
            s.id = idmap[s.id]
            s.depends_on = [idmap[d] for d in s.depends_on if d in idmap]
        return steps

    def _replan(self, state: LoopState, goal: str, gen: int) -> bool:
        """Re-decompose the remaining work when the plan was wrong. Keeps the steps that DID finish,
        replaces the failed/blocked/unrun ones with a fresh plan that accounts for what exists and
        what went wrong. Returns True if a new plan was produced. Never fails the run — best-effort."""
        stuck = [s for s in state.steps if s.status in (FAILED, BLOCKED)]
        if not stuck:
            return False
        why = "; ".join(f"{s.file or s.id}: {s.result}"[:120] for s in stuck)[:400]
        self.emit("stage", "Replan")
        self.emit("note", "The plan didn't work — re-planning the remaining work.")
        augmented = (
            f"{goal}\n\nSome files may already exist in the project. The previous attempt got stuck: "
            f"{why}. Plan the steps needed to FINISH and FIX this — prefer EDITING existing files "
            f"over recreating them, and end with a step that runs the tests.")
        try:
            new_steps = decompose(self.s.agent_model, augmented, project_tree(self.root))
        except Exception:
            return False
        if not new_steps:
            return False
        self._reid(new_steps, f"r{gen}_")
        done = [s for s in state.steps if s.status == DONE]
        state.steps = done + new_steps               # keep what finished; retry the rest with a new plan
        state.log.append(f"replan {gen}: {len(new_steps)} new steps after — {why[:100]}")
        state.save(self.checkpoint_path)
        self.emit("plan", [{"id": s.id, "goal": s.goal, "kind": s.kind, "file": s.file,
                            "depends_on": s.depends_on} for s in new_steps])
        return True

    def _run_verdict(self, state: LoopState):
        """A graduated, advisory verdict for the finished build (PASS / NEEDS ATTENTION / WOULD BLOCK),
        composed from Newton's own signals — did every step verify, and do other files depend on the
        changed ones (blast radius). Advisory only: it never changes the loop's behaviour. Builds the
        code graph once and queries it, so it's cheap at finalize time."""
        from .verdict import review_run
        failed = sum(1 for s in state.steps if s.status in (FAILED, BLOCKED))
        fwd: list[tuple[str, list[str]]] = []
        try:
            graph = self._code_graph()
        except Exception:
            graph = None
        if graph is not None:
            for s in state.steps:
                base = s.file.replace("\\", "/").rsplit("/", 1)[-1]
                if s.status == DONE and s.kind != RUN and s.file.endswith(".py") and not base.startswith("test_"):
                    deps = [d for d in graph.dependents(s.file.replace("\\", "/"))
                            if not d.rsplit("/", 1)[-1].startswith("test_")]
                    if deps:
                        fwd.append((s.file, deps))
        return review_run(failed, fwd)

    def _finalize(self, state: LoopState) -> LoopResult:
        for s in state.steps:                          # anything left had a failed/blocked dependency
            if s.status in (PENDING, RUNNING):
                s.status = BLOCKED
        state.save(self.checkpoint_path)
        c = state.counts()
        ok = state.all_done()
        counts = (f"{c[DONE]} done, {c[FAILED]} failed, {c[BLOCKED]} blocked "
                  f"of {len(state.steps)} steps.")
        vd = self._run_verdict(state)
        self.emit("verdict", {"level": vd.level, "label": vd.label, "reason": vd.reason})
        self._decisions.append(Decision(
            name="verdict", kind=SCORE,
            question="Is the finished build trustworthy?",
            answer=vd.label, confidence=1.0, reason=vd.reason))
        self._advisory_judge(state)                    # opt-in: Laya scores checks alongside truth (no-op otherwise)
        # Evidence-binding: attach what the run PROVED — the checks that ran (tests, route probe), a
        # content fingerprint of every produced file, AND the bounded DECISIONS the loop took (each
        # with its confidence + reason — the "keep the probabilities" discipline) — so the verdict is
        # trustworthy and auditable without a re-run. Emitted live and returned for the History log.
        ev = collect_evidence(state, self.root, self._decisions)
        self.emit("evidence", ev.as_dict())
        proof = f" · proof: {ev.checks_passed}/{len(ev.checks)} checks, {len(ev.artifacts)} files"
        answer = f"[{vd.label}] {counts} — {vd.reason}{proof if ev.checks or ev.artifacts else ''}"
        self.emit("loop", {"ok": ok, "answer": answer, "counts": c})
        return LoopResult(ok, answer, state, evidence=ev)

    def _advisory_judge(self, state: LoopState) -> None:
        """Eval-first (never acts): score each check's output with the advisory Laya judge and record
        its answer as a Decision ALONGSIDE the deterministic ground truth, appending the pair to the
        advisory log. This is the doc's safe placement — it changes nothing the loop does; over real
        runs the log becomes a labeled corpus to measure Laya's agreement/drift on Newton's own
        outputs. Off by default; enable with NEWTON_LOOP_ADVISORY_JUDGE=1 (or inject a judge in tests).
        Fully fail-soft — an advisory error never touches the run's result."""
        judge = self._judge
        if judge is None:
            if os.getenv("NEWTON_LOOP_ADVISORY_JUDGE", "0") != "1":
                return
            judge = LayaJudge()
        try:
            log_path = self.root / ".newton" / "advisory.jsonl"
            for s in state.steps:
                if s.kind != RUN or not s.result:
                    continue
                d = judge.judge_failure(s.result)
                if d is None:
                    continue
                truth_failure = s.status != DONE           # ground truth: did the check actually fail?
                pred_failure = d.answer == "failure"
                d.reason += f" | truth={'failure' if truth_failure else 'ok'}"
                self._decisions.append(d)
                append_advisory(log_path, {
                    "check": s.id, "truth_failure": truth_failure, "pred_failure": pred_failure,
                    "confidence": d.confidence, "correct": pred_failure == truth_failure})
        except Exception:
            pass                                           # advisory must never break a run

    def _load_or_plan(self, goal: str, resume: bool) -> LoopState | None:
        if resume and self.checkpoint_path.is_file():
            state = LoopState.load(self.checkpoint_path)
            for s in state.steps:                          # a step caught mid-flight at interrupt was
                if s.status == RUNNING:                    # never finished — redo it, don't strand it
                    s.status = PENDING
            self.emit("note", f"Resuming: {state.counts()[DONE]} step(s) already done — continuing.")
            return state
        self.emit("stage", "Plan")
        steps = decompose(self.s.agent_model, goal, project_tree(self.root))
        if not steps:
            self.emit("halt", "could not decompose the goal into atomic steps")
            return None
        state = LoopState(goal=goal, steps=steps)
        state.save(self.checkpoint_path)
        self.emit("plan", [{"id": s.id, "goal": s.goal, "kind": s.kind, "file": s.file,
                            "depends_on": s.depends_on} for s in steps])
        self.emit("note", f"Planned {len(steps)} atomic step(s).")
        return state

    def _project_symbols(self) -> dict[str, str]:
        """Map each public top-level symbol (function/class/constant) → the module that defines it.
        The deterministic ground truth the model keeps getting wrong."""
        import ast
        skip = {".venv", "node_modules", "__pycache__", ".git", ".newton", "vendor"}
        out: dict[str, str] = {}
        for py in self.root.rglob("*.py"):
            rel = py.relative_to(self.root).as_posix()
            if any(p in skip for p in py.parts) or rel.rsplit("/", 1)[-1].startswith("test_"):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, SyntaxError):
                continue
            mod = _module_of(rel)
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                    out.setdefault(n.name, mod)
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name) and not t.id.startswith("_"):
                            out.setdefault(t.id, mod)
        return out

    def _repair_imports(self, rel: str) -> bool:
        """Deterministically fix a written file's intra-project imports: if `from M import name`
        names a symbol that actually lives in module M', rewrite M→M'. The model writes the logic;
        the system guarantees the imports resolve. Leaves third-party and unknown names alone."""
        import ast
        path = self.root / rel
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            return False
        symbols = self._project_symbols()
        self_mod = _module_of(rel)
        lines = src.splitlines()
        changed = False
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.module is None or node.level != 0:
                continue
            real_mods = {symbols[a.name] for a in node.names if a.name in symbols}
            # only act when every named symbol resolves to ONE real module that isn't what was written
            if len(real_mods) == 1:
                real = real_mods.pop()
                if real != node.module and real != self_mod:
                    ln = node.lineno - 1
                    lines[ln] = lines[ln].replace(f"from {node.module} import", f"from {real} import", 1)
                    changed = True
        if changed:
            path.write_text("\n".join(lines) + ("\n" if src.endswith("\n") else ""), encoding="utf-8")
        return changed

    def _verify(self, step: Step) -> StepResult:
        """A RUN step's success IS its verification. A WRITE .py step must (1) parse and (2) IMPORT
        cleanly — importing catches hallucinated/wrong module paths (`from project.pricing ...` when
        the real module is `store.pricing`) that parsing alone misses, so the loop retries and the
        model self-corrects with the error fed back. A missing THIRD-PARTY dep is an env gap, skipped."""
        if step.kind == RUN or not step.file.endswith(".py"):
            return StepResult(True, "")
        path = self.root / step.file
        src = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
        if not python_parses(src):
            return StepResult(False, f"{step.file} does not parse")

        module = _module_of(step.file)
        out = ToolBelt(self.root).run(cmd=f'"{sys.executable}" -B -c "import {module}"')
        if out.startswith("[exit 0]"):
            return StepResult(True, "")
        body = out.split("]", 1)[-1].strip()
        miss = re.search(r"No module named ['\"]([\w.]+)['\"]", body)
        if miss and miss.group(1).split(".")[0] in _EXTERNAL_DEPS:
            return StepResult(True, "")                 # real third-party dep, not a code bug
        return StepResult(False, f"import of {step.file} failed: {body[:200]}")
