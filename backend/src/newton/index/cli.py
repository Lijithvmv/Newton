"""Inspect the repo index from the terminal.

    python -m newton.index.cli [root] --query "add a json flag"
    python -m newton.index.cli [root] --defs Conductor
    python -m newton.index.cli [root] --deps newton/conductor/state.py
    python -m newton.index.cli [root] --stats
"""

from __future__ import annotations

import sys

from .embeddings import Embedder
from .store import RepoIndex


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = "."
    query = defs = deps = None
    k = 8
    stats = False
    it = iter(argv)
    for a in it:
        if a == "--query":
            query = next(it, "")
        elif a == "--defs":
            defs = next(it, "")
        elif a == "--deps":
            deps = next(it, "")
        elif a == "--k":
            k = int(next(it, "8"))
        elif a == "--stats":
            stats = True
        elif not a.startswith("-"):
            root = a

    idx = RepoIndex(root, embedder=Embedder()).build()
    s = idx.stats()
    sem = "semantic+BM25" if idx.embedder.available() else "BM25 only"
    print(f"indexed {s['files']} files · {s['chunks']} chunks · {s['symbols']} symbols · {sem}\n")

    if stats:
        return 0
    if defs is not None:
        hits = idx.where_defined(defs)
        print(f"'{defs}' defined in:" if hits else f"'{defs}' not found")
        for h in hits:
            print(f"  {h}")
        return 0
    if deps is not None:
        dep = idx.dependents(deps)
        print(f"files that import {deps}:" if dep else f"nothing imports {deps}")
        for d in dep:
            print(f"  {d}")
        return 0
    if query is not None:
        print(f"top {k} chunks for: {query!r}\n")
        for c, score in idx.search(query, k):
            head = c.text.strip().splitlines()[0][:70] if c.text.strip() else ""
            print(f"  {score:5.2f}  {c.kind:8} {c.name:24} {c.ref}")
            print(f"         {head}")
        return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
