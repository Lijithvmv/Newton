"""`newton-fix` — investigate an open-ended problem and fix it, in your terminal.

The build loop (`newton-loop`) plans-then-builds a goal whose shape is known. This is the other
half: point it at a broken project and describe the problem ("the tests fail", "add() returns the
wrong number"). It investigates — reads files, runs commands — forms a fix, edits, and re-runs the
check every turn until it passes. The check, not the model's word, decides when it's done.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_settings
from .explore import ExploreEngine


def _make_emitter():
    """Plain-English terminal output — investigation steps, no engine internals (Master Rule 7)."""
    verb = {"read": "looking at", "run": "running", "edit": "editing", "done": "checking the fix"}

    def emit(ch: str, p) -> None:
        if ch != "explore":
            return
        if p.get("done"):
            print("  \033[32m✓ the check passes\033[0m", flush=True)
            return
        action = str(p.get("action", ""))
        target = str(p.get("target", ""))
        label = verb.get(action, action)
        print(f"→ {label} {target}".rstrip(), flush=True)
    return emit


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        prog="newton-fix",
        description="Investigate and fix an open-ended problem: the loop reads, runs, and edits until "
                    "a check passes. Point it at a broken project and describe what's wrong.")
    ap.add_argument("problem", nargs="?", default="", help="what's wrong / what to fix")
    ap.add_argument("--project", "-C", default=".", help="project directory (default: current)")
    ap.add_argument("--check", default=None,
                    help="command whose exit 0 means solved (default: auto-detect pytest). "
                         "Use {py} for the python interpreter, e.g. '{py} -m pytest -q'")
    ap.add_argument("--model", default=None, help="override the model")
    args = ap.parse_args(argv)

    if not args.problem:
        ap.error("describe the problem to investigate and fix")

    settings = load_settings(args.project)
    if args.model:
        settings.agent_model = args.model

    print(f"\033[1mNewton · fix\033[0m · {settings.project_root}  ·  model: {settings.agent_model}")
    print("(investigates and edits project files autonomously; a check decides when it's fixed)")

    engine = ExploreEngine(settings, emit=_make_emitter(), check=args.check)
    result = engine.run(args.problem)

    tag = "Fixed" if result.ok else "Unresolved"
    color = "32" if result.ok else "31"
    print(f"\n\033[1;{color}m{tag}\033[0m — {result.answer} ({result.steps} steps)")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
