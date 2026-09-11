"""Deterministic verify gates — how a weak model yields a correct end result.

The Conductor never trusts a stage's output on faith. Between stages, cheap deterministic
checks confirm the work is sound: does the edit still apply, does the code parse, does it
run. A failed gate sends the stage back once with the error as fresh context, before the
mistake can poison the next step. Catching errors at each bounded step is the whole reason
the pipeline can outperform the model's raw one-shot ability.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GateResult:
    ok: bool
    detail: str = ""


def python_parses(code: str) -> GateResult:
    """The edited source is still valid Python."""
    try:
        ast.parse(code)
        return GateResult(True, "parses")
    except SyntaxError as e:
        return GateResult(False, f"SyntaxError: {e.msg} at line {e.lineno}")


def edit_applies(file_text: str, old: str, new: str) -> GateResult:
    """The proposed old->new edit is unambiguous against the current file."""
    if not old:
        return GateResult(False, "empty `old` — nothing to anchor the edit to")
    n = file_text.count(old)
    if n == 0:
        return GateResult(False, "`old` text not found in the file; it must match exactly")
    if n > 1:
        return GateResult(False, f"`old` matches {n} times; it must be unique")
    return GateResult(True, "edit anchors uniquely")


def run_succeeds(exit_code: int, output: str, expect: str = "") -> GateResult:
    """A command exited cleanly and (optionally) produced expected output."""
    if exit_code != 0:
        return GateResult(False, f"non-zero exit {exit_code}: {output[:300]}")
    if expect and expect not in output:
        return GateResult(False, f"expected '{expect}' in output, got: {output[:300]}")
    return GateResult(True, "ran clean" + (f", found '{expect}'" if expect else ""))


# --- import-based verification (works for libraries, not just scripts) ---
#
# Running `python file.py` only verifies files that print something and take no required
# args. Most real code is a library: functions/classes with no output. The robust check is
# to IMPORT the module (which runs its top level but not its __main__ guard, so it never
# trips on required CLI args), confirm the expected symbols exist, and — when the request
# gives a concrete example — call the function and assert the result.

def module_import_name(rel: str) -> str | None:
    """Repo-relative path → dotted import name. `calc.py`→`calc`, `pkg/m.py`→`pkg.m`."""
    if not rel.endswith(".py"):
        return None
    p = rel[:-3].replace("\\", "/")
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".") or None


def build_check_script(
    root: Path, target_path: Path, symbols: list[str], behaviours: list[tuple[str, str]]
) -> str:
    """Emit a self-contained Python script that imports the target FILE (by path, robust to
    any location or name), asserts each symbol exists, and runs each behaviour assertion,
    printing VERIFY_OK or VERIFY_FAIL: <reason>. Importing by path avoids the fragility of
    turning a repo path into a dotted module name (leading dots, sub-packages, odd names)."""
    lines = [
        "import importlib.util, sys",
        f"sys.path.insert(0, {str(root)!r})",   # so the target's sibling imports resolve
        "errors = []",
        "try:",
        f"    _spec = importlib.util.spec_from_file_location('_newton_target', {str(target_path)!r})",
        "    M = importlib.util.module_from_spec(_spec)",
        "    _spec.loader.exec_module(M)",
        "except Exception as e:",
        "    print('VERIFY_FAIL: import failed: ' + repr(e)); sys.exit(1)",
    ]
    for name in symbols:
        lines.append(f"if not hasattr(M, {name!r}): errors.append('missing symbol: {name}')")
    for call, expected in behaviours:
        msg = repr(f"{call} should equal {expected}")
        lines += [
            "try:",
            f"    assert M.{call} == {expected}, {msg}",
            "except Exception as e:",
            "    errors.append(str(e))",
        ]
    lines += [
        "if errors:",
        "    print('VERIFY_FAIL: ' + '; '.join(errors)); sys.exit(1)",
        "print('VERIFY_OK')",
    ]
    return "\n".join(lines) + "\n"
