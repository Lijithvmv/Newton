"""Label-free Choice de-biasing (AnyJev L0): rotation-averaging cancels position bias while
preserving a genuine signal, and label-prior correction removes a model's standing lean."""

from __future__ import annotations

from newton.loop.debias import debiased_choice, rotations, top_choice


def test_rotations_put_each_option_first_once():
    assert rotations(["a", "b", "c"]) == [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]]
    assert rotations(["a", "b", "c", "d"], cap=2) == [["a", "b", "c", "d"], ["b", "c", "d", "a"]]


def _position_biased(order):
    """A model that just favours whichever option is presented FIRST (pure position bias)."""
    n = len(order)
    d = {o: 1.0 / n for o in order}
    d[order[0]] += 0.3
    s = sum(d.values())
    return {o: d[o] / s for o in d}


def test_debias_cancels_pure_position_bias():
    opts = ["a.py", "b.py", "c.py"]
    one_order = _position_biased(opts)
    assert one_order["a.py"] > one_order["b.py"]          # biased toward the first option
    deb = debiased_choice(_position_biased, opts)
    assert max(deb.values()) - min(deb.values()) < 0.02   # rotation-averaged -> ~uniform


def _signal_plus_bias(order):
    """b.py is genuinely the culprit (high wherever it sits), plus a position bias on the first slot."""
    d = {o: 0.1 for o in order}
    d["b.py"] = 0.6
    d[order[0]] += 0.3
    s = sum(d.values())
    return {o: d[o] / s for o in d}


def test_debias_preserves_the_true_signal():
    opts = ["a.py", "b.py", "c.py"]
    deb = debiased_choice(_signal_plus_bias, opts)
    assert top_choice(deb)[0] == "b.py"                   # the real culprit still wins after de-biasing


def test_prior_correction_removes_a_standing_lean():
    opts = ["x", "y"]
    # the model always leans 0.7/0.3 toward x regardless of order (a label prior, not a real signal)
    biased = debiased_choice(lambda o: {"x": 0.7, "y": 0.3}, opts)
    assert biased["x"] > biased["y"]
    corrected = debiased_choice(lambda o: {"x": 0.7, "y": 0.3}, opts, priors={"x": 0.7, "y": 0.3})
    assert abs(corrected["x"] - corrected["y"]) < 1e-6    # divided out -> even
