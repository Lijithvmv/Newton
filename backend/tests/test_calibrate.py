"""Temperature-scaling calibration (AnyJev's L1) - a probability that means what it says.

Fits a single temperature on (P(failure), actually_failed) pairs to correct over/under-confidence, so
'act above 0.9' behaves. Data-driven and honest: too little data leaves T=1.0 (a pass-through)."""

from __future__ import annotations

from newton.loop.calibrate import (
    TemperatureCalibrator,
    ece,
    fit_temperature,
    pairs_from_advisory,
)


def test_temperature_scaling_reduces_overconfidence():
    pairs = [(0.9, i < 60) for i in range(100)]          # claims 0.9 but only 60% actually failed
    before = ece(pairs)
    cal = TemperatureCalibrator().fit(pairs)
    assert cal.T != 1.0                                  # it learned to soften
    after = ece([(cal.apply(p), y) for p, y in pairs])
    assert after < before and before > 0.2               # calibration measurably tightened it


def test_unfit_calibrator_is_a_passthrough():
    assert TemperatureCalibrator().apply(0.83) == 0.83   # default T=1.0 changes nothing


def test_too_little_data_is_a_noop():
    assert fit_temperature([(0.9, True), (0.1, False)]) == 1.0   # < 20 pairs -> don't fit blindly


def test_pairs_from_advisory_reconstructs_probabilities():
    rows = [
        {"p_failure": 0.8, "truth_failure": True},
        {"confidence": 0.7, "pred_failure": False, "truth_failure": False},   # older row -> p=0.3
    ]
    pairs = pairs_from_advisory(rows)
    assert pairs[0] == (0.8, True)
    assert abs(pairs[1][0] - 0.3) < 1e-9 and pairs[1][1] is False
