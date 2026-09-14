"""`newton-eval` — run the pipeline-vs-baseline measurement and print the report."""

from __future__ import annotations

import argparse

from .harness import report, run_suite
from .tasks import HARD_TASKS, LARGE_TASKS, REAL_TASKS, SEED_TASKS


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="newton-eval",
        description="Measure whether Newton's engine beats a naive one-prompt baseline on the same "
                    "local model, over objectively-checkable tasks. `--suite hard` = cross-file tasks "
                    "a one-shot prompt structurally can't do; `--arms baseline,loop` compares the "
                    "decomposition-loop engine against the baseline.")
    ap.add_argument("--model", default="ollama:qwen2.5-coder:7b", help="Ollama model to test")
    ap.add_argument("--suite", default="seed", choices=["seed", "hard", "large", "real"],
                    help="task suite: seed (easy) | hard (cross-file) | large (retrieval) | "
                         "real (whole runnable apps — the limit test)")
    ap.add_argument("--reps", type=int, default=3, help="runs per task per arm (variance)")
    ap.add_argument("--arms", default="baseline,loop", help="comma list: baseline,newton,loop")
    ap.add_argument("--task", default="", help="run only this task id")
    ap.add_argument("--memory", action="store_true",
                    help="run the model-free memory-recall eval (provenance + decay) and exit")
    ap.add_argument("--curve", action="store_true",
                    help="sweep the effort dial and measure pass-rate vs compute spent (the moat, as a "
                         "number). Uses --levels instead of --arms.")
    ap.add_argument("--levels", default="quick,normal,thorough,max",
                    help="comma list of effort levels for --curve (quick,normal,thorough,max)")
    args = ap.parse_args()

    if args.memory:
        from .memory_eval import report as memory_report
        print(memory_report())
        return

    suite = {"hard": HARD_TASKS, "large": LARGE_TASKS, "real": REAL_TASKS}.get(args.suite, SEED_TASKS)
    tasks = [t for t in suite if not args.task or t.id == args.task]

    if args.curve:
        from .effort_curve import _order_levels, curve_report, run_curve
        levels = _order_levels([x.strip() for x in args.levels.split(",") if x.strip()])

        def on_curve(level: str, tid: str, i: int, ok: bool) -> None:
            print(f"  {level:>9}  {tid:<22} rep {i}: {'PASS' if ok else 'fail'}", flush=True)

        print(f"Effort curve: {len(tasks)} task(s) x {args.reps} rep(s) x {len(levels)} level(s) "
              f"on {args.model} ...\n")
        results = run_curve(args.model, tasks, levels, reps=args.reps, on_run=on_curve)
        print("\n" + curve_report(results, tasks, levels, model=args.model))
        return

    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())

    def on_run(arm: str, tid: str, i: int, ok: bool) -> None:
        print(f"  {arm:>8}  {tid:<20} rep {i}: {'PASS' if ok else 'fail'}", flush=True)

    print(f"Running {len(tasks)} task(s) x {args.reps} rep(s) x {len(arms)} arm(s) on {args.model} ...\n")
    results = run_suite(args.model, tasks, reps=args.reps, arms=arms, on_run=on_run)
    print("\n" + report(results, tasks, model=args.model, arms=arms))


if __name__ == "__main__":
    main()
