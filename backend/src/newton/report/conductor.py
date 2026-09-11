"""ReportConductor — read the whole project, draft a grounded markdown report.

Same philosophy as the Task Conductor (`conductor/pipeline.py`): the SYSTEM owns the
report's skeleton — a fixed set of sections per report type — and gathers the facts
deterministically (git history, the repo index, the mission-control notebook, memory,
TODOs). The small local model only writes the prose for each section, grounded strictly
in the facts it is handed. It never invents files, numbers, or status.

Reports are written under `docs/` (STATUS.md / ARCHITECTURE.md / PROGRESS.md) so they
also feed back in as project context on the next session — a compounding notebook.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..conductor.assembler import ContextAssembler, ContextBlock
from ..conductor.state import GoalSpec, WorkingState, strip_leading_heading
from ..config import Settings
from ..context import project_tree
from ..index import RepoIndex
from ..index.embeddings import Embedder
from ..llm import complete
from ..memory import Memory
from ..tools import ToolBelt

Emit = Callable[[str, Any], None]
Approve = Callable[[str, dict], bool]

SYSTEM = (
    "You are Newton, a local project assistant writing one section of a project report. "
    "You are given FACTS gathered from the project. Write clear, professional markdown "
    "grounded ONLY in those facts — never invent files, numbers, dates, or status. Write "
    "just the section body: no heading, no preamble, no sign-off. Be concise."
)


@dataclass
class ReportSpec:
    """A report's fixed skeleton. `sections` is ordered; each is (title, instruction,
    fact_keys) where fact_keys select which gathered facts that section may use."""

    title: str
    filename: str
    sections: list[tuple[str, str, list[str]]]


# The fixed report skeletons. Fact keys map into gather_facts() output.
REPORT_SPECS: dict[str, ReportSpec] = {
    "status": ReportSpec(
        "Project Status Report",
        "docs/STATUS.md",
        [
            ("Summary",
             "In 2-4 sentences, summarize what this project is and its current state.",
             ["overview", "stats"]),
            ("Recent Activity",
             "Summarize recent development activity as a short bulleted list.",
             ["git"]),
            ("Codebase Snapshot",
             "Describe the shape of the codebase: size, main areas, and notable modules.",
             ["stats", "modules", "tree"]),
            ("Progress & Decisions",
             "Summarize progress made and the key decisions taken so far.",
             ["progress", "decisions", "memory"]),
            ("Open Items",
             "List open items, TODOs, or risks. If none are evident, say so in one line.",
             ["todos"]),
        ],
    ),
    "architecture": ReportSpec(
        "Architecture Overview",
        "docs/ARCHITECTURE.md",
        [
            ("Overview",
             "Summarize the system's purpose and high-level architecture in 2-4 sentences.",
             ["overview", "stats"]),
            ("Structure",
             "Describe how the project is organized: the main directories and modules.",
             ["tree", "modules"]),
            ("Key Modules",
             "Describe the most significant modules and their responsibilities.",
             ["modules", "overview"]),
            ("Design Decisions",
             "Summarize notable architectural decisions and conventions.",
             ["decisions", "overview"]),
        ],
    ),
    "progress": ReportSpec(
        "Progress Report",
        "docs/PROGRESS.md",
        [
            ("Completed",
             "Summarize what has been completed, as a bulleted list.",
             ["git", "progress", "memory"]),
            ("In Progress / Next",
             "Summarize what is in progress or planned next.",
             ["phases", "todos"]),
            ("Risks & Blockers",
             "List any risks or blockers. If none are evident, say so in one line.",
             ["todos"]),
        ],
    ),
}


@dataclass
class ReportResult:
    ok: bool
    path: str
    content: str
    report_type: str


# --- deterministic fact gathering (no model) ---------------------------------

_GENERATED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock"}


def _is_generated(path: str) -> bool:
    """True for build/generated files that shouldn't count as 'notable modules'."""
    name = path.rsplit("/", 1)[-1]
    return (
        name in _GENERATED_NAMES
        or name.endswith(".lock")
        or ".egg-info/" in path
        or "/dist/" in path
        or "/build/" in path
        or path.endswith(".min.js")
    )


def _trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n…[trimmed]"


def _read(root: Path, rel: str, limit: int = 2000) -> str:
    p = root / rel
    try:
        return _trim(p.read_text(encoding="utf-8", errors="replace"), limit) if p.is_file() else ""
    except OSError:
        return ""


def _first_present(root: Path, rels: list[str], limit: int = 2000) -> str:
    for rel in rels:
        got = _read(root, rel, limit)
        if got:
            return got
    return ""


def gather_facts(
    root: Path, index: RepoIndex | None, memory: Memory, belt: ToolBelt
) -> dict[str, str]:
    """Collect project facts deterministically — no model call. Absent sources yield ''.

    This is the system owning the ground truth: the model later only phrases these, so a
    report can never claim a commit, file, or number the project doesn't actually have.
    """
    facts: dict[str, str] = {}

    facts["overview"] = _first_present(root, ["README.md", "readme.md", "NEWTON.md"], 2000)
    facts["tree"] = project_tree(root)

    if index is not None:
        s = index.stats()
        facts["stats"] = f"{s['files']} files · {s['chunks']} chunks · {s['symbols']} symbols indexed."
        # Top files by chunk count — a proxy for the largest / most significant modules.
        # Generated/build files (lock files, dist bundles, egg-info) would otherwise
        # dominate and misrepresent the architecture, so they're filtered out.
        counts = Counter(c.path for c in index.chunks if not _is_generated(c.path))
        facts["modules"] = "\n".join(f"- {path} ({n} chunks)" for path, n in counts.most_common(12))
    else:
        facts["stats"] = ""
        facts["modules"] = ""

    # Git — recent activity. Empty (not an error) when the project isn't a git repo.
    git_raw = belt.run(cmd="git log --oneline -20")
    facts["git"] = _trim(git_raw.split("]", 1)[-1].strip(), 1500) if git_raw.startswith("[exit 0]") else ""

    # Mission-control notebook / docs, whichever exists.
    facts["progress"] = _first_present(root, ["mission-control/progress.md", "docs/STATUS.md"], 1800)
    facts["decisions"] = _first_present(root, ["mission-control/decisions.md", "docs/DECISIONS.md"], 1800)
    facts["phases"] = _first_present(
        root, ["mission-control/phases.md", "mission-control/development-plan.md", "docs/ROADMAP.md"], 1500
    )

    # Recent cross-session memory (first line of each entry).
    items = memory.all()[-8:]
    facts["memory"] = "\n".join(f"- {it.text.splitlines()[0][:160]}" for it in items if it.text.strip())

    # Outstanding markers in the code.
    todos: list[str] = []
    for needle in ("TODO", "FIXME"):
        hit = belt.grep(pattern=needle, path=".")
        if hit and "no matches" not in hit:
            todos.append(hit)
    facts["todos"] = _trim("\n".join(todos), 1200)

    return facts


def compose_report(
    spec: ReportSpec, project_name: str, model: str, sections: list[tuple[str, str]]
) -> str:
    """Stitch the per-section prose into the final markdown document (pure function)."""
    header = (
        f"# {spec.title} — {project_name}\n\n"
        f"*Generated by Newton · {date.today().isoformat()} · {model}*\n"
    )
    body = "\n\n".join(f"## {title}\n\n{content.strip()}" for title, content in sections)
    return header + "\n" + body + "\n"


# --- the conductor -----------------------------------------------------------

class ReportConductor:
    def __init__(self, settings: Settings, *, emit: Emit, approve: Approve) -> None:
        self.s = settings
        self.emit = emit
        self.approve = approve
        self.model = settings.agent_model
        self.belt = ToolBelt(settings.project_root)
        self.embedder = Embedder()
        self.memory = Memory(settings.project_root / ".newton" / "memory.jsonl", embedder=self.embedder)
        self.state = WorkingState(goal=GoalSpec(request=""))

    def _write_section(self, title: str, instruction: str, blocks: list[ContextBlock]) -> str:
        asm = ContextAssembler(self.state, budget_tokens=5000)
        a = asm.assemble(
            system=SYSTEM,
            stage_goal=f"Write the '{title}' section of the report.",
            instruction=instruction + " Output only the section body as markdown.",
            sources=blocks,
        )
        self.emit("context", {
            "stage": f"Report · {title}",
            "used": a.used_tokens,
            "budget": a.budget,
            "blocks": [(b.kind, b.label, b.tokens, b.pinned) for b in a.blocks],
            "dropped": [(b.kind, b.label, b.tokens) for b in a.dropped],
        })
        resp = complete(self.model, a.messages, temperature=0.2)
        return strip_leading_heading((resp.choices[0].message.content or "").strip())

    def run(self, report_type: str) -> ReportResult:
        report_type = (report_type or "status").strip().lower()
        spec = REPORT_SPECS.get(report_type)
        if spec is None:
            self.emit("halt", f"unknown report type '{report_type}' (choose: {', '.join(REPORT_SPECS)})")
            return ReportResult(False, "", "", report_type)

        # Gather ground truth once.
        self.emit("stage", "Gather")
        try:
            index: RepoIndex | None = RepoIndex(self.s.project_root, embedder=self.embedder).build()
            self.emit("note", f"Indexed {index.stats()['files']} files for the report.")
        except Exception as e:
            index = None
            self.emit("note", f"(repo index unavailable: {e})")
        facts = gather_facts(self.s.project_root, index, self.memory, self.belt)
        present = [k for k, v in facts.items() if v]
        self.emit("note", f"Gathered: {', '.join(present) or 'nothing'}")

        # Write each section, grounded only in its facts.
        sections: list[tuple[str, str]] = []
        for title, instruction, keys in spec.sections:
            self.emit("stage", f"Write · {title}")
            blocks = [ContextBlock("project", k, facts[k]) for k in keys if facts.get(k)]
            if not blocks:
                body = "_No information available for this section yet._"
            else:
                body = self._write_section(title, instruction, blocks) \
                    or "_No information available for this section yet._"
            sections.append((title, body))
            self.emit("section", {"title": title, "body": body})

        content = compose_report(spec, self.s.project_root.name, self.model, sections)
        rel = spec.filename

        # Human gate before writing to disk.
        self.emit("diff", {"file": rel, "old": _read(self.s.project_root, rel, 100_000), "new": content})
        if not self.approve("report", {"path": rel, "content": content}):
            self.emit("halt", "Report not saved (declined).")
            return ReportResult(False, rel, content, report_type)

        self.belt.write_file(path=rel, content=content)
        self.emit("report", {"path": rel, "type": report_type, "content": content})
        self.emit("note", f"Wrote {rel} ({len(content)} chars).")
        return ReportResult(True, rel, content, report_type)
