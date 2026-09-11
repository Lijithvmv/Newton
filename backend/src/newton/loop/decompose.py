"""The Planner — break a compound goal into ATOMIC, explicitly-specified steps.

The spike showed a small model executes an atomic step reliably but drowns in a compound goal. So
the plan's job is to make every step atomic: one file to write, or one command to run, with explicit
dependencies. Deliberately NOT a rigid script the executor must follow verbatim — the loop replans a
step that fails; this is the initial decomposition the loop then drives and adapts.
"""

from __future__ import annotations

from ..conductor.state import extract_json
from ..llm import complete
from .state import RUN, WRITE, Step

DECOMPOSE_SYSTEM = (
    "You break a software goal into a small ordered list of ATOMIC steps a junior coder can each do "
    "in one sitting: exactly one file to write, or one command to run. Output ONLY JSON."
)

_INSTRUCTION = (
    "Goal:\n{goal}\n\n"
    "Existing files:\n{tree}\n\n"
    "Break this into atomic steps. Output JSON:\n"
    '{{"steps": [{{"id": "s1", "kind": "write", "file": "<one file this step creates>", '
    '"goal": "<exactly what this one file must contain>", "depends_on": []}}, '
    '{{"id": "s2", "kind": "run", "command": "{{py}} -m pytest -q", '
    '"goal": "run the tests", "depends_on": ["s1"]}}]}}\n'
    "Rules: one file per write step; a run step\'s command may use {{py}} for the Python interpreter; "
    "order matters — a step depends_on the steps whose files it needs. Keep it minimal. ONLY JSON."
)


def decompose(model: str, goal: str, tree: str = "", *, temperature: float = 0.1,
              max_steps: int = 12) -> list[Step]:
    """Return the atomic step plan for a goal. Empty list if the model can't produce a usable plan
    (the caller then falls back — a plan we can't parse must not crash the run)."""
    resp = complete(model, [
        {"role": "system", "content": DECOMPOSE_SYSTEM},
        {"role": "user", "content": _INSTRUCTION.format(goal=goal, tree=tree or "(empty project)")},
    ], temperature=temperature)
    data = extract_json(resp.choices[0].message.content or "")
    raw = data.get("steps") if isinstance(data, dict) else None
    if not raw or not isinstance(raw, list):
        return []
    steps: list[Step] = []
    for i, s in enumerate(raw[:max_steps], 1):
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or WRITE).lower()
        kind = RUN if kind == RUN else WRITE
        goal_text = str(s.get("goal") or "").strip()
        if not goal_text:
            continue
        steps.append(Step(
            id=str(s.get("id") or f"s{i}"),
            goal=goal_text,
            kind=kind,
            file=str(s.get("file") or "").strip(),
            command=str(s.get("command") or "").strip(),
            depends_on=[str(d) for d in (s.get("depends_on") or []) if d],
        ))
    # Keep only backward dependencies so the graph is always an acyclic, runnable plan.
    seen: set[str] = set()
    for s in steps:
        s.depends_on = [d for d in s.depends_on if d in seen]
        seen.add(s.id)
    return steps
