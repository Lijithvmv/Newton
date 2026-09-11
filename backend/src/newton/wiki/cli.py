"""Curate and query the project wiki.

    newton-wiki [--project P] list
    newton-wiki [--project P] show <page>
    newton-wiki [--project P] add <page> <file|->        # content from a file or stdin
    newton-wiki [--project P] search "<query>"
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..index.embeddings import Embedder
from .store import Wiki


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    project = "."
    rest: list[str] = []
    it = iter(argv)
    for a in it:
        if a in ("--project", "-p"):
            project = next(it, ".")
        else:
            rest.append(a)
    if not rest:
        print(__doc__)
        return 0

    wiki = Wiki(Path(project).resolve(), embedder=Embedder())
    cmd, args = rest[0], rest[1:]

    if cmd == "list":
        pages = wiki.pages()
        print("\n".join(f"  {p}" for p in pages) if pages else "(no wiki pages yet)")
    elif cmd == "show" and args:
        print(wiki.read(args[0]) or f"(no such page: {args[0]})")
    elif cmd == "add" and len(args) >= 2:
        src = args[1]
        content = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
        p = wiki.write(args[0], content)
        print(f"wrote {p}")
    elif cmd == "search" and args:
        hits = wiki.search(" ".join(args), k=4)
        if not hits:
            print("(no matches)")
        for ref, text in hits:
            print(f"\n▸ {ref}\n  " + text.strip().replace("\n", "\n  ")[:400])
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
