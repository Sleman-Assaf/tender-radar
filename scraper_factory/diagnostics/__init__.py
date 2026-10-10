"""Diagnostics — find the earliest failing subsystem, then repair only that.

    from scraper_factory.diagnostics import DiagnosticEngine

    report = DiagnosticEngine().diagnose(
        investigation=..., plan=..., test_outcome=..., tender_report=...)
    report.root_cause      # the first proven failure
    report.symptoms        # everything downstream of it
    report.repair_scope    # the only concerns a repair may touch
"""

from .engine import DiagnosticEngine, DiagnosticReport, Finding, Subsystem
from .repair import Repair, map_repair, repair_instruction

__all__ = ["DiagnosticEngine", "DiagnosticReport", "Finding", "Repair",
           "Subsystem", "map_repair", "repair_instruction"]
