"""Ensemble failure decider - the deterministic floor plus high-confidence, calibrated Laya.

The honest way to let a calibrated model ACT (once measured): never surrender the guarantees the
deterministic check already gives. `error_shaped` has perfect precision (it only flags unambiguous
markers) but poor recall (it misses pytest summaries, npm errors, panics). Laya has the recall but
introduces false positives - including the occasional CONFIDENT one the spike caught. So:

  1. If the deterministic marker fires -> failure, confidence 1.0 (the zero-false-positive floor).
  2. Else if calibrated Laya says failure with confidence >= a high threshold -> failure (recover the
     marker-less failures the regex misses), tagged as the lower-trust source.
  3. Else -> not a failure.

This preserves the regex's precision as a floor and only lets Laya ADD recall in its trustworthy band -
never blind-trust, exactly the "repeatable is not right" lesson. It stays a `Decider` that returns a
typed `Decision`; it is NOT wired into the acting loop until the advisory corpus shows it beats the
deterministic baseline on real data (Rule 4).
"""

from __future__ import annotations

from typing import Protocol

from .calibrate import TemperatureCalibrator
from .decision import NOUL, Decision
from .output_check import error_shaped


class _FailureJudge(Protocol):
    def judge_failure(self, text: str) -> Decision | None: ...


class EnsembleFailureDecider:
    """Combine the deterministic marker floor with a high-confidence, calibrated Laya judgment."""

    name = "ensemble (regex floor + high-confidence laya)"

    def __init__(self, judge: _FailureJudge | None = None,
                 calibrator: TemperatureCalibrator | None = None, high: float = 0.9) -> None:
        self.judge = judge
        self.calibrator = calibrator or TemperatureCalibrator()
        self.high = high

    def decide(self, text: str) -> Decision:
        if error_shaped(text):
            return Decision(name="failure_check", kind=NOUL, question="Is this output a failure?",
                            answer="failure", confidence=1.0, decider="heuristic",
                            reason="deterministic failure marker")
        if self.judge is not None:
            d = self.judge.judge_failure(text)
            if d is not None and d.answer == "failure":
                raw = d.confidence                          # P(failure) when the judge says failure
                p = self.calibrator.apply(raw)
                if p >= self.high:
                    return Decision(name="failure_check", kind=NOUL,
                                    question="Is this output a failure?", answer="failure",
                                    confidence=p, decider="laya",
                                    reason=f"marker-less failure, calibrated P={p:.2f} (raw {raw:.2f})")
        return Decision(name="failure_check", kind=NOUL, question="Is this output a failure?",
                        answer="ok", confidence=1.0, decider="heuristic",
                        reason="no deterministic marker and no high-confidence model signal")
