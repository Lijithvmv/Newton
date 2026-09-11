"""The Conductor — Newton's staged context-injection engine.

Claude is a map; Newton is GPS. A capable model gets the whole task and navigates
itself. A small local model can't, so the Conductor keeps the full route — the goal and
everything established so far — in WorkingState (on disk, unbounded, never shown whole),
and at each stage materializes only the few thousand tokens that step needs. The model
solves one bounded, well-lit problem at a time; deterministic gates catch errors before
they compound; results distill back into WorkingState instead of piling into the window.

Fixed skeleton: Understand -> Retrieve -> Plan -> Edit -> Verify -> Remember.
"""

from .pipeline import Conductor, ConductorResult
from .state import GoalSpec, WorkingState

__all__ = ["Conductor", "ConductorResult", "GoalSpec", "WorkingState"]
