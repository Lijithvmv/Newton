"""Headless runner for the Conductor — watch the staged mechanism work in the terminal.

    python -m newton.conductor.run "task"  [--project P] [--model M] [--yes]

Prints each stage, the exact Context-Inspector window composition per stage (this is the
data the UI rail will render), the plan gate, edit diffs, and verify gates.
"""

from __future__ import annotations

import sys

from ..config import load_settings
from .pipeline import Conductor

_T = sys.stdout.isatty()
def _c(code, t): return f"\033[{code}m{t}\033[0m" if _T else t
def DIM(t): return _c("2", t)
def B(t): return _c("1", t)
def CY(t): return _c("36", t)
def GR(t): return _c("32", t)
def YE(t): return _c("33", t)
def RE(t): return _c("31", t)
def MA(t): return _c("35", t)

_KIND = {"system": "sys", "goal": "goal", "state": "state", "project": "proj",
         "code": "code", "wiki": "wiki", "memory": "mem", "plan": "plan", "error": "err"}


def _emit(channel, payload):
    if channel == "stage":
        print("\n" + B(MA(f"◆ {payload}")))
    elif channel == "context":
        p = payload
        bar_w = 24
        filled = min(bar_w, int(bar_w * p["used"] / max(1, p["budget"])))
        bar = "█" * filled + "░" * (bar_w - filled)
        print(DIM(f"  ┌ context window  [{bar}] {p['used']}/{p['budget']} tok"))
        for kind, label, tok, pinned in p["blocks"]:
            pin = "📌" if pinned else "  "
            print(DIM(f"  │ {pin} ") + CY(f"{_KIND.get(kind,kind):>5}") + DIM(f" · {label} ") + DIM(f"({tok}t)"))
        for kind, label, tok in p.get("dropped", []):
            print(DIM(f"  │ 🚫 {_KIND.get(kind,kind):>5} · {label} ({tok}t)  — dropped, over budget"))
        print(DIM("  └"))
    elif channel == "note":
        print(GR(f"  ✓ {payload}"))
    elif channel == "plan":
        print(YE("  plan:"))
        for i, s in enumerate(payload, 1):
            print(YE(f"     {i}. {s}"))
    elif channel == "diff":
        print(YE(f"  ✎ edit {payload['file']}:"))
        print(RE(f"     - {payload['old'][:160].splitlines()[0] if payload['old'] else ''}"))
        print(GR(f"     + {payload['new'][:160].splitlines()[0] if payload['new'] else ''}"))
    elif channel == "verify":
        print(CY(f"  ⚖ {payload}"))
    elif channel == "halt":
        print(RE(f"  ✕ {payload}"))


def _approver(auto: bool):
    def approve(kind, args):
        if auto:
            print(DIM(f"  [auto-approve {kind}]"))
            return True
        if kind == "plan":
            print(YE("  approve plan? [y/N] "), end="")
        else:
            print(YE(f"  approve {kind} to {args.get('path')}? [y/N] "), end="")
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
        print("usage: python -m newton.conductor.run \"task\" [--project P] [--model M] [--yes]")
        return 1

    settings = load_settings(project)
    if model:
        settings.agent_model = model if model.startswith("ollama:") else f"ollama:{model}"

    print(B("Newton Conductor") + DIM(f" · {settings.agent_model} · {settings.project_root}"))
    print(DIM("staged context injection: Understand → Retrieve → Plan → Edit → Verify → Remember"))
    print(B(f"\n▸ {' '.join(parts)}"))

    conductor = Conductor(settings, emit=_emit, approve=_approver(auto), auto_approve=auto)
    result = conductor.run(" ".join(parts))

    print(("\n" + GR("✓ SUCCESS") if result.ok else "\n" + RE("✕ HALTED")) +
          DIM(f"  · stages: {' → '.join(result.stages)}"))
    print(result.answer)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
