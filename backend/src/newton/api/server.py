"""FastAPI server: runs the Conductor and streams it to the browser.

Endpoints:
  GET  /                      → the workspace SPA
  GET  /api/models            → locally installed Ollama models
  POST /api/run               → start a Conductor run (worker thread), returns run_id
  GET  /api/events/{run_id}   → SSE stream of engine events
  POST /api/approve/{run_id}  → resolve a pending approval gate
"""

from __future__ import annotations

import asyncio
import json
import queue
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import savings as _savings
from ..author.conductor import AuthorConductor
from ..chat.conductor import ChatConductor
from ..conductor.pipeline import Conductor
from ..config import load_settings
from ..intake.converter import IntakeError, ingest
from ..report.conductor import ReportConductor

OLLAMA = "http://localhost:11434"

# In production the backend serves the built React app (frontend/dist); in dev the Vite
# server serves it and proxies /api here. A legacy static SPA is the fallback.
_REPO_ROOT = Path(__file__).resolve().parents[4]          # …/backend/src/newton/api → repo root
FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"
LEGACY_STATIC = Path(__file__).parent / "static"

# Point the cloud-cost-avoided meter at this project so every LLM call is tallied.
_savings.configure(_REPO_ROOT)


@dataclass
class Run:
    id: str
    events: queue.Queue[dict] = field(default_factory=queue.Queue)
    gate: threading.Event = field(default_factory=threading.Event)
    decision: bool = False
    finished: bool = False
    task: str = ""              # what was asked — for the activity feed
    mode: str = ""              # chat | task | project | author | report
    status: str = "running"     # running | done | failed
    started: float = 0.0        # epoch seconds, for ordering the feed
    ok: bool = False


class RunManager:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}

    def start(self, task: str, project: str, model: str | None, auto: bool, mode: str,
              doc_type: str = "prd", effort: str = "normal") -> str:
        run = Run(id=uuid.uuid4().hex[:12], task=task, mode=mode, started=time.time())
        self.runs[run.id] = run
        self._prune()
        t = threading.Thread(target=self._worker,
                             args=(run, task, project, model, auto, mode, doc_type, effort), daemon=True)
        t.start()
        return run.id

    def _prune(self, keep: int = 40) -> None:
        """Cap in-memory run history: drop the oldest FINISHED runs beyond `keep` (never active ones)."""
        if len(self.runs) <= keep:
            return
        finished = sorted((r for r in self.runs.values() if r.finished), key=lambda r: r.started)
        for r in finished[: len(self.runs) - keep]:
            self.runs.pop(r.id, None)

    def recent(self, limit: int = 15) -> list[Run]:
        """Recent runs, newest first — active ones plus lately-finished, for the activity feed."""
        return sorted(self.runs.values(), key=lambda r: r.started, reverse=True)[:limit]

    def _worker(self, run: Run, task: str, project: str, model: str | None, auto: bool,
                mode: str, doc_type: str = "prd", effort: str = "normal") -> None:
        settings = load_settings(project)
        if model:
            settings.agent_model = model if model.startswith("ollama:") else f"ollama:{model}"

        def emit(channel: str, payload: Any) -> None:
            run.events.put({"ch": channel, "payload": payload})

        def approve(kind: str, args: dict) -> bool:
            if auto:
                run.events.put({"ch": "gate", "payload": {"kind": kind, "args": args, "auto": True}})
                return True
            run.gate.clear()
            run.events.put({"ch": "gate", "payload": {"kind": kind, "args": args, "auto": False}})
            run.gate.wait()               # block the engine until the browser answers
            return run.decision

        final_ok = False
        try:
            if mode == "chat":
                ChatConductor(settings, emit=emit, approve=approve).run(task)
                final_ok = True
                run.events.put({"ch": "done", "payload": {"ok": True, "answer": "", "stages": []}})
            elif mode == "project":
                # Build is now powered by the LoopEngine (the proven decomposition loop: retrieval,
                # compaction, deterministic import-repair, checkpoint/resume). Its plan/step/loop
                # events are translated to the backlog/task/project shapes the Build UI already
                # renders, so no UI component changes are needed.
                from ..loop import LoopEngine
                from ..loop import effort as resolve_effort

                def loop_emit(ch: str, p: Any) -> None:
                    if ch == "plan":
                        emit("backlog", [{"id": s.get("id"), "title": (s.get("goal") or "")[:80],
                                          "file": s.get("file", ""), "depends_on": s.get("depends_on", [])}
                                         for s in p])
                    elif ch == "step":
                        emit("task", {"id": p.get("id"),
                                      "title": p.get("file") or (p.get("goal") or "")[:80],
                                      "status": p.get("status"), "file": p.get("file", ""),
                                      "summary": p.get("detail", "")})
                    elif ch == "loop":
                        emit("project", p)
                    else:
                        emit(ch, p)                 # stage, note, halt pass through unchanged

                result = LoopEngine(settings, emit=loop_emit, effort=resolve_effort(effort)).run(task)
                final_ok = result.ok
                run.events.put({"ch": "done", "payload": {"ok": result.ok, "answer": result.answer, "stages": []}})
            elif mode == "report":
                rep = ReportConductor(settings, emit=emit, approve=approve).run(task or "status")
                answer = f"Report written to {rep.path}" if rep.ok else "Report not saved."
                final_ok = rep.ok
                run.events.put({"ch": "done", "payload": {"ok": rep.ok, "answer": answer, "stages": []}})
            elif mode == "author":
                doc = AuthorConductor(settings, emit=emit, approve=approve).run(doc_type, task)
                answer = f"Drafted {doc.path}" if doc.ok else "Document not saved."
                final_ok = doc.ok
                run.events.put({"ch": "done", "payload": {"ok": doc.ok, "answer": answer, "stages": []}})
            else:
                result = Conductor(settings, emit=emit, approve=approve, auto_approve=auto).run(task)
                final_ok = result.ok
                run.events.put({"ch": "done", "payload": {
                    "ok": result.ok, "answer": result.answer, "stages": result.stages}})
        except Exception as e:  # never leave the stream hanging on an engine crash
            run.events.put({"ch": "done", "payload": {"ok": False, "answer": f"engine error: {e}", "stages": []}})
        finally:
            run.ok = final_ok
            run.status = "done" if final_ok else "failed"
            run.finished = True

    def resolve(self, run_id: str, decision: bool) -> bool:
        run = self.runs.get(run_id)
        if not run:
            return False
        run.decision = decision
        run.gate.set()
        return True


manager = RunManager()
app = FastAPI(title="Newton", version="0.1.0")

# Allow the Vite dev server (5173) to call the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunReq(BaseModel):
    task: str
    project: str = "."
    model: str | None = None
    auto: bool = False
    mode: str = "task"          # "task" | "project" | "report" | "author"
    doc_type: str = "prd"       # for mode="author": prd | architecture | brainstorm | design
    effort: str = "normal"      # for mode="project" (Build): quick | normal | thorough | max


class ApproveReq(BaseModel):
    decision: bool


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    # Prefer the built React app; fall back to the legacy SPA, then a helpful message.
    for candidate in (FRONTEND_DIST / "index.html", LEGACY_STATIC / "index.html"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return ("<h1>Newton API is running.</h1><p>Build the frontend "
            "(<code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>) "
            "or run the Vite dev server (<code>npm run dev</code>) at "
            "<a href='http://localhost:5173'>localhost:5173</a>.</p>")


MISSION_CONTROL = _REPO_ROOT / "mission-control"


@app.get("/api/knowledge")
async def knowledge_list() -> dict:
    """The mission-control markdown vault — the project's notebook, surfaced in the app."""
    files = []
    if MISSION_CONTROL.is_dir():
        for p in sorted(MISSION_CONTROL.rglob("*.md")):
            files.append({"path": p.relative_to(MISSION_CONTROL).as_posix(), "name": p.stem})
    return {"files": files}


@app.get("/api/knowledge/{path:path}")
async def knowledge_read(path: str) -> dict:
    f = (MISSION_CONTROL / path).resolve()
    if MISSION_CONTROL in f.parents and f.is_file() and f.suffix == ".md":
        return {"path": path, "content": f.read_text(encoding="utf-8")}
    return {"path": path, "content": "*(file not found)*"}


@app.get("/api/wiki")
async def wiki_list() -> dict:
    """The self-maintaining wiki — curated knowledge pages Newton writes as it learns."""
    from ..wiki import Wiki
    pages = Wiki(_REPO_ROOT).pages()
    return {"files": [{"path": p, "name": p[:-3] if p.endswith(".md") else p} for p in pages]}


@app.get("/api/wiki/{name:path}")
async def wiki_read(name: str) -> dict:
    from ..wiki import Wiki
    content = Wiki(_REPO_ROOT).read(name)
    return {"path": name, "content": content or "*(page not found)*"}


@app.get("/api/skills")
async def skills_list() -> dict:
    """Procedure playbooks the Conductor follows — reusable how-tos (SKILL.md)."""
    from ..skills import Skills
    return {"files": [{"path": m.file, "name": m.file[:-3], "desc": m.description}
                      for m in Skills(_REPO_ROOT).list()]}


@app.get("/api/skills/{name:path}")
async def skills_read(name: str) -> dict:
    from ..skills import Skills
    content = Skills(_REPO_ROOT).read(name)
    return {"path": name, "content": content or "*(skill not found)*"}


class SkillReq(BaseModel):
    name: str
    description: str = ""
    body: str = ""              # the procedure, as markdown (or steps, one per line)


@app.post("/api/skills")
async def skills_create(req: SkillReq) -> dict:
    """Author a new SKILL.md from the web app — the same store the conductor reads and learns into."""
    from ..skills import Skills, build_skill_md
    name = req.name.strip()
    if not name:
        return {"ok": False, "error": "a name is required"}
    if not req.body.strip():
        return {"ok": False, "error": "a procedure body is required"}
    skills = Skills(_REPO_ROOT)
    dup = skills.would_duplicate(name, req.description)
    if dup:
        return {"ok": False, "error": f"a similar skill already exists: '{dup}'"}
    content = build_skill_md(name, req.description.strip(), req.body.strip())
    p = skills.write(name, content)
    return {"ok": True, "file": p.name}


@app.get("/api/memory")
async def memory() -> dict:
    f = _REPO_ROOT / ".newton" / "memory.jsonl"
    entries = []
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"entries": list(reversed(entries))}


class VerifyReq(BaseModel):
    match: str


@app.post("/api/memory/verify")
async def verify_memory(req: VerifyReq) -> dict:
    """Promote the memory that best matches `match` to 'verified' (top recall trust) — the operator
    confirming a fact so it outranks any unconfirmed memory."""
    from ..index.embeddings import Embedder
    from ..memory import Memory

    mem = Memory(_REPO_ROOT / ".newton" / "memory.jsonl", embedder=Embedder())
    item = mem.verify(req.match)
    if item is None:
        return {"ok": False, "detail": "no matching memory found"}
    return {"ok": True, "verified": {"text": item.text, "origin": item.origin, "ts": item.ts}}


@app.get("/api/savings")
async def savings() -> dict:
    """The cloud-cost-avoided meter: what Newton's local (free) inference would have cost on a cloud
    API, priced against a static offline rate snapshot. Newton's zero-token-cost edge, in dollars."""
    return _savings.summary()


@app.get("/api/runs")
async def runs() -> dict:
    """Recent runs — active + lately-finished — for the Overview activity feed."""
    return {"runs": [{"id": r.id, "task": r.task, "mode": r.mode, "status": r.status,
                      "started": r.started, "ok": r.ok} for r in manager.recent()]}


@app.get("/api/components")
async def components() -> dict:
    return {"components": [
        {"name": "Task Conductor", "status": "done", "desc": "Staged pipeline · gates · self-correction · honest halting"},
        {"name": "Whole-repo context", "status": "done", "desc": "Code-aware BM25 search + AST code-graph"},
        {"name": "Project Conductor", "status": "done", "desc": "Decompose a goal → backlog → delegate to task conductors"},
        {"name": "Semantic re-rank", "status": "done", "desc": "Local Ollama embeddings blended on top of BM25"},
        {"name": "Memory", "status": "done", "desc": "Cross-session semantic memory (local embeddings, no cloud)"},
        {"name": "LLM-wiki", "status": "done", "desc": "Self-maintaining curated knowledge pages"},
        {"name": "Reports", "status": "done", "desc": "Read the project → draft status/architecture/progress reports"},
        {"name": "Document intake", "status": "done", "desc": "MarkItDown: any file (PDF/DOCX/PPTX/XLSX) → indexed markdown in raw/"},
        {"name": "Authoring", "status": "done", "desc": "Draft grounded PRD / architecture / brainstorm / design docs from a topic"},
    ]}


@app.get("/api/models")
async def models() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA}/api/tags")
            names = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        names = []
    return {"models": names}


_PROJECT_MARKERS = (".git", "NEWTON.md", "pyproject.toml", "package.json", "Cargo.toml", "go.mod")


def _is_project(p: Path) -> bool:
    return any((p / m).exists() for m in _PROJECT_MARKERS)


@app.get("/api/browse")
async def browse(path: str = "") -> dict:
    """List subdirectories of `path` so the web app can pick a local project folder to link.

    This is a local-only tool — the server runs on the user's machine with their filesystem
    access — so browsing their own directories is expected. It lists directories only (never
    file contents) and flags folders that look like a project (git/NEWTON.md/manifest present).
    Defaults to the Desktop, then home.
    """
    home = Path.home()
    if path:
        root = Path(path).expanduser()
    else:
        desktop = home / "Desktop"
        root = desktop if desktop.is_dir() else home
    try:
        root = root.resolve()
    except Exception:
        root = home
    if not root.is_dir():
        root = home

    dirs = []
    try:
        for child in sorted(root.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_dir() and not child.name.startswith("."):
                    dirs.append({"name": child.name, "path": str(child), "project": _is_project(child)})
            except OSError:
                continue
    except PermissionError:
        pass

    parent = str(root.parent) if root.parent != root else None
    return {"path": str(root), "parent": parent, "project": _is_project(root),
            "home": str(home), "dirs": dirs}


@app.post("/api/run")
async def run(req: RunReq) -> dict:
    run_id = manager.start(req.task, req.project, req.model, req.auto, req.mode, req.doc_type, req.effort)
    return {"run_id": run_id}


@app.post("/api/approve/{run_id}")
async def approve(run_id: str, req: ApproveReq) -> dict:
    return {"ok": manager.resolve(run_id, req.decision)}


@app.post("/api/intake")
async def intake(file: UploadFile = File(...), project: str = Form(".")) -> dict:
    """Convert an uploaded document to markdown under <project>/raw/ (then it's indexed).

    The upload is written to a temp dir under its ORIGINAL name so the ingested slug and
    provenance frontmatter stay meaningful, then converted and the temp dir is removed.
    """
    root = Path(project).expanduser().resolve()
    orig = Path(file.filename or "document").name  # strip any client path components
    tmpdir = Path(tempfile.mkdtemp(prefix="newton-intake-"))
    try:
        src = tmpdir / orig
        src.write_bytes(await file.read())
        result = ingest(root, src)
        return {"ok": True, "dest": result.dest, "chars": result.chars, "name": orig}
    except IntakeError as e:
        return {"ok": False, "error": str(e), "name": orig}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.get("/api/events/{run_id}")
async def events(run_id: str) -> StreamingResponse:
    run = manager.runs.get(run_id)

    async def gen():
        if not run:
            yield f"data: {json.dumps({'ch': 'done', 'payload': {'ok': False, 'answer': 'no such run'}})}\n\n"
            return
        while True:
            try:
                item = await asyncio.to_thread(run.events.get, True, 1.0)
            except queue.Empty:
                if run.finished and run.events.empty():
                    break
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(item)}\n\n"
            if item["ch"] == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


# Serve the built React app's hashed assets (…/dist/assets/*) in production.
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8770, log_level="warning")


if __name__ == "__main__":
    main()
