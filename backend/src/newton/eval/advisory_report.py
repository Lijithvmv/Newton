"""Report the advisory Laya judge's agreement with ground truth, from the accumulated log.

The advisory judge (loop/judge.py) logs (ground_truth, prediction, confidence) for every check it
scored on real runs, without ever acting. This reads that log and reports how well Laya agreed with
Newton's OWN deterministic outcomes - agreement, precision/recall on the 'failure' class,
calibration (ECE), and the coverage-vs-accuracy curve. This is the eval-first evidence that must
accumulate before Laya is trusted to act on anything.

Run:  python -m newton.eval.advisory_report [project_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import load_settings
from ..loop.judge import read_advisory
from .laya_spike import _coverage_curve, _ece


def report(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        print("No advisory records yet. Run builds with NEWTON_LOOP_ADVISORY_JUDGE=1 (Laya installed) "
              "to accumulate them, then re-run this report.")
        return {"n": 0}
    correct = sum(1 for r in rows if r.get("correct"))
    tp = sum(1 for r in rows if r.get("pred_failure") and r.get("truth_failure"))
    fp = sum(1 for r in rows if r.get("pred_failure") and not r.get("truth_failure"))
    fn = sum(1 for r in rows if not r.get("pred_failure") and r.get("truth_failure"))
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    confs = [(float(r.get("confidence", 0.0)), bool(r.get("correct"))) for r in rows]
    truths = sum(1 for r in rows if r.get("truth_failure"))
    print(f"Advisory Laya judge - {n} judged checks on REAL runs")
    print("=" * 48)
    print(f"  agreement {correct / n:.2f}  precision {prec:.2f}  recall {rec:.2f}  ECE {_ece(confs):.3f}")
    print(f"  class balance: {truths} failures / {n - truths} ok")
    print("  coverage curve (act above threshold):")
    for thr, acc, cov in _coverage_curve(confs):
        print(f"    conf >= {thr:.1f}: acc {acc:.2f} on {cov}/{n}")
    return {"n": n, "agreement": correct / n, "precision": prec, "recall": rec}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    project = argv[0] if argv else "."
    root = Path(load_settings(project).project_root)
    report(read_advisory(root / ".newton" / "advisory.jsonl"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
