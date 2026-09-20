"""Confidence signals for the loop's routing decisions.

This is the local, honest version of the "typed decision + confidence" idea (the shape TypeSafe's
Jev productised): NOT a calibrated model probability — a quantised 7B's logits are not calibrated, so
a number off them would be fiction — but a RELATIVE signal derived from the loop's OWN trajectory.
The loop already makes bounded decisions (which file is the culprit, repair vs. replan, done vs.
stuck); today it makes them as hard binary branches with no notion of how sure it is. A trajectory
signal lets it route on uncertainty the way Jev's callers do — act when confident, escalate when
not — while staying inside Newton's edge (free compute, unlimited time, fully local, no cloud model).

The one signal here, `error_signature`, powers repair-progress routing: if a fix for a given file is
followed by the SAME error, another repair on that file has low odds, so the loop should escalate
(re-plan) rather than keep re-fixing a file it can't crack. It can only ever make the loop MORE
cautious — stop wasted work — never force a confident wrong action, so a misfire costs nothing worse
than the baseline.
"""

from __future__ import annotations

import re

_ERR_LINE = re.compile(r"[A-Za-z_][\w.]*Error\b")


def error_signature(text: str) -> str:
    """A stable fingerprint of a failure, so 'the same error again' is detectable across attempts.

    Keeps the telling lines (an `ExceptionType: message`, a pytest `E ...`/`FAILED ...`) and strips
    the volatile parts — file paths, line numbers, hex addresses, and bare integers — so two runs of
    the SAME bug hash equal while a genuinely different failure does not. Deliberately tolerant: a
    repair that only changes an assertion's numbers (still the same broken logic) reads as no progress,
    which is exactly what we want to catch."""
    keep: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith(("E ", "FAILED ", "ERROR ")) or s.endswith("Error") \
                or _ERR_LINE.match(s) or ": error:" in s:
            keep.append(s)
    sig = " | ".join(keep) if keep else (text or "").strip()[-300:]
    sig = re.sub(r'File "[^"]+", line \d+', "", sig)      # drop path + line
    sig = re.sub(r"0x[0-9a-fA-F]+", "", sig)              # drop memory addresses
    sig = re.sub(r"\bline \d+\b", "", sig)
    sig = re.sub(r"\d+", "#", sig)                        # collapse remaining integers
    sig = re.sub(r"\s+", " ", sig).strip().lower()
    return sig[:200]
