"""Reports — read the whole project, draft a grounded markdown report.

The Report Conductor applies the same discipline as the Task Conductor: the system owns
the report's skeleton and gathers the facts deterministically; the local model only writes
the prose for each section, grounded strictly in the facts it is handed.
"""

from .conductor import (
    REPORT_SPECS,
    ReportConductor,
    ReportResult,
    ReportSpec,
    compose_report,
    gather_facts,
)

__all__ = [
    "REPORT_SPECS",
    "ReportConductor",
    "ReportResult",
    "ReportSpec",
    "compose_report",
    "gather_facts",
]
