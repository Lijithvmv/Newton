"""Repair-loop feedback fixes (from the ledger diagnostic, 2026-09-14).

Two gaps made ledger fail at every effort level: (1) the failure signal fed to repair was the useless
leading pytest dots, not the assertion; (2) a behavioural assertion names only the test file, so the
culprit-finder blamed the test instead of the production code it exercises. These prove both fixes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from newton.config import load_settings
from newton.loop import LoopEngine, effort
from newton.loop.engine import ContextShaper
from newton.loop.execute import _extract_failure
from newton.loop.state import RUN, WRITE, LoopState, Step

# A realistic `pytest -q` failure: progress dots first, the actionable lines at the end.
_PYTEST_OUT = """..F.                                                                     [100%]
=================================== FAILURES ===================================
____________________ test_overdraft_raises_and_is_atomic _____________________
    def test_overdraft_raises_and_is_atomic(tmp_path):
>       with pytest.raises(ValueError):
E       Failed: DID NOT RAISE ValueError
test_ledger.py:32: Failed
=========================== short test summary info ===========================
FAILED test_ledger.py::test_overdraft_raises_and_is_atomic - Failed: DID NOT RAISE ValueError
1 failed, 3 passed in 0.18s"""


def test_extract_failure_surfaces_the_assertion_not_the_dots():
    got = _extract_failure(_PYTEST_OUT)
    assert "DID NOT RAISE ValueError" in got            # the actionable signal
    assert "FAILED test_ledger.py::test_overdraft_raises_and_is_atomic" in got
    assert not got.startswith("..F.")                   # not the useless progress line


def _engine(root: Path):
    return LoopEngine(load_settings(str(root)), shaper=ContextShaper(root), effort=effort("normal"))


def test_culprit_routes_behavioural_failure_to_the_imported_module():
    """When the failure names only the test file, the fix must target the production module the test
    imports (service.py), NOT the test."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "test_ledger.py").write_text("from service import Ledger\n", encoding="utf-8")
        eng = _engine(root)
        state = LoopState(goal="build a ledger", steps=[
            Step(id="s_db", kind=WRITE, file="db.py", goal="db"),
            Step(id="s_svc", kind=WRITE, file="service.py", goal="service"),
            Step(id="s_run", kind=RUN, command="{py} -m pytest -q", goal="run tests"),
        ])
        culprit = eng._culprit_step(
            "FAILED test_ledger.py::test_overdraft_raises_and_is_atomic - Failed: DID NOT RAISE ValueError",
            state)
        assert culprit is not None
        assert culprit.file == "service.py"             # routed to the imported production module


def test_blast_radius_lists_production_dependents_and_used_symbols():
    """A fix to a shared file must be told which PRODUCTION files depend on it and the names they use,
    so it keeps their interface working (the D63 cross-file break). Test files are excluded."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "geometry.py").write_text("def circle_area(r):\n    return 3.14159 * r * r\n", encoding="utf-8")
        (root / "report.py").write_text(
            "from geometry import circle_area\n\ndef summary(r):\n    return circle_area(r)\n", encoding="utf-8")
        (root / "test_geo.py").write_text(
            "from geometry import circle_area\n\ndef test_x():\n    assert circle_area(0) == 0\n", encoding="utf-8")
        eng = _engine(root)
        blast = eng._blast_radius("geometry.py")
        assert "report.py" in blast and "circle_area" in blast    # names the caller + the symbol it uses
        assert "test_geo.py" not in blast                          # test files are not "callers to protect"
        assert eng._blast_radius("report.py") == ""                # nothing imports report.py → no blast


def test_repair_injects_blast_radius_into_the_pending_fix():
    """End-to-end wiring: a failed check that blames a shared file makes the repair carry that file's
    blast radius (its production dependents), so the next attempt keeps their interface working."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "geometry.py").write_text("def area_of_circle(r):\n    return 3.14159 * r * r\n", encoding="utf-8")
        (root / "report.py").write_text(
            "from geometry import area_of_circle\n\ndef summary(r):\n    return area_of_circle(r)\n", encoding="utf-8")
        eng = _engine(root)
        run = Step(id="s_run", kind=RUN, command="{py} -m pytest -q", goal="run tests",
                   status="failed", result="geometry.py:2: TypeError: bad thing")
        state = LoopState(goal="g", steps=[
            Step(id="s_geo", kind=WRITE, file="geometry.py", goal="geometry"),
            Step(id="s_rep", kind=WRITE, file="report.py", goal="report"),
            run,
        ])
        assert eng._repair_from_test_failure(state) is True
        pending = eng._pending_error.get("s_geo", "")
        assert "Blast radius" in pending and "report.py" in pending


def test_culprit_still_prefers_a_named_production_file():
    """A traceback that names the production file directly still fixes that file."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        eng = _engine(root)
        state = LoopState(goal="g", steps=[
            Step(id="s_svc", kind=WRITE, file="service.py", goal="service"),
            Step(id="s_run", kind=RUN, command="{py} -m pytest -q", goal="run tests"),
        ])
        culprit = eng._culprit_step("service.py:20: TypeError: bad thing", state)
        assert culprit is not None and culprit.file == "service.py"
