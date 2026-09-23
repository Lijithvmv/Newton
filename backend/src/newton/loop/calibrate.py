"""Temperature-scaling calibration for a local decision head - AnyJev's L1, applied to Laya.

The Jev-loop discipline only works if a probability MEANS something: "act above 0.9" is safe only when
0.9 really is right ~90% of the time. Base Laya is close (spike ECE 0.068) but its own card and load
warning say to calibrate per-domain. AnyJev (Nokia) frames the recipe cleanly: L1 is temperature
scaling on a modest labeled set. This fits a single temperature T on the advisory corpus (item 2's
log of Laya's predictions vs Newton's deterministic ground truth) that minimizes negative
log-likelihood, and applies it to sharpen or soften future confidences so thresholds behave.

It's data-driven and honest: with no corpus T stays 1.0 (a no-op); it only ever adjusts once real
Newton outcomes have been observed. Pure stdlib - no numpy, no torch.
"""

from __future__ import annotations

import math
from pathlib import Path

from .judge import read_advisory

_EPS = 1e-6


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1 + ex)


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float:
    """Expected Calibration Error over (p_true, label) pairs — mean gap between confidence and
    accuracy, binned. 0 = a probability that means what it says."""
    if not pairs:
        return 0.0
    total = len(pairs)
    e = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(p, y) for p, y in pairs if (lo < p <= hi) or (b == 0 and p <= hi)]
        if not bucket:
            continue
        conf = sum(p for p, _ in bucket) / len(bucket)
        acc = sum(1 for _, y in bucket if y) / len(bucket)
        e += (len(bucket) / total) * abs(conf - acc)
    return e


def fit_temperature(pairs: list[tuple[float, bool]]) -> float:
    """The temperature T in [0.1, 5.0] that minimizes NLL of the (p_true, label) pairs under
    p' = sigmoid(logit(p)/T). T>1 softens overconfidence; T<1 sharpens. Returns 1.0 (no-op) when
    there's too little data to fit responsibly (< 20 pairs)."""
    if len(pairs) < 20:
        return 1.0
    best_t, best_nll = 1.0, float("inf")
    t = 0.1
    while t <= 5.0 + 1e-9:
        nll = 0.0
        for p, y in pairs:
            q = min(max(_sigmoid(_logit(p) / t), _EPS), 1 - _EPS)
            nll -= math.log(q) if y else math.log(1 - q)
        if nll < best_nll:
            best_nll, best_t = nll, t
        t += 0.05
    return round(best_t, 3)


class TemperatureCalibrator:
    """A fitted temperature applied to a decision head's raw probability. Default T=1.0 is a pass-through
    until it's fit on real data, so wrapping a judge with an unfit calibrator changes nothing."""

    def __init__(self, temperature: float = 1.0) -> None:
        self.T = temperature

    def apply(self, p: float) -> float:
        """Calibrate a raw P(true)."""
        if self.T == 1.0:
            return p
        return _sigmoid(_logit(p) / self.T)

    def fit(self, pairs: list[tuple[float, bool]]) -> TemperatureCalibrator:
        self.T = fit_temperature(pairs)
        return self


def pairs_from_advisory(rows: list[dict]) -> list[tuple[float, bool]]:
    """Reconstruct (P(failure), actually_failed) pairs from advisory log rows, so a corpus of Laya's
    predictions vs ground truth can fit a temperature."""
    pairs: list[tuple[float, bool]] = []
    for r in rows:
        if "p_failure" in r:
            p = float(r["p_failure"])
        else:                                          # older rows: reconstruct from confidence + pred
            conf = float(r.get("confidence", 0.5))
            p = conf if r.get("pred_failure") else 1 - conf
        pairs.append((p, bool(r.get("truth_failure"))))
    return pairs


def calibrator_from_advisory(path: Path) -> TemperatureCalibrator:
    """Fit a calibrator directly from a project's advisory log (empty/small → a 1.0 no-op)."""
    return TemperatureCalibrator().fit(pairs_from_advisory(read_advisory(path)))
