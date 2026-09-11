"""Whole-repo context — Newton's local retrieval layer.

A small local model can't hold a repository in its window, so the Conductor must aim:
pull only the few relevant *chunks* out of hundreds of files. This package builds that
capability with no external services — a code-aware chunker, a pure-Python BM25 index,
and an AST code-graph (symbols + import dependencies). All stdlib, all local.

  RepoIndex(root).build()          # walk, chunk, analyze, index
  index.search("add a json flag")  # -> ranked chunks across the whole repo
  index.where_defined("Conductor") # -> files defining the symbol
  index.dependents("newton/conductor/state.py")  # -> files that import it
"""

from .chunker import Chunk
from .store import RepoIndex

__all__ = ["RepoIndex", "Chunk"]
