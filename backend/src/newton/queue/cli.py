"""`newton-queue` — run a batch of goals unattended, one at a time, resumable.

    newton-queue jobs.txt              # each line is a goal; runs them all, checkpointing each
    newton-queue --resume              # continue an interrupted batch from the next unfinished job

The batch survives interruption: kill it any time and re-run with --resume to pick up where it
stopped — the local, offline, checkpointed workflow of the "flight story", driven by the loop engine.
"""

from __future__ import annotations

import argparse
import sys

from ..config import load_settings
from .runner import JobQueue
from .state import DONE, FAILED, QueueState


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        prog="newton-queue",
        description="Run a batch of goals unattended, one at a time, checkpointed and resumable. "
                    "Each line of the jobs file is a goal (or a JSON object with goal/project/id).")
    ap.add_argument("jobs", nargs="?", default="", help="jobs file (one goal per line)")
    ap.add_argument("--project", "-C", default=".", help="project dir for jobs without their own")
    ap.add_argument("--model", default=None, help="override the model")
    ap.add_argument("--resume", action="store_true", help="resume an interrupted batch")
    args = ap.parse_args(argv)

    if not args.jobs and not args.resume:
        ap.error("give a jobs file to run, or --resume to continue the last batch")

    settings = load_settings(args.project)
    if args.model:
        settings.agent_model = args.model

    state = None
    if not args.resume:
        state = QueueState.from_jobs_file(args.jobs)
        if not state.jobs:
            print("No jobs found in the file.")
            return 1
        print(f"\033[1mNewton queue\033[0m · {len(state.jobs)} jobs · model: {settings.agent_model}")

    def emit(ch, p):
        if ch == "job":
            if p.get("status") == "start":
                print(f"\n\033[1m[{p.get('index')}/{p.get('total')}]\033[0m {p['goal'][:72]}", flush=True)
            elif p.get("status") == DONE:
                print("  \033[32m✓ done\033[0m", flush=True)
            elif p.get("status") == FAILED:
                print(f"  \033[31m✗ failed\033[0m — {str(p.get('detail',''))[:70]}", flush=True)
        elif ch == "step" and p.get("status") == "start":       # a step inside the current job
            print(f"    · {(p.get('file') or p.get('goal',''))[:60]}", flush=True)
        elif ch == "note" and "Resuming" in str(p):
            print(p, flush=True)

    result = JobQueue(settings, emit=emit).run(state, resume=args.resume)
    print(f"\n\033[1mBatch complete\033[0m — {result.done} done, {result.failed} failed "
          f"of {len(result.state.jobs)} jobs.")
    if not result.ok:
        print("Re-run with --resume to retry unfinished jobs.")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
