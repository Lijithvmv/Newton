"""Chunking — turn files into retrievable units.

You retrieve chunks, not files: a function, a class, a markdown section. Code is split by
AST so each chunk is a coherent symbol with real line numbers; markdown by heading; other
text by line window. Small enough to fit several into an 8k window, whole enough to mean
something on their own.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class Chunk:
    path: str          # repo-relative path
    kind: str          # module | function | class | section | block
    name: str          # symbol or heading
    start: int         # 1-based start line
    end: int           # 1-based end line
    text: str

    @property
    def ref(self) -> str:
        return f"{self.path}:{self.start}"


def chunk_file(rel: str, source: str) -> list[Chunk]:
    if rel.endswith(".py"):
        return _chunk_python(rel, source)
    if rel.endswith((".md", ".markdown")):
        return _chunk_markdown(rel, source)
    return _chunk_lines(rel, source)


def _chunk_python(rel: str, source: str) -> list[Chunk]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _chunk_lines(rel, source)  # unparsable: fall back to windows

    lines = source.splitlines()
    _DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    chunks: list[Chunk] = []

    def _seg(node) -> str:
        s = ast.get_source_segment(source, node)
        if s is None:
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            s = "\n".join(lines[node.lineno - 1 : end])
        return s

    # Module chunk — ALL top-level non-def statements: imports, docstring, AND constants/config
    # WHEREVER they sit. (Previously only lines before the first def were captured, so a constant
    # defined below a function was invisible to retrieval — a real bug on config-at-bottom files.)
    non_defs = [n for n in tree.body if not isinstance(n, _DEFS)]
    if non_defs:
        module_text = "\n".join(_seg(n) for n in non_defs).strip()
        if module_text:
            lo = min(n.lineno for n in non_defs)
            hi = max((getattr(n, "end_lineno", n.lineno) or n.lineno) for n in non_defs)
            chunks.append(Chunk(rel, "module", "<module>", lo, hi, module_text))

    for node in tree.body:
        if not isinstance(node, _DEFS):
            continue
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        chunks.append(Chunk(rel, kind, node.name, node.lineno, end, _seg(node)))
    return chunks


def _chunk_markdown(rel: str, source: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[str] = []
    title, start = "<top>", 1
    for i, line in enumerate(source.splitlines(), 1):
        if line.lstrip().startswith("#"):
            if any(c.strip() for c in cur):
                chunks.append(Chunk(rel, "section", title, start, i - 1, "\n".join(cur).strip()))
            title = line.lstrip("# ").strip() or "<section>"
            cur, start = [line], i
        else:
            cur.append(line)
    if any(c.strip() for c in cur):
        chunks.append(Chunk(rel, "section", title, start, len(source.splitlines()), "\n".join(cur).strip()))
    return chunks


def _chunk_lines(rel: str, source: str, window: int = 50) -> list[Chunk]:
    lines = source.splitlines()
    chunks: list[Chunk] = []
    for i in range(0, len(lines), window):
        block = "\n".join(lines[i : i + window]).strip()
        if block:
            chunks.append(Chunk(rel, "block", f"L{i+1}", i + 1, min(i + window, len(lines)), block))
    return chunks
