"""The ensemble failure decider - deterministic floor + high-confidence calibrated Laya.

Proves the safe-acting contract: the regex's zero-false-positive floor is never surrendered, and Laya
only ADDS recall in its high-confidence band - never blind-trusted (the 'repeatable is not right'
lesson). Tested with a fake judge, so offline and fast."""

from __future__ import annotations

from newton.loop.decision import NOUL, Decision
from newton.loop.ensemble import EnsembleFailureDecider


class _FakeJudge:
    """Returns a fixed P(failure) regardless of input, so each branch can be exercised."""
    def __init__(self, p_failure: float) -> None:
        self.p = p_failure

    def judge_failure(self, text: str) -> Decision:
        pred = "failure" if self.p >= 0.5 else "ok"
        conf = self.p if pred == "failure" else 1 - self.p
        return Decision("advisory_output_check", NOUL, "q", pred, conf, "fake", "laya")


def test_floor_catches_markers_even_when_judge_says_ok():
    d = EnsembleFailureDecider(judge=_FakeJudge(0.0)).decide(
        "Traceback (most recent call last):\nValueError: bad")
    assert d.answer == "failure" and d.decider == "heuristic"   # deterministic floor wins


def test_high_confidence_laya_recovers_a_markerless_failure():
    d = EnsembleFailureDecider(judge=_FakeJudge(0.95)).decide("=== 1 failed, 2 passed ===")
    assert d.answer == "failure" and d.decider == "laya"        # regex misses it; Laya recovers it


def test_low_confidence_laya_does_not_flag():
    d = EnsembleFailureDecider(judge=_FakeJudge(0.6), high=0.9).decide("=== 1 failed ===")
    assert d.answer == "ok"                                     # below the trust threshold -> abstain


def test_clean_success_is_never_flagged():
    d = EnsembleFailureDecider(judge=_FakeJudge(0.1)).decide("3 passed in 0.1s")
    assert d.answer == "ok"


def test_works_without_a_judge_as_the_pure_floor():
    dec = EnsembleFailureDecider(judge=None)
    assert dec.decide("ModuleNotFoundError: no module named x").answer == "failure"
    assert dec.decide("all good").answer == "ok"
