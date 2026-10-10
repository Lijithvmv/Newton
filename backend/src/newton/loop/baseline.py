"""Baseline-replay check validation — "could this check have failed?"

Newton's verdict trusts the checks its own plan wrote. A weak local model sometimes plans a check that
cannot fail: `python greet.py` for "add a --shout flag" exits 0 on the code BEFORE the change too, so a
run that never implemented the flag was rated verified (2 of 23 corpus runs, both this shape; the
hidden task checker failed both). A check that passes on the pre-change code proves nothing about the
change.

So: snapshot the project when the run starts; after a check passes, run the SAME command again on the
snapshot with the run's current test files copied in (the check itself must exist there, or it would
"fail" for the wrong reason). If it still passes, the check is vacuous for this change. No model call,
no labels — the code before the change is the counterfactual.

Opt-in (NEWTON_LOOP_BASELINE_CHECK=1) until measured. Limitation: for behaviour-preserving goals
(pure refactors) a baseline pass is expected; this only judges runs whose every passing check is
vacuous.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

# Newton's own state and heavy/derived trees never belong to the baseline.
_SKIP = {".newton", ".venv", "venv", "__pycache__", ".git", "node_modules", ".pytest_cache"}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _SKIP}


def is_check_file(rel: str) -> bool:
    """Files that ARE the check (copied into the baseline so the command can run there)."""
    parts = rel.replace("\\", "/").split("/")
    base = parts[-1]
    return (base.startswith("test_") or base.endswith("_test.py") or base in {"conftest.py", "check.py"}
            or "tests" in parts[:-1])


def snapshot(root: Path, dest: Path) -> Path:
    """Copy the project as it is before the run changes anything."""
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(root, dest, ignore=_ignore)
    return dest


def _overlay_checks(root: Path, target: Path) -> None:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _SKIP for part in rel.parts) or not is_check_file(str(rel)):
            continue
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)


def passes_on_baseline(executor: Any, step: Any, root: Path, baseline: Path) -> bool | None:
    """True if `step`'s check also passes on the pre-change code (vacuous); False if it fails there;
    None if it can't be judged (executor can't run elsewhere, or the replay errored)."""
    run_in = getattr(executor, "run_in", None)
    if run_in is None or not baseline.is_dir():
        return None
    work = Path(tempfile.mkdtemp(prefix="newton_baseline_"))
    try:
        shutil.copytree(baseline, work, dirs_exist_ok=True)
        _overlay_checks(Path(root), work)
        return bool(run_in(step, work).ok)
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
