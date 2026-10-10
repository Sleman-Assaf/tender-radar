"""Golden Benchmark — measures the investigation and planning stages.

    from scraper_factory.benchmarking import BenchmarkEngine, render_run
"""

from .engine import (ERROR, FAIL, PASS, UNKNOWN, BenchmarkEngine, CaseResult,
                     render_run)

__all__ = ["BenchmarkEngine", "CaseResult", "render_run",
           "PASS", "FAIL", "UNKNOWN", "ERROR"]
