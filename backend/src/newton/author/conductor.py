"""AuthorConductor — draft a grounded project deliverable from a topic.

Flow (one deliverable):
  Ground → retrieve project context aimed at the topic (index search + memory + wiki +
           overview), deterministically.
  Draft  → for each section in the doc's fixed skeleton, the model writes the body, given
           the topic, the grounding context, and every section drafted so far.
  Save   → compose the markdown and write it to docs/<type>-<slug>.md, behind an approval gate.

The doc types (PRD, architecture, brainstorm, design) differ only in their section list;
the mechanism is identical. The model does real generative work here (higher temperature),
but it is told to ground claims in the provided context and mark assumptions, never invent
files or APIs the project doesn't have.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..conductor.assembler import ContextAssembler, ContextBlock
from ..conductor.state import GoalSpec, WorkingState, strip_leading_heading
from ..config import Settings
from ..context import load_context
from ..index import RepoIndex
from ..index.embeddings import Embedder
from ..llm import complete
from ..memory import Memory
from ..wiki import Wiki

Emit = Callable[[str, Any], None]
Approve = Callable[[str, dict], bool]


@dataclass
class DocSpec:
    """A deliverable's fixed skeleton. `sections` is ordered; each is (title, instruction)."""

    title: str
    prefix: str          # filename prefix, e.g. "prd" → docs/prd-<slug>.md
    sections: list[tuple[str, str]]


AUTHOR_SPECS: dict[str, DocSpec] = {
    "prd": DocSpec(
        "Product Requirements", "prd",
        [
            ("Problem", "State the problem this addresses and who has it. Be specific."),
            ("Goals", "List the concrete outcomes this must achieve, as a bulleted list."),
            ("Non-Goals", "List what is explicitly out of scope, to prevent scope creep."),
            ("Requirements", "List concrete functional requirements. Where the project context "
             "shows relevant existing code, reference it; do not invent APIs that don't exist."),
            ("User Experience", "Describe the key user flows or interactions in plain language."),
            ("Success Metrics", "How will we know this worked? Give measurable signals."),
            ("Risks & Open Questions", "Call out risks, dependencies, and unknowns honestly."),
        ],
    ),
    "architecture": DocSpec(
        "Architecture Proposal", "architecture",
        [
            ("Context", "Summarize the current system and why this change is needed, grounded in "
             "the project context."),
            ("Options Considered", "Lay out 2-3 viable approaches, each with its tradeoffs."),
            ("Recommendation", "Recommend one option and justify it against the alternatives."),
            ("Components", "Describe the components/modules involved and their responsibilities."),
            ("Data & Flow", "Describe the data flow and key interfaces between components."),
            ("Risks & Tradeoffs", "State the honest tradeoffs of the recommendation and mitigations."),
        ],
    ),
    "brainstorm": DocSpec(
        "Brainstorm", "brainstorm",
        [
            ("Framing", "Restate the question and what a good answer would look like."),
            ("Ideas", "Generate a diverse set of concrete ideas as a bulleted list — quantity first."),
            ("Evaluation", "Weigh the most promising ideas against their tradeoffs."),
            ("Recommendation", "Recommend a direction and the immediate next steps to try it."),
        ],
    ),
    "design": DocSpec(
        "Design Doc", "design",
        [
            ("Overview", "Summarize what is being built and why, in a few sentences."),
            ("Goals & Non-Goals", "State the goals and what is explicitly out of scope."),
            ("Approach", "Describe the chosen approach at a high level, grounded in the codebase."),
            ("Details", "Go into the concrete design: modules, interfaces, data, edge cases."),
            ("Alternatives", "Note alternatives considered and why they were not chosen."),
            ("Risks", "Call out risks and how they'll be handled."),
        ],
    ),
}

SYSTEM = (
    "You are Newton, drafting a professional project document for the user. Ground every "
    "claim in the PROJECT CONTEXT you are given; be concrete and specific to this project. "
    "Do NOT invent files, APIs, numbers, or facts that the context doesn't support — where "
    "you must assume something, mark it as an assumption. Write only the section body as "
    "markdown: no heading, no preamble, no sign-off."
)


@dataclass
class DocResult:
    ok: bool
    path: str
    content: str
    doc_type: str


def _slug(topic: str) -> str:
    s = re.sub(r"[^\w-]+", "-", topic.lower()).strip("-")
    return (s or "untitled")[:60].strip("-")


def output_path(doc_type: str, topic: str) -> str:
    spec = AUTHOR_SPECS[doc_type]
    return f"docs/{spec.prefix}-{_slug(topic)}.md"


def compose_doc(spec: DocSpec, topic: str, model: str, sections: list[tuple[str, str]]) -> str:
    """Stitch the drafted sections into the final markdown document (pure function)."""
    header = (
        f"# {spec.title}: {topic}\n\n"
        f"*Drafted by Newton · {date.today().isoformat()} · {model}*\n"
    )
    body = "\n\n".join(f"## {title}\n\n{content.strip()}" for title, content in sections)
    return header + "\n" + body + "\n"


class AuthorConductor:
    def __init__(self, settings: Settings, *, emit: Emit, approve: Approve) -> None:
        self.s = settings
        self.emit = emit
        self.approve = approve
        self.model = settings.agent_model
        self.embedder = Embedder()
        self.memory = Memory(settings.project_root / ".newton" / "memory.jsonl", embedder=self.embedder)
        self.wiki = Wiki(settings.project_root, embedder=self.embedder)
        self.state = WorkingState(goal=GoalSpec(request=""))

    # --- deterministic grounding, aimed at the topic --------------------

    def _ground(self, topic: str) -> list[ContextBlock]:
        blocks: list[ContextBlock] = []
        overview = load_context(self.s.project_root, self.s.context_file)
        if overview:
            blocks.append(ContextBlock("project", "project overview", overview[:1800]))

        try:
            index = RepoIndex(self.s.project_root, embedder=self.embedder).build()
            for c, _score in index.search(topic, k=5):
                blocks.append(ContextBlock("code", f"{c.path}:{c.start} ({c.name})", f"```\n{c.text[:900]}\n```"))
        except Exception as e:
            self.emit("note", f"(repo index unavailable: {e})")

        try:
            for it in self.memory.recall(topic, k=2):
                blocks.append(ContextBlock("memory", "related past work", it.text[:600]))
        except Exception:
            pass
        try:
            for ref, text in self.wiki.search(topic, k=2):
                blocks.append(ContextBlock("wiki", ref, text[:600]))
        except Exception:
            pass
        return blocks

    def _write_section(self, doc_title: str, topic: str, title: str, instruction: str,
                       grounding: list[ContextBlock], draft_so_far: str) -> str:
        sources = list(grounding)
        if draft_so_far.strip():
            sources.append(ContextBlock("state", "the document so far", draft_so_far[-3000:]))
        asm = ContextAssembler(self.state, budget_tokens=5000)
        a = asm.assemble(
            system=SYSTEM,
            stage_goal=f"Draft the '{title}' section of a {doc_title} about: {topic}",
            instruction=instruction + " Output only the section body as markdown.",
            sources=sources,
        )
        self.emit("context", {
            "stage": f"Draft · {title}",
            "used": a.used_tokens, "budget": a.budget,
            "blocks": [(b.kind, b.label, b.tokens, b.pinned) for b in a.blocks],
            "dropped": [(b.kind, b.label, b.tokens) for b in a.dropped],
        })
        resp = complete(self.model, a.messages, temperature=0.4)
        return strip_leading_heading((resp.choices[0].message.content or "").strip())

    # --- run ------------------------------------------------------------

    def run(self, doc_type: str, topic: str) -> DocResult:
        doc_type = (doc_type or "prd").strip().lower()
        spec = AUTHOR_SPECS.get(doc_type)
        if spec is None:
            self.emit("halt", f"unknown doc type '{doc_type}' (choose: {', '.join(AUTHOR_SPECS)})")
            return DocResult(False, "", "", doc_type)
        topic = (topic or "").strip()
        if not topic:
            self.emit("halt", "a topic is required (what should the document be about?)")
            return DocResult(False, "", "", doc_type)

        self.emit("stage", "Ground")
        grounding = self._ground(topic)
        self.emit("note", f"Grounded on {len(grounding)} context source(s) for: {topic}")

        sections: list[tuple[str, str]] = []
        draft_so_far = ""
        for title, instruction in spec.sections:
            self.emit("stage", f"Draft · {title}")
            body = self._write_section(spec.title, topic, title, instruction, grounding, draft_so_far) \
                or "_(to be completed)_"
            sections.append((title, body))
            draft_so_far += f"\n## {title}\n{body}\n"
            self.emit("section", {"title": title, "body": body})

        content = compose_doc(spec, topic, self.model, sections)
        rel = output_path(doc_type, topic)

        self.emit("diff", {"file": rel, "old": "", "new": content})
        if not self.approve("author", {"path": rel, "content": content}):
            self.emit("halt", "Document not saved (declined).")
            return DocResult(False, rel, content, doc_type)

        dest = self.s.project_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        self.emit("report", {"path": rel, "type": doc_type, "content": content})
        self.emit("note", f"Wrote {rel} ({len(content)} chars).")
        return DocResult(True, rel, content, doc_type)
