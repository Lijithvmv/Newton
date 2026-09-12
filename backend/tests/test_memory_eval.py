"""Guard the memory-recall eval: weighting (provenance + decay) must beat flat cosine.

This locks the Rule-4 measurement in place — if a future change regresses recall so that the
weighted ranker no longer improves on flat cosine, this test fails instead of the claim quietly
becoming false.
"""

from __future__ import annotations

from newton.eval.memory_eval import run


def test_weighted_recall_beats_flat_on_the_adversarial_seed_set():
    r = run(k=2)
    # Flat cosine is fooled: an injected tool line and a stale memory out-score the real ones.
    assert r.flat_precision <= 0.5
    # Weighting recovers both trusted, recent memories into the top-2.
    assert r.weighted_precision == 1.0
    assert r.delta > 0


def test_injection_and_stale_are_demoted_by_weighting():
    r = run(k=2)
    labels_top2 = {s.label for s in r.weighted_ranked[:2]}
    assert "injection" not in labels_top2       # untrusted tool origin pushed out of the top
    assert "stale" not in labels_top2           # 150-day-old memory decayed out of the top
    assert labels_top2 == {"real-agent", "real-user"}
