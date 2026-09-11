"""Headless runner for the Author Conductor.

    python -m newton.author.run <type> "<topic>" [--project P] [--model M] [--yes] [--print]

    <type> ∈ prd | architecture | brainstorm | design

Drafts a grounded markdown deliverable to docs/<type>-<slug>.md, behind an approval gate.
"""

from __future__ import annotations

import sys

from ..config import load_settings
from .conductor import AUTHOR_SPECS, AuthorConductor

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

    if not parts:
        print(f'usage: newton-author <type> "<topic>"   (type ∈ {", ".join(AUTHOR_SPECS)})')
        return 1
    doc_type = parts[0].lower()
    if doc_type not in AUTHOR_SPECS:
        print(f"unknown doc type '{doc_type}'. Choose: {', '.join(AUTHOR_SPECS)}")
        return 2
    topic = " ".join(parts[1:]).strip()
    if not topic:
        print(f'a topic is required, e.g.:  newton-author {doc_type} "dark mode toggle"')
        return 2

    settings = load_settings(project)
    if model:
        settings.agent_model = model if model.startswith("ollama:") else f"ollama:{model}"

    print(B(f"Newton Author · {doc_type}") + DIM(f" · {settings.agent_model} · {settings.project_root}"))
    print(B(f"▸ {topic}"))
    result = AuthorConductor(settings, emit=_emit, approve=_approver(auto)).run(doc_type, topic)
    if show and result.content:
        print("\n" + result.content)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
