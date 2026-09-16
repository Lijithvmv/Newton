"""Change verdict — a graduated, advisory review of a finished build.

Newton already knows two things about the code it produced: whether each change VERIFIES (parses +
imports/tests) and its BLAST RADIUS (which production files depend on it). Collapsing that to a binary
green/red throws away the middle case — a change that *works* but touches code other files rely on,
which a human should glance at before trusting it. This classifies the run (the idea from kin's
change-review):

  * WOULD BLOCK    — something did not verify.
  * NEEDS ATTENTION — everything verified, but other code depends on the changed files.
  * PASS           — verified, and nothing else in the project depends on the changes.

Advisory only: it never changes what the loop does — it makes the human-in-the-loop signal honest
instead of just green/red.
"""

from __future__ import annotations

from dataclasses import dataclass

PASS, ATTENTION, BLOCK = "pass", "attention", "block"
_LABEL = {PASS: "PASS", ATTENTION: "NEEDS ATTENTION", BLOCK: "WOULD BLOCK"}


@dataclass
class Verdict:
    level: str          # pass | attention | block
    reason: str

    @property
    def label(self) -> str:
        return _LABEL.get(self.level, self.level.upper())


def review_run(failed_steps: int, files_with_dependents: list[tuple[str, list[str]]]) -> Verdict:
    """Classify a finished build.

    `failed_steps`: number of steps that failed or were blocked (i.e. did not verify).
    `files_with_dependents`: (changed_file, [production files that import it]) for changed files that
    other code depends on. Order of precedence: a failure BLOCKS; otherwise cross-file impact earns
    ATTENTION; otherwise PASS.
    """
    if failed_steps > 0:
        s = "step" if failed_steps == 1 else "steps"
        return Verdict(BLOCK, f"{failed_steps} {s} did not verify")
    if files_with_dependents:
        names = ", ".join(f for f, _ in files_with_dependents[:4])
        total = sum(len(deps) for _, deps in files_with_dependents)
        noun = "file" if total == 1 else "files"
        return Verdict(
            ATTENTION,
            f"verified, but {total} other {noun} depend on changes to {names} — "
            f"review the cross-file impact before trusting it",
        )
    return Verdict(PASS, "verified; nothing else in the project depends on the changes")
