"""Newton's terminal front-end — a small REPL that drives the agent loop.

Dependency-free on purpose (stdlib only) so it runs on the current venv with nothing
new to install. It owns all I/O: printing the agent's thoughts/actions/observations and
prompting the user to approve any write or shell command before it happens.

Usage:
    python -m newton                 # interactive REPL
    python -m newton "your task"     # one-shot
    python -m newton --project PATH  # point at another project root
    python -m newton --yes           # auto-approve mutations (careful)
"""

from __future__ import annotations

import sys
from typing import Any

from .agent import Agent
from .config import load_settings

# Windows consoles default to cp1252 and choke on the box-drawing glyphs below;
# force UTF-8 so Newton renders the same on every platform.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ANSI colours, disabled when output isn't a TTY.
_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


DIM = lambda t: _c("2", t)
BOLD = lambda t: _c("1", t)
CYAN = lambda t: _c("36", t)
GREEN = lambda t: _c("32", t)
YELLOW = lambda t: _c("33", t)
RED = lambda t: _c("31", t)


def _emit(channel: str, text: str) -> None:
    if channel == "thought":
        print(DIM(f"  · {text}"))
    elif channel == "action":
        print(CYAN(f"  → {text}"))
    elif channel == "observation":
        first = text.splitlines()[0] if text else ""
        extra = "" if text.count("\n") == 0 else DIM(f"  (+{text.count(chr(10))} more lines)")
        print(DIM(f"    {first[:200]}") + extra)
    elif channel == "model":
        print(YELLOW(f"  [off-protocol] {text[:200]}"))


def _make_approver(auto: bool):
    def approve(tool: str, args: dict[str, Any]) -> bool:
        if auto:
            return True
        print(YELLOW(f"\n  APPROVE {tool}?"))
        if tool == "run":
            print(f"    $ {args.get('cmd', '')}")
        elif tool == "write_file":
            content = str(args.get("content", ""))
            print(f"    write {args.get('path', '')} ({len(content)} chars)")
            for line in content.splitlines()[:12]:
                print(DIM(f"    | {line}"))
            if content.count("\n") > 12:
                print(DIM("    | ..."))
        elif tool == "edit_file":
            print(f"    edit {args.get('path', '')}")
            print(RED(f"    - {str(args.get('old',''))[:120]}"))
            print(GREEN(f"    + {str(args.get('new',''))[:120]}"))
        try:
            ans = input(BOLD("    proceed? [y/N] ")).strip().lower()
        except EOFError:
            return False
        return ans in ("y", "yes")

    return approve


def _run_task(agent: Agent, task: str) -> None:
    print(BOLD(f"\n▸ {task}"))
    result = agent.run(task)
    print(GREEN(f"\n✓ ({result.turns} turns)\n") + result.answer + "\n")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    project = "."
    auto = False
    task_parts: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg in ("--project", "-p"):
            project = next(it, ".")
        elif arg in ("--yes", "-y"):
            auto = True
        elif arg in ("--help", "-h"):
            print(__doc__)
            return 0
        else:
            task_parts.append(arg)

    settings = load_settings(project)
    agent = Agent(settings, approve=_make_approver(auto), emit=_emit)

    print(BOLD("Newton") + DIM(f" · {settings.agent_model} · {settings.project_root}"))

    if task_parts:
        _run_task(agent, " ".join(task_parts))
        return 0

    print(DIM("Interactive mode. Type a task, or 'exit' to quit.\n"))
    while True:
        try:
            task = input(BOLD("newton › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task.lower() in ("exit", "quit", ":q"):
            break
        try:
            _run_task(agent, task)
        except KeyboardInterrupt:
            print(RED("\n  interrupted\n"))
        except Exception as e:  # keep the REPL alive on any single-task failure
            print(RED(f"\n  error: {e}\n"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
