"""Evidence-binding — a finished run records what it PROVED: the checks that ran and a content
fingerprint of every file produced, so the verdict is trustworthy without a re-run."""

from __future__ import annotations

import hashlib

from newton.loop.evidence import collect_evidence
from newton.loop.state import BLOCKED, DONE, RUN, WRITE, LoopState, Step


def _wrote(tmp_path, file, text):
    (tmp_path / file).write_text(text, encoding="utf-8")
    return Step(id=file, goal="", kind=WRITE, file=file, status=DONE)


def test_collect_records_checks_and_fingerprints_files(tmp_path):
    state = LoopState(goal="g", steps=[
        _wrote(tmp_path, "service.py", "def add(a, b):\n    return a + b\n"),
        Step(id="check", goal="run the tests", kind=RUN, command="pytest -q",
             status=DONE, result="2 passed"),
    ])
    ev = collect_evidence(state, tmp_path)
    assert ev.verified is True
    assert [c.id for c in ev.checks] == ["check"] and ev.checks[0].passed
    assert ev.checks_passed == 1
    assert len(ev.artifacts) == 1
    a = ev.artifacts[0]
    assert a.file == "service.py"
    assert a.sha256 == hashlib.sha256((tmp_path / "service.py").read_bytes()).hexdigest()
    assert a.bytes > 0

def test_fingerprint_changes_with_content(tmp_path):
    s1 = LoopState(goal="g", steps=[_wrote(tmp_path, "m.py", "x = 1\n")])
    h1 = collect_evidence(s1, tmp_path).artifacts[0].sha256
    (tmp_path / "m.py").write_text("x = 2\n", encoding="utf-8")
    h2 = collect_evidence(s1, tmp_path).artifacts[0].sha256
    assert h1 != h2                                       # a real change moves the fingerprint

def test_a_failed_check_makes_it_unverified(tmp_path):
    state = LoopState(goal="g", steps=[
        _wrote(tmp_path, "m.py", "x = 1\n"),
        Step(id="check", goal="tests", kind=RUN, command="pytest", status=BLOCKED,
             result="E AssertionError"),
    ])
    ev = collect_evidence(state, tmp_path)
    assert ev.verified is False
    assert ev.checks[0].passed is False
    assert "AssertionError" in ev.checks[0].detail

def test_summary_is_compact(tmp_path):
    state = LoopState(goal="g", steps=[
        _wrote(tmp_path, "a.py", "x=1\n"), _wrote(tmp_path, "b.py", "y=2\n"),
        Step(id="c", goal="t", kind=RUN, command="pytest", status=DONE, result="ok"),
    ])
    assert collect_evidence(state, tmp_path).summary() == {
        "verified": True, "checks": 1, "checks_passed": 1, "artifacts": 2}
