"""BestOfN (D67) — whole-task best-of-N by directory isolation + verified selection.

Attempts are scripted through an injected fake engine (no model), but the isolation and selection are
real: each attempt runs in its own directory copy, and the winner's files are materialised into the
project root."""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.bestofn import Attempt, BestOfN
from newton.loop.engine import LoopResult
from newton.loop.state import DONE, FAILED, LoopState, Step


def _result(ok: bool, done: int = 1, failed: int = 0):
    steps = [Step(id=f"d{i}", goal="", status=DONE) for i in range(done)]
    steps += [Step(id=f"f{i}", goal="", status=FAILED) for i in range(failed)]
    return LoopResult(ok, f"{done} done, {failed} failed", LoopState(goal="g", steps=steps))


def _factory(winner_index: int):
    """A fake engine: writes a marker file into its own project_root and returns a scripted result;
    only `winner_index` verifies OK. Attempt index is read from the dir name (…/attempt-<i>)."""
    def make(settings, emit):
        root = settings.project_root
        idx = int(root.name.split("-")[-1]) if root.name.startswith("attempt-") else 0
        class _E:
            def run(self, goal, resume=False):
                (root / "out.txt").write_text(f"attempt {idx}", encoding="utf-8")
                return _result(ok=(idx == winner_index), done=(3 if idx == winner_index else 1),
                               failed=(0 if idx == winner_index else 2))
        return _E()
    return make


# --- scoring ---

def test_score_prefers_ok_then_done():
    a_ok = Attempt(0, None, _result(True, done=1))
    a_more = Attempt(1, None, _result(False, done=5))
    a_fewer = Attempt(2, None, _result(False, done=2))
    assert a_ok.score > a_more.score                 # verified beats more-done-but-unverified
    assert a_more.score > a_fewer.score              # among unverified, more done wins


# --- opt-in: N==1 is a plain in-place run ---

def test_best_of_one_runs_in_place(tmp_path):
    b = BestOfN(load_settings(tmp_path), 1, make_engine=_factory(0))
    res = b.run("build it")
    assert res.ok and res.winner.root == tmp_path
    assert (tmp_path / "out.txt").read_text() == "attempt 0"   # built in place
    assert not (tmp_path / ".newton" / "bestofn").exists()     # no isolation overhead


# --- N>1: isolate, select the verified winner, materialise it ---

def test_best_of_n_selects_verified_winner_and_materialises_it(tmp_path):
    b = BestOfN(load_settings(tmp_path), 3, make_engine=_factory(winner_index=1))
    res = b.run("build it")
    assert res.ok and res.winner.index == 1                    # the one that verified
    assert (tmp_path / "out.txt").read_text() == "attempt 1"   # winner copied into the real root
    assert len(res.attempts) == 3


def test_best_of_n_keeps_losers_for_inspection(tmp_path):
    BestOfN(load_settings(tmp_path), 3, make_engine=_factory(winner_index=2)).run("build it")
    base = tmp_path / ".newton" / "bestofn"
    for i in range(3):
        assert (base / f"attempt-{i}" / "out.txt").read_text() == f"attempt {i}"   # all kept


def test_best_of_n_picks_best_of_a_bad_lot_when_none_verify(tmp_path):
    # winner_index=99 → nobody verifies; selection falls to most-done/fewest-failed. All losers here
    # are identical (done=1, failed=2), so it just picks a deterministic max — and still runs.
    res = BestOfN(load_settings(tmp_path), 2, make_engine=_factory(winner_index=99)).run("build it")
    assert not res.ok and len(res.attempts) == 2               # honest: reports not-verified
    assert (tmp_path / "out.txt").exists()                     # still materialised a best effort
