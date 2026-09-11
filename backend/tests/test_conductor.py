"""Tests for the Conductor's pure logic — the parts that must never regress silently.

No model calls: these lock the deterministic machinery that lets a weak model produce a
correct end result — the verify-contract derivation, state distillation, the gates, and
the assembler's token budgeting.
"""

from __future__ import annotations

import subprocess
import sys

from newton.conductor.assembler import ContextAssembler, ContextBlock
from newton.conductor.pipeline import (
    cli_input_samples,
    derive_expect,
    extract_behaviours,
    extract_symbols,
    parse_review,
    pattern_to_page,
)
from newton.conductor.state import (
    GoalSpec,
    WorkingState,
    extract_code,
    extract_json,
    guess_target_file,
    strip_leading_heading,
)
from newton.conductor.verify import (
    build_check_script,
    edit_applies,
    module_import_name,
    python_parses,
    run_succeeds,
)

# --- verify-contract derivation (Newton owns the test, not the model) ---

def test_derive_expect_json_example():
    assert derive_expect('print it as {"count": 3} please') == '{"count": 3}'

def test_derive_expect_should_be():
    assert derive_expect("print multiply(2,3) which should be 6") == "6"

def test_derive_expect_absent():
    assert derive_expect("just refactor the thing") == ""


# --- JSON extraction from messy model output ---

def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

def test_extract_json_bare_with_prose():
    assert extract_json('Sure! {"steps": ["x"]} done') == {"steps": ["x"]}

def test_extract_json_list():
    assert extract_json("here: [1, 2, 3]") == [1, 2, 3]


# --- JSON resilience: repair almost-JSON from a weak model ---

def test_extract_json_repairs_trailing_comma():
    assert extract_json('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}

def test_extract_json_repairs_python_literals():
    assert extract_json('{"ok": True, "x": None}') == {"ok": True, "x": None}


# --- deterministic target_file fallback ---

def test_guess_target_file_finds_filename():
    assert guess_target_file("Add a --json flag to scripts/wordcount.py please") == "scripts/wordcount.py"

def test_guess_target_file_prefers_tree_match():
    tree = "utils.py\nmain.py"
    # request names two files; the one that exists in the tree wins
    assert guess_target_file("update helper.py and main.py", tree) == "main.py"

def test_guess_target_file_none_when_no_file():
    assert guess_target_file("just refactor the thing") == ""


# --- whole-file code extraction (the pass-rate lever) ---

def test_extract_code_from_fence():
    out = extract_code("Here is the file:\n```python\nx = 1\nprint(x)\n```\nDone.")
    assert out == "x = 1\nprint(x)\n"

def test_extract_code_without_fence():
    assert extract_code("x = 1\n") == "x = 1\n"

def test_extract_code_strips_any_language_fence():
    # Non-Python tags (html/js/css) must be stripped too — otherwise the raw ``` lines
    # survive into the written file and corrupt it.
    assert extract_code("```html\n<!DOCTYPE html>\n<p>hi</p>\n```") == "<!DOCTYPE html>\n<p>hi</p>\n"
    assert extract_code("```css\nbody { margin: 0; }\n```") == "body { margin: 0; }\n"

def test_extract_code_whole_output_is_one_fence():
    # When the entire model reply is a single fenced block, no fence markers remain.
    out = extract_code("```javascript\nconst x = 1;\n```")
    assert "```" not in out and out == "const x = 1;\n"


# --- section heading de-duplication (model echoes the heading) ---

def test_strip_leading_heading_removes_echoed_heading():
    assert strip_leading_heading("## Framing\n\nThe real body.") == "The real body."
    assert strip_leading_heading("# Ideas: cool\n- one\n- two") == "- one\n- two"

def test_strip_leading_heading_keeps_bodies_without_heading():
    assert strip_leading_heading("- one\n- two") == "- one\n- two"
    # a deeper sub-heading is preserved; only a leading one is dropped
    assert strip_leading_heading("Intro line\n## Sub\nmore") == "Intro line\n## Sub\nmore"


# --- WorkingState: distillation + budgeted summary ---

def test_state_summary_reflects_distilled_facts():
    s = WorkingState(goal=GoalSpec(request="do x"))
    s.add_fact("config.py sets the model")
    s.add_decision("use argparse")
    out = s.summary(budget_tokens=500)
    assert "config.py sets the model" in out and "use argparse" in out

def test_state_summary_respects_budget():
    s = WorkingState(goal=GoalSpec(request="do x"))
    for i in range(200):
        s.add_fact(f"fact number {i} with some length to it")
    # A tiny budget must force truncation well under the full content size.
    assert len(s.summary(budget_tokens=40)) < 1200


# --- deterministic gates ---

def test_python_parses_good_and_bad():
    assert python_parses("x = 1\n").ok
    assert not python_parses("def f(:\n").ok

def test_edit_applies_uniqueness():
    assert edit_applies("a = 1\nb = 2\n", "a = 1", "a = 9").ok
    assert not edit_applies("x\nx\n", "x", "y").ok          # ambiguous
    assert not edit_applies("abc", "zzz", "y").ok            # not found

def test_run_succeeds_checks_exit_and_expect():
    assert run_succeeds(0, "count: 3", "count").ok
    assert not run_succeeds(1, "boom", "").ok                # non-zero exit
    assert not run_succeeds(0, "count: 0", "count: 3").ok    # value mismatch


# --- import-based verification (the library / interface-change fix) ---

def test_module_import_name():
    assert module_import_name("calc.py") == "calc"
    assert module_import_name("pkg/mod.py") == "pkg.mod"
    assert module_import_name("pkg/__init__.py") == "pkg"
    assert module_import_name("README.md") is None

def test_extract_symbols():
    got = set(extract_symbols("module calc.py defining add(a,b) and multiply(a,b)"))
    assert {"add", "multiply"} <= got
    assert "the" not in got and "a" not in got               # stopwords filtered

def test_extract_symbols_rename_expects_new_not_old():
    # A rename must expect the NEW name and NOT assert the old one still exists.
    got = set(extract_symbols("Rename the function greet to salute in utils.py"))
    assert "salute" in got and "greet" not in got

def test_extract_symbols_ignores_prose_parentheticals():
    # The D64 bug: verbose blueprint prose was mined as required symbols. A parenthetical (a space
    # before the paren) or a proper-noun library must NOT become a required symbol.
    got = set(extract_symbols(
        "Build app/models.py — part of the database component (db layer for persistence (SQLite)) "
        "that stores structured scene inputs (title, setting, characters)."))
    assert not ({"inputs", "SQLite", "component", "persistence"} & got)   # none of the prose nouns

def test_extract_symbols_keeps_real_calls_and_classes():
    got = set(extract_symbols("add a function greet(name) and a class Parser, plus `build_prompt(x)`"))
    assert {"greet", "Parser", "build_prompt"} <= got     # def-cue, class-cue, backticked call all kept

def test_rename_targets_parses_pairs():
    from newton.conductor.pipeline import rename_targets
    drops, adds = rename_targets("rename greet to salute; also replace foo with bar")
    assert drops == {"greet", "foo"} and adds == {"salute", "bar"}

def test_extract_behaviours():
    assert extract_behaviours("add(2, 3) should be 5") == [("add(2, 3)", "5")]
    assert extract_behaviours("shout('hi') returns HELLO") == [("shout('hi')", "'HELLO'")]
    # already-quoted expected value must not get double-quoted
    assert extract_behaviours("slugify('Hi There') should be 'hi-there'") == \
        [("slugify('Hi There')", "'hi-there'")]
    # a multi-word quoted expected value must be captured whole, not truncated at the space
    assert extract_behaviours("titlecase('hi there') should be 'Hi There'") == \
        [("titlecase('hi there')", "'Hi There'")]


# --- behavioural CLI verification: expected-output + input extraction ---

def test_derive_expect_patterns():
    assert derive_expect('prints {"count": 3}') == '{"count": 3}'          # JSON
    assert derive_expect("it should print 'done here'") == "done here"       # quoted output
    assert derive_expect("prints the greeting uppercase, e.g. HELLO WORLD") == "HELLO WORLD"
    assert derive_expect("multiply(2,3) should be 6") == "6"
    assert derive_expect("just refactor it") == ""

def test_cli_input_samples():
    assert cli_input_samples("greet('world') should print HELLO WORLD") == ["world"]
    assert cli_input_samples("add(2, 3) is 5") == ["2", "3"]
    assert cli_input_samples("add a --shout flag, e.g. HELLO WORLD") == []   # no call → no input


# --- wiki self-maintenance: pattern → page decision ---

def test_pattern_to_page_writes_novel():
    ok, slug, page = pattern_to_page(
        {"reusable": True, "title": "CLI Flag Pattern", "content": "Use argparse."}, [])
    assert ok and slug == "cli-flag-pattern.md"
    assert "# CLI Flag Pattern" in page and "argparse" in page

def test_pattern_to_page_skips_non_reusable():
    assert pattern_to_page({"reusable": False, "title": "x", "content": "y"}, []) == (False, "", "")

def test_pattern_to_page_skips_empty_content():
    assert pattern_to_page({"reusable": True, "title": "x", "content": "   "}, []) == (False, "", "")

def test_pattern_to_page_dedups_existing_title():
    ok, slug, page = pattern_to_page(
        {"reusable": True, "title": "CLI Flag Pattern", "content": "z"}, ["cli-flag-pattern.md"])
    assert not ok and slug == "cli-flag-pattern.md" and page == ""


# --- independent review: response parsing ---

def test_parse_review_approves_clean():
    assert parse_review({"approved": True, "issues": []}) == (True, [])

def test_parse_review_rejects_with_concrete_issues():
    assert parse_review({"approved": False, "issues": ["dead code left in", "off-by-one"]}) \
        == (False, ["dead code left in", "off-by-one"])

def test_parse_review_no_concrete_issues_does_not_block():
    assert parse_review({"approved": False, "issues": []}) == (True, [])   # never block on vibes

def test_parse_review_issues_override_approved_flag():
    assert parse_review({"approved": True, "issues": ["leftover import"]}) == (False, ["leftover import"])

def test_parse_review_bad_output_does_not_block():
    assert parse_review("not json") == (True, [])

def test_build_check_script_passes_for_correct_library(tmp_path):
    # A pure library (no output when run) — the old run-based verify could never pass this.
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    script = build_check_script(tmp_path, tmp_path / "calc.py", ["add"], [("add(2, 3)", "5")])
    (tmp_path / "check.py").write_text(script)
    r = subprocess.run([sys.executable, str(tmp_path / "check.py")], capture_output=True, text=True)
    assert r.returncode == 0 and "VERIFY_OK" in r.stdout

def test_build_check_script_fails_on_wrong_behaviour(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")   # bug: minus
    script = build_check_script(tmp_path, tmp_path / "calc.py", ["add"], [("add(2, 3)", "5")])
    (tmp_path / "check.py").write_text(script)
    r = subprocess.run([sys.executable, str(tmp_path / "check.py")], capture_output=True, text=True)
    assert r.returncode == 1 and "VERIFY_FAIL" in r.stdout

def test_build_check_script_fails_on_missing_symbol(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    script = build_check_script(tmp_path, tmp_path / "calc.py", ["multiply"], [])   # not defined
    (tmp_path / "check.py").write_text(script)
    r = subprocess.run([sys.executable, str(tmp_path / "check.py")], capture_output=True, text=True)
    assert r.returncode == 1 and "missing symbol: multiply" in r.stdout


# --- _verify must complete on a passing target (regression: dangling `module` NameError) ---

def test_verify_completes_on_correct_target(tmp_path):
    # Runs the real verify (no model): a correct library must verify PASS without crashing.
    # Guards against a _verify refactor leaving an undefined variable in the PASS/emit path —
    # a bug unit tests missed because Verify needs a live run.
    from newton.conductor.state import GoalSpec, WorkingState
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    c = _conductor(tmp_path)
    c.state = WorkingState(goal=GoalSpec(request="module calc.py defining add(a, b)"))
    c.state.goal.intent = {"target_file": "calc.py"}
    ok, msg = c._verify()
    assert ok, msg


def test_verify_defers_third_party_import(tmp_path):
    # A file that imports an uninstalled THIRD-PARTY package must not fail per-file verify —
    # Newton's own venv lacks the project's deps; the real check happens at integration.
    from newton.conductor.state import GoalSpec, WorkingState
    (tmp_path / "models.py").write_text(
        "import definitely_not_a_real_pkg_xyz\n\ndef thing():\n    return 1\n", encoding="utf-8")
    c = _conductor(tmp_path)
    c.state = WorkingState(goal=GoalSpec(request="module models.py"))
    c.state.goal.intent = {"target_file": "models.py"}
    ok, msg = c._verify()
    assert ok and "deferred" in msg                      # passed, not failed on the missing dep

def test_verify_passes_normally_on_existing_local_import(tmp_path):
    # An import that resolves to a real project file verifies for real (not via the deferral path).
    from newton.conductor.state import GoalSpec, WorkingState
    (tmp_path / "helper.py").write_text("def h():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from helper import h\n\ndef go():\n    return h()\n", encoding="utf-8")
    c = _conductor(tmp_path)
    c.state = WorkingState(goal=GoalSpec(request="module main.py"))
    c.state.goal.intent = {"target_file": "main.py"}
    ok, msg = c._verify()
    assert ok and "deferred" not in msg                  # real PASS, the local import resolved


# --- edit strategy: new/empty files must regenerate whole, never surgical ---

def test_edit_strategy_new_or_empty_file_is_whole():
    from newton.conductor.pipeline import edit_strategy
    assert edit_strategy(True, "", 4000) == "whole"
    assert edit_strategy(False, "", 4000) == "whole"      # a new doc can't be spliced (no `old`)
    assert edit_strategy(False, "  \n ", 4000) == "whole"  # blank counts as empty

def test_edit_strategy_size_and_kind():
    from newton.conductor.pipeline import edit_strategy
    assert edit_strategy(True, "x" * 100, 4000) == "whole"      # small code → whole
    assert edit_strategy(True, "x" * 5000, 4000) == "surgical"  # large code → surgical
    assert edit_strategy(False, "# Title\nbody", 4000) == "surgical"  # existing doc → surgical


# --- multi-file coordinated edits (backward-compatible extension) ---

def _conductor(tmp_path):
    from newton.conductor.pipeline import Conductor
    from newton.config import load_settings
    return Conductor(load_settings(tmp_path), emit=lambda *a: None, approve=lambda *a: True)

def test_edit_targets_single_file_unchanged(tmp_path):
    c = _conductor(tmp_path)
    c.state.goal.intent = {"target_file": "a.py", "change": "x"}
    assert c._edit_targets() == [("a.py", "x", True)]

def test_edit_targets_includes_coordinated_files(tmp_path):
    c = _conductor(tmp_path)
    c.state.goal.intent = {
        "target_file": "a.py", "change": "rename foo→bar",
        "also_edit": [{"file": "b.py", "change": "update the call to bar"}],
        "doc_file": "README.md", "doc_change": "note the rename",
    }
    assert c._edit_targets() == [
        ("a.py", "rename foo→bar", True),
        ("b.py", "update the call to bar", True),   # coordinated, as code, after the primary
        ("README.md", "note the rename", False),    # docs stay last
    ]

def test_edit_targets_empty_also_edit_is_single_file(tmp_path):
    c = _conductor(tmp_path)
    c.state.goal.intent = {"target_file": "a.py", "change": "x", "also_edit": []}
    assert c._edit_targets() == [("a.py", "x", True)]


# --- assembler token budgeting ---

def test_assembler_drops_over_budget_sources():
    state = WorkingState(goal=GoalSpec(request="r"))
    asm = ContextAssembler(state, budget_tokens=200)
    big = ContextBlock("code", "huge", "x" * 4000)           # ~1000 tokens, over budget
    small = ContextBlock("code", "tiny", "hello")
    a = asm.assemble(system="sys", stage_goal="g", instruction="do", sources=[small, big])
    kept = {b.label for b in a.blocks}
    dropped = {b.label for b in a.dropped}
    assert "tiny" in kept and "huge" in dropped
    assert a.used_tokens <= a.budget
