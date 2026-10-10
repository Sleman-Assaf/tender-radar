"""Investigation Engine — evidence gathering that must precede generation.

Public surface:

    from scraper_factory.investigation import InvestigationEngine, Stage

    report = InvestigationEngine().investigate(url)
    if not report.complete:
        ...                       # root cause is report.root_cause()
"""

from .contracts import (Evidence, Failure, InvestigationReport, Stage,
                        StageResult, UNKNOWN)
from .engine import InvestigationEngine

__all__ = [
    "Evidence", "Failure", "InvestigationEngine", "InvestigationReport",
    "Stage", "StageResult", "UNKNOWN",
]
