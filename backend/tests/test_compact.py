"""Mechanical compaction: fit reference context by dropping/truncating, never rewriting."""

from __future__ import annotations

from newton.loop.compact import mechanical_compact, skeleton
from newton.loop.engine import ContextShaper
from newton.loop.state import DONE, WRITE, LoopState, Step

_CODE = '''import os
from decimal import Decimal

SURCHARGE_RATE = Decimal("0.37")


@cache
def total(amount: Decimal,
          region: str = "IN") -> Decimal:
    """Amount plus the regional surcharge."""
    rate = SURCHARGE_RATE if region == "IN" else Decimal(0)
    return amount * (1 + rate)


class Ledger:
    """Append-only ledger."""
    LIMIT = 100

    def add(self, x):
        self.items.append(x)

    def one_liner(self): return 1


if __name__ == "__main__":
    print(total(Decimal(100)))
'''


def test_skeleton_keeps_interface_verbatim_and_drops_bodies():
    s = skeleton(_CODE)
    for kept in ('import os', 'from decimal import Decimal', 'SURCHARGE_RATE = Decimal("0.37")',
                 '@cache', 'def total(amount: Decimal,', '          region: str = "IN") -> Decimal:',
                 '    """Amount plus the regional surcharge."""', 'class Ledger:', '    LIMIT = 100',
                 '    def add(self, x):', '    def one_liner(self): return 1'):
        assert kept in s.splitlines()
    assert "rate = SURCHARGE_RATE" not in s and "self.items.append" not in s
    assert "__main__" not in s
    assert all(ln in _CODE.splitlines() or ln.strip() == "..." for ln in s.splitlines())   # never rewritten


def test_skeleton_of_unparseable_fragment_filters_lines():
    frag = "    return x\n\ndef helper(y):\n    z = y * 2\nRATE = 3\n  broken ("
    s = skeleton(frag)
    assert "def helper(y):" in s and "RATE = 3" in s and "return x" not in s


def test_mechanical_compact_fits_room_and_keeps_headers():
    parts = [f"Relevant existing code — import it as module `pkg.m{i}` (file pkg/m{i}.py):\n```\n{_CODE}\n```"
             for i in range(6)]
    room = 1500
    out = mechanical_compact(parts, room)
    assert len(out) <= room
    for i in range(6):
        assert f"`pkg.m{i}`" in out                           # every import header survives
    assert "rate = SURCHARGE_RATE" not in out                 # bodies went first


def test_mechanical_compact_is_a_no_op_when_it_fits():
    parts = ["Already built `a.py`:\n```\nx = 1\n```"]
    assert mechanical_compact(parts, 1000) == parts[0]


def test_shaper_mechanical_mode_never_calls_the_summarizer(tmp_path):
    for i in range(4):
        (tmp_path / f"m{i}.py").write_text(_CODE * 3, encoding="utf-8")
    steps = [Step(id=f"s{i}", goal="", kind=WRITE, file=f"m{i}.py", status=DONE) for i in range(4)]
    target = Step(id="t", goal="", kind=WRITE, file="t.py", depends_on=[s.id for s in steps])
    state = LoopState(goal="build", steps=[*steps, target])

    def boom(text, room):
        raise AssertionError("summarizer must not run in mechanical mode")

    sh = ContextShaper(tmp_path, budget_chars=2500, summarize=boom, compactor="mechanical")
    ctx = sh.shape(state, target)
    assert len(ctx) <= 2500 and "Overall goal: build" in ctx
    assert sh.stats["compactions"] == 1 and sh.stats["ref_chars_out"] < sh.stats["ref_chars_in"]
