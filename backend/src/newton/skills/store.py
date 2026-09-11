"""Skills store — SKILL.md procedure files under `<project>/skills/`, retrieved by meaning.

A thin wrapper over RepoIndex (same as the wiki): the skills directory is indexed and
searched with hybrid BM25 + semantic, so the right procedure surfaces for a task. Unlike
the wiki, `search()` returns the WHOLE skill (a procedure only makes sense in full), and a
skill carries `SKILL.md` frontmatter (`name`, `description`) that names what it is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..index.embeddings import cosine
from ..index.store import tokenize

# A skill is selected by matching the task against its `name` + `description` — the SKILL.md
# "when to use" contract — not its whole body. Floors below which nothing is loaded (a wrong
# skill is worse than none, since it's pinned into the plan): tuned per scoring method.
_EMBED_FLOOR = 0.45
_TOKEN_FLOOR = 0.18
_STOP = {"a", "an", "the", "to", "of", "in", "on", "and", "or", "for", "it", "is", "add",
         "new", "that", "this", "with", "make", "change", "update", "so", "its"}


def _keywords(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in _STOP and len(t) > 2}


def build_skill_md(name: str, description: str, body, *, learned: bool = False) -> str:
    """Assemble a SKILL.md from a name, a 'when to use' description, and either a list of
    procedure steps or a freeform markdown body. `learned` tags a skill Newton wrote itself."""
    front = [f"name: {name}", f"description: {description}"]
    if learned:
        front.append("source: learned")
    head = "---\n" + "\n".join(front) + "\n---\n\n"
    if isinstance(body, (list, tuple)):
        text = ("## When to use\n" + description + "\n\n## Procedure\n"
                + "\n".join(f"{i}. {s}" for i, s in enumerate(body, 1)))
    else:
        text = str(body).strip()
    return head + text + "\n"


def parse_learned_skill(data) -> tuple[str, str, list[str]] | None:
    """Validate a model's skill-from-experience JSON → (name, description, steps), or None if it
    declined (`skill: false`) or the result is too thin to be a useful, reusable procedure."""
    if not isinstance(data, dict) or not data.get("skill"):
        return None
    name = str(data.get("name") or "").strip()
    desc = str(data.get("description") or "").strip()
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
    if not name or not desc or len(steps) < 2:
        return None
    return name, desc, steps


@dataclass
class SkillMeta:
    name: str
    description: str
    file: str


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading `---` frontmatter block into (dict, body). Tolerant: no frontmatter
    returns ({}, text). Only simple `key: value` lines are parsed (enough for SKILL.md)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, m.group(2).strip()


class Skills:
    def __init__(self, project_root: Path, embedder=None) -> None:
        self.dir = Path(project_root) / "skills"
        self.embedder = embedder

    # --- authoring -----------------------------------------------------

    def files(self) -> list[str]:
        return sorted(p.name for p in self.dir.glob("*.md")) if self.dir.is_dir() else []

    @staticmethod
    def _filename(name: str) -> str:
        name = name.strip()
        if not name.endswith(".md"):
            name = re.sub(r"[^\w-]+", "-", name.lower()).strip("-") + ".md"
        return name

    def read(self, name: str) -> str:
        p = self.dir / self._filename(name)
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def meta(self, name: str) -> SkillMeta:
        fm, _ = parse_frontmatter(self.read(name))
        fn = self._filename(name)
        return SkillMeta(name=fm.get("name") or fn[:-3], description=fm.get("description", ""), file=fn)

    def list(self) -> list[SkillMeta]:
        return [self.meta(f) for f in self.files()]

    def write(self, name: str, content: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / self._filename(name)
        p.write_text(content.rstrip() + "\n", encoding="utf-8")
        return p

    def would_duplicate(self, name: str, description: str) -> str | None:
        """The name of an existing skill this would duplicate — same file, or a highly
        overlapping 'when to use' contract — else None. Keeps auto-learned skills from piling up."""
        slug = self._filename(name)
        metas = self.list()
        for m in metas:
            if m.file == slug:
                return m.name
        nk = _keywords(f"{name} {description}")
        if not nk:
            return None
        for m in metas:
            mk = _keywords(f"{m.name} {m.description}")
            if mk and len(nk & mk) / len(nk | mk) >= 0.6:      # Jaccard on the "when to use" words
                return m.name
        return None

    # --- retrieval -----------------------------------------------------

    def search(self, query: str, k: int = 1) -> list[tuple[str, str]]:
        """The skill(s) most relevant to a task, as (filename, full skill text).

        Relevance is scored against each skill's `name` + `description` — the "when to use"
        contract, which discriminates far better than the whole body — using the embedder
        (cosine) when available, else keyword overlap. Nothing is returned below the floor:
        a wrong skill is worse than none because it's pinned into the plan. Whole skills are
        returned (a procedure is only useful in full)."""
        metas = self.list()
        if not metas:
            return []
        docs = [f"{m.name}. {m.description}" for m in metas]

        scored: list[tuple[float, SkillMeta]] = []
        floor = _EMBED_FLOOR
        if self.embedder is not None:
            try:
                if self.embedder.available():
                    qv = self.embedder.embed([query])[0]
                    dvs = self.embedder.embed(docs)
                    scored = [(cosine(qv, dv), m) for dv, m in zip(dvs, metas)]
            except Exception:
                scored = []
        if not scored:  # no embedder (or it failed) → keyword overlap
            floor = _TOKEN_FLOOR
            qk = _keywords(query)
            scored = [(len(qk & _keywords(d)) / (len(qk) or 1), m) for d, m in zip(docs, metas)]

        scored.sort(key=lambda x: x[0], reverse=True)
        return [(m.file, self.read(m.file)) for score, m in scored[:k] if score >= floor]
