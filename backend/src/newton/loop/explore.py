"""ExploreEngine — investigate an open-ended problem and fix it (read → decide → act → verify).

The build loop plans-then-executes a known-shaped goal. This is the other half: an open-ended task
("the tests fail — fix it", "why does X crash", "make Y do Z") where the plan ISN'T known upfront.
The model investigates by reading files and running commands, forms a fix, edits, and the system
re-runs the check every iteration until it passes — a ReAct loop with the check as the ground truth.
Bounded, and the check (not the model's say-so) decides success.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..conductor.state import extract_json
from ..config import Settings
from ..context import project_tree
from ..llm import complete
from ..tools import ToolBelt

ACTION_SYSTEM = (
    "You investigate and fix a codebase. Each turn, look at the evidence and choose ONE next action, "
    "as JSON only:\n"
    '  {"action":"read","path":"<file>"}            — inspect a file before changing it\n'
    '  {"action":"run","cmd":"<command>"}           — run a command to gather evidence ({py}=python)\n'
    '  {"action":"edit","path":"<file>","content":"<the file\'s COMPLETE new content>"}  — fix a file\n'
    '  {"action":"done"}                            — you believe it is fixed\n'
    "Read the relevant file before editing it. Base the fix on the actual error. Output ONLY the JSON."
)


@dataclass
class ExploreResult:
    ok: bool
    answer: str
    steps: int = 0


class ExploreEngine:
    MAX_STEPS = 14

    def __init__(self, settings: Settings, *, emit: Callable[[str, Any], None] | None = None,
                 check: str | None = None) -> None:
        self.s = settings
        self.root = Path(settings.project_root)
        self.emit = emit or (lambda *a: None)
        self.model = settings.agent_model
        self.belt = ToolBelt(self.root)
        self.check = check                      # command whose exit 0 means solved; None = auto-detect
        self.obs: list[str] = []                # running observations (the model's evidence)

    # --- the check (ground truth) --------------------------------------

    def _detect_check(self) -> str | None:
        for p in self.root.rglob("test_*.py"):
            if ".venv" not in p.parts and "node_modules" not in p.parts:
                # -B: never write .pyc — so a same-size edit (a-b → a+b) re-run in the same
                # filesystem-second can't be masked by stale timestamp-invalidated bytecode.
                return "{py} -B -m pytest -q"
        return None

    def _run_check(self, check: str) -> tuple[bool, str]:
        out = self.belt.run(cmd=check.replace("{py}", f'"{sys.executable}"'))
        return out.startswith("[exit 0]"), out.split("]", 1)[-1].strip()

    # --- the loop ------------------------------------------------------

    def run(self, problem: str) -> ExploreResult:
        check = self.check or self._detect_check()
        for step in range(self.MAX_STEPS):
            if check:                           # ground truth first: is it already solved?
                ok, detail = self._run_check(check)
                if ok:
                    self.emit("explore", {"done": True, "detail": "the check passes"})
                    return ExploreResult(True, "solved — the check passes", step)
                self.obs.append(f"CHECK still failing:\n{detail[:700]}")

            action = self._decide(problem)
            if action is None:
                self.obs.append("(no valid action — reconsidering)")
                continue
            kind = str(action.get("action", "")).lower()
            target = action.get("path") or action.get("cmd") or ""
            self.emit("explore", {"step": step + 1, "action": kind, "target": str(target)[:70]})
            if kind == "done":
                continue                        # next iteration re-checks the ground truth
            self.obs.append(self._do(action))

        if check:                               # last word to the check, not the model
            ok, _ = self._run_check(check)
            if ok:
                return ExploreResult(True, "solved — the check passes", self.MAX_STEPS)
        return ExploreResult(False, "investigated but could not resolve it in the step budget", self.MAX_STEPS)

    def _decide(self, problem: str) -> dict | None:
        recent = "\n\n".join(self.obs[-6:]) or "(nothing yet — start by looking)"
        prompt = (f"Problem to solve:\n{problem}\n\nProject files:\n{project_tree(self.root)}\n\n"
                  f"Evidence so far:\n{recent}\n\nChoose ONE next action as JSON.")
        try:
            resp = complete(self.model, [{"role": "system", "content": ACTION_SYSTEM},
                                         {"role": "user", "content": prompt}], temperature=0.1)
            data = extract_json(resp.choices[0].message.content or "")
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("action") else None

    def _do(self, action: dict) -> str:
        kind = str(action.get("action", "")).lower()
        try:
            if kind == "read":
                path = str(action.get("path", ""))
                return f"READ {path}:\n{self.belt.read_file(path=path)[:1500]}"
            if kind == "run":
                shown = str(action.get("cmd", ""))
                out = self.belt.run(cmd=shown.replace("{py}", f'"{sys.executable}"'))
                return f"RAN `{shown}`:\n{out[:1000]}"
            if kind == "edit":
                path, content = str(action.get("path", "")), str(action.get("content", ""))
                if not path or not content.strip():
                    return "edit had no path/content — ignored"
                return "EDIT " + self.belt.write_file(path=path, content=content)
        except Exception as e:
            return f"action `{kind}` failed: {e}"
        return f"unknown action: {kind}"
