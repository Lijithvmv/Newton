"""The agent loop — Newton's Claude-Code-style plan → act → observe cycle.

Because local models can't be assumed to support native tool-calling, the loop uses a
text protocol: each turn the model emits ONE JSON object naming a tool and its args.
Newton parses it, runs the tool (asking first for anything that writes or runs code),
feeds the result back as an observation, and repeats until the model calls `finish`.

Everything runs against the local Ollama model in `Settings.agent_model`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .context import load_context, project_tree
from .llm import complete
from .tools import MUTATING, ToolBelt, ToolError

# Callback signatures let the CLI own all I/O; the loop stays pure and testable.
Approver = Callable[[str, dict[str, Any]], bool]   # (tool_name, args) -> allowed?
Emitter = Callable[[str, str], None]               # (channel, text) -> None


SYSTEM_TEMPLATE = """You are Newton, a fully-local coding and project assistant running on the user's machine.
You accomplish tasks by using tools, one step at a time.

## How to respond
On EVERY turn, reply with EXACTLY ONE JSON object and nothing else — no prose, no markdown fences.
The object has this shape:
{{"thought": "<one short sentence of reasoning>", "tool": "<tool name>", "args": {{...}}}}

Call exactly one tool per turn. Wait for its OBSERVATION before the next step.
When the task is complete, call the `finish` tool with your final answer in `args.message`.

## Available tools
{tools}

## Project root
{root}

## Project layout
{tree}

## Project context
{context}

## Rules
- Read files before editing them; match `old` text exactly for edit_file.
- Prefer small, targeted edits over rewriting whole files.
- Do not invent file contents — use read_file to check.
- Keep going until the task is done, then call finish."""


@dataclass
class AgentResult:
    answer: str
    turns: int
    transcript: list[dict[str, str]] = field(default_factory=list)


def _extract_action(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object containing a "tool" key out of model output."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                chunk = text[start : i + 1]
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(obj, dict) and "tool" in obj:
                    return obj
                start = -1
    return None


class Agent:
    def __init__(
        self,
        settings: Settings,
        *,
        approve: Approver,
        emit: Emitter,
    ) -> None:
        self.settings = settings
        self.belt = ToolBelt(settings.project_root)
        self.approve = approve
        self.emit = emit

    def _system_prompt(self) -> str:
        return SYSTEM_TEMPLATE.format(
            tools=self.belt.catalog(),
            root=self.settings.project_root,
            tree=project_tree(self.settings.project_root),
            context=load_context(self.settings.project_root, self.settings.context_file),
        )

    def run(self, task: str) -> AgentResult:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": task},
        ]

        for turn in range(1, self.settings.max_turns + 1):
            resp = complete(
                self.settings.agent_model, messages, temperature=self.settings.temperature
            )
            raw = (resp.choices[0].message.content or "").strip()
            messages.append({"role": "assistant", "content": raw})

            action = _extract_action(raw)
            if action is None:
                # Model drifted off-protocol; nudge it back with a reminder observation.
                self.emit("model", raw)
                messages.append({
                    "role": "user",
                    "content": "OBSERVATION: No valid JSON action found. Reply with exactly one "
                               "JSON object naming a tool, or call finish.",
                })
                continue

            name = str(action.get("tool", ""))
            args = action.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            thought = str(action.get("thought", "")).strip()
            if thought:
                self.emit("thought", thought)
            self.emit("action", f"{name}({', '.join(f'{k}={_short(v)}' for k, v in args.items())})")

            if name == "finish":
                answer = str(args.get("message", "")).strip() or "Done."
                return AgentResult(answer=answer, turns=turn, transcript=messages)

            # Approval gate for anything that mutates the world.
            if name in MUTATING and not self.approve(name, args):
                obs = "Action declined by the user."
                self.emit("observation", obs)
                messages.append({"role": "user", "content": f"OBSERVATION: {obs}"})
                continue

            try:
                obs = self.belt.get(name).func(**args)
            except ToolError as e:
                obs = f"ERROR: {e}"
            except TypeError as e:
                obs = f"ERROR: bad arguments for {name}: {e}"
            self.emit("observation", obs)
            messages.append({"role": "user", "content": f"OBSERVATION:\n{obs}"})

        return AgentResult(
            answer="Reached the turn limit without finishing. Try a narrower task.",
            turns=self.settings.max_turns,
            transcript=messages,
        )


def _short(v: Any, limit: int = 60) -> str:
    s = str(v).replace("\n", "\\n")
    return s if len(s) <= limit else s[:limit] + "…"
