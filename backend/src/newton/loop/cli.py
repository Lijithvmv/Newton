"""`newton-loop` — build a feature with the durable decomposition loop, in your terminal.

Point it at a project and give it a goal; it plans the work into atomic steps, builds them one at a
time with the right context, verifies each, and checkpoints after every step — so a long run can be
interrupted and resumed. This is the engine's real, usable form.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_settings
from .bestofn import BestOfN
from .effort import ORDER, effort
from .keepawake import keep_awake
from .state import DONE, FAILED


def _make_emitter():
    """Plain-English terminal output — no engine internals leak to the user (Master Rule 7)."""
    def emit(ch: str, p) -> None:
        if ch == "stage" and p == "Plan":
            print("\n\033[1mPlanning the work…\033[0m", flush=True)
        elif ch == "plan":
            print("\nPlan:", flush=True)
            for i, st in enumerate(p, 1):
                what = st.get("file") or st.get("goal", "")[:60]
                print(f"  {i}. {what}", flush=True)
            print("", flush=True)
        elif ch == "note":
            print(f"  · {p}", flush=True)
        elif ch == "step":
            if p.get("status") == "start":
                label = p.get("file") or p.get("goal", "")[:60]
                print(f"→ {label}", flush=True)
            elif p.get("status") == DONE:
                print("  \033[32m✓ done\033[0m", flush=True)
            elif p.get("status") == FAILED:
                print(f"  \033[31m✗ failed:\033[0m {str(p.get('detail',''))[:120]}", flush=True)
        elif ch == "verdict":
            colour = {"pass": "\033[32m✓ PASS\033[0m",
                      "attention": "\033[33m⚠ NEEDS ATTENTION\033[0m",
                      "block": "\033[31m✗ WOULD BLOCK\033[0m"}.get(p.get("level"), p.get("label", ""))
            print(f"\nReview: {colour} — {p.get('reason', '')}", flush=True)
        elif ch == "halt":
            print(f"\n\033[31mStopped:\033[0m {p}", flush=True)
    return emit


def main(argv=None) -> int:
    # The pretty output uses arrows/checkmarks; Windows consoles default to cp1252 and would crash
    # on them. Force UTF-8 with a replace fallback so the CLI is robust everywhere.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        prog="newton-loop",
        description="Build a feature with the durable decomposition loop (plan, build, verify, "
                    "checkpoint). Resumable: re-run with --resume to continue an interrupted build.")
    ap.add_argument("goal", nargs="?", default="", help="what to build")
    ap.add_argument("--project", "-C", default=".", help="project directory (default: current)")
    ap.add_argument("--model", default=None, help="override the model (e.g. ollama:qwen2.5-coder:32b)")
    ap.add_argument("--resume", action="store_true", help="resume the last interrupted build")
    ap.add_argument("--effort", choices=ORDER, default=None,
                    help="how much free local compute to spend for quality: quick | normal | "
                         "thorough | max. Higher = more attempts, wider retrieval, more replans, and "
                         "more verified whole-build attempts. Default: $NEWTON_EFFORT or normal.")
    ap.add_argument("--best-of", type=int, default=None, metavar="N",
                    help="run the whole build N times in isolated copies and keep the attempt that "
                         "verifies. Overrides the effort level's default (quick/normal=1, "
                         "thorough=3, max=5).")
    ap.add_argument("--keep-awake", action="store_true",
                    help="stop the machine sleeping while the build runs, so a long unattended run "
                         "finishes instead of freezing (Windows; no-op elsewhere).")
    args = ap.parse_args(argv)

    if not args.goal and not args.resume:
        ap.error("give a goal to build, or pass --resume to continue the last one")

    settings = load_settings(args.project)
    if args.model:
        settings.agent_model = args.model

    eff = effort(args.effort)
    bo = max(1, args.best_of) if args.best_of is not None else eff.best_of_n
    print(f"\033[1mNewton\033[0m · {settings.project_root}  ·  model: {settings.agent_model}"
          f"  ·  effort: {eff.name}" + (f"  ·  best-of-{bo}" if bo > 1 else ""))
    print("(builds run autonomously and modify project files; every step is checkpointed)")

    with keep_awake(args.keep_awake, notify=lambda m: print(f"  · {m}", flush=True)):
        result = BestOfN(settings, bo, emit=_make_emitter(), effort=eff).run(args.goal, resume=args.resume)

    print(f"\n\033[1m{'Done' if result.ok else 'Incomplete'}\033[0m — {result.answer}")
    if not result.ok:
        print("Re-run with --resume to continue from the last checkpoint.")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
