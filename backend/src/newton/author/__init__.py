"""Authoring — draft project deliverables (PRD, architecture, brainstorm, design).

Where the Report Conductor *summarizes* existing project state, the Author Conductor
*generates new thinking* from a topic — but under the same discipline: the system owns
the document's section skeleton and retrieves project context aimed at the topic, so the
model's output is grounded in the real codebase rather than generic filler. Each section
also sees the sections drafted before it, so the document coheres.

Output lands in `docs/<type>-<slug>.md`, which the RepoIndex then picks up — a PRD written
today is retrievable context for a coding task tomorrow.
"""

from .conductor import (
    AUTHOR_SPECS,
    AuthorConductor,
    DocResult,
    DocSpec,
    compose_doc,
    output_path,
)

__all__ = [
    "AUTHOR_SPECS",
    "AuthorConductor",
    "DocResult",
    "DocSpec",
    "compose_doc",
    "output_path",
]
