"""Tests for the loop engine — the durable Plan→Execute→Verify→Replan differentiator.

The DAG, checkpointing, resume, and orchestration are tested deterministically with a fake executor
(no model). The decomposer is tested with a fake `complete`. The real model-driven end-to-end is
proven separately by a live run.
"""

from __future__ import annotations

from newton.config import load_settings
from newton.loop.engine import ContextShaper, LoopEngine
from newton.loop.execute import StepResult
from newton.loop.state import BLOCKED, DONE, FAILED, PENDING, RUN, WRITE, LoopState, Step

# --- DAG readiness + durability ---

def test_ready_respects_dependencies():
    st = LoopState(goal="g", steps=[
        Step(id="s1", goal="a", file="a.py"),
        Step(id="s2", goal="b", file="b.py", depends_on=["s1"]),
    ])
    assert st.ready().id == "s1"              # s2 blocked until s1 done
    st.by_id("s1").status = DONE
    assert st.ready().id == "s2"

def test_ready_blocks_on_failed_dependency():
    st = LoopState(goal="g", steps=[
        Step(id="s1", goal="a", status=FAILED),
        Step(id="s2", goal="b", depends_on=["s1"]),
    ])
    assert st.ready() is None
    assert st.by_id("s2").status == BLOCKED

def test_checkpoint_roundtrip(tmp_path):
    st = LoopState(goal="build x", steps=[Step(id="s1", goal="a", file="a.py", status=DONE),
                                          Step(id="s2", goal="b", file="b.py")])
    p = tmp_path / "loop.json"
    st.save(p)
    back = LoopState.load(p)
    assert back.goal == "build x" and len(back.steps) == 2
    assert back.by_id("s1").status == DONE and back.by_id("s2").status == PENDING

def test_all_done_and_counts():
    st = LoopState(goal="g", steps=[Step(id="s1", goal="a", status=DONE),
                                    Step(id="s2", goal="b", status=DONE)])
    assert st.all_done() and st.counts()[DONE] == 2


# --- context shaper: upstream propagation + error feedback ---

def test_context_shaper_propagates_upstream_and_error(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    st = LoopState(goal="build a calculator app", steps=[
        Step(id="s1", goal="calc", file="calc.py", status=DONE),
        Step(id="s2", goal="tests", file="test_calc.py", depends_on=["s1"]),
    ])
    ctx = ContextShaper(tmp_path).shape(st, st.by_id("s2"), error="import failed")
    assert "build a calculator app" in ctx          # overall goal
    assert "def add" in ctx                          # upstream output propagated
    assert "import failed" in ctx                    # prior-attempt error fed back


# --- compaction: summarise reference context when the window overflows ---

def _dep_state(goal="the overall goal"):
    return LoopState(goal=goal, steps=[
        Step(id="s1", goal="a", file="dep.py", status=DONE),
        Step(id="s2", goal="b", file="new.py", depends_on=["s1"])])

def test_shaper_compacts_reference_over_budget_keeps_priority(tmp_path):
    (tmp_path / "dep.py").write_text("x = 1  # padding\n" * 500)      # large reference file
    calls = []
    shaper = ContextShaper(tmp_path, budget_chars=300, per_file=10000,
                           summarize=lambda t, r: calls.append((len(t), r)) or "COMPACT-SUMMARY")
    ctx = shaper.shape(_dep_state(), _dep_state().by_id("s2"))
    assert "the overall goal" in ctx            # priority kept verbatim
    assert "COMPACT-SUMMARY" in ctx             # reference was summarised, not dumped
    assert calls and "x = 1  # padding" not in ctx   # raw bulk replaced

def test_shaper_no_compaction_under_budget(tmp_path):
    (tmp_path / "dep.py").write_text("y = 2\n")
    called = []
    shaper = ContextShaper(tmp_path, budget_chars=6000, summarize=lambda t, r: called.append(1) or "S")
    ctx = shaper.shape(_dep_state("g"), _dep_state("g").by_id("s2"))
    assert "y = 2" in ctx and not called        # small enough → verbatim, summarizer untouched

def test_compact_falls_back_to_clip_without_summarizer(tmp_path):
    (tmp_path / "dep.py").write_text("z = 3\n" * 500)
    shaper = ContextShaper(tmp_path, budget_chars=200, per_file=10000)   # no summarizer
    ctx = shaper.shape(_dep_state("g"), _dep_state("g").by_id("s2"))
    assert "g" in ctx and len(ctx) <= 200       # clipped to budget, priority present


# --- the full loop, with a fake executor (no model) ---

class _FakeExec:
    def __init__(self, root):
        self.root = root
        self.calls: list[str] = []

    def execute(self, step, context, **kwargs):
        self.calls.append(step.id)
        if step.kind == WRITE:
            (self.root / step.file).write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            return StepResult(True, f"wrote {step.file}")
        return StepResult(True, "ran")


def _plan(*steps):
    return [Step(id=i, goal=g, kind=k, file=f, command=cmd, depends_on=list(dep))
            for (i, g, k, f, cmd, dep) in steps]


def test_loop_drives_a_compound_goal_to_done(tmp_path, monkeypatch):
    plan = lambda *a, **k: _plan(
        ("s1", "write calc.py", WRITE, "calc.py", "", []),
        ("s2", "write test", WRITE, "test_calc.py", "", ["s1"]),
        ("s3", "run tests", RUN, "", '{py} -c "import calc"', ["s1", "s2"]),
    )
    monkeypatch.setattr("newton.loop.engine.decompose", plan)
    ex = _FakeExec(tmp_path)
    res = LoopEngine(load_settings(tmp_path), executor=ex).run("build a calc with tests")
    assert res.ok and res.state.all_done()
    assert ex.calls == ["s1", "s2", "s3"]                # dependency order
    assert (tmp_path / "calc.py").is_file() and (tmp_path / "test_calc.py").is_file()
    assert (tmp_path / ".newton" / "loop.json").is_file()   # checkpoint written


def test_loop_checkpoints_after_each_step(tmp_path, monkeypatch):
    monkeypatch.setattr("newton.loop.engine.decompose",
                        lambda *a, **k: _plan(("s1", "w", WRITE, "a.py", "", [])))
    LoopEngine(load_settings(tmp_path), executor=_FakeExec(tmp_path)).run("g")
    saved = LoopState.load(tmp_path / ".newton" / "loop.json")
    assert saved.by_id("s1").status == DONE             # progress persisted


def test_resume_skips_done_steps(tmp_path):
    # A checkpoint with s1 done, s2 pending → resume runs ONLY s2 (idempotent resume).
    st = LoopState(goal="g", steps=[Step(id="s1", goal="a", file="a.py", status=DONE),
                                    Step(id="s2", goal="b", file="b.py")])
    st.save(tmp_path / ".newton" / "loop.json")
    ex = _FakeExec(tmp_path)
    res = LoopEngine(load_settings(tmp_path), executor=ex, shaper=ContextShaper(tmp_path)).run("g", resume=True)
    assert res.ok and "s1" not in ex.calls and ex.calls[0] == "s2"   # s1 skipped; s2 ran first


# --- test-driven self-correction: a failed check drives a code fix + re-check ---

def test_culprit_step_finds_code_file_from_error(tmp_path):
    eng = LoopEngine(load_settings(tmp_path))
    state = LoopState(goal="g", steps=[
        Step(id="s1", goal="", kind=WRITE, file="app/logic.py"),
        Step(id="s2", goal="", kind=WRITE, file="test_logic.py"),
        Step(id="s3", goal="", kind=RUN, command="{py} -m pytest -q"),
    ])
    culprit = eng._culprit_step("assert failed\n  app/logic.py:4: in run\n  test_logic.py:2", state)
    assert culprit.id == "s1"                    # the code file, not the test file

class _RepairExec:
    """Writes buggy code first; the second write (after a repair cycle) is correct. The RUN step
    fails until the code is fixed — simulating tests catching a logic bug the model then corrects."""
    def __init__(self, root):
        self.root = root
        self.code_writes = 0

    def execute(self, step, context, **kwargs):
        from newton.loop.state import RUN as _RUN
        if step.kind != _RUN:
            self.code_writes += 1
            body = "def val():\n    return 2\n" if self.code_writes >= 2 else "def val():\n    return 1\n"
            (self.root / step.file).write_text(body, encoding="utf-8")
            return StepResult(True, "wrote")
        ok = self.code_writes >= 2
        return StepResult(ok, "ran ok" if ok else "assert failed in calc.py: expected 2 got 1")

def test_failed_check_drives_a_code_fix(tmp_path, monkeypatch):
    from newton.loop.state import RUN
    plan = lambda *a, **k: [Step(id="s1", goal="write calc", kind=WRITE, file="calc.py"),
                            Step(id="s2", goal="run tests", kind=RUN, command='{py} -c "import calc"',
                                 depends_on=["s1"])]
    monkeypatch.setattr("newton.loop.engine.decompose", plan)
    ex = _RepairExec(tmp_path)
    res = LoopEngine(load_settings(tmp_path), executor=ex).run("build calc that returns 2")
    assert res.ok and res.state.all_done()       # recovered: test failure → code fix → re-check pass
    assert ex.code_writes >= 2                    # the code was re-written after the check failed


# --- self-generated verification: the loop writes its own check when the plan has none ---

def test_self_verification_adds_check_when_none(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "1")
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    state = LoopState(goal="build calc with add", steps=[
        Step(id="s1", goal="", kind=WRITE, file="calc.py", status=DONE)])   # code, but NO check

    class _E:
        def execute(self, step, context, **kw):
            if step.kind == WRITE:
                (tmp_path / step.file).write_text("def test_x():\n    assert True\n", encoding="utf-8")
            return StepResult(True, "ok")

    LoopEngine(load_settings(tmp_path), executor=_E())._ensure_verification(state, "build calc with add")
    assert "v_test" in [s.id for s in state.steps] and "v_run" in [s.id for s in state.steps]

def test_self_verification_skips_when_a_check_exists(tmp_path):
    state = LoopState(goal="g", steps=[
        Step(id="s1", goal="", kind=WRITE, file="calc.py", status=DONE),
        Step(id="s2", goal="", kind=RUN, command="{py} -m pytest -q")])     # model already planned a test
    LoopEngine(load_settings(tmp_path), executor=object())._ensure_verification(state, "g")
    assert [s.id for s in state.steps] == ["s1", "s2"]                       # unchanged — trust it

def test_self_verification_off_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_SELFTEST", "0")
    state = LoopState(goal="g", steps=[Step(id="s1", goal="", kind=WRITE, file="calc.py", status=DONE)])
    LoopEngine(load_settings(tmp_path), executor=object())._ensure_verification(state, "g")
    assert len(state.steps) == 1                                            # disabled → nothing added


# --- replanning: when the plan itself is wrong, re-decompose the remaining work ---

def test_reid_prefixes_ids_and_internal_deps():
    steps = [Step(id="s1", goal="a"), Step(id="s2", goal="b", depends_on=["s1"])]
    LoopEngine._reid(steps, "r1_")
    assert [s.id for s in steps] == ["r1_s1", "r1_s2"]
    assert steps[1].depends_on == ["r1_s2".replace("s2", "s1")]   # dep re-mapped to the new id

def test_replan_recovers_when_the_plan_is_wrong(tmp_path, monkeypatch):
    # First plan's only step can't be built; the replan produces a different, buildable plan.
    plans = iter([
        [Step(id="s1", goal="write the wrong file", kind=WRITE, file="bad.py")],
        [Step(id="s1", goal="write the right file", kind=WRITE, file="good.py")],
    ])
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: next(plans))

    class _E:
        def execute(self, step, context, **kw):
            if step.file == "good.py":
                (tmp_path / "good.py").write_text("ok = 1\n", encoding="utf-8")
                return StepResult(True, "wrote")
            return StepResult(False, "cannot build bad.py")     # first plan fails

    res = LoopEngine(load_settings(tmp_path), executor=_E()).run("build the thing")
    assert res.ok and res.state.all_done()                       # recovered by re-planning
    assert (tmp_path / "good.py").is_file()
    assert [s.id for s in res.state.steps] == ["r1_s1"]          # bad plan replaced, done record kept


# --- deterministic import repair (the system fixes what the model gets wrong) ---

def test_repair_imports_fixes_wrong_module(tmp_path):
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "pricing.py").write_text("DISCOUNT = 0.1\n")
    (tmp_path / "store" / "cart.py").write_text("from test_cart import DISCOUNT\n\nx = DISCOUNT\n")
    eng = LoopEngine(load_settings(tmp_path))
    assert eng._repair_imports("store/cart.py")                          # a fix was applied
    assert "from store.pricing import DISCOUNT" in (tmp_path / "store" / "cart.py").read_text()

def test_repair_imports_leaves_correct_and_thirdparty_alone(tmp_path):
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "models.py").write_text("class Product:\n    pass\n")
    (tmp_path / "store" / "cart.py").write_text(
        "from dataclasses import dataclass\nfrom store.models import Product\n")
    eng = LoopEngine(load_settings(tmp_path))
    assert not eng._repair_imports("store/cart.py")                      # nothing to fix

def test_repair_imports_ignores_unknown_symbol(tmp_path):
    (tmp_path / "a.py").write_text("from nowhere import THING\n")        # THING defined nowhere
    eng = LoopEngine(load_settings(tmp_path))
    assert not eng._repair_imports("a.py")                               # can't resolve → left as-is


# --- native executor strips the enclosing markdown fence for ANY language ---
# Regression: extract_code only stripped a fixed set of language tags, so an html/js/css fence
# survived and the raw ``` lines corrupted the written non-Python file (Python was spared because
# _verify's parse-check turned a stray fence into a correctable SyntaxError).

class _Resp:
    def __init__(self, c): self.choices = [type("C", (), {"message": type("M", (), {"content": c})()})()]


def test_native_executor_strips_html_fence(tmp_path, monkeypatch):
    from newton.loop.execute import NativeStepExecutor
    from newton.loop.state import WRITE, Step
    fenced = "```html\n<!DOCTYPE html>\n<html><body>hi</body></html>\n```"
    monkeypatch.setattr("newton.loop.execute.complete", lambda *a, **k: _Resp(fenced))
    ex = NativeStepExecutor(tmp_path, model="m")
    res = ex.execute(Step(id="s1", goal="write page", kind=WRITE, file="index.html"), context="")
    assert res.ok
    written = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "```" not in written                       # no fence markers survive into the file
    assert written.startswith("<!DOCTYPE html>")      # real content preserved, no leading tag line
    assert "<body>hi</body>" in written


def test_native_executor_strips_js_fence(tmp_path, monkeypatch):
    from newton.loop.execute import NativeStepExecutor
    from newton.loop.state import WRITE, Step
    fenced = "```javascript\nconst x = 1;\nconsole.log(x);\n```"
    monkeypatch.setattr("newton.loop.execute.complete", lambda *a, **k: _Resp(fenced))
    ex = NativeStepExecutor(tmp_path, model="m")
    ex.execute(Step(id="s1", goal="write app", kind=WRITE, file="app.js"), context="")
    written = (tmp_path / "app.js").read_text(encoding="utf-8")
    assert "```" not in written and "javascript" not in written.splitlines()[0]
    assert written == "const x = 1;\nconsole.log(x);\n"


# --- pytest exit-5 ("no tests collected") is a malformed TEST, not a code failure ---

def test_run_step_flags_pytest_no_tests_distinctly(tmp_path):
    from newton.loop.execute import NativeStepExecutor
    (tmp_path / "test_x.py").write_text("assert 1 == 1\n")     # bare assert → pytest collects nothing
    ex = NativeStepExecutor(tmp_path, model="m")
    res = ex.execute(Step(id="r", goal="", kind=RUN, command="{py} -m pytest -q"), context="")
    assert not res.ok and "NO_TESTS_COLLECTED" in res.detail   # distinct signal, not a generic fail

def test_no_tests_collected_routes_repair_to_the_test_file(tmp_path):
    eng = LoopEngine(load_settings(tmp_path))
    state = LoopState(goal="g", steps=[
        Step(id="s1", goal="", kind=WRITE, file="mathx.py", status=DONE),
        Step(id="s2", goal="", kind=WRITE, file="test_mathx.py", status=DONE),
        Step(id="s3", goal="", kind=RUN, command="{py} -m pytest -q", status=FAILED,
             result="NO_TESTS_COLLECTED (pytest exit 5): the test file defines no def test_* functions"),
    ])
    assert eng._repair_from_test_failure(state)
    assert state.by_id("s2").status == PENDING                 # the TEST file is what gets rewritten
    assert state.by_id("s1").status == DONE                    # the code is NOT blamed
    assert state.by_id("s3").status == PENDING                 # the check is re-queued
    assert "test_" in eng._pending_error["s2"]                 # told to use def test_* functions


# --- decomposer parses a model plan ---

def test_decompose_parses_and_orders(monkeypatch):
    from newton.loop import decompose as dmod
    js = ('{"steps": [{"id":"s1","kind":"write","file":"calc.py","goal":"add/mul","depends_on":[]},'
          '{"id":"s2","kind":"run","command":"{py} -m pytest -q","goal":"test","depends_on":["s1"]}]}')
    monkeypatch.setattr(dmod, "complete", lambda *a, **k: _Resp(js))
    steps = dmod.decompose("m", "build calc")
    assert [s.id for s in steps] == ["s1", "s2"]
    assert steps[0].kind == WRITE and steps[1].kind == RUN and steps[1].depends_on == ["s1"]

def test_decompose_drops_forward_dependencies(monkeypatch):
    from newton.loop import decompose as dmod
    # s1 depends on s2 (a forward edge) — must be dropped so the graph stays acyclic.
    js = '{"steps": [{"id":"s1","file":"a.py","goal":"a","depends_on":["s2"]},{"id":"s2","file":"b.py","goal":"b"}]}'
    monkeypatch.setattr(dmod, "complete", lambda *a, **k: _Resp(js))
    steps = dmod.decompose("m", "g")
    assert steps[0].depends_on == []                    # forward edge removed


# --- parallel step execution: independent ready steps run as a wave ------------------
# ready_batch is the safety-checked wave selector (distinct files; checks isolated; deps respected);
# the drive test proves the steps actually overlap, not just that they all finish.

def test_ready_batch_returns_independent_writes():
    st = LoopState(goal="g", steps=[Step(id="a", goal="", file="a.py"),
                                    Step(id="b", goal="", file="b.py"),
                                    Step(id="c", goal="", file="c.py")])
    assert [s.id for s in st.ready_batch(3)] == ["a", "b", "c"]   # all independent → one wave
    assert [s.id for s in st.ready_batch(1)] == ["a"]             # n<=1 stays strictly sequential

def test_ready_batch_waits_for_dependencies():
    st = LoopState(goal="g", steps=[Step(id="s1", goal="", file="a.py"),
                                    Step(id="s2", goal="", file="b.py", depends_on=["s1"])])
    assert [s.id for s in st.ready_batch(4)] == ["s1"]            # s2 waits — its upstream isn't done

def test_ready_batch_never_shares_a_file():
    st = LoopState(goal="g", steps=[Step(id="a", goal="", file="same.py"),
                                    Step(id="b", goal="", file="same.py")])
    assert [s.id for s in st.ready_batch(4)] == ["a"]             # two writes on one file don't overlap

def test_ready_batch_isolates_a_run_step():
    st = LoopState(goal="g", steps=[Step(id="chk", goal="", kind=RUN, command="{py} -c pass"),
                                    Step(id="a", goal="", file="a.py"),
                                    Step(id="b", goal="", file="b.py")])
    assert [s.id for s in st.ready_batch(4)] == ["chk"]           # a check runs alone (reads whole tree)

def test_parallel_drive_runs_a_wave_concurrently(tmp_path, monkeypatch):
    import threading
    import time
    class _ConcurrentExec:
        def __init__(self, root):
            self.root, self.calls = root, []
            self._lk = threading.Lock(); self.active = 0; self.max_active = 0
        def execute(self, step, context, **kw):
            with self._lk:
                self.active += 1; self.max_active = max(self.max_active, self.active)
                self.calls.append(step.id)
            time.sleep(0.05)                                     # hold the slot so peers overlap
            with self._lk:
                self.active -= 1
            if step.kind == WRITE:
                (self.root / step.file).write_text("x = 1\n", encoding="utf-8")
                return StepResult(True, "wrote")
            return StepResult(True, "ran")
    monkeypatch.setattr("newton.loop.engine.decompose", lambda *a, **k: _plan(
        ("a", "wa", WRITE, "a.py", "", []),
        ("b", "wb", WRITE, "b.py", "", []),
        ("c", "wc", WRITE, "c.py", "", []),
        ("chk", "check", RUN, "", '{py} -c "import a,b,c"', ["a", "b", "c"]),
    ))
    ex = _ConcurrentExec(tmp_path)
    eng = LoopEngine(load_settings(tmp_path), executor=ex, shaper=ContextShaper(tmp_path))
    eng.parallel = 3
    res = eng.run("build three files then check")
    assert res.ok and res.state.all_done()
    assert ex.max_active == 3                                    # the three writes truly ran at once
    assert ex.calls[-1] == "chk"                                 # the check still ran last, alone

def test_resume_resets_an_interrupted_running_step(tmp_path):
    from newton.loop.state import RUNNING
    st = LoopState(goal="g", steps=[Step(id="s1", goal="", file="a.py", status=RUNNING)])   # killed mid-flight
    st.save(tmp_path / ".newton" / "loop.json")
    ex = _FakeExec(tmp_path)
    res = LoopEngine(load_settings(tmp_path), executor=ex, shaper=ContextShaper(tmp_path)).run("g", resume=True)
    assert res.ok and ex.calls[0] == "s1"                        # not stranded in RUNNING — redone first
