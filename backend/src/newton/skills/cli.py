"""newton-skill — curate and query the project's skill playbooks.

    newton-skill list                       # list skills (name — description)
    newton-skill show <name>                # print a skill
    newton-skill search "<query>"           # the skill most relevant to a task
    newton-skill add <name> [--file PATH]   # create/overwrite a skill (body from PATH or stdin)

    (add --project PATH to point at another project)
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import load_settings
from ..index.embeddings import Embedder
from .store import Skills

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    project, file_path, rest = ".", None, []
    it = iter(argv)
    for a in it:
        if a in ("--project", "-p"):
            project = next(it, ".")
        elif a in ("--file", "-f"):
            file_path = next(it, None)
        elif a in ("--help", "-h"):
            print(__doc__)
            return 0
        else:
            rest.append(a)

    skills = Skills(load_settings(project).project_root, embedder=Embedder())
    cmd = rest[0] if rest else "list"

    if cmd == "list":
        items = skills.list()
        if not items:
            print("(no skills yet — add one with `newton-skill add <name>`)")
            return 0
        for m in items:
            print(f"- {m.file[:-3]}" + (f" — {m.description}" if m.description else ""))
        return 0

    if cmd == "show":
        if len(rest) < 2:
            print("usage: newton-skill show <name>")
            return 1
        text = skills.read(rest[1])
        print(text or f"(no such skill: {rest[1]})")
        return 0 if text else 1

    if cmd == "search":
        query = " ".join(rest[1:]).strip()
        if not query:
            print('usage: newton-skill search "<query>"')
            return 1
        hits = skills.search(query, k=1)
        if not hits:
            print("(no relevant skill)")
            return 0
        name, text = hits[0]
        print(f"# best match: {name}\n\n{text}")
        return 0

    if cmd == "add":
        if len(rest) < 2:
            print("usage: newton-skill add <name> [--file PATH]   (else reads stdin)")
            return 1
        name = rest[1]
        body = Path(file_path).read_text(encoding="utf-8") if file_path else sys.stdin.read()
        if not body.strip():
            print("nothing to write (empty body)")
            return 1
        p = skills.write(name, body)
        print(f"wrote {p}")
        return 0

    print(f"unknown command '{cmd}'. Try: list | show | search | add")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
