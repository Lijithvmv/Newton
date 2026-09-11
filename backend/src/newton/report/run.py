"""Headless runner for the Report Conductor.

    python -m newton.report.run [status|architecture|progress] [--project P] [--model M] [--yes] [--print]

Reads the whole project and drafts a grounded markdown report to docs/, gated by approval.
"""

from __future__ import annotations

import sys

from ..config import load_settings
from .conductor import REPORT_SPECS, ReportConductor

# Windows consoles default to cp1252 and choke on the glyphs below; force UTF-8 so the
# report runner renders the same on every platform (mirrors newton/cli.py).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_T = sys.stdout.isatty()
def _c(code, t): return f"\033[{code}m{t}\033[0m" if _T else t
def DIM(t): return _c("2", t)
def B(t): return _c("1", t)
def CY(t): return _c("36", t)
def GR(t): return _c("32", t)
def YE(t): return _c("33", t)
def RE(t): return _c("31", t)


def _emit(channel, payload):
    if channel == "stage":
        print(CY(f"\n◆ {payload}"))
    elif channel == "note":
        print(DIM(f"  ✓ {payload}"))
    elif channel == "section":
        print(GR(f"  ▸ {payload['title']}"))
    elif channel == "diff":
        print(YE(f"  ✎ {payload['file']}"))
    elif channel == "report":
        print(GR(f"\n█ Wrote {payload['path']}"))
    elif channel == "halt":
        print(RE(f"  ✕ {payload}"))
    # 'context' is intentionally quiet in the CLI.


def _approver(auto):
    def approve(kind, args):
        if auto:
            print(DIM(f"  [auto-approve {kind} {args.get('path','')}]"))
            return True
        print(YE(f"  save {args.get('path','')}? [y/N] "), end="")
        try:
            return input().strip().lower() in ("y", "yes")
        except EOFError:
            return False
    return approve


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    project, model, auto, show, parts = ".", None, False, False, []
    it = iter(argv)
    for a in it:
        if a in ("--project", "-p"): project = next(it, ".")
        elif a in ("--model", "-m"): model = next(it, None)
        elif a in ("--yes", "-y"): auto = True
        elif a in ("--print",): show = True
        elif a in ("--help", "-h"):
            print(__doc__)
            return 0
        else:
            parts.append(a)

    report_type = (parts[0] if parts else "status").lower()
    if report_type not in REPORT_SPECS:
        print(f"unknown report type '{report_type}'. Choose: {', '.join(REPORT_SPECS)}")
        return 2

    settings = load_settings(project)
    if model:
        settings.agent_model = model if model.startswith("ollama:") else f"ollama:{model}"

    print(B(f"Newton Report · {report_type}") + DIM(f" · {settings.agent_model} · {settings.project_root}"))
    result = ReportConductor(settings, emit=_emit, approve=_approver(auto)).run(report_type)
    if show and result.content:
        print("\n" + result.content)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
