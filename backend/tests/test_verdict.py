"""Graduated verify verdict (from kin's change-review): PASS / NEEDS ATTENTION / WOULD BLOCK.

Advisory only — it composes Newton's existing signals (did steps verify + blast radius) into a clearer
human-in-the-loop signal than binary green/red. These prove the classification and the engine wiring.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from newton.config import load_settings
from newton.loop import LoopEngine, effort
from newton.loop.engine import ContextShaper
from newton.loop.state import WRITE, LoopState, Step
from newton.loop.verdict import ATTENTION, BLOCK, PASS, review_run


def test_pass_when_verified_and_no_dependents():
    v = review_run(0, [])
    assert v.level == PASS and v.label == "PASS"


def test_attention_when_verified_but_changes_have_dependents():
    v = review_run(0, [("service.py", ["report.py", "api.py"])])
    assert v.level == ATTENTION and v.label == "NEEDS ATTENTION"
    assert "service.py" in v.reason and "2 other files" in v.reason


def test_block_when_a_step_failed():
    v = review_run(2, [])
    assert v.level == BLOCK and v.label == "WOULD BLOCK" and "2 steps" in v.reason


def test_block_takes_precedence_over_attention():
    v = review_run(1, [("x.py", ["y.py"])])
    assert v.level == BLOCK          # a failure blocks even if there is also cross-file impact


def _engine(root: Path) -> LoopEngine:
    return LoopEngine(load_settings(str(root)), shaper=ContextShaper(root), effort=effort("normal"))


def test_engine_verdict_flags_cross_file_impact():
    """A finished build where a changed file has a production dependent → NEEDS ATTENTION."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "geometry.py").write_text("def area(r):\n    return 3.14 * r * r\n", encoding="utf-8")
        (root / "report.py").write_text(
            "from geometry import area\n\ndef summary(r):\n    return area(r)\n", encoding="utf-8")
        eng = _engine(root)
        state = LoopState(goal="g", steps=[
            Step(id="geo", kind=WRITE, file="geometry.py", goal="geometry", status="done"),
            Step(id="rep", kind=WRITE, file="report.py", goal="report", status="done"),
        ])
        vd = eng._run_verdict(state)
        assert vd.level == ATTENTION and "geometry.py" in vd.reason


def test_engine_verdict_blocks_on_failed_step():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        eng = _engine(root)
        state = LoopState(goal="g", steps=[
            Step(id="a", kind=WRITE, file="a.py", goal="a", status="done"),
            Step(id="run", kind="run", command="{py} -m pytest -q", goal="tests", status="failed"),
        ])
        assert eng._run_verdict(state).level == BLOCK


def test_engine_verdict_pass_when_clean():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "solo.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        eng = _engine(root)
        state = LoopState(goal="g", steps=[
            Step(id="s", kind=WRITE, file="solo.py", goal="solo", status="done"),
        ])
        assert eng._run_verdict(state).level == PASS
