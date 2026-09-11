"""BestOfN — whole-task best-of-N by isolation + verified selection (D67).

The convergent pattern across the local-agent field (superset's git worktrees, MyAgents' per-session
sidecars, harness-remote's opt-in isolation): isolate each attempt, run many, keep the one that
verifies. D58's best-of-N samples a single STEP by temperature; this samples the WHOLE TASK — run the
full loop N times, each in its own directory copy, then keep the attempt that best survives the
integration check (D65). On a single GPU the N runs are sequential (no speedup — that's expected); the
lever is SELECTION, not speed: it turns one probably-wrong 7B build into "N builds, keep the one that
actually loads and passes its tests."

Design (from harness-remote's principles): isolation is OPT-IN, never the default (N==1 is a plain
in-place run); the selector owns only orchestration while each attempt is a full, independent
LoopEngine run owning its own state + checkpoint; and the outcome is honest — every attempt's verify
result is emitted, the winner is named, and losing attempts are left on disk for inspection. No git:
Newton builds into a directory, so attempts are plain directory copies, not worktrees.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from .engine import LoopEngine, LoopResult

# Never copied into an attempt (seeding) or back out of it (materialising): Newton's own state, the
# per-attempt venv the integration check builds, and the usual heavy/derived trees.
_SKIP = {".newton", ".venv", "venv", "__pycache__", ".git", "node_modules", ".pytest_cache"}


@dataclass
class Attempt:
    index: int
    root: Path
    result: LoopResult

    @property
    def score(self) -> tuple[int, int, int, int]:
        """Higher is better: verified-ok first (the D65 gate passed AND everything's done), then most
        steps done, then fewest failed, then fewest blocked."""
        c = self.result.state.counts() if self.result.state else {}
        return (1 if self.result.ok else 0,
                c.get("done", 0), -c.get("failed", 0), -c.get("blocked", 0))


@dataclass
class BestOfNResult:
    ok: bool
    answer: str
    winner: Attempt
    attempts: list[Attempt]


class BestOfN:
    def __init__(self, settings: Settings, n: int, *,
                 emit: Callable[[str, Any], None] | None = None,
                 make_engine: Callable[[Settings, Callable], Any] | None = None) -> None:
        self.s = settings
        self.n = max(1, int(n))
        self.emit = emit or (lambda *a: None)
        self.root = Path(settings.project_root)
        # Injectable so tests can script attempts without a model; default is a real LoopEngine.
        self._make_engine = make_engine or (lambda st, emit: LoopEngine(st, emit=emit))

    def run(self, goal: str, *, resume: bool = False) -> BestOfNResult:
        if self.n == 1:                                    # opt-in: N==1 is today's in-place run
            res = self._make_engine(self.s, self.emit).run(goal, resume=resume)
            return BestOfNResult(res.ok, res.answer, Attempt(0, self.root, res), [])

        base = self.root / ".newton" / "bestofn"
        attempts: list[Attempt] = []
        for i in range(self.n):
            root_i = base / f"attempt-{i}"
            self._seed(root_i)                             # each attempt starts from the same state
            self.emit("note", f"Best-of-{self.n}: running attempt {i + 1} of {self.n}…")
            st_i = dataclasses.replace(self.s, project_root=root_i)
            res = self._make_engine(st_i, self.emit).run(goal)
            attempts.append(Attempt(i, root_i, res))
            self.emit("note", f"Attempt {i + 1}: "
                              f"{'verified OK' if res.ok else 'did not verify'} — {res.answer}")

        winner = max(attempts, key=lambda a: a.score)
        self.emit("note", f"Best-of-{self.n}: attempt {winner.index + 1} wins "
                          f"({'verified' if winner.result.ok else 'best of a bad lot'}). "
                          f"Losing attempts kept under .newton/bestofn for inspection.")
        self._materialise(winner.root)                     # copy the winner into the real project root
        return BestOfNResult(winner.result.ok, winner.result.answer, winner, attempts)

    # --- directory isolation (git-free) --------------------------------

    def _seed(self, dest: Path) -> None:
        """Copy the project's current starting state into a fresh attempt dir (empty on a new build,
        the existing files on a feature-add)."""
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        self._copy_tree(self.root, dest)

    def _materialise(self, win_root: Path) -> None:
        """Copy the winning attempt's files into the real project root, overwriting."""
        self._copy_tree(win_root, self.root, overwrite=True)

    def _copy_tree(self, src: Path, dest: Path, *, overwrite: bool = False) -> None:
        for item in src.iterdir():
            if item.name in _SKIP:
                continue
            tgt = dest / item.name
            if item.is_dir():
                if overwrite and tgt.exists():
                    shutil.rmtree(tgt, ignore_errors=True)
                shutil.copytree(item, tgt, ignore=shutil.ignore_patterns(*_SKIP), dirs_exist_ok=True)
            else:
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, tgt)


def best_of_default() -> int:
    """N from the environment (opt-in), defaulting to 1 = plain in-place run."""
    try:
        return max(1, int(os.environ.get("NEWTON_BEST_OF_N", "1")))
    except ValueError:
        return 1
