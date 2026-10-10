"""Scheduler integration — the Factory watches, it does not rewrite.

Health tracking has to happen after every scheduled run, and there were two ways
to arrange that: edit the scheduler's two run loops in `tenders/apps.py`, or
observe the record they already write. This module does the second.

Observing wins on every count that matters here:

  * `tenders/apps.py` is not modified at all, so scheduler behaviour for the 73
    hand-written scrapers is byte-for-byte what it was.
  * One hook covers every path that runs a scraper — the 6-hour loop, the manual
    "run now" button, the management commands, and the Factory's own first run —
    because all of them write a ScrapeLog.
  * If this code fails, the scraper run has already been recorded. A bug in
    Factory telemetry cannot lose tenders.

The signal fires on every ScrapeLog save, so the handler exits immediately
unless the row is a *finished* run belonging to a scraper the Factory authored.
That check is two indexed queries and it is the common case, since most sources
are hand-written.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("scraper_factory")

#: Set once by apps.ready(). Guards against double registration under the
#: autoreloader, which would otherwise record every run twice.
_connected = False


def connect() -> None:
    """Attach the observer. Idempotent."""
    global _connected
    if _connected:
        return
    from django.db.models.signals import post_save

    from tenders.models import ScrapeLog

    post_save.connect(_on_scrape_log_saved, sender=ScrapeLog,
                      dispatch_uid="scraper_factory.health_observer")
    _connected = True
    logger.info("[factory] health observer attached to ScrapeLog")


def _on_scrape_log_saved(sender, instance, created, **kwargs) -> None:
    """React to a finished run of a factory-generated scraper."""
    try:
        if created:
            return                      # the RUNNING row; nothing to evaluate yet
        if instance.finished_at is None:
            return
        from tenders.models import ScrapeLog
        if instance.status == ScrapeLog.RunStatus.RUNNING:
            return

        scraper = _factory_scraper_for(instance)
        if scraper is None:
            return                      # hand-written source: not ours to judge

        record_execution(scraper, instance)
    except Exception as exc:  # noqa: BLE001
        # Telemetry must never propagate into a scraper run.
        logger.warning("[factory] health observer failed: %s", exc, exc_info=True)


def _factory_scraper_for(log):
    """The GeneratedScraper behind this log, or None for hand-written sources."""
    from .models import FactoryStatus, GeneratedScraper

    scraper_class = getattr(getattr(log, "source", None), "scraper_class", "")
    if not scraper_class:
        return None
    return (GeneratedScraper.objects
            .filter(class_name=scraper_class,
                    status=FactoryStatus.INSTALLED)
            .first())


def record_execution(scraper, log):
    """Evaluate one run, update health, store metrics, raise alerts.

    This is the pipeline the Phase 4A brief specifies, in order:
        run → evaluate → update health → store metrics → alert
    Alerts are raised inside HealthMonitor, which owns the degradation policy.
    """
    from django.utils import timezone

    from tenders.models import ScrapeLog, Tender

    from .diagnostics import DiagnosticEngine
    from .health import HealthMonitor
    from .models import ExecutionMetric, InvestigationRecord
    from .validation.field_telemetry import FieldTelemetry
    from .validation.tender_validator import TenderValidator

    # ── evaluate ─────────────────────────────────────────────────────────────
    failed = log.status == ScrapeLog.RunStatus.FAILED
    found = int(log.tenders_found or 0)
    started = log.started_at
    finished = log.finished_at or timezone.now()
    execution_ms = max(0, int((finished - started).total_seconds() * 1000))

    # Validate a sample of what this run actually saved. The scheduler does not
    # hand us the TenderData objects, so the saved rows are the evidence
    # available — which is also what a reviewer would look at.
    rows = list(Tender.objects.filter(source=log.source)
                .order_by("-id")
                .values("title", "source_url", "external_id", "reference_no",
                        "entity", "description", "publication_date",
                        "submission_deadline")[:40])
    tender_report = TenderValidator().validate(rows) if rows else None
    field_report = FieldTelemetry().measure(rows) if rows else None

    diagnosis = None
    investigation_record = (InvestigationRecord.objects
                            .filter(scraper=scraper)
                            .order_by("-created_at")
                            .first())
    if failed or found == 0 or (tender_report is not None
                                and not tender_report.passed):
        diagnosis = _diagnose(scraper, log, tender_report, investigation_record,
                              field_report)

    # ── update health (raises the alerts) ────────────────────────────────────
    monitor = HealthMonitor()
    health = monitor.record_run(
        scraper, tenders_found=found, diagnosis=diagnosis,
        tender_report=tender_report,
        investigation_record=investigation_record,
        error=(log.error_message or "") if failed else "")

    # ── store metrics ────────────────────────────────────────────────────────
    ai = _ai_counts(log.source)
    try:
        ExecutionMetric.objects.create(
            scraper=scraper, scrape_log=log, phase="EXECUTION",
            succeeded=bool(found > 0 and not failed
                           and (tender_report is None or tender_report.rows_valid > 0)),
            failure_category=(diagnosis.category if diagnosis else ""),
            root_cause_stage=(diagnosis.subsystem if diagnosis else ""),
            strategy=_strategy_of(investigation_record),
            execution_ms=execution_ms, extraction_ms=execution_ms,
            tenders_total=found,
            tenders_valid=(tender_report.rows_valid if tender_report else 0),
            tenders_rejected=(tender_report.rows_rejected if tender_report else 0),
            tenders_new=int(log.tenders_new or 0),
            tenders_updated=int(log.tenders_updated or 0),
            ai_relevant=ai["relevant"], ai_not_relevant=ai["not_relevant"],
            ai_unchecked=ai["unchecked"],
            details={
                "log_status": log.status,
                "health_state": health.state if health else "",
                "consecutive_failures": health.consecutive_failures if health else 0,
                "validation_confidence": (tender_report.confidence
                                          if tender_report else 0),
                "stop_category": (tender_report.stop_category
                                  if tender_report else ""),
                # Field-level detail, so a slow decay in one column is visible
                # before it becomes a whole-source failure.
                "field_coverage": ({name: round(s.coverage, 3)
                                    for name, s in field_report.fields.items()}
                                   if field_report else {}),
                "missing_core": (field_report.missing_core
                                 if field_report else []),
                "sparse_fields": (field_report.sparse_fields
                                  if field_report else []),
                "date_parse_rate": (round(field_report.date_parse_rate, 3)
                                    if field_report else 0),
                "dates_expected": (field_report.dates_expected
                                   if field_report else False),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] could not store execution metric: %s", exc)

    logger.info("[factory] recorded run of %s: found=%s health=%s",
                scraper.class_name, found, health.state if health else "?")
    return health


def _diagnose(scraper, log, tender_report, investigation_record,
              field_report=None):
    """Diagnose a failed or empty run using the stored investigation."""
    from .diagnostics import DiagnosticEngine
    from .interfaces import TestOutcome

    outcome = TestOutcome(
        ok=log.status != "FAILED" and int(log.tenders_found or 0) > 0,
        tenders_found=int(log.tenders_found or 0),
        error_message=(log.error_message or "")[:2000],
        error_category=("ENGINE" if log.status == "FAILED" else ""))

    investigation = _rehydrate(investigation_record)
    try:
        return DiagnosticEngine().diagnose(
            investigation=investigation, test_outcome=outcome,
            tender_report=tender_report, field_report=field_report)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] scheduled-run diagnosis failed: %s", exc)
        return None


def _rehydrate(record):
    """Rebuild an InvestigationReport from its stored JSON.

    Diagnosis needs the original evidence — particularly whether rows were ever
    proven in the HTML, which is what gates the SELECTOR_MISMATCH verdict. Without
    this the scheduler could only ever report UNKNOWN.
    """
    if record is None:
        return None
    from .investigation.contracts import (Evidence, InvestigationReport,
                                          StageResult)

    report = InvestigationReport(url=record.url, tier=record.tier)
    report.escalated = record.escalated
    report.data_source = record.data_source
    report.data_source_confidence = record.data_source_confidence
    report.site_signature = record.site_signature
    report.language = record.language
    report.country_hint = record.country_hint

    for name, payload in (record.stages or {}).items():
        if not isinstance(payload, dict):
            continue
        stage = StageResult(stage=name)
        stage.conclusion = payload.get("conclusion", "UNKNOWN")
        stage.confidence = int(payload.get("confidence") or 0)
        stage.failure = payload.get("failure", "") or ""
        stage.unknowns = list(payload.get("unknowns") or [])
        stage.data = payload.get("data") or {}
        for e in payload.get("evidence") or []:
            if isinstance(e, dict):
                stage.evidence.append(Evidence(what=e.get("what", ""),
                                               where=e.get("where", ""),
                                               detail=e.get("detail", "")))
        report.stages[name] = stage
    return report


def _ai_counts(source) -> dict:
    """How the AI classifier has judged this source's tenders so far."""
    from tenders.models import Tender
    try:
        qs = Tender.objects.filter(source=source)
        checked = qs.filter(is_aec_checked=True)
        return {
            "relevant": checked.filter(is_aec=True).count(),
            "not_relevant": checked.filter(is_aec=False).count(),
            "unchecked": qs.filter(is_aec_checked=False).count(),
        }
    except Exception:  # noqa: BLE001
        return {"relevant": 0, "not_relevant": 0, "unchecked": 0}


def _strategy_of(record) -> str:
    return getattr(record, "strategy", "") or ""
