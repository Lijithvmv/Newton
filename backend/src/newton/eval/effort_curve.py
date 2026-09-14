"""The effort curve — measure pass-rate as a function of compute spent.

This is the instrument for Newton's central claim: *free tokens + unlimited local time can lift a
weak model to a quality a single pass can't reach.* A cloud model can't run this experiment — paying
for 8 attempts × best-of-5 per task is uneconomic when tokens are metered. Newton can.

For each effort level (quick → normal → thorough → max, strictly increasing compute), run the loop on
each task `reps` times in a fresh sandbox and record the pass-rate. The report lays the levels side by
side so the curve is visible: if the rate CLIMBS with effort, that is the moat stated as a measured
number; if it's FLAT, we've found the model's ceiling honestly and know that more compute is not the
lever (Rule 4 — either outcome is a real finding, neither is a claim).

Reuses the harness's fair sandbox + objective checker; the only new thing is sweeping the dial.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..config import load_settings
from ..loop import ORDER, BestOfN, effort
from .harness import ArmResult, _restore_checkers, _run_check, _write_files
from .tasks import EvalTask


def run_level(task: EvalTask, model: str, root: Path, level: str) -> bool:
    """One attempt at `task` on `model` at effort `level`, in the sandbox `root`. Uses BestOfN so the
    level's whole-task attempt count is honoured; each attempt is a full LoopEngine at that effort."""
    eff = effort(level)
    _write_files(root, task.files)
    settings = load_settings(str(root))
    settings.agent_model = model
    try:
        BestOfN(settings, eff.best_of_n, emit=lambda *a: None, effort=eff).run(task.goal)
    except Exception:
        pass                                        # a crash is just a failed run; the check decides
    _restore_checkers(root, task)
    return _run_check(root, task.check)


def run_curve(model: str, tasks: list[EvalTask], levels: list[str], *, reps: int = 3,
              on_run=None) -> dict[tuple[str, str], ArmResult]:
    """Run each task at each effort level `reps` times. Returns {(level, task_id): ArmResult}."""
    results: dict[tuple[str, str], ArmResult] = {}
    for level in levels:
        for task in tasks:
            ar = ArmResult()
            for i in range(reps):
                root = Path(tempfile.mkdtemp(prefix=f"ncurve_{task.id}_{level}_"))
                try:
                    ok = run_level(task, model, root, level)
                except Exception:
                    ok = False
                finally:
                    shutil.rmtree(root, ignore_errors=True)
                ar.passes += int(ok)
                ar.runs += 1
                if on_run:
                    on_run(level, task.id, i + 1, ok)
            results[(level, task.id)] = ar
    return results


def curve_report(results: dict[tuple[str, str], ArmResult], tasks: list[EvalTask],
                 levels: list[str], *, model: str = "") -> str:
    """Levels across the columns, tasks down the rows, plus an OVERALL row — so the pass-rate-vs-
    compute curve reads left to right."""
    lines = [f"Newton effort curve - model: {model or '?'}  (pass-rate as compute rises ->)"]
    header = f"{'task':<22}" + "".join(f"{lvl:>12}" for lvl in levels)
    lines.append(header)
    lines.append("-" * len(header))
    totals = {lvl: ArmResult() for lvl in levels}
    for task in tasks:
        row = f"{task.id:<22}"
        for lvl in levels:
            ar = results.get((lvl, task.id), ArmResult())
            totals[lvl].passes += ar.passes
            totals[lvl].runs += ar.runs
            row += f"{ar.passes}/{ar.runs} ({ar.rate:.0%})".rjust(12)
        lines.append(row)
    lines.append("-" * len(header))
    trow = f"{'OVERALL':<22}" + "".join(f"{totals[lvl].rate:.0%}".rjust(12) for lvl in levels)
    lines.append(trow)
    # The verdict line: did more compute actually buy quality?
    if len(levels) >= 2:
        lo, hi = totals[levels[0]].rate, totals[levels[-1]].rate
        delta = hi - lo
        verdict = ("compute BUYS quality" if delta > 0.01
                   else "FLAT - more compute is not the lever here (model ceiling)")
        lines.append(f"{'lift ' + levels[0] + '->' + levels[-1]:<22}{delta:+.0%}   {verdict}")
    return "\n".join(lines)


def _order_levels(names: list[str]) -> list[str]:
    """Keep requested levels in ladder order (so the curve always reads low→high compute)."""
    return [lvl for lvl in ORDER if lvl in names]
