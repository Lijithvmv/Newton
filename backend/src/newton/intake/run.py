"""Headless runner for document intake.

    python -m newton.intake.run <file> [<file> ...] [--project P] [--dest raw]

Converts each document to markdown under <project>/<dest>/ and reports where it landed.
The RepoIndex picks the new markdown up on its next build, so it's immediately searchable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .converter import IntakeError, ingest

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_T = sys.stdout.isatty()
def _c(code, t): return f"\033[{code}m{t}\033[0m" if _T else t
def DIM(t): return _c("2", t)
def B(t): return _c("1", t)
def GR(t): return _c("32", t)
def RE(t): return _c("31", t)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    project, dest, files = ".", "raw", []
    it = iter(argv)
    for a in it:
        if a in ("--project", "-p"): project = next(it, ".")
        elif a in ("--dest", "-d"): dest = next(it, "raw")
        elif a in ("--help", "-h"):
            print(__doc__)
            return 0
        else:
            files.append(a)

    if not files:
        print("usage: newton-intake <file> [<file> ...] [--project P] [--dest raw]")
        return 1

    root = Path(project).resolve()
    print(B("Newton Intake") + DIM(f" · {root} · → {dest}/"))
    failures = 0
    for f in files:
        try:
            r = ingest(root, f, dest_dir=dest)
            print(GR(f"  ✓ {Path(f).name}") + DIM(f" → {r.dest} ({r.chars} chars)"))
        except IntakeError as e:
            failures += 1
            print(RE(f"  ✕ {Path(f).name}: {e}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
