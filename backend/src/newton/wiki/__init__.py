"""LLM-wiki — curated, authored knowledge pages as a retrieval source.

Distinct from memory (auto-extracted, episodic): the wiki is authored truth — coding
patterns, conventions, how-tos for this project — that the Conductor injects into a task
when relevant. It lives in `<project>/wiki/*.md` and is searched with the same hybrid
BM25 + semantic retrieval as the code index (RepoIndex, reused).
"""

from .store import Wiki

__all__ = ["Wiki"]
