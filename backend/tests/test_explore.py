"""ExploreEngine — the open-ended investigate-and-fix loop. The model is scripted (a fake `complete`)
so no Ollama is needed, but the CHECK is real: an actual subprocess whose exit code decides success,
so these prove the loop's ground-truth contract, not the model's word."""

from newton.config import load_settings
from newton.loop.explore import ExploreEngine


class _FakeResp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _Script:
    """A deterministic stand-in for the model: hands back queued action-JSON, one per turn."""
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, model, messages, **kw):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else '{"action":"done"}'
        return _FakeResp(reply)


def _engine(tmp_path, script, monkeypatch, **kw):
    monkeypatch.setattr("newton.loop.explore.complete", script)
    return ExploreEngine(load_settings(tmp_path), **kw)


# --- the headline: read → edit → the real check flips to green -----------------

def test_explore_investigates_then_fixes_until_the_check_passes(tmp_path, monkeypatch):
    (tmp_path / "mathy.py").write_text("def add(a, b):\n    return a - b\n")   # bug: subtracts
    (tmp_path / "test_mathy.py").write_text(
        "from mathy import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    script = _Script(
        '{"action":"read","path":"mathy.py"}',                                 # look first
        '{"action":"edit","path":"mathy.py","content":"def add(a, b):\\n    return a + b\\n"}',
    )
    res = _engine(tmp_path, script, monkeypatch).run("add() returns the wrong number — fix it")
    assert res.ok                                                             # pytest (auto-detected) now passes
    assert "return a + b" in (tmp_path / "mathy.py").read_text()             # the fix actually landed
    assert script.calls >= 2                                                  # it read before it edited


# --- ground truth, not the model's claim: already-green needs zero model calls ---

def test_explore_stops_immediately_when_already_passing(tmp_path, monkeypatch):
    (tmp_path / "mathy.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_mathy.py").write_text(
        "from mathy import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    script = _Script('{"action":"edit","path":"mathy.py","content":"broken"}')  # must never run
    res = _engine(tmp_path, script, monkeypatch).run("make sure add works")
    assert res.ok and res.steps == 0 and script.calls == 0                    # the check settled it first


# --- bounded: a model that never fixes it stops, it does not spin forever --------

def test_explore_gives_up_within_the_step_budget(tmp_path, monkeypatch):
    (tmp_path / "mathy.py").write_text("def add(a, b):\n    return a - b\n")   # stays broken
    monkeypatch.setattr(ExploreEngine, "MAX_STEPS", 3)
    script = _Script(*(['{"action":"read","path":"mathy.py"}'] * 5))          # only ever reads
    check = '{py} -c "import mathy; assert mathy.add(2, 3) == 5"'             # fast, no pytest
    res = _engine(tmp_path, script, monkeypatch, check=check).run("fix add")
    assert not res.ok and res.steps == 3                                       # stopped at the budget
