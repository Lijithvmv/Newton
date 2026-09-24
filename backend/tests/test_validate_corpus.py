"""Ensemble validation on the real advisory corpus: the deterministic floor catches markers, Laya
catches marker-less failures, and the ensemble combines both against the stored ground truth."""

from __future__ import annotations

from newton.eval.validate_corpus import validate


def test_ensemble_combines_floor_and_laya_on_real_rows():
    rows = [
        {"output": "Traceback (most recent call last):\nValueError", "truth_failure": True, "p_failure": 0.2},
        {"output": "=== 1 failed, 2 passed ===", "truth_failure": True, "p_failure": 0.95},
        {"output": "3 passed in 0.1s", "truth_failure": False, "p_failure": 0.05},
        {"output": "Successfully installed x", "truth_failure": False, "p_failure": 0.1},
    ]
    res = validate(rows, high=0.9)
    assert res["n"] == 4 and res["held_out"] is False    # <30 -> in-sample, flagged
    # heuristic catches only the marker failure; misses the pytest summary
    assert res["heuristic"]["tp"] == 1 and res["heuristic"]["fp"] == 0
    # ensemble catches BOTH failures (marker via floor, summary via high-confidence Laya), no FPs
    assert res["ensemble"]["tp"] == 2 and res["ensemble"]["fp"] == 0
    assert res["ensemble"]["rec"] == 1.0


def test_too_few_rows_is_reported_not_crashed():
    assert validate([{"output": "x", "truth_failure": False}]).get("note")
