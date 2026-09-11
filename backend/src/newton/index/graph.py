"""Code graph — symbols and dependencies from Python AST.

Grep can't tell you that changing `state.py` affects `pipeline.py`; a dependency graph
can. For each file we record what it defines and what it imports, then resolve imports
(absolute and relative) to real repo files so we can walk the edges both ways. This is the
'what depends on X' sense of whole-repo context — the thing that keeps a small model from
editing a file blind to its callers.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class FileInfo:
    path: str
    defs: list[str] = field(default_factory=list)      # functions/classes defined here
    imports: list[str] = field(default_factory=list)   # raw import targets (may be relative)


def analyze_python(rel: str, source: str) -> FileInfo:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return FileInfo(rel)
    info = FileInfo(rel)
    for node in tree.body:  # top-level defs are the public surface
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            info.defs.append(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info.imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            info.imports.append("." * node.level + (node.module or ""))
    return info


def _module_of(rel: str) -> str:
    """Repo path -> dotted module name. `a/b/c.py` -> `a.b.c`, `a/b/__init__.py` -> `a.b`."""
    mod = rel[:-3] if rel.endswith(".py") else rel
    mod = mod.replace("\\", "/")
    if mod.endswith("/__init__"):
        mod = mod[: -len("/__init__")]
    return mod.replace("/", ".")


class CodeGraph:
    def __init__(self) -> None:
        self.files: dict[str, FileInfo] = {}
        self.symbol_index: dict[str, list[str]] = defaultdict(list)
        self._edges: dict[str, set[str]] = {}          # path -> set of repo paths it imports
        self._rev: dict[str, set[str]] = defaultdict(set)
        self._pr_cache: dict = {}                       # memoized PageRank by personalization key

    def add(self, info: FileInfo) -> None:
        self.files[info.path] = info
        for d in info.defs:
            self.symbol_index[d].append(info.path)

    def finalize(self) -> None:
        """Resolve every import to a repo file (if it is one) and build the edge maps."""
        mod_to_path = {_module_of(p): p for p in self.files}
        for path, info in self.files.items():
            importer_mod = _module_of(path)
            targets: set[str] = set()
            for imp in info.imports:
                resolved = self._resolve(importer_mod, imp)
                # An import may name a module OR a symbol inside one; try progressively shorter.
                parts = resolved.split(".")
                for i in range(len(parts), 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in mod_to_path and mod_to_path[cand] != path:
                        targets.add(mod_to_path[cand])
                        break
            self._edges[path] = targets
            for t in targets:
                self._rev[t].add(path)
        self._pr_cache.clear()                          # edges changed — stale ranks

    # --- centrality (PageRank over the import graph) -------------------

    def pagerank(self, personalization: dict[str, float] | None = None,
                 damping: float = 0.85, iters: int = 30) -> dict[str, float]:
        """Structural importance of each file: rank flows along 'A imports B', so a module
        many files depend on (config, models, core helpers) scores high — the same signal
        Aider's repo map uses. `personalization` biases the walk toward focus files (the
        task's target) so retrieval leans toward that file's neighbourhood. Memoized."""
        key = None if not personalization else tuple(sorted(personalization.items()))
        if key in self._pr_cache:
            return self._pr_cache[key]

        nodes = list(self.files)
        n = len(nodes)
        if n == 0:
            self._pr_cache[key] = {}
            return {}
        idx = {p: i for i, p in enumerate(nodes)}
        out: list[list[int]] = [[] for _ in nodes]
        for a, targets in self._edges.items():
            if a not in idx:
                continue
            for b in targets:
                if b in idx:
                    out[idx[a]].append(idx[b])

        # Teleport distribution: uniform, or concentrated on the focus files if given.
        teleport = [1.0 / n] * n
        if personalization:
            weights = [0.0] * n
            total = 0.0
            for p, w in personalization.items():
                if p in idx and w > 0:
                    weights[idx[p]] += w
                    total += w
            if total > 0:
                teleport = [w / total for w in weights]

        rank = [1.0 / n] * n
        for _ in range(iters):
            dangling = sum(rank[i] for i in range(n) if not out[i])
            new = [(1 - damping) * teleport[i] + damping * dangling * teleport[i] for i in range(n)]
            for i in range(n):
                if out[i]:
                    share = damping * rank[i] / len(out[i])
                    for j in out[i]:
                        new[j] += share
            rank = new

        result = {nodes[i]: rank[i] for i in range(n)}
        self._pr_cache[key] = result
        return result

    @staticmethod
    def _resolve(importer_mod: str, imp: str) -> str:
        if not imp.startswith("."):
            return imp
        # Relative import: strip one package level per leading dot.
        level = len(imp) - len(imp.lstrip("."))
        base = importer_mod.split(".")
        base = base[: max(0, len(base) - level)]
        tail = imp.lstrip(".")
        return ".".join(base + ([tail] if tail else []))

    # --- queries -------------------------------------------------------

    def where_defined(self, name: str) -> list[str]:
        return list(self.symbol_index.get(name, []))

    def imports_of(self, path: str) -> list[str]:
        return sorted(self._edges.get(path, set()))

    def dependents(self, path: str) -> list[str]:
        return sorted(self._rev.get(path, set()))

    def to_dict(self) -> dict:
        return {"files": {p: {"defs": i.defs, "imports": i.imports} for p, i in self.files.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> CodeGraph:
        g = cls()
        for p, fi in d.get("files", {}).items():
            g.add(FileInfo(p, list(fi.get("defs", [])), list(fi.get("imports", []))))
        g.finalize()
        return g
