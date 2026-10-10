"""F11 — benchmark framework.

Every generation produces a BenchmarkReport so quality is measured, not felt.
Metrics fall into three groups:

  execution   did it run, how fast, how stably
  extraction  did it get real data, with which fields, how many duplicates
  process     how close to house style, how many repairs, how long, approved?

`overall_score` is a weighted 0-100 summary for dashboards; the individual
metrics are what you act on.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger("scraper_factory")

# Weights for the headline score.
W_EXECUTION = 30
W_EXTRACTION = 25
W_FIELDS = 15
W_STYLE = 20
W_PROCESS = 10

REQUIRED_FIELDS = ("title", "source_url")
IMPORTANT_FIELDS = ("submission_deadline", "reference_no", "entity")


@dataclass
class BenchmarkResult:
    execution_success: bool = False
    runtime_ms: int = 0
    runtime_stability: float = 0.0
    extraction_accuracy: float = 0.0
    field_accuracy: dict[str, float] = field(default_factory=dict)
    tenders_extracted: int = 0
    duplicate_rate: float = 0.0
    style_similarity: float = 0.0
    repair_iterations: int = 0
    repair_success: bool = False
    generation_ms: int = 0
    overall_score: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class Benchmarker:
    """Computes metrics for one generation run."""

    def evaluate(self, *, test_outcome, style_score: int,
                 repair_iterations: int, generation_ms: int,
                 previous_runtimes: Optional[list[int]] = None,
                 validation_passed: bool = False) -> BenchmarkResult:
        r = BenchmarkResult()
        rows = list(getattr(test_outcome, "sample_rows", []) or [])

        # ── execution ─────────────────────────────────────────────────────────
        r.execution_success = bool(getattr(test_outcome, "ok", False))
        r.runtime_ms = int(getattr(test_outcome, "runtime_ms", 0) or 0)
        r.tenders_extracted = int(getattr(test_outcome, "tenders_found", 0) or 0)
        r.runtime_stability = self._stability(r.runtime_ms, previous_runtimes)

        # ── extraction ────────────────────────────────────────────────────────
        r.extraction_accuracy = self._extraction_accuracy(rows)
        r.field_accuracy = self._field_accuracy(rows)
        r.duplicate_rate = self._duplicate_rate(rows)

        # ── process ───────────────────────────────────────────────────────────
        r.style_similarity = max(0.0, min(1.0, style_score / 100))
        r.repair_iterations = repair_iterations
        # "Repair success" asks whether the repair loop fixed what the validator
        # rejected. It is independent of sandbox execution, which is measured
        # separately by execution_success — otherwise a validation-only run
        # would always report a failed repair.
        r.repair_success = repair_iterations > 0 and validation_passed
        r.generation_ms = generation_ms

        r.overall_score = self._overall(r)
        r.details = {
            "required_fields": list(REQUIRED_FIELDS),
            "rows_sampled": len(rows),
            "error_category": getattr(test_outcome, "error_category", ""),
            "error_message": (getattr(test_outcome, "error_message", "") or "")[:400],
            "weights": {"execution": W_EXECUTION, "extraction": W_EXTRACTION,
                        "fields": W_FIELDS, "style": W_STYLE, "process": W_PROCESS},
        }
        return r

    # ── metric helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _stability(runtime_ms: int, previous: Optional[list[int]]) -> float:
        """1.0 when this run's runtime matches history; lower when it swings."""
        history = [t for t in (previous or []) if t > 0]
        if not history or runtime_ms <= 0:
            return 1.0 if runtime_ms > 0 else 0.0
        avg = sum(history) / len(history)
        if avg <= 0:
            return 1.0
        deviation = abs(runtime_ms - avg) / avg
        return round(max(0.0, 1.0 - min(deviation, 1.0)), 4)

    @staticmethod
    def _extraction_accuracy(rows: list[dict]) -> float:
        """Share of rows carrying every required field."""
        if not rows:
            return 0.0
        good = sum(1 for row in rows
                   if all(str(row.get(f) or "").strip() for f in REQUIRED_FIELDS))
        return round(good / len(rows), 4)

    def _field_accuracy(self, rows: list[dict]) -> dict[str, float]:
        """Per-field fill rate, plus plausibility for dates and links."""
        if not rows:
            return {}
        out: dict[str, float] = {}
        for f in REQUIRED_FIELDS + IMPORTANT_FIELDS:
            filled = sum(1 for row in rows if str(row.get(f) or "").strip())
            out[f] = round(filled / len(rows), 4)

        out["deadline_plausible"] = self._date_plausibility(rows)
        out["link_valid"] = self._link_validity(rows)
        return out

    @staticmethod
    def _date_plausibility(rows: list[dict]) -> float:
        """Share of parsed deadlines inside [today-2y, today+3y].

        Catches the worst hallucination mode: a confidently wrong date.
        """
        parsed, plausible = 0, 0
        today = date.today()
        low = today.replace(year=today.year - 2)
        high = today.replace(year=today.year + 3)
        for row in rows:
            raw = str(row.get("submission_deadline") or "").strip()
            if not raw or raw in ("None", "none"):
                continue
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
            if not m:
                continue
            try:
                value = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            parsed += 1
            if low <= value <= high:
                plausible += 1
        if parsed == 0:
            return 0.0
        return round(plausible / parsed, 4)

    @staticmethod
    def _link_validity(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        good = sum(1 for row in rows
                   if str(row.get("source_url") or "").startswith(("http://",
                                                                   "https://")))
        return round(good / len(rows), 4)

    @staticmethod
    def _duplicate_rate(rows: list[dict]) -> float:
        """Share of rows sharing an external_id — a broken key looks like this."""
        if not rows:
            return 0.0
        keys = [str(row.get("external_id") or row.get("source_url") or "")
                for row in rows]
        keys = [k for k in keys if k]
        if not keys:
            return 1.0
        unique = len(set(keys))
        return round(1 - (unique / len(keys)), 4)

    # ── headline score ────────────────────────────────────────────────────────

    @staticmethod
    def _overall(r: BenchmarkResult) -> int:
        score = 0.0
        score += W_EXECUTION * (1.0 if r.execution_success else 0.0)
        score += W_EXTRACTION * r.extraction_accuracy

        fields = r.field_accuracy or {}
        important = [fields.get(f, 0.0) for f in IMPORTANT_FIELDS]
        field_component = (sum(important) / len(important)) if important else 0.0
        # Plausibility gates the field component: filled-but-wrong is not credit.
        field_component *= (0.5 + 0.5 * fields.get("deadline_plausible", 0.0))
        score += W_FIELDS * field_component

        score += W_STYLE * r.style_similarity

        process = 1.0
        process -= 0.2 * min(r.repair_iterations, 3)
        process -= 0.3 * r.duplicate_rate
        process = max(0.0, process)
        score += W_PROCESS * process

        return int(round(max(0.0, min(100.0, score))))


def persist(result: BenchmarkResult, *, generation_run, scraper=None,
            human_approved: Optional[bool] = None):
    """Write a BenchmarkReport row for a generation run."""
    from ..models import BenchmarkReport
    try:
        report, _ = BenchmarkReport.objects.update_or_create(
            generation_run=generation_run,
            defaults={
                "scraper": scraper,
                "prompt_version": getattr(
                    generation_run.prompt_version, "version", "") or "",
                "model_name": generation_run.model_name,
                "family": generation_run.family,
                "execution_success": result.execution_success,
                "runtime_ms": result.runtime_ms,
                "runtime_stability": result.runtime_stability,
                "extraction_accuracy": result.extraction_accuracy,
                "field_accuracy": result.field_accuracy,
                "tenders_extracted": result.tenders_extracted,
                "duplicate_rate": result.duplicate_rate,
                "style_similarity": result.style_similarity,
                "repair_iterations": result.repair_iterations,
                "repair_success": result.repair_success,
                "generation_ms": result.generation_ms,
                "human_approved": human_approved,
                "overall_score": result.overall_score,
                "details": result.details,
            },
        )
        return report
    except Exception as exc:  # noqa: BLE001 — benchmarking never blocks generation
        logger.warning("[factory] could not persist benchmark: %s", exc)
        return None
