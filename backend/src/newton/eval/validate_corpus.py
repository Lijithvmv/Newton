"""Validate the ensemble on the REAL advisory corpus - the Rule-4 gate on Newton's own data.

The advisory collector logs every intermediate check attempt with its output text and Newton's
deterministic verdict as the label. This scores three deciders on that real corpus - the heuristic
(`error_shaped`), Laya alone, and the ensemble (deterministic floor + high-confidence CALIBRATED
Laya) - and reports accuracy / precision / recall / F1 for each, plus the calibrated ECE.

Honest methodology: when there are enough rows it holds out a test split and fits the temperature on
the TRAIN half only, so the calibrator is never scored on data it saw. Below that it scores in-sample
and says so. This is the real-data counterpart to the synthetic spike validation.

Run:  python -m newton.eval.validate_corpus [project_dir]
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

from ..config import load_settings
from ..loop.calibrate import TemperatureCalibrator, ece
from ..loop.judge import read_advisory
from ..loop.output_check import error_shaped

HELD_OUT_MIN = 30       # below this, a split leaves too few of each class to mean anything


def _metrics(preds: list[bool], truths: list[bool]) -> dict:
    tp = sum(1 for p, t in zip(preds, truths) if p and t)
    fp = sum(1 for p, t in zip(preds, truths) if p and not t)
    fn = sum(1 for p, t in zip(preds, truths) if not p and t)
    tn = sum(1 for p, t in zip(preds, truths) if not p and not t)
    n = len(truths)
    acc = (tp + tn) / n if n else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"acc": acc, "prec": prec, "rec": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _p_fail(row: dict) -> float:
    if "p_failure" in row:
        return float(row["p_failure"])
    conf = float(row.get("confidence", 0.5))            # older rows: reconstruct from confidence+pred
    return conf if row.get("pred_failure") else 1.0 - conf


def validate(rows: list[dict], high: float = 0.9, seed: int = 0) -> dict:
    rows = [r for r in rows if r.get("output") is not None and "truth_failure" in r]
    n = len(rows)
    if n < 4:
        return {"n": n, "note": "too few rows with output text to validate"}
    random.Random(seed).shuffle(rows)
    if n >= HELD_OUT_MIN:
        cut = n // 2
        train, test, held_out = rows[:cut], rows[cut:], True
    else:
        train, test, held_out = rows, rows, False
    cal = TemperatureCalibrator().fit([(_p_fail(r), bool(r["truth_failure"])) for r in train])

    truths = [bool(r["truth_failure"]) for r in test]
    heuristic = [error_shaped(r["output"]) for r in test]
    laya = [_p_fail(r) >= 0.5 for r in test]
    ensemble = [error_shaped(r["output"]) or (cal.apply(_p_fail(r)) >= high) for r in test]
    laya_ece = ece([(cal.apply(_p_fail(r)), bool(r["truth_failure"])) for r in test])
    return {"n": n, "test_n": len(test), "held_out": held_out, "T": cal.T,
            "fail": sum(truths), "ok": len(truths) - sum(truths), "laya_ece": laya_ece,
            "heuristic": _metrics(heuristic, truths), "laya": _metrics(laya, truths),
            "ensemble": _metrics(ensemble, truths)}


def report(res: dict) -> dict:
    if res.get("note"):
        print(f"{res['note']} (n={res['n']})")
        return res
    split = "held-out" if res["held_out"] else "in-sample (too few rows to hold out)"
    print(f"Ensemble validation on REAL corpus - {res['n']} rows "
          f"({res['fail']} fail / {res['ok']} ok); test={res['test_n']} [{split}], T={res['T']}")
    print("=" * 62)
    for name in ("heuristic", "laya", "ensemble"):
        m = res[name]
        print(f"  {name:10s} acc {m['acc']:.2f}  prec {m['prec']:.2f}  rec {m['rec']:.2f}  "
              f"f1 {m['f1']:.2f}  (tp {m['tp']} fp {m['fp']} fn {m['fn']} tn {m['tn']})")
    print(f"  calibrated Laya ECE on test: {res['laya_ece']:.3f}")
    return res


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    project = argv[0] if argv else "."
    root = Path(load_settings(project).project_root)
    report(validate(read_advisory(root / ".newton" / "advisory.jsonl")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
