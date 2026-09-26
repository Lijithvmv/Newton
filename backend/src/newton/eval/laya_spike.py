"""Laya eval spike - does a LOCAL, calibrated System-One decision model beat Newton's heuristic on a
bounded verify decision? (Measure, never assert - Rule 4.)

Target decision: **output-failure detection** - "does this command/test output indicate the command
FAILED?" It's a low-cardinality Noul (best case for base Laya, which the model card says is near-chance
on high-cardinality tasks), it sits on Newton's verify moat, and it already HAS a heuristic baseline
(`loop/output_check.error_shaped`) - so this is a true A/B, not a demo.

What we measure on a labeled set that deliberately stresses the heuristic's blind spots (pytest
`FAILED`/`1 failed` summaries, `npm ERR!`, `Error: connection refused`, bare `AssertionError`,
panics - real failures with no marker the regex looks for):
  * accuracy / precision / recall / F1 for each decider, and
  * for a confidence-bearing decider (Laya): calibration (ECE) + a coverage-vs-accuracy curve, i.e.
    can we ACT on the high-confidence band and abstain on the rest (the whole point of calibration).

Run:  python -m newton.eval.laya_spike
Laya is optional: if it isn't installed the baseline still runs and the harness reports how to add it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..loop.output_check import error_shaped

# --- Labeled dataset: (output_text, is_failure, note). Balanced, and loaded with cases the
# conservative regex MISSES (marked [MISS]) so recall is actually tested, plus tricky successes that
# contain error-ish words but are fine (so precision is actually tested). ---
CASES: list[tuple[str, bool, str]] = [
    # failures the heuristic already catches (markers)
    ("Traceback (most recent call last):\n  File \"a.py\", line 3\nValueError: bad", True, "traceback"),
    ("ModuleNotFoundError: No module named 'requests'", True, "modulenotfound"),
    ("bash: pytest: command not found", True, "command not found"),
    ("<!DOCTYPE html><html><body>500 Internal Server Error</body></html>", True, "html error page"),
    ("Segmentation fault (core dumped)", True, "segfault"),
    # failures the heuristic MISSES ([MISS] the real value test - no marker it looks for)
    ("=== 1 failed, 2 passed in 0.31s ===", True, "[MISS] pytest summary"),
    ("FAILED tests/test_bank.py::test_transfer - assert 90 == 100", True, "[MISS] pytest FAILED line"),
    ("npm ERR! code ELIFECYCLE\nnpm ERR! Exit status 1", True, "[MISS] npm error"),
    ("Error: connect ECONNREFUSED 127.0.0.1:5432", True, "[MISS] generic Error:"),
    ("AssertionError: expected status 200 but got 500", True, "[MISS] bare AssertionError"),
    ("panic: runtime error: index out of range [3] with length 3", True, "[MISS] go panic"),
    ("fatal error: 'stdio.h' file not found", True, "[MISS] compiler fatal"),
    ("Exception in thread \"main\" java.lang.NullPointerException", True, "[MISS] java exception"),
    ("build failed with 2 errors", True, "[MISS] build failed"),
    # successes - several tricky (contain error-ish words but are NOT failures)
    ("=== 3 passed in 0.12s ===", False, "tests passed"),
    ("Successfully installed newton-0.1.0", False, "pip ok"),
    ("Build complete. 42 modules transformed.", False, "build ok"),
    ("All checks passed!", False, "ruff ok"),
    ("Compiled successfully.", False, "tsc ok"),
    ("warning: 'x' is deprecated [-Wdeprecated]", False, "warning only"),
    ("=== 5 passed, 1 warning in 0.4s ===", False, "passed w/ warning"),
    ("Added error handling to the auth module", False, "'error' in a success msg"),
    ("Done. 0 errors, 0 warnings.", False, "'0 errors'"),
    ("Downloading model... done", False, "progress ok"),
]

NOUL_INSTRUCTIONS = (
    "Does this command, build, or test output indicate that it FAILED - an error, a traceback, "
    "a crash, or failing tests - as opposed to succeeding (tests passing, a clean build, a warning "
    "that is not fatal)?"
)


@dataclass
class Pred:
    label: bool          # predicted failure?
    confidence: float    # in [0,1]; the heuristic is degenerate (always 1.0 - its whole limitation)


class Decider(Protocol):
    name: str
    def predict(self, text: str) -> Pred: ...


class HeuristicDecider:
    """Newton's current baseline: the conservative regex. Binary - no real confidence (always 1.0),
    which is exactly what a calibrated model would add."""
    name = "heuristic (error_shaped)"

    def predict(self, text: str) -> Pred:
        return Pred(label=error_shaped(text), confidence=1.0)


class LayaDecider:
    """The candidate: a local, calibrated System-One model answering the failure question as a Noul.
    Lazily loaded so Newton never depends on it; raises RuntimeError with install help if absent."""
    name = "laya (noul)"

    def __init__(self, model: str = "convaiinnovations/laya") -> None:
        try:
            import laya  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Laya isn't installed. This is an OPTIONAL spike dependency - install it in the "
                "backend venv with `pip install laya` (~808MB weights, CPU-capable). Newton itself "
                "never requires it."
            ) from e
        from ..loop.judge import resolve_laya_path  # same pinned weights the judge uses
        self._agent = laya.load(resolve_laya_path(model))

    def predict(self, text: str) -> Pred:
        q = {"failed": {"type": "noul", "instructions": NOUL_INSTRUCTIONS}}
        result = self._agent.predict(text, q)
        # Real shape (probed 2026-09-23):
        #   {'answers': {'failed': {'type':'noul', 'noul': 0.3184, 'confidence': 0.6816, ...}}}
        ans = result["answers"]["failed"]
        p = float(ans["noul"])                                  # P(true) = P(failed)
        conf = float(ans.get("confidence", max(p, 1.0 - p)))    # Laya's own calibrated confidence
        return Pred(label=p >= 0.5, confidence=conf)


class _EnsembleSpikeDecider:
    """The proposed acting decider: the deterministic marker floor (never false-positives) plus Laya
    only in its HIGH-confidence band. The Rule-4 question this answers: does it keep the regex's
    precision while recovering the marker-less failures Laya catches?"""
    name = "ensemble (regex floor + high-conf laya)"

    def __init__(self, laya: LayaDecider, high: float = 0.9) -> None:
        self.laya = laya
        self.high = high

    def predict(self, text: str) -> Pred:
        if error_shaped(text):
            return Pred(label=True, confidence=1.0)             # deterministic floor
        pr = self.laya.predict(text)
        if pr.label and pr.confidence >= self.high:             # trust Laya only when confident
            return Pred(label=True, confidence=pr.confidence)
        return Pred(label=False, confidence=max(pr.confidence, 1.0 - pr.confidence))


def score(decider: Decider) -> dict:
    tp = fp = fn = tn = 0
    confs: list[tuple[float, bool]] = []       # (confidence, correct) for calibration/coverage
    for text, truth, _ in CASES:
        pr = decider.predict(text)
        correct = pr.label == truth
        confs.append((pr.confidence, correct))
        if pr.label and truth:
            tp += 1
        elif pr.label and not truth:
            fp += 1
        elif not pr.label and truth:
            fn += 1
        else:
            tn += 1
    n = len(CASES)
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    ece = _ece(confs)
    return {"acc": acc, "prec": prec, "rec": rec, "f1": f1, "tp": tp, "fp": fp,
            "fn": fn, "tn": tn, "ece": ece, "confs": confs}


def _ece(confs: list[tuple[float, bool]], bins: int = 5) -> float:
    """Expected Calibration Error - how far predicted confidence sits from actual accuracy, binned.
    ~0 means 'a 0.9 really is right ~90% of the time'. Degenerate (all-1.0) confidence just measures
    (1 - accuracy), which is why it's uninformative for the heuristic - noted in the report."""
    if not confs:
        return 0.0
    total = len(confs)
    e = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [c for c in confs if (lo < c[0] <= hi) or (b == 0 and c[0] <= hi)]
        if not bucket:
            continue
        avg_conf = sum(c[0] for c in bucket) / len(bucket)
        acc = sum(1 for c in bucket if c[1]) / len(bucket)
        e += (len(bucket) / total) * abs(avg_conf - acc)
    return e


def _coverage_curve(confs: list[tuple[float, bool]]) -> list[tuple[float, float, int]]:
    """Accuracy when we ACT only on the most-confident items: (min_confidence, accuracy, n_covered).
    The payoff of calibration - a high-confidence band you can automate + a low band you abstain on."""
    out = []
    for thr in (0.9, 0.8, 0.7, 0.6, 0.5):
        covered = [c for c in confs if c[0] >= thr]
        if covered:
            acc = sum(1 for c in covered if c[1]) / len(covered)
            out.append((thr, acc, len(covered)))
    return out


def main() -> int:
    print("\nLaya eval spike - output-failure detection (Noul)\n" + "=" * 52)
    print(f"{len(CASES)} labeled cases "
          f"({sum(1 for _, t, _ in CASES if t)} failures / {sum(1 for _, t, _ in CASES if not t)} ok); "
          f"[MISS] = a failure the heuristic structurally misses.\n")

    deciders: list[Decider] = [HeuristicDecider()]
    laya_err = None
    try:
        laya = LayaDecider()
        deciders.append(laya)
        deciders.append(_EnsembleSpikeDecider(laya))   # the Rule-4 gate: does floor+high-conf beat both?
    except RuntimeError as e:
        laya_err = str(e)

    for d in deciders:
        s = score(d)
        # Which cases this decider gets WRONG (with confidence) — the honest per-item picture.
        wrong = []
        for text, truth, note in CASES:
            pr = d.predict(text)
            if pr.label != truth:
                kind = "false-positive" if pr.label else "false-negative"
                wrong.append(f"    {kind} (conf {pr.confidence:.2f}): {note}")
        print(f"[{d.name}]")
        print(f"  acc {s['acc']:.2f}  prec {s['prec']:.2f}  rec {s['rec']:.2f}  f1 {s['f1']:.2f}  "
              f"(tp {s['tp']} fp {s['fp']} fn {s['fn']} tn {s['tn']})")
        if "laya" in d.name:
            print(f"  ECE {s['ece']:.3f} (0 = perfectly calibrated)")
            cov = _coverage_curve(s["confs"])
            print("  coverage curve (act above threshold):")
            for thr, acc, n in cov:
                print(f"    conf >= {thr:.1f}: acc {acc:.2f} on {n}/{len(CASES)} cases")
        else:
            print("  (confidence is degenerate - always 1.0; no abstain band. That's the gap.)")
        if wrong:
            print("  wrong on:")
            for w in wrong:
                print(w)
        print()

    # Where the baseline fails - the cases that motivate a calibrated model.
    h = HeuristicDecider()
    missed = [(txt, note) for txt, truth, note in CASES if truth and not h.predict(txt).label]
    if missed:
        print(f"Heuristic false-negatives ({len(missed)} real failures it misses):")
        for _txt, note in missed:
            print(f"  - {note}")
        print()

    if laya_err:
        print("Laya not measured: " + laya_err)
        print("Then re-run:  python -m newton.eval.laya_spike")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
