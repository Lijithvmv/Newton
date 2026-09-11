"""The Project Conductor — fractal orchestration, one altitude above the Task Conductor.

A whole-project goal ("build a CLI todo app") is not one leaf task; it's a tree of them.
The Project Conductor decomposes the goal into a dependency-ordered backlog, then delegates
each task to a fresh Task Conductor — the hardened leaf executor with staged context
injection, gates, and whole-repo retrieval. It holds project-level state (the backlog,
what's built, what failed) the task level never sees, and injects only one task's goal
downward. Same staged pattern as the leaf, recursed.
"""

from .conductor import ProjectConductor, ProjectResult
from .state import ProjectState, Task

__all__ = ["ProjectConductor", "ProjectResult", "ProjectState", "Task"]
