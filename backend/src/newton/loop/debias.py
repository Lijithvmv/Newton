"""Label-free de-biasing for Choice decisions - AnyJev's L0.

A decision model asked to pick among options is swayed by the ORDER the options are presented in
(position bias) and by its own prior lean toward certain labels. AnyJev's L0 corrects both without any
labels: present the same Choice under every rotation of the options and average the per-option
probabilities (position averages out), then optionally divide out the label priors and renormalize.

For Newton this hardens a Choice like "which file caused this failure?" so the answer doesn't depend on
the order we happened to list the candidate files. Pure functions over a `query` callable, so it's
model-agnostic and testable without loading anything: `query(ordered_options) -> {option: probability}`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

Query = Callable[[list[str]], dict[str, float]]


def rotations(options: Sequence[str], cap: int | None = None) -> list[list[str]]:
    """The cyclic rotations of `options` (each option appears first exactly once). `cap` limits how
    many rotations to use when the option set is large."""
    opts = list(options)
    n = len(opts)
    k = n if cap is None else min(cap, n)
    return [opts[i:] + opts[:i] for i in range(k)]


def debiased_choice(query: Query, options: Sequence[str],
                    priors: dict[str, float] | None = None, cap: int | None = None) -> dict[str, float]:
    """A position- (and optionally prior-) de-biased distribution over `options`.

    `query(ordered_options)` returns the model's probability per option for that presentation order.
    We average those distributions over cyclic rotations so no option is favoured by its slot; then, if
    `priors` is given (the model's lean measured on a neutral state), divide it out and renormalize."""
    opts = list(options)
    if not opts:
        return {}
    rots = rotations(opts, cap)
    acc = {o: 0.0 for o in opts}
    for order in rots:
        dist = query(order)
        for o in opts:
            acc[o] += float(dist.get(o, 0.0))
    avg = {o: acc[o] / len(rots) for o in opts}
    if priors:
        avg = {o: avg[o] / max(priors.get(o, 1e-6), 1e-6) for o in opts}
    total = sum(avg.values()) or 1.0
    return {o: avg[o] / total for o in opts}


def top_choice(dist: dict[str, float]) -> tuple[str, float]:
    """The winning option and its probability (empty distribution -> ('', 0.0))."""
    if not dist:
        return "", 0.0
    o = max(dist, key=lambda k: dist[k])
    return o, dist[o]
