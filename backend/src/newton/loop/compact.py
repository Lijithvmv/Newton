"""Mechanical compaction — fit reference context into a small window WITHOUT rewriting it.

The model-backed compactor asks the local model to summarise reference code when a step's window
overflows. A summary is lossy in exactly the wrong way for code: a module path, a constant's value or a
parameter name can be paraphrased away or invented, and a 4B model is a weak summariser. The field's
converging answer (CliffCompaction; winnow; fast-jev-compaction) is to only DROP or TRUNCATE, never
rephrase — so whatever survives is faithful by construction.

Newton's version, specialised for code. A ladder, each rung applied only if the previous one didn't fit:
  1. nothing — everything fits;
  2. skeleton — Python blocks keep imports, module/class constants, and def/class signatures (+ the
     first docstring line) verbatim; function bodies become `...`. Chunks that don't parse on their own
     (retrieved fragments) fall back to a line filter keeping the same kinds of lines;
  3. fair-share truncation — every block keeps its header (which carries the `import it as module X`
     line) and a proportional share of its lines, cut at a line boundary with an omission marker.
Only original lines survive, plus markers that say what was omitted. No model call, deterministic.
"""

from __future__ import annotations

import ast
import re

_BLOCK = re.compile(r"^(?P<head>.*?)\n```(?P<lang>[^\n]*)\n(?P<code>.*)\n```\s*$", re.DOTALL)
_KEEP_LINE = re.compile(
    r"^\s*(?:@|def |async def |class |import |from \S+ import |[A-Za-z_][A-Za-z0-9_]*\s*(?::[^=]+)?=)")


def _split(part: str) -> tuple[str, str, str | None]:
    """(header, fence_lang, code) for a `header\\n```\\ncode\\n```` part; code None if not fenced."""
    m = _BLOCK.match(part)
    if not m:
        return part, "", None
    return m.group("head"), m.group("lang"), m.group("code")


def _render(head: str, lang: str, code: str | None) -> str:
    return head if code is None else f"{head}\n```{lang}\n{code}\n```"


def _stmt_lines(node: ast.stmt, lines: list[str], out: list[str]) -> None:
    """Append the kept lines of one statement (imports, assignments, signatures) to `out`."""
    start, end = node.lineno - 1, (node.end_lineno or node.lineno) - 1
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        out.extend(lines[start:end + 1])
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        out.extend(lines[start:min(end, start + 2) + 1])      # a long literal keeps its first lines
        if end > start + 2:
            out.append(" " * node.col_offset + "# …")
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        top = min([d.lineno - 1 for d in node.decorator_list] + [start])
        first = node.body[0]
        if first.lineno - 1 <= start:                           # `def f(): return 1` — one line
            out.extend(lines[top:start + 1])
            return
        out.extend(lines[top:first.lineno - 1])                 # decorators + (multi-line) signature
        body = node.body
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.append(lines[first.lineno - 1])                 # first docstring line
            body = body[1:]
        if isinstance(node, ast.ClassDef):
            n = len(out)
            for child in body:
                _stmt_lines(child, lines, out)
            if len(out) == n and body:
                out.append(" " * first.col_offset + "...")
        elif body:
            out.append(" " * first.col_offset + "...")


def skeleton(code: str) -> str:
    """Signatures, imports and constants of Python `code`, verbatim; bodies elided to `...`."""
    lines = code.splitlines()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # A retrieved fragment may start/end mid-statement: keep the same kinds of lines by pattern.
        kept = [ln for ln in lines if _KEEP_LINE.match(ln)]
        return "\n".join(kept) if kept else code
    out: list[str] = []
    for node in tree.body:
        _stmt_lines(node, lines, out)
    return "\n".join(out) if out else code


def _truncate(code: str, room: int) -> str:
    """Keep whole leading lines within `room` chars, then an omission marker."""
    if len(code) <= room:
        return code
    room -= 40                                                  # the omission marker's own cost
    lines = code.splitlines()
    kept, used = [], 0
    for ln in lines:
        if used + len(ln) + 1 > room:
            break
        kept.append(ln)
        used += len(ln) + 1
    dropped = len(lines) - len(kept)
    if dropped:
        kept.append(f"# … [{dropped} more line(s) omitted]")
    return "\n".join(kept)


def mechanical_compact(parts: list[str], room: int, *, sep: str = "\n\n") -> str:
    """Fit reference `parts` into `room` chars by the skeleton → fair-share-truncation ladder."""
    def join(blocks) -> str:
        return sep.join(_render(*b) for b in blocks)

    blocks = [_split(p) for p in parts]
    text = join(blocks)
    if len(text) <= room:
        return text
    blocks = [(h, lang, skeleton(c) if c is not None and lang in ("", "py", "python") else c)
              for h, lang, c in blocks]
    text = join(blocks)
    if len(text) <= room:
        return text
    # Fair share of the room for code, after every header + fence + separator is paid for.
    fixed = sum(len(_render(h, lang, "" if c is not None else None)) for h, lang, c in blocks)
    fixed += len(sep) * (len(blocks) - 1)
    budget = room - fixed
    codes = [c for _, _, c in blocks if c is not None]
    if budget > 0 and codes:
        # Water-filling: small blocks keep everything, the rest split what's left evenly.
        share, pending = {}, sorted(range(len(blocks)), key=lambda i: len(blocks[i][2] or ""))
        pending = [i for i in pending if blocks[i][2] is not None]
        left = budget
        while pending:
            even = left // len(pending)
            i = pending[0]
            need = len(blocks[i][2])
            share[i] = min(need, even)
            left -= share[i]
            pending.pop(0)
        blocks = [(h, lang, _truncate(c, share[i]) if c is not None else None)
                  for i, (h, lang, c) in enumerate(blocks)]
        text = join(blocks)
    return text[:room]
