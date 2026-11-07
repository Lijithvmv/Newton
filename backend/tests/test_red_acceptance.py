"""Red acceptance test (NEWTON_LOOP_RED_ACCEPT=1): an independent test, proven able to fail, frozen,
and required for PASS. Offline: the model call and the step executor are fakes; pytest runs for real."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from newton.config import load_settings
from newton.loop.acceptance import FILE, classify, prepare
from newton.loop.effort import Effort
from newton.loop.engine import LoopEngine
from newton.loop.execute import StepResult
from newton.loop.state import RUN, WRITE, Step
from newton.proc import run_shell

_ISOLATED = Effort("test", max_attempts=1, repair_cycles=1, replan_cycles=0,
                   best_of_n=1, retrieval_k=3, budget_chars=6000)

GOOD_TEST = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
PASSES_ALREADY = "def test_trivial():\n    assert True\n"
NO_TESTS = "assert 1 == 1\n"
BROKEN = "def test_x(:\n    pass\n"


def _model(*replies):
    """A fake `complete` that returns the given test files in order (fenced, like a real model)."""
    calls = []

    def complete(model, messages, **kw):
        calls.append(messages)
        body = replies[min(len(calls) - 1, len(replies) - 1)]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=f"```python\n{body}```"))])
    complete.calls = calls
    return complete


# ---- qualification (the red gate) ---------------------------------------------------------------

def test_classify_rules():
    assert classify(0, "1 passed")[0] is False                       # can't test the change
    assert classify(5, "no tests ran")[0] is False                   # P1: nothing collected
    assert classify(2, f"ERROR {FILE} SyntaxError")[0] is False      # the test itself is broken
    assert classify(1, "assert 1 == 2")[0] is True                   # fails by assertion → red
    assert classify(2, "ModuleNotFoundError: No module named 'calc'")[0] is True  # missing name → red


def test_prepare_rejects_a_test_that_already_passes_then_accepts_a_red_one(tmp_path):
    complete = _model(PASSES_ALREADY, GOOD_TEST)
    acc = prepare(tmp_path, "add(a, b) returns a+b in calc.py", "(empty)", "m", complete)
    assert acc.qualified and acc.attempts == 2 and acc.content == GOOD_TEST
    assert (tmp_path / FILE).read_text(encoding="utf-8") == GOOD_TEST
    assert "rejected" in complete.calls[1][1]["content"]             # the rewrite was told why


def test_prepare_never_qualifies_leaves_no_file(tmp_path):
    acc = prepare(tmp_path, "goal", "", "m", _model(NO_TESTS, BROKEN, PASSES_ALREADY))
    assert not acc.qualified and acc.attempts == 3
    assert not (tmp_path / FILE).exists()


def test_prepare_model_down_is_an_honest_no(tmp_path):
    def down(*a, **k):
        raise ConnectionError("ollama down")
    acc = prepare(tmp_path, "goal", "", "m", down)
    assert not acc.qualified and "model call failed" in acc.reason


def test_writer_sees_goal_and_file_names_not_code(tmp_path):
    (tmp_path / "calc.py").write_text("SECRET_IMPL = 1\n", encoding="utf-8")
    complete = _model(GOOD_TEST)
    prepare(tmp_path, "add numbers", "calc.py", "m", complete)
    sent = " ".join(m["content"] for m in complete.calls[0])
    assert "calc.py" in sent and "SECRET_IMPL" not in sent


# ---- engine wiring (freeze + green gate + verdict) ----------------------------------------------

class _Exec:
    """Writes calc.py (right or wrong); runs RUN steps for real so the frozen test actually executes."""
    def __init__(self, root, code, extra_writes=None):
        self.root, self.code, self.extra = root, code, extra_writes or {}

    def execute(self, step, context, *, temperature: float = 0.1, **kw):
        if step.kind == RUN:
            r = run_shell(step.command.replace("{py}", f'"{sys.executable}"'), cwd=self.root, timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            return StepResult(r.returncode == 0, out[-1500:])
        body = self.extra.get(step.file, self.code)
        (self.root / step.file).write_text(body, encoding="utf-8")
        return StepResult(True, f"wrote {step.file}")


def _run(tmp_path, monkeypatch, *, code, replies, plan=None, extra=None):
    monkeypatch.setenv("NEWTON_LOOP_RED_ACCEPT", "1")
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.llm.complete", _model(*replies))
    steps = plan or [Step(id="calc", goal="write add", kind=WRITE, file="calc.py")]
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: steps)
    eng = LoopEngine(load_settings(tmp_path), executor=_Exec(tmp_path, code, extra), effort=_ISOLATED)
    return eng.run("add(a, b) returns a+b in calc.py")


def _verdict(res):
    return next(d for d in res.evidence.as_dict()["decisions"] if d["name"] == "verdict")["answer"]


def test_correct_code_passing_the_frozen_test_is_pass(tmp_path, monkeypatch):
    res = _run(tmp_path, monkeypatch, code="def add(a, b):\n    return a + b\n", replies=[GOOD_TEST])
    assert res.ok and _verdict(res) == "PASS"
    assert res.state.by_id("a_run").status == "done"


def test_wrong_code_is_would_block_with_the_assertion(tmp_path, monkeypatch):
    res = _run(tmp_path, monkeypatch, code="def add(a, b):\n    return a - b\n", replies=[GOOD_TEST])
    assert _verdict(res) == "WOULD BLOCK"
    assert "frozen acceptance test did not pass" in res.answer


def test_no_qualified_test_caps_verdict_at_needs_attention(tmp_path, monkeypatch):
    res = _run(tmp_path, monkeypatch, code="def add(a, b):\n    return a + b\n",
               replies=[PASSES_ALREADY])
    assert res.ok and _verdict(res) == "NEEDS ATTENTION"
    assert "no independent acceptance test qualified" in res.answer


def test_a_step_that_would_rewrite_the_frozen_test_is_skipped(tmp_path, monkeypatch):
    plan = [Step(id="calc", goal="write add", kind=WRITE, file="calc.py"),
            Step(id="t", goal="write tests", kind=WRITE, file=FILE, depends_on=["calc"])]
    res = _run(tmp_path, monkeypatch, code="def add(a, b):\n    return a - b\n", replies=[GOOD_TEST],
               plan=plan, extra={FILE: PASSES_ALREADY})        # would game the check if allowed
    assert (tmp_path / FILE).read_text(encoding="utf-8") == GOOD_TEST
    assert _verdict(res) == "WOULD BLOCK"


def test_a_changed_test_is_restored_before_the_gate(tmp_path, monkeypatch):
    plan = [Step(id="calc", goal="write add", kind=WRITE, file="calc.py")]

    class _Tamper(_Exec):
        def execute(self, step, context, **kw):
            r = super().execute(step, context, **kw)
            if step.kind == WRITE:
                (self.root / FILE).write_text(PASSES_ALREADY, encoding="utf-8")
            return r

    monkeypatch.setenv("NEWTON_LOOP_RED_ACCEPT", "1")
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.llm.complete", _model(GOOD_TEST))
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: plan)
    eng = LoopEngine(load_settings(tmp_path), effort=_ISOLATED,
                     executor=_Tamper(tmp_path, "def add(a, b):\n    return a - b\n"))
    res = eng.run("add")
    assert any("restored the frozen version" in ln for ln in res.state.log)
    assert _verdict(res) == "WOULD BLOCK"


def test_flag_off_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.delenv("NEWTON_LOOP_RED_ACCEPT", raising=False)
    called = []
    monkeypatch.setattr("newton.llm.complete", lambda *a, **k: called.append(1))
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: [Step(id="calc", goal="g", kind=WRITE, file="calc.py")])
    eng = LoopEngine(load_settings(tmp_path), effort=_ISOLATED,
                     executor=_Exec(tmp_path, "def add(a, b):\n    return a + b\n"))
    res = eng.run("add")
    assert res.ok and not called and not (tmp_path / FILE).exists()
    assert res.state.by_id("a_run") is None


CLI_TEST = ("import subprocess, sys\n\n"
            "def test_shout():\n"
            "    r = subprocess.run([sys.executable, 'greet.py', '--shout'], capture_output=True, text=True)\n"
            "    assert r.stdout.strip() == 'HELLO'\n")


def test_subprocess_cli_failure_is_repaired_in_the_program_not_the_test(tmp_path, monkeypatch):
    """The CLI test imports nothing from the project, so the normal culprit finder has no file to
    fix; the acceptance fallback routes repair to greet.py (named in the test), and the retry passes."""
    wrong = "import sys\nprint('hello')\n"
    right = "import sys\nprint('HELLO' if '--shout' in sys.argv else 'hello')\n"

    class _LearnsOnRepair(_Exec):
        n = 0

        def execute(self, step, context, **kw):
            if step.kind == WRITE:
                _LearnsOnRepair.n += 1
                self.code = wrong if _LearnsOnRepair.n == 1 else right
            return super().execute(step, context, **kw)

    monkeypatch.setenv("NEWTON_LOOP_RED_ACCEPT", "1")
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    monkeypatch.setenv("NEWTON_LOOP_INTEGRATION", "0")
    monkeypatch.setattr("newton.llm.complete", _model(CLI_TEST))
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: [Step(id="greet", goal="cli", kind=WRITE, file="greet.py")])
    eng = LoopEngine(load_settings(tmp_path), effort=_ISOLATED, executor=_LearnsOnRepair(tmp_path, wrong))
    res = eng.run("greet.py prints HELLO with --shout")
    assert _LearnsOnRepair.n == 2                        # greet.py was re-written by the repair
    assert (tmp_path / FILE).read_text(encoding="utf-8") == CLI_TEST
    assert _verdict(res) == "PASS"
