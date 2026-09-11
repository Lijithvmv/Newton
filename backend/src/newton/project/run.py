"""Headless runner for the Project Conductor.

    python -m newton.project.run "build a ..." [--project P] [--model M] [--yes]

Renders the two altitudes: the project backlog and per-task status, with the nested Task
Conductor's stages/context/gates indented beneath the task currently running.
"""

from __future__ import annotations

import sys

from ..config import load_settings
from .conductor import ProjectConductor

_T = sys.stdout.isatty()
def _c(code, t): return f"\033[{code}m{t}\033[0m" if _T else t
def DIM(t): return _c("2", t)
def B(t): return _c("1", t)
def CY(t): return _c("36", t)
def GR(t): return _c("32", t)
def YE(t): return _c("33", t)
def RE(t): return _c("31", t)
def MA(t): return _c("35", t)
def BL(t): return _c("34", t)


def _emit(channel, payload):
    if channel == "backlog":
        print(B(BL("\n▤ Backlog:")))
        for i, t in enumerate(payload, 1):
            dep = f"  ⟵ {', '.join(t['depends_on'])}" if t.get("depends_on") else ""
            print(BL(f"   {i}. [{t['id']}] {t['title']}") + DIM(f"  ({t.get('file') or '—'}){dep}"))
    elif channel == "task":
        p = payload
        if p["status"] == "start":
            print(B(BL(f"\n╔══ TASK [{p['id']}] {p['title']}")) + DIM(f"  {p.get('file') or ''}"))
        else:
            ok = p["status"] == "done"
            print((GR("╚══ ✓ task done") if ok else RE("╚══ ✕ task failed")) + DIM(f"  {p.get('summary','')}"))
    elif channel == "project":
        p = payload
        print(("\n" + GR("█ PROJECT COMPLETE") if p["ok"] else "\n" + YE("█ PROJECT ENDED")) + "  " + p["answer"])
    # nested Task Conductor events — indented under the running task
    elif channel == "stage":
        print("  " + MA(f"◆ {payload}"))
    elif channel == "note":
        print(DIM(f"    ✓ {payload}"))
    elif channel == "verify":
        print(CY(f"    ⚖ {payload}"))
    elif channel == "plan":
        for i, s in enumerate(payload, 1):
            print(YE(f"    {i}. {s}"))
    elif channel == "diff":
        print(YE(f"    ✎ {payload['file']}"))
    elif channel == "halt":
        print(RE(f"    ✕ {payload}"))
    # 'context' is intentionally quiet here to keep the two-level view readable.


def _approver(auto):
    def approve(kind, args):
        if auto:
            print(DIM(f"    [auto-approve {kind}]"))
            return True
        label = "backlog" if kind == "backlog" else f"{kind} {args.get('path','')}"
        print(YE(f"    approve {label}? [y/N] "), end="")
        try:
            return input().strip().lower() in ("y", "yes")
        except EOFError:
            return False
    return approve


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    project, model, auto, parts = ".", None, False, []
    it = iter(argv)
    for a in it:
        if a in ("--project", "-p"): project = next(it, ".")
        elif a in ("--model", "-m"): model = next(it, None)
        elif a in ("--yes", "-y"): auto = True
        else: parts.append(a)
    if not parts:
        print('usage: python -m newton.project.run "build a ..." [--project P] [--model M] [--yes]')
        return 1

    settings = load_settings(project)
    if model:
        settings.agent_model = model if model.startswith("ollama:") else f"ollama:{model}"

    print(B("Newton Project Conductor") + DIM(f" · {settings.agent_model} · {settings.project_root}"))
    print(DIM("fractal: decompose → per task, a full Task Conductor (staged, gated, verified)"))
    print(B(f"\n▸ {' '.join(parts)}"))

    result = ProjectConductor(settings, emit=_emit, approve=_approver(auto)).run(" ".join(parts))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
