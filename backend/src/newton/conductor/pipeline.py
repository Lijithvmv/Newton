"""The Conductor loop and the fixed six-stage skeleton.

Understand -> Retrieve -> Plan -> Edit -> Verify -> Remember. The skeleton is system-owned
(reliable); the small model only fills bounded slots inside each stage. Every model call
goes through the assembler so the window is freshly aimed and budget-capped, and every
consequential stage is checked by a deterministic gate before the next begins.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..context import load_context, project_tree
from ..index import RepoIndex
from ..index.embeddings import Embedder
from ..llm import complete
from ..memory import Memory
from ..skills import Skills, build_skill_md, parse_learned_skill
from ..tools import ToolBelt
from ..wiki import Wiki
from .assembler import Assembled, ContextAssembler, ContextBlock
from .state import GoalSpec, WorkingState, extract_code, extract_json, guess_target_file
from .verify import (
    build_check_script,
    edit_applies,
    module_import_name,
    python_parses,
    run_succeeds,
)

Emit = Callable[[str, Any], None]
Approve = Callable[[str, dict], bool]

import re


def derive_expect(request: str) -> str:
    """Pull an expected output value straight from the user's request, so verification
    doesn't depend on the weak model authoring an honest test. Matches an explicit JSON
    example, a quoted output, an uppercase example (e.g. HELLO WORLD), or 'should be 6'."""
    m = re.search(r'(\{[^{}]*\})', request)                                   # JSON example
    if m:
        return m.group(1)
    m = re.search(r'(?:print|output|show|display)s?\s+["\']([^"\']+)["\']', request, re.I)
    if m:                                                                     # quoted output
        return m.group(1)
    m = re.search(r'(?:e\.g\.|like|such as|prints?|outputs?|shows?)\s+([A-Z][A-Z0-9](?:[\w -]*[A-Z0-9])?)', request)
    if m:                                                                     # UPPERCASE example
        return m.group(1).strip()
    m = re.search(r'should (?:be|print|return|output|equal|show)\s+([^\s.,;]+)', request, re.I)
    if m:
        return m.group(1)
    return ""


def cli_input_samples(request: str) -> list[str]:
    """Concrete positional inputs a CLI feature can be exercised with — harvested ONLY from
    an explicit function-call example in the request (e.g. `greet('world')` → ['world']), so
    we never mistake an expected *output* for an input. Empty when nothing is inferable."""
    m = re.search(r'\b[A-Za-z_]\w*\(([^()]+)\)', request)
    if not m:
        return []
    return [a.strip().strip("'\"") for a in m.group(1).split(",") if a.strip()]


LEARN_INSTRUCTION = (
    "Looking back at the task just completed, is there a REUSABLE coding pattern or convention "
    "worth recording for future tasks in this project — something general, not specific to this "
    "one task? Output JSON: {\"reusable\": true/false, \"title\": \"<short kebab-case title>\", "
    "\"content\": \"<2-4 lines of markdown: the general principle, no task-specific code>\"}. "
    "Set reusable=false for routine one-off changes. Output ONLY the JSON."
)

# Skills-from-experience: unlike the wiki (which records KNOWLEDGE), this records a PROCEDURE —
# the repeatable steps that would help on a future task of the same kind.
SKILL_LEARN_INSTRUCTION = (
    "Was this task an instance of a REPEATABLE procedure — a kind of task you'll see again "
    "(e.g. 'adding a CLI flag to a script', 'adding a REST endpoint')? If so, output a reusable "
    "skill as JSON: {\"skill\": true, \"name\": \"<short kebab-case name>\", "
    "\"description\": \"<one line naming WHEN to use this — the trigger>\", "
    "\"steps\": [\"<generalized step>\", \"...\"]}. Write the general PROCEDURE — generalize away "
    "this task's specific file and symbol names. 3 to 6 steps. If it was a genuine one-off with no "
    "reusable procedure, output {\"skill\": false}. Output ONLY the JSON."
)


def parse_review(data) -> tuple[bool, list[str]]:
    """(approved, issues) from a reviewer response. Concrete issues mean 'not approved and
    fix these'; an approval flag with no concrete issues means pass (we never block on vibes)."""
    if not isinstance(data, dict):
        return (True, [])
    issues = [str(i).strip() for i in (data.get("issues") or []) if str(i).strip()]
    if issues:
        return (False, issues)
    return (True, [])


def pattern_to_page(data, existing_pages: list[str]) -> tuple[bool, str, str]:
    """Decide whether a model 'learn' response becomes a new wiki page. Returns
    (should_write, slug, markdown). Skips non-reusable, empty, or duplicate-title pages."""
    from ..wiki import Wiki
    if not isinstance(data, dict) or not data.get("reusable"):
        return (False, "", "")
    content = str(data.get("content", "")).strip()
    if not content:
        return (False, "", "")
    title = str(data.get("title") or "pattern").strip()
    slug = Wiki._filename(title)
    if slug in existing_pages:
        return (False, slug, "")            # already known — don't duplicate
    return (True, slug, f"# {title}\n\n{content}\n")


_SYMBOL_STOP = {"print", "range", "len", "int", "str", "float", "open", "list", "dict",
                "set", "tuple", "input", "inputs", "output", "outputs", "type", "the", "a",
                "an", "e", "g", "i", "component", "components"}

_RENAME = re.compile(
    r'\b(?:rename|change|replace)\s+(?:the\s+)?(?:function|method|class|fn|def)?\s*'
    r'`?([A-Za-z_]\w*)`?\s+(?:to|with|into|→|->)\s+`?([A-Za-z_]\w*)`?', re.I)


def rename_targets(request: str) -> tuple[set[str], set[str]]:
    """(old_names, new_names) for 'rename/change/replace X to Y' phrasings. After a rename the
    OLD symbol must be gone and the NEW one present — the opposite of what naive extraction
    would assert — so verification uses this to drop olds and require news."""
    drops: set[str] = set()
    adds: set[str] = set()
    for m in _RENAME.finditer(request):
        drops.add(m.group(1))
        adds.add(m.group(2))
    return drops, adds


def extract_symbols(request: str) -> list[str]:
    """Function/class names the task expects the module to define. Conservative BY DESIGN — only
    HIGH-CONFIDENCE signals, so verbose task prose doesn't turn every word-before-a-paren into a
    required symbol. (Real bug this fixes: blueprint text like 'scene inputs (title, ...)' or
    'persistence (SQLite)' was mined as required symbols `inputs`/`SQLite` and failed correct code
    forever.) Rename-aware: for 'rename X to Y' the old name X is dropped and the new name Y required."""
    names: set[str] = set()
    # 1) explicit declaration cue — "function/def/method/class NAME" (backticks optional), trusted.
    for m in re.finditer(r'\b(?:function|def|method|class)\s+`?([A-Za-z_]\w*)`?', request, re.I):
        if len(m.group(1)) > 1:
            names.add(m.group(1))
    # 2) backticked call `name(...)` — unambiguously code, trusted (keeps CamelCase class calls).
    for m in re.finditer(r'`\s*([A-Za-z_]\w*)\s*\(', request):
        if len(m.group(1)) > 1:
            names.add(m.group(1))
    # 3) bare call name( — ONLY with NO space before '(' (a real call site, not a prose parenthetical
    #    like "inputs (title, ...)") AND a function-shaped lower/snake name (classes come from the
    #    'class' cue above; proper-noun libraries like SQLite are excluded). Stop-listed generic nouns.
    for m in re.finditer(r'\b([a-z_]\w*)\(', request):
        n = m.group(1)
        if len(n) > 1 and n not in _SYMBOL_STOP:
            names.add(n)
    drops, adds = rename_targets(request)
    names = (names - drops) | adds
    return sorted(names)


def _literal(tok: str) -> str:
    """Format an expected value as a Python literal for an assertion (number or string).
    Strips quotes already present in the request so `'hello-world'` becomes the string
    literal 'hello-world', not the double-quoted "'hello-world'"."""
    for cast in (int, float):
        try:
            cast(tok)
            return tok
        except ValueError:
            continue
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\"":
        tok = tok[1:-1]
    return repr(tok)

_BEHAVIOUR = re.compile(
    r'([A-Za-z_]\w*\([^()]*\))\s*'
    r'(?:==|=|->|→|should\s+(?:be|return|equal|print|output|give)|returns?|prints?|outputs?|gives?|is)\s*'
    r'(\'[^\']*\'|"[^"]*"|[^\s.,;]+)', re.I)   # a quoted string (may contain spaces) or a token

def extract_behaviours(request: str) -> list[tuple[str, str]]:
    """Concrete (call, expected) examples in the request, e.g. 'add(2, 3) should be 5'."""
    out: list[tuple[str, str]] = []
    for m in _BEHAVIOUR.finditer(request):
        out.append((m.group(1).strip(), _literal(m.group(2).strip())))
    return out[:5]

SYSTEM = (
    "You are Newton, a local coding assistant driven by a staged Conductor. You are given "
    "ONE bounded step at a time with exactly the context it needs. Do only the step asked. "
    "When a step asks for JSON, output ONLY the JSON object — no prose, no code fences."
)


def edit_strategy(is_code: bool, current: str, whole_file_max: int) -> str:
    """Pick 'whole' (regenerate the file) or 'surgical' (old→new splice) for an edit.

    A new/empty file can never be edited surgically — there is no `old` snippet to match —
    so it is always regenerated whole. Small code files also regenerate more reliably whole
    (weak models produce dedented blocks with surgical splicing). Existing docs stay surgical.
    """
    if not current.strip() or (is_code and len(current) <= whole_file_max):
        return "whole"
    return "surgical"


@dataclass
class ConductorResult:
    ok: bool
    answer: str
    stages: list[str] = field(default_factory=list)
    state: WorkingState | None = None


class Conductor:
    def __init__(self, settings: Settings, *, emit: Emit, approve: Approve,
                 learn_skills: bool = True, auto_approve: bool = True) -> None:
        self.s = settings
        self.belt = ToolBelt(settings.project_root)
        self.emit = emit
        self.approve = approve
        # Whether this run is unattended. A human-attended run (auto_approve=False) means the operator
        # reviewed and approved the work, so its distilled memory is written with higher trust
        # (`user`) than a fully-autonomous run's (`agent`). See memory provenance.
        self.auto_approve = auto_approve
        # Whether a successful task may distill a reusable skill. Off for Build sub-tasks so a
        # project's many small file-tasks don't each spawn a skill.
        self.learn_skills = learn_skills
        self.model = settings.agent_model
        self.state: WorkingState = WorkingState(goal=GoalSpec(request=""))
        self.index: RepoIndex | None = None
        self.embedder = Embedder()        # shared by the index, memory, wiki, and skills
        self.memory = Memory(settings.project_root / ".newton" / "memory.jsonl", embedder=self.embedder)
        self.wiki = Wiki(settings.project_root, embedder=self.embedder)
        self.skills = Skills(settings.project_root, embedder=self.embedder)

    # --- model call routed through the assembler -----------------------

    def _ask(self, stage: str, stage_goal: str, instruction: str,
             sources: list[ContextBlock], *, temperature: float = 0.1) -> str:
        asm = ContextAssembler(self.state, budget_tokens=self.s_budget)
        a: Assembled = asm.assemble(
            system=SYSTEM, stage_goal=stage_goal, instruction=instruction, sources=sources
        )
        # Surface the exact window composition — this is the Context Inspector's data.
        self.emit("context", {
            "stage": stage,
            "used": a.used_tokens,
            "budget": a.budget,
            "blocks": [(b.kind, b.label, b.tokens, b.pinned) for b in a.blocks],
            "dropped": [(b.kind, b.label, b.tokens) for b in a.dropped],
        })
        resp = complete(self.model, a.messages, temperature=temperature)
        return (resp.choices[0].message.content or "").strip()

    def _ask_json(self, stage: str, stage_goal: str, instruction: str,
                  sources: list[ContextBlock], *, require: tuple[str, ...] = (),
                  attempts: int = 2) -> dict | None:
        """A model call that must return JSON with the required keys. Weak models fail this
        intermittently, so on a miss we retry once with a corrective nudge before giving up —
        turning a flaky one-shot into a reliable stage."""
        nudge = ""
        data = None
        for attempt in range(1, attempts + 1):
            raw = self._ask(stage, stage_goal, instruction + nudge, sources)
            data = extract_json(raw)
            if isinstance(data, dict) and all(data.get(k) for k in require):
                return data
            missing = [k for k in require if not (isinstance(data, dict) and data.get(k))]
            if attempt < attempts:
                self.emit("note", f"{stage}: output wasn't valid JSON"
                          + (f" (missing {', '.join(missing)})" if missing else "") + " — retrying.")
                nudge = ("\n\nIMPORTANT: your previous reply could not be parsed as the required "
                         f"JSON object with keys {list(require)}"
                         + (f" (missing: {missing})" if missing else "")
                         + ". Reply with ONLY that JSON object and nothing else.")
        return data if isinstance(data, dict) else None

    s_budget = 6000

    # --- weak-model compensation: resolve an approximate path ----------

    def _resolve(self, rel: str) -> str:
        """Map a path the model guessed to a real file in the project.

        Small models routinely hallucinate a prefix (e.g. `newton/scripts/x.py` for
        `scripts/x.py`). Rather than fail, the system resolves it deterministically by
        matching the basename against the real tree — compensation, not trust.
        """
        if not rel:
            return rel
        root = self.s.project_root
        if (root / rel).is_file():
            return rel
        base = Path(rel).name
        skip = {".venv", "node_modules", "__pycache__", ".git", "vendor", ".pytest_cache"}
        matches = [
            p for p in root.rglob(base)
            if p.is_file() and not any(part in skip for part in p.relative_to(root).parts)
        ]
        if len(matches) == 1:
            resolved = str(matches[0].relative_to(root)).replace("\\", "/")
            if resolved != rel:
                self.emit("note", f"Resolved '{rel}' → '{resolved}'")
            return resolved
        return rel  # ambiguous or absent; let the caller's gate report it

    # --- the run loop --------------------------------------------------

    MAX_CORRECTIONS = 2  # Edit↔Verify cycles before giving up
    REVIEW = os.environ.get("NEWTON_REVIEW", "1") != "0"  # independent-review stage (on by default)

    def run(self, request: str) -> ConductorResult:
        self.state = WorkingState(goal=GoalSpec(request=request))
        done: list[str] = []

        # Build whole-repo context once per run so every stage can aim at the codebase.
        try:
            self.index = RepoIndex(self.s.project_root, embedder=self.embedder).build()
            s = self.index.stats()
            sem = " · semantic" if (self.index.embedder and self.index.embedder.available()) else ""
            self.emit("note", f"Indexed repo: {s['files']} files · {s['chunks']} chunks · {s['symbols']} symbols{sem}")
        except Exception as e:
            self.index = None
            self.emit("note", f"(repo index unavailable: {e})")

        # Linear stages up to the edit.
        for name, fn in (("Understand", self._understand), ("Retrieve", self._retrieve),
                         ("Plan", self._plan)):
            self.emit("stage", name)
            ok, msg = fn()
            done.append(name)
            if not ok:
                self.emit("halt", f"{name}: {msg}")
                return ConductorResult(False, f"Stopped at {name}: {msg}", done, self.state)

        # Edit ↔ Verify correction loop — the self-correcting core. A failed verify sends
        # Edit back with the error as fresh context; originals are restored first so a bad
        # edit never compounds. This is how a weak model reaches a correct end result.
        targets = self._edit_targets()
        originals = {rel: self._read_or_empty(rel) for rel, *_ in targets}
        verified = False
        for cycle in range(1, self.MAX_CORRECTIONS + 1):
            if cycle > 1:
                for rel, content in originals.items():   # restore before re-editing
                    self.belt.write_file(path=rel, content=content)
                    self.state.set_file(rel, content)
            self.emit("stage", "Edit" + (f" · correction {cycle-1}" if cycle > 1 else ""))
            ok, msg = self._edit()
            if not ok:
                self.emit("halt", f"Edit: {msg}")
                return ConductorResult(False, f"Stopped at Edit: {msg}", done + ["Edit"], self.state)

            self.emit("stage", "Verify")
            ok, msg = self._verify()
            done += ["Edit", "Verify"]
            if ok:
                verified = True
                break
            self.state.add_finding(f"Verify attempt {cycle} failed: {msg}")
            if cycle < self.MAX_CORRECTIONS:
                self.emit("note", f"Verify failed — sending Edit back with the error (cycle {cycle+1}).")

        if not verified:
            self.emit("halt", "Verify: could not satisfy the check after corrections")
            return ConductorResult(False, "Stopped at Verify after corrections.", done, self.state)

        # Independent review — a strict second opinion on the verified change. It can only
        # improve or keep the result: on issues it makes ONE fix attempt (re-verified), and
        # if that fix regresses, it reverts to the earlier verified-good version. Never halts.
        if self.REVIEW:
            self.emit("stage", "Review")
            approved, issues = self._review()
            done.append("Review")
            if not approved:
                good = {rel: self.state.file_states.get(rel, "") for rel, *_ in targets}
                self.state.add_finding("Address these review issues: " + "; ".join(issues))
                for rel, content in originals.items():        # re-edit from the original
                    self.belt.write_file(path=rel, content=content)
                    self.state.set_file(rel, content)
                self.emit("stage", "Edit · review fix")
                ok, _ = self._edit()
                if ok:
                    self.emit("stage", "Verify")
                    ok, _ = self._verify()
                if ok:
                    self.emit("note", "Applied the review fix (re-verified).")
                else:
                    for rel, content in good.items():         # fix regressed → revert
                        self.belt.write_file(path=rel, content=content)
                        self.state.set_file(rel, content)
                    self.emit("note", "Review fix didn't verify — kept the earlier verified version.")

        self.emit("stage", "Remember")
        self._remember()
        done.append("Remember")
        return ConductorResult(True, self.state.trail[-1] if self.state.trail else "Done.", done, self.state)

    def _read_or_empty(self, rel: str) -> str:
        try:
            return self.belt.read_file(path=rel)
        except Exception:
            return ""

    def _edit_targets(self) -> list[tuple[str, str, bool]]:
        intent = self.state.goal.intent
        t = []
        if intent.get("target_file"):
            t.append((intent["target_file"], intent.get("change", ""), True))
        # Additional code files a coordinated change must touch together (usually none;
        # populated only for genuine multi-file tasks). Edited after the primary target.
        for entry in intent.get("also_edit", []):
            t.append((entry["file"], entry.get("change", ""), True))
        if intent.get("doc_file"):
            t.append((intent["doc_file"], intent.get("doc_change", ""), False))
        return t

    # --- STAGE 1: Understand -------------------------------------------

    def _understand(self) -> tuple[bool, str]:
        tree = project_tree(self.s.project_root)
        proj = load_context(self.s.project_root, self.s.context_file)
        instruction = (
            "Read the user's request and produce a JSON object describing the work:\n"
            '{"target_file": "<path to the PRIMARY code file to change>",\n'
            ' "change": "<one sentence: what to change in it>",\n'
            ' "also_edit": [{"file": "<another EXISTING code file that must change together>", '
            '"change": "<what to change there>"}],\n'
            ' "doc_file": "<path to the docs/markdown file to update, or "">",\n'
            ' "doc_change": "<one sentence: how to update the docs, or "">",\n'
            ' "verify_args": "<ONLY flags the script itself defines, e.g. --json ; never pass '
            'input text as an argument; "" if none>",\n'
            ' "verify_expect": "<the EXACT output substring that proves correctness, including '
            'the concrete value — e.g. for a 3-word sample expect \\"count\\": 3, not just count>"}\n'
            "Use `also_edit` ONLY when the change genuinely spans multiple existing code files "
            "(e.g. a renamed function's callers must update). For the common single-file task, "
            "make it []. Newton verifies by running the target file itself — first with no args (the "
            "existing behaviour must still work), then with verify_args. Do NOT write a shell "
            "command or reference other tools.\n"
            f"\nUser request:\n{self.state.goal.request}\n\nOutput ONLY the JSON."
        )
        intent = self._ask_json("Understand", "Turn the request into a structured intent.",
                                instruction, [
                                    ContextBlock("project", self.s.context_file, proj),
                                    ContextBlock("code", "project tree", tree),
                                ], require=("target_file",))
        if not isinstance(intent, dict):
            intent = {}
        # Safety net: if the model still didn't name a target file, extract one from the
        # request itself so a well-formed task never dies on a bad JSON roll.
        if not intent.get("target_file"):
            guess = guess_target_file(self.state.goal.request, tree)
            if not guess:
                return False, "could not extract a target_file from the request"
            self.emit("note", f"Model gave no target_file — inferred '{guess}' from the request.")
            intent["target_file"] = guess
            intent.setdefault("change", self.state.goal.request)
        # Resolve approximate paths to real files before any downstream stage uses them.
        intent["target_file"] = self._resolve(intent.get("target_file", ""))
        if intent.get("doc_file"):
            resolved_doc = self._resolve(intent["doc_file"])
            # Drop a hallucinated doc target that isn't a real file (this slice edits
            # existing files; creating new docs is a later feature).
            if not (self.s.project_root / resolved_doc).is_file():
                self.emit("note", f"Ignoring doc target '{intent['doc_file']}' — not an existing file.")
                resolved_doc = ""
            intent["doc_file"] = resolved_doc
        # Additional coordinated code files (multi-file edits). Conservative on purpose: resolve
        # each, keep only EXISTING files (creating new files is the Project Conductor's job),
        # drop the primary/doc and duplicates, and cap the fan-out. [] for the single-file task.
        resolved_also: list[dict[str, str]] = []
        seen = {intent["target_file"], intent.get("doc_file") or ""}
        raw_also = intent.get("also_edit")
        if isinstance(raw_also, list):
            for entry in raw_also[:3]:
                if not isinstance(entry, dict) or not entry.get("file"):
                    continue
                f = self._resolve(str(entry["file"]))
                if f and f not in seen and (self.s.project_root / f).is_file():
                    resolved_also.append({"file": f, "change": str(entry.get("change", ""))})
                    seen.add(f)
        intent["also_edit"] = resolved_also
        # Sanitize the verification contract — the model does NOT get to author the test.
        # Honor a flag only if it literally appears in the request; derive the expected
        # value from the request itself, falling back to the model's guess.
        req = self.state.goal.request
        kept = [t for t in str(intent.get("verify_args", "")).split()
                if not t.startswith("-") or t in req]
        intent["verify_args"] = " ".join(kept)
        derived = derive_expect(req)
        if derived:
            intent["verify_expect"] = derived
        self.state.goal.intent = intent
        self.state.add_fact(f"Target file is {intent['target_file']}: {intent.get('change','')}")
        for entry in intent.get("also_edit", []):
            self.state.add_fact(f"Also edit {entry['file']}: {entry.get('change','')}")
        if intent.get("doc_file"):
            self.state.add_fact(f"Docs to update: {intent['doc_file']} — {intent.get('doc_change','')}")
        also_n = len(intent.get("also_edit", []))
        self.emit("note", f"Understood → {intent.get('target_file')}"
                  + (f" (+{also_n} coordinated file{'s' if also_n != 1 else ''})" if also_n else "")
                  + f" (+docs: {intent.get('doc_file') or 'none'})")
        self.state.note(f"Understood the request: {intent.get('change','')}")
        return True, "ok"

    # --- STAGE 2: Retrieve (deterministic) -----------------------------

    def _retrieve(self) -> tuple[bool, str]:
        intent = self.state.goal.intent
        pulled: list[str] = []          # real path keys into file_states
        new_files: set[str] = set()
        for key in ("target_file", "doc_file"):
            rel = intent.get(key)
            if not rel:
                continue
            try:
                content = self.belt.read_file(path=rel)
                self.state.set_file(rel, content)
                pulled.append(rel)
            except Exception:
                # A missing target is a NEW file to create (build-from-scratch), not an error.
                if key == "target_file":
                    self.state.set_file(rel, "")
                    self.state.add_fact(f"{rel} does not exist yet — create it from scratch.")
                    pulled.append(rel)
                    new_files.add(rel)
        # Coordinated files (multi-file tasks) are existing files — read them into context too,
        # so Plan and Edit see the code they must change alongside the primary target.
        for entry in intent.get("also_edit", []):
            rel = entry["file"]
            if rel in self.state.file_states:
                continue
            try:
                self.state.set_file(rel, self.belt.read_file(path=rel))
                pulled.append(rel)
            except Exception:
                self.state.set_file(rel, "")
        # Whole-repo context: search across the entire codebase for chunks relevant to the
        # goal (not just the named file), and trace who depends on the file we'll edit.
        related: list[tuple[str, str, str]] = []
        target = intent.get("target_file", "")
        if self.index:
            query = f"{self.state.goal.request} {intent.get('change','')}"
            # Personalize centrality toward the file we're about to edit: retrieval leans to its
            # neighbourhood (its callers and dependencies) over unrelated but central files.
            focus = [target] if target else None
            for c, _score in self.index.search(query, k=6, focus=focus):
                if c.path != target and c.path != intent.get("doc_file"):
                    related.append((c.ref, c.name, c.text))
                if len(related) >= 4:
                    break
            deps = self.index.dependents(target)
            if deps:
                self.state.add_fact(f"Files importing {target}: {', '.join(deps)} — keep them working")
                self.emit("note", f"Callers of {target}: {', '.join(deps)}")
        self.state.artifacts["related_chunks"] = related

        # Cross-session memory: recall past work by meaning AND by the files this task touches
        # (graph recall — "what have we done to these files before?").
        touch_files = [f for f in (intent.get("target_file"), intent.get("doc_file")) if f]
        touch_files += [str(e.get("file", "")) for e in (intent.get("also_edit") or []) if e.get("file")]
        recalls = self.memory.recall(self.state.goal.request, k=2, files=touch_files)
        if recalls:
            self.state.artifacts["recalled"] = [r.text for r in recalls]
            self.emit("note", f"Recalled {len(recalls)} related past task(s) from memory.")
            for r in recalls:
                self.state.add_fact(f"Past work (memory): {r.text.splitlines()[0][:160]}")

        # Wiki: pull curated project knowledge (patterns/conventions) relevant to this task.
        try:
            wiki_hits = self.wiki.search(f"{self.state.goal.request} {intent.get('change','')}", k=2)
        except Exception:
            wiki_hits = []
        if wiki_hits:
            self.state.artifacts["wiki"] = wiki_hits
            self.emit("note", f"Consulted the wiki: {', '.join(ref for ref, _ in wiki_hits)}")

        # Skills: load the single most relevant procedure playbook, injected whole into Plan.
        try:
            skill_hits = self.skills.search(f"{self.state.goal.request} {intent.get('change','')}", k=1)
        except Exception:
            skill_hits = []
        if skill_hits:
            self.state.artifacts["skills"] = skill_hits
            self.emit("note", f"Loaded skill: {', '.join(ref for ref, _ in skill_hits)}")

        shown = [f"{p} (new)" if p in new_files else p for p in pulled]
        self.emit("note", f"Retrieved: {', '.join(shown) or 'nothing'}"
                          + (f" · +{len(related)} related chunks from the repo" if related else ""))
        self.emit("context", {
            "stage": "Retrieve",
            "used": sum(len(c) // 4 for c in self.state.file_states.values()) + sum(len(t) // 4 for _, _, t in related),
            "budget": self.s_budget,
            "blocks": [("code", p, len(self.state.file_states[p]) // 4, False) for p in pulled]
                      + [("code", f"related · {ref}", len(t) // 4, False) for ref, _, t in related],
            "dropped": [],
        })
        self.state.note(f"Read {len(pulled)} file(s) + {len(related)} related chunks.")
        return True, "ok"

    def _related_sources(self) -> list[ContextBlock]:
        """Repo chunks the index found relevant — offered to Plan/Edit within the budget."""
        return [ContextBlock("code", f"related · {ref} ({name})", f"```\n{text}\n```")
                for ref, name, text in self.state.artifacts.get("related_chunks", [])]

    # --- STAGE 3: Plan -------------------------------------------------

    def _plan(self) -> tuple[bool, str]:
        srcs = [ContextBlock("code", p, f"```\n{c}\n```") for p, c in self.state.file_states.items()]
        srcs += self._related_sources()  # whole-repo context, budget-packed by the assembler
        srcs += [ContextBlock("memory", f"past task {i + 1}", t)
                 for i, t in enumerate(self.state.artifacts.get("recalled", []))]
        srcs += [ContextBlock("wiki", ref, text)
                 for ref, text in self.state.artifacts.get("wiki", [])]
        # A relevant procedure playbook, pinned so it always reaches the planner.
        skill_srcs = [ContextBlock("skill", ref, text, pinned=True)
                      for ref, text in self.state.artifacts.get("skills", [])]
        srcs += skill_srcs
        instruction = (
            "Produce a short implementation plan as JSON: "
            '{"steps": ["<concrete step>", "..."]}. 2 to 4 steps, each a single concrete '
            "change to a named file. Base it strictly on the intent and the files shown."
            + (" A relevant SKILL playbook is included above — follow its procedure where it applies."
               if skill_srcs else "")
            + " Output ONLY JSON."
        )
        plan = self._ask_json("Plan", "Commit to a concrete, minimal plan.", instruction, srcs,
                              require=("steps",))
        raw_steps = plan.get("steps") if isinstance(plan, dict) else None
        if not raw_steps or not isinstance(raw_steps, list):
            # The plan is advisory context for Edit, not the work itself — synthesize a minimal
            # plan from the intent rather than halting a task the model could still complete.
            raw_steps = [f"Edit {t}: {c}" for t, c, _ in self._edit_targets()] or \
                        [self.state.goal.request]
            self.emit("note", "Model gave no plan — using a minimal plan derived from the intent.")
        # Weak models return steps as either strings or {file, change} dicts — normalize.
        steps = [
            " — ".join(str(v) for v in s.values()) if isinstance(s, dict) else str(s)
            for s in raw_steps
        ]
        self.state.artifacts["plan"] = steps
        self.emit("plan", steps)
        # Human gate — cheap guidance replaces expensive autonomy.
        if not self.approve("plan", {"steps": steps}):
            return False, "plan rejected by user"
        self.state.add_decision("Plan approved: " + " | ".join(steps))
        self.state.note("Plan approved.")
        return True, "ok"

    # --- STAGE 4: Edit (with gate + one retry) -------------------------

    def _edit(self) -> tuple[bool, str]:
        for rel, change, is_code in self._edit_targets():
            if rel not in self.state.file_states:
                try:
                    self.state.set_file(rel, self.belt.read_file(path=rel))
                except Exception:
                    self.state.set_file(rel, "")
            ok, msg = self._edit_one(rel, change, is_code, self.state.file_states[rel])
            if not ok:
                return False, msg
        return True, "ok"

    WHOLE_FILE_MAX = 4000  # chars; small files are rewritten whole, large ones spliced

    def _edit_one(self, rel: str, change: str, is_code: bool, current: str) -> tuple[bool, str]:
        # Weak models are far better at rewriting a small file whole than at surgical
        # old/new splicing (which produced dedented blocks and stray lines). A new/empty file
        # can't be spliced at all (no `old` to match), so it always regenerates whole.
        if edit_strategy(is_code, current, self.WHOLE_FILE_MAX) == "whole":
            return self._edit_whole(rel, change, current, is_code=is_code)
        return self._edit_surgical(rel, change, is_code, current)

    def _edit_whole(self, rel: str, change: str, current: str, *, is_code: bool = True) -> tuple[bool, str]:
        plan = self.state.artifacts.get("plan", [])
        # Carry any prior verify failure straight into the regeneration so corrections improve.
        prior = [f for f in self.state.findings if "Verify attempt" in f]
        lang = "python" if is_code else "markdown"
        for _attempt in (1, 2):
            instruction = (
                f"Rewrite the ENTIRE file {rel} to accomplish this goal:\n{change}\n\n"
                f"Output the complete new file inside one ```{lang} code block — nothing else. "
                "Preserve everything that should stay and make the change cleanly."
                + (" Ensure it runs correctly (e.g. use json.dumps for JSON output, not a Python dict)."
                   if is_code else "")
                + (f"\n\nA previous attempt failed verification: {prior[-1]} Fix that." if prior else "")
            )
            raw = self._ask("Edit", f"Rewrite {rel} whole.", instruction, [
                ContextBlock("code", rel, f"```\n{current}\n```"),
                ContextBlock("plan", "approved plan", json.dumps(plan)),
            ])
            content = extract_code(raw)
            # The parse gate only applies to code; a rewritten markdown/doc file has no parse.
            if is_code:
                pg = python_parses(content)
                if not pg.ok:
                    self.emit("verify", f"Parse gate failed: {pg.detail}")
                    prior = prior + [f"parse error: {pg.detail}"]
                    continue
            self.emit("diff", {"file": rel, "old": current, "new": content})
            if not self.approve("edit", {"path": rel, "old": current, "new": content}):
                return False, f"edit to {rel} rejected by user"
            self.belt.write_file(path=rel, content=content)
            self.state.set_file(rel, content)
            self.state.add_fact(f"Rewrote {rel} whole for: {change}")
            self.emit("note", f"Rewrote {rel} whole (gate: {'parse' if is_code else 'text'}).")
            return True, "ok"
        return False, f"could not produce a rewrite of {rel}"

    def _edit_surgical(self, rel: str, change: str, is_code: bool, current: str) -> tuple[bool, str]:
        plan = self.state.artifacts.get("plan", [])
        error_ctx = ""
        for _attempt in (1, 2):
            instruction = (
                f"Edit the file {rel}. Goal: {change}\n"
                'Output ONLY JSON: {"old": "<exact unique snippet currently in the file>", '
                '"new": "<replacement snippet>"}. The `old` must appear verbatim and exactly '
                "once in the file. Keep the edit as small as possible."
                + (f"\n\nYour previous attempt failed: {error_ctx} Fix it." if error_ctx else "")
            )
            raw = self._ask("Edit", f"Produce one exact edit for {rel}.",
                            instruction, [
                                ContextBlock("code", rel, f"```\n{current}\n```"),
                                ContextBlock("plan", "approved plan", json.dumps(plan)),
                            ])
            edit = extract_json(raw)
            if not isinstance(edit, dict) or "old" not in edit or "new" not in edit:
                error_ctx = "output was not a JSON object with `old` and `new`."
                continue
            old, new = str(edit["old"]), str(edit["new"])
            gate = edit_applies(current, old, new)
            if not gate.ok:
                error_ctx = gate.detail
                self.emit("verify", f"Edit gate failed: {gate.detail}")
                continue
            candidate = current.replace(old, new, 1)
            if is_code:
                pg = python_parses(candidate)
                if not pg.ok:
                    error_ctx = pg.detail
                    self.emit("verify", f"Parse gate failed: {pg.detail}")
                    continue
            self.emit("diff", {"file": rel, "old": old, "new": new})
            if not self.approve("edit", {"path": rel, "old": old, "new": new}):
                return False, f"edit to {rel} rejected by user"
            self.belt.write_file(path=rel, content=candidate)
            self.state.set_file(rel, candidate)
            self.state.add_fact(f"Edited {rel}: applied change ({'code' if is_code else 'docs'}).")
            self.emit("note", f"Applied edit to {rel} (gates: edit+{'parse' if is_code else 'text'}).")
            return True, "ok"
        return False, f"could not produce a valid edit for {rel} after 2 attempts ({error_ctx})"

    # --- STAGE 5: Verify -----------------------------------------------

    def _verify(self) -> tuple[bool, str]:
        """Verify by importing the module and checking its symbols/behaviour — robust for
        libraries and interface changes, unlike running the bare file. A flagged CLI task
        adds a supplementary run, but a CLI arg-interface hiccup never fails a sound module."""
        intent = self.state.goal.intent
        target = intent.get("target_file", "")
        request = self.state.goal.request
        py = f'"{sys.executable}"'

        if module_import_name(target) is None:
            return True, "non-python target — nothing to run-verify"

        symbols = extract_symbols(request)
        behaviours = extract_behaviours(request)
        script = build_check_script(self.s.project_root, self.s.project_root / target, symbols, behaviours)
        self.belt.write_file(path=".newton/_verify_check.py", content=script)
        self.emit("note",
                  f"Verifying: import {target}"
                  + (f" · symbols {', '.join(symbols)}" if symbols else "")
                  + (f" · {len(behaviours)} behaviour check(s)" if behaviours else ""))
        out = self.belt.run(cmd=f'{py} ".newton/_verify_check.py"')
        ok = out.startswith("[exit 0]") and "VERIFY_OK" in out
        if not ok:
            detail = (out.split("VERIFY_FAIL:", 1)[-1].strip()[:200]
                      if "VERIFY_FAIL" in out else out.split("]", 1)[-1].strip()[:200])
            # A missing THIRD-PARTY dependency is an environment gap: Newton's own venv lacks the
            # project's deps (e.g. sqlalchemy, fastapi). The code may be perfectly correct — its
            # runtime import is validated later at the integration stage, which installs the deps.
            # Only a missing LOCAL module (a project file) is a real, per-file failure.
            miss = re.search(r"No module named ['\"]([\w.]+)['\"]", out)
            if miss:
                top = miss.group(1).split(".")[0]
                root = self.s.project_root
                is_local = (root / f"{top}.py").exists() or (root / top).is_dir()
                if not is_local:
                    self.emit("verify", f"PASS (deferred) — parses; import of third-party "
                                        f"'{top}' is checked at integration, not in Newton's env")
                    return True, f"parse-verified; runtime import of '{top}' deferred to integration"
            self.emit("verify", f"FAIL — {detail}")
            return False, detail
        self.emit("verify", f"PASS — import {target}"
                  + (f" + {len(symbols)} symbol(s)" if symbols else "")
                  + (f" + {len(behaviours)} behaviour(s)" if behaviours else ""))

        # Behavioural CLI check — when the task defines a real flag, actually exercise it and
        # assert its output. Try the bare flag first (scripts that hardcode their input), then
        # with concrete inputs harvested from the request (scripts that take a positional). Any
        # clean run hard-asserts the expected output; we soft-pass only if it can't be invoked.
        args = str(intent.get("verify_args", "")).strip()
        expect = str(intent.get("verify_expect", "")).strip().strip('"\'')
        if args:
            samples = cli_input_samples(request)
            invocations = [args]
            if samples:
                invocations.append(f"{' '.join(samples)} {args}")
            ran = False
            for inv in invocations:
                cli = self.belt.run(cmd=f'{py} "{target}" {inv}'.strip())
                body = cli.split("]", 1)[-1].strip()
                if cli.startswith("[exit 0]"):
                    ran = True
                    g = run_succeeds(0, body, expect)
                    self.emit("verify", f"{'PASS' if g.ok else 'FAIL'} — CLI `{target} {inv}`: {g.detail}")
                    if not g.ok:
                        return False, f"feature check: {g.detail}"
                    break
            if not ran:
                self.emit("note", f"(CLI `{target} {args}` needs an input we can't infer — module already verified)")

        # Multi-file: every coordinated code file must at least import cleanly. Each already
        # passed its own parse gate at Edit; importing catches cross-file wiring (e.g. a caller
        # left referencing a renamed symbol) that verifying the primary alone would miss.
        for entry in intent.get("also_edit", []):
            afile = entry["file"]
            if module_import_name(afile) is None:
                continue
            self.belt.write_file(path=".newton/_verify_check.py",
                                 content=build_check_script(self.s.project_root, self.s.project_root / afile, [], []))
            aout = self.belt.run(cmd=f'{py} ".newton/_verify_check.py"')
            if not (aout.startswith("[exit 0]") and "VERIFY_OK" in aout):
                adetail = (aout.split("VERIFY_FAIL:", 1)[-1].strip()[:200]
                           if "VERIFY_FAIL" in aout else aout.split("]", 1)[-1].strip()[:200])
                self.emit("verify", f"FAIL — import {afile}: {adetail}")
                return False, f"{entry['file']}: {adetail}"
            self.emit("verify", f"PASS — import {afile}")

        self.state.add_fact(f"Verified: import {target} + {len(symbols)} symbol(s) + {len(behaviours)} behaviour(s)")
        self.state.note("Verified via import + symbol + behaviour checks.")
        return True, "ok"

    # --- STAGE 6: Remember ---------------------------------------------

    # --- STAGE 5b: Independent Review ----------------------------------

    def _review(self) -> tuple[bool, list[str]]:
        """A second, strict model pass over the change AFTER it passed the automated gates —
        catches logic bugs, convention violations, and dead/incomplete code the tests miss."""
        files_text = "\n\n".join(
            f"### {rel}\n```\n{self.state.file_states.get(rel, '')[:2000]}\n```"
            for rel, *_ in self._edit_targets())
        conventions = "\n".join(t for _, t in self.state.artifacts.get("wiki", []))[:800]
        instruction = (
            "You are a strict but fair code reviewer. The change below ALREADY PASSED automated "
            "verification — it runs and produces the expected output. Review it ONLY for real, "
            "concrete problems the tests could miss: logic bugs, obvious convention violations, "
            "leftover/dead/duplicate code, or an incomplete implementation of the stated goal. "
            "If it is correct and clean, APPROVE. Do NOT invent problems.\n"
            'Output ONLY JSON: {"approved": true/false, "issues": ["<specific, fixable issue>"]}.'
            + (f"\n\nProject conventions to enforce:\n{conventions}" if conventions else ""))
        raw = self._ask("Review", "Independently review the completed change.", instruction,
                        [ContextBlock("code", "final files", files_text)], temperature=0.0)
        approved, issues = parse_review(extract_json(raw))
        for i in issues:
            self.emit("verify", f"Review: {i}")
        self.emit("verify", "Review PASS" if approved else f"Review found {len(issues)} issue(s)")
        return approved, issues

    def _remember(self) -> tuple[bool, str]:
        # Distill the task into a durable memory (the "learns" arrow), tagged with the files
        # AND symbols it touched so it can later be recalled by graph edge, not just by meaning.
        files = list(self.state.file_states)
        symbols = self._touched_symbols()
        decisions = "; ".join(self.state.decisions[-3:])
        text = (f"Task: {self.state.goal.request}\n"
                f"Changed: {', '.join(files)}"
                + (f" (defines {', '.join(symbols)})" if symbols else "")
                + (f"\nApproach: {decisions}" if decisions else ""))
        stored = self.memory.add(text, request=self.state.goal.request,
                                 files=files, symbols=symbols, kind="task",
                                 origin=("agent" if self.auto_approve else "user"))
        self.state.note(text)
        self.emit("note", "Remembered this task (semantic memory)."
                  if stored else "Already remembered something like this — not duplicating (salience).")
        self._maybe_learn_wiki()          # self-maintaining wiki (knowledge)
        self._maybe_learn_skill()         # skills-from-experience (procedure)
        return True, "ok"

    def _touched_symbols(self) -> list[str]:
        """Function/class names defined in the code files this task changed — the memory's
        graph edges alongside the file paths."""
        from ..index.graph import analyze_python
        syms: list[str] = []
        for rel, content in self.state.file_states.items():
            if rel.endswith(".py"):
                syms.extend(analyze_python(rel, content).defs)
        return sorted(set(syms))

    def _maybe_learn_wiki(self) -> None:
        """Author a wiki page when this task established a reusable pattern — Newton
        maintaining its own knowledge. Bounded, deduped, and best-effort (never fails a task)."""
        try:
            raw = self._ask("Learn", "Extract a reusable pattern from this task, if any.",
                            LEARN_INSTRUCTION,
                            [ContextBlock("state", "what was done", self.state.summary(400))])
            write, slug, page = pattern_to_page(extract_json(raw), self.wiki.pages())
        except Exception:
            return
        if not write:
            self.emit("note", f"Pattern already in the wiki ({slug})." if slug
                      else "No new reusable pattern to record from this task.")
            return
        try:
            self.wiki.write(slug, page)
            self.emit("note", f"Learned a reusable pattern → wrote wiki/{slug}.")
        except Exception:
            pass

    def _maybe_learn_skill(self) -> None:
        """Distill a reusable PROCEDURE from a successful task into a SKILL.md — skills-from-
        experience. Conservative: only for standalone tasks (not Build sub-tasks), only when the
        model recognizes a repeatable procedure, and never a near-duplicate of an existing skill.
        Best-effort — never fails a task. Off with NEWTON_LEARN_SKILLS=0."""
        if not self.learn_skills or os.getenv("NEWTON_LEARN_SKILLS", "1") == "0":
            return
        try:
            raw = self._ask("Learn", "Extract a reusable procedure from this task, if any.",
                            SKILL_LEARN_INSTRUCTION,
                            [ContextBlock("state", "what was done", self.state.summary(500)),
                             ContextBlock("plan", "the plan that worked",
                                          "\n".join(self.state.artifacts.get("plan", [])) or "(none)")])
            parsed = parse_learned_skill(extract_json(raw))
        except Exception:
            return
        if not parsed:
            self.emit("note", "No reusable procedure to learn from this task.")
            return
        name, desc, steps = parsed
        dup = self.skills.would_duplicate(name, desc)
        if dup:
            self.emit("note", f"Procedure already covered by the skill '{dup}'.")
            return
        try:
            self.skills.write(name, build_skill_md(name, desc, steps, learned=True))
            self.emit("note", f"Learned a reusable procedure → skills/{name}.")
        except Exception:
            pass
