"""Tests for the eval harness — the deterministic parts (checks, baseline write, aggregation,
report). The model-driven arms are exercised with a fake `complete`, so no Ollama is needed."""

from __future__ import annotations

from newton.eval import ArmResult, EvalTask, report, run_baseline, run_suite
from newton.eval.harness import _run_check
from newton.eval.tasks import SEED_TASKS


class _FakeResp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


def _fake_complete(reply):
    return lambda model, messages, **kw: _FakeResp(reply)


# --- objective checker ---

def test_run_check_pass_and_fail(tmp_path):
    (tmp_path / "check.py").write_text("import sys; sys.exit(0)\n")
    assert _run_check(tmp_path, "{py} check.py") is True
    (tmp_path / "check.py").write_text("import sys; sys.exit(1)\n")
    assert _run_check(tmp_path, "{py} check.py") is False


# --- seed tasks are well-formed ---

def test_seed_tasks_have_checkers():
    assert SEED_TASKS
    for t in SEED_TASKS:
        assert t.id and t.goal and t.target and t.check
        # a pytest task ships a test_*, a script task ships its check.py
        assert any(f.startswith("test_") for f in t.files) or "check.py" in t.files


# --- baseline arm: writes the model's code, then the objective check decides ---

def test_baseline_passes_when_model_writes_correct_code(tmp_path, monkeypatch):
    task = next(t for t in SEED_TASKS if t.id == "implement-calc")
    good = "```python\ndef add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n```"
    monkeypatch.setattr("newton.eval.harness.complete", _fake_complete(good))
    assert run_baseline(task, "fake", tmp_path) is True
    assert (tmp_path / "calc.py").exists()

def test_baseline_fails_when_model_writes_wrong_code(tmp_path, monkeypatch):
    task = next(t for t in SEED_TASKS if t.id == "implement-calc")
    bad = "```python\ndef add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n```"
    monkeypatch.setattr("newton.eval.harness.complete", _fake_complete(bad))
    assert run_baseline(task, "fake", tmp_path) is False


# --- suite aggregation + report ---

def test_run_suite_aggregates_baseline_only(monkeypatch):
    task = next(t for t in SEED_TASKS if t.id == "implement-calc")
    good = "```python\ndef add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n```"
    monkeypatch.setattr("newton.eval.harness.complete", _fake_complete(good))
    res = run_suite("fake", [task], reps=2, arms=("baseline",))
    ar = res[("baseline", "implement-calc")]
    assert ar.runs == 2 and ar.passes == 2 and ar.rate == 1.0

def test_report_shows_delta():
    tasks = [EvalTask(id="t1", goal="", target="x.py")]
    results = {("baseline", "t1"): ArmResult(passes=1, runs=4),   # 25%
               ("newton", "t1"): ArmResult(passes=3, runs=4)}     # 75%
    out = report(results, tasks, model="m")
    assert "t1" in out and "+50%" in out and "OVERALL" in out
