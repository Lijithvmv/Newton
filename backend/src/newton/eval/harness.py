"""The eval harness — run baseline vs. Newton on the same model and report the pass-rate delta.

Two arms, one model, objective checks, repeated runs (models are stochastic so a single run proves
nothing). The baseline is a FAIR control — a single, reasonable prompt asking for the whole file —
not a strawman. The newton arm is the full staged Conductor. Whichever wins, the number is real.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..conductor.pipeline import Conductor
from ..conductor.state import extract_code
from ..config import load_settings
from ..llm import complete
from ..proc import run_shell
from .tasks import SEED_TASKS, EvalTask

BASELINE_SYSTEM = (
    "You are a coding assistant. Make the requested change correctly and completely across ALL "
    "files it touches. Output only the changed files, nothing else."
)

_FILE_BLOCK = re.compile(r"===FILE:\s*(.+?)\s*===\n(.*?)(?:\n===END===|\Z)", re.DOTALL)


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _is_checker(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return base.startswith("test_") or base == "check.py"


def _restore_checkers(root: Path, task: EvalTask) -> None:
    """Rewrite the task's checker files to their originals before checking, so neither arm can
    make the test pass by editing the test."""
    for rel, content in task.files.items():
        if _is_checker(rel):
            (root / rel).write_text(content, encoding="utf-8")


def _parse_multifile(text: str) -> dict[str, str]:
    """Parse `===FILE: path===\\n...\\n===END===` blocks into {path: content}."""
    out: dict[str, str] = {}
    for m in _FILE_BLOCK.finditer(text):
        path = m.group(1).strip().strip("`\"'")
        out[path] = m.group(2).rstrip("\n")
    return out


def _run_check(root: Path, check: str) -> bool:
    """Run the task's objective checker in the sandbox. Exit 0 == the task succeeded."""
    cmd = check.replace("{py}", f'"{sys.executable}"')
    try:
        r = run_shell(cmd, cwd=root, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def run_baseline(task: EvalTask, model: str, root: Path) -> bool:
    """The control arm: one naive prompt for ALL changed files, write them, check. No staging,
    retrieval, or verify — a fair one-shot attempt at the whole task."""
    _write_files(root, task.files)
    shown = "\n\n".join(f"### {rel}\n```\n{content}\n```" for rel, content in task.files.items()) \
        or "(no files yet)"
    messages = [
        {"role": "system", "content": BASELINE_SYSTEM},
        {"role": "user", "content":
            f"Current files:\n{shown}\n\nTask: {task.goal}\n\n"
            "For EACH file you must create or change, output it in this EXACT format:\n"
            "===FILE: <path>===\n<the file's complete new content>\n===END===\n"
            "Output only these blocks, nothing else."},
    ]
    try:
        resp = complete(model, messages, temperature=0.1)
        content = resp.choices[0].message.content or ""
    except Exception:
        content = ""
    written = _parse_multifile(content)
    if written:
        for rel, body in written.items():
            if _is_checker(rel):
                continue                            # never let it overwrite the checker
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    elif task.target:                               # fallback: single-file code block → target
        code = extract_code(content)
        if code:
            (root / task.target).write_text(code, encoding="utf-8")
    _restore_checkers(root, task)
    return _run_check(root, task.check)


def run_newton(task: EvalTask, model: str, root: Path) -> bool:
    """Treatment arm A: the staged Conductor (Understand→…→Verify→Review→Remember)."""
    _write_files(root, task.files)
    settings = load_settings(str(root))
    settings.agent_model = model
    try:
        Conductor(settings, emit=lambda *a: None, approve=lambda *a: True,
                  learn_skills=False).run(task.goal)
    except Exception:
        pass                                        # a crash is just a failed run; the check decides
    _restore_checkers(root, task)
    return _run_check(root, task.check)


def run_loop(task: EvalTask, model: str, root: Path) -> bool:
    """Treatment arm B: the decomposition-loop engine (the differentiator) — decompose into atomic
    steps, shape per-step context, verify, checkpoint."""
    from ..loop import LoopEngine
    _write_files(root, task.files)
    settings = load_settings(str(root))
    settings.agent_model = model
    try:
        LoopEngine(settings, emit=lambda *a: None).run(task.goal)
    except Exception:
        pass
    _restore_checkers(root, task)
    return _run_check(root, task.check)


@dataclass
class ArmResult:
    passes: int = 0
    runs: int = 0

    @property
    def rate(self) -> float:
        return self.passes / self.runs if self.runs else 0.0


_ARMS = {"baseline": run_baseline, "newton": run_newton, "loop": run_loop}


def run_suite(model: str, tasks: list[EvalTask] | None = None, *, reps: int = 3,
              arms: tuple[str, ...] = ("baseline", "newton"),
              on_run=None) -> dict[tuple[str, str], ArmResult]:
    """Run each arm on each task `reps` times in a fresh sandbox. Returns {(arm, task_id): ArmResult}."""
    tasks = tasks or SEED_TASKS
    results: dict[tuple[str, str], ArmResult] = {}
    for task in tasks:
        for arm in arms:
            runner = _ARMS[arm]
            ar = ArmResult()
            for i in range(reps):
                root = Path(tempfile.mkdtemp(prefix=f"neval_{task.id}_{arm}_"))
                try:
                    ok = runner(task, model, root)
                except Exception:
                    ok = False
                finally:
                    shutil.rmtree(root, ignore_errors=True)
                ar.passes += int(ok)
                ar.runs += 1
                if on_run:
                    on_run(arm, task.id, i + 1, ok)
            results[(arm, task.id)] = ar
    return results


def report(results: dict[tuple[str, str], ArmResult], tasks: list[EvalTask] | None = None,
           *, model: str = "", arms: tuple[str, ...] = ("baseline", "newton")) -> str:
    """A text report: per-task pass rates per arm, overall, and the newton−baseline delta."""
    tasks = tasks or SEED_TASKS
    lines = [f"Newton eval — model: {model or '?'}"]
    header = f"{'task':<20}" + "".join(f"{a:>12}" for a in arms) + f"{'delta':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    totals = {a: ArmResult() for a in arms}
    for task in tasks:
        row = f"{task.id:<20}"
        rates = {}
        for a in arms:
            ar = results.get((a, task.id), ArmResult())
            rates[a] = ar.rate
            totals[a].passes += ar.passes
            totals[a].runs += ar.runs
            row += f"{ar.passes}/{ar.runs} ({ar.rate:.0%})".rjust(12)
        if "newton" in arms and "baseline" in arms:
            row += f"{(rates['newton'] - rates['baseline']):+.0%}".rjust(10)
        lines.append(row)
    lines.append("-" * len(header))
    trow = f"{'OVERALL':<20}"
    for a in arms:
        trow += f"{totals[a].rate:.0%}".rjust(12)
    if "newton" in arms and "baseline" in arms:
        trow += f"{(totals['newton'].rate - totals['baseline'].rate):+.0%}".rjust(10)
    lines.append(trow)
    return "\n".join(lines)
