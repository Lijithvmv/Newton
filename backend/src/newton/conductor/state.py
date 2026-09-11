"""WorkingState — the conductor's score. The large context the model never sees whole.

This is the heart of the mechanism. Autonomous agents let raw conversation history grow
until it overflows a small window; Newton never does. After every stage, results are
*distilled* into this structured ledger — facts, decisions, the known state of touched
files, findings — and the window is swept clean. The ledger can grow large on disk; what
the model receives each turn is only `summary()`, packed to a token budget.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def est_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token). Good enough for budgeting."""
    return max(1, len(text) // 4)


def extract_json(text: str) -> Any | None:
    """Pull the first balanced JSON value out of model output, tolerant of surrounding prose
    and ```json fences. Weak models wrap JSON in chatter; this digs it out."""
    text = text.strip()
    # Strip a leading fence if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # Otherwise scan for the first balanced {...} or [...].
    for opener, closer in (("{", "}"), ("[", "]")):
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == opener:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == closer and depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    chunk = text[start : i + 1]
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        repaired = _repair_json(chunk)   # weak models leave trailing commas etc.
                        if repaired is not None:
                            return repaired
                        start = -1
    return None


def _repair_json(chunk: str) -> Any | None:
    """Best-effort repair of almost-JSON from a weak model: strip trailing commas and
    convert Python literals (True/False/None). Returns the parsed value or None."""
    fixed = re.sub(r",(\s*[}\]])", r"\1", chunk)                 # trailing commas
    fixed = re.sub(r"\bTrue\b", "true", fixed)
    fixed = re.sub(r"\bFalse\b", "false", fixed)
    fixed = re.sub(r"\bNone\b", "null", fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def guess_target_file(request: str, tree: str = "") -> str:
    """Deterministically pull a filename from the request — the safety net when the model
    fails to produce a target_file. Prefers a filename that appears in the project tree."""
    candidates = re.findall(r"\b([\w./-]+\.[A-Za-z0-9]{1,6})\b", request)
    if not candidates:
        return ""
    if tree:
        for c in candidates:                                    # prefer one that really exists
            base = c.rsplit("/", 1)[-1]
            if base in tree:
                return c
    return candidates[0]


def strip_leading_heading(body: str) -> str:
    """Drop a leading markdown heading the model added despite being told not to, so the
    document composer's own '## <title>' stays the single heading for a section. Only the
    first line is considered; genuine sub-headings deeper in the body are preserved."""
    lines = body.lstrip().splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        rest = lines[1:]
        while rest and not rest[0].strip():
            rest.pop(0)
        return "\n".join(rest).strip()
    return body.strip()


def extract_code(text: str) -> str:
    """Pull a whole-file rewrite out of model output. Weak models emit code far more
    reliably inside a ``` fence than as a JSON-escaped string, so prefer the fence.

    The opening fence may carry ANY language tag (``python``, ``html``, ``javascript``,
    ``css``, ``…`` or none). Matching only a fixed set of tags meant an ``html``/``js``/``css``
    fence failed the pattern entirely and the raw ```` ``` ```` lines survived into the written
    file, corrupting non-Python output; ``[^\n`]*`` accepts whatever tag the model used."""
    m = re.search(r"```[^\n`]*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).rstrip("\n") + "\n"
    return text.strip() + "\n"


@dataclass
class GoalSpec:
    """The north star. `request` is what the user asked; `intent` is the structured
    reading the Understand stage produces (target files, the change, the doc to update)."""
    request: str
    intent: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkingState:
    """The unbounded ledger. Everything the conductor knows, kept off the model's window
    except via `summary()`."""
    goal: GoalSpec
    facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    # Current known content of files the task has touched, keyed by relative path.
    file_states: dict[str, str] = field(default_factory=dict)
    # Named artifacts a stage produced for a later stage (the plan, etc.).
    artifacts: dict[str, Any] = field(default_factory=dict)
    # A short append-only trail of what each stage concluded (for the Remember step + audit).
    trail: list[str] = field(default_factory=list)

    # --- writers (stages distill into these) ---------------------------

    def add_fact(self, fact: str) -> None:
        if fact and fact not in self.facts:
            self.facts.append(fact)

    def add_decision(self, d: str) -> None:
        if d and d not in self.decisions:
            self.decisions.append(d)

    def add_finding(self, f: str) -> None:
        if f and f not in self.findings:
            self.findings.append(f)

    def set_file(self, path: str, content: str) -> None:
        self.file_states[path] = content

    def note(self, line: str) -> None:
        self.trail.append(line)

    # --- the only thing the model ever sees of state -------------------

    def summary(self, budget_tokens: int = 500) -> str:
        """A compact, budget-limited digest injected into the window. NOT the full state —
        just what a downstream stage needs to stay on the rails."""
        lines: list[str] = []
        if self.goal.intent:
            lines.append("INTENT: " + json.dumps(self.goal.intent, ensure_ascii=False))
        if self.decisions:
            lines.append("DECISIONS:")
            lines += [f"  - {d}" for d in self.decisions[-6:]]
        if self.facts:
            lines.append("ESTABLISHED:")
            lines += [f"  - {f}" for f in self.facts[-8:]]
        if self.findings:
            lines.append("FINDINGS:")
            lines += [f"  - {f}" for f in self.findings[-6:]]
        if self.file_states:
            lines.append("FILES TOUCHED: " + ", ".join(self.file_states))
        out = "\n".join(lines) or "(nothing established yet)"
        # Trim from the top if over budget, keeping the most recent.
        while est_tokens(out) > budget_tokens and len(lines) > 1:
            lines.pop(0)
            out = "\n".join(lines)
        return out

    # --- persistence (the ledger survives across sessions) -------------

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(_encode(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> WorkingState:
        raw = json.loads(path.read_text(encoding="utf-8"))
        goal = GoalSpec(**raw.pop("goal"))
        return cls(goal=goal, **raw)


def _encode(state: WorkingState) -> dict[str, Any]:
    d = asdict(state)
    return d
