"""Operational read model — one query set behind the dashboard and the reports.

The dashboard, the daily report and the CLI all want the same seventeen facts
about each generated scraper. Computing them in one place means the three
surfaces can never disagree about what "success rate" means, which is the usual
way operational dashboards start lying.

Everything here is read-only and derived from tables that other code owns:
ScraperHealth (current state), ExecutionMetric (history), InvestigationRecord
(how the scraper was built), ScraperSource (whether the scheduler will run it).
Nothing is duplicated, so the numbers cannot drift out of date — they are
recomputed on read, which is also why "updates after every execution" needs no
extra machinery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

logger = logging.getLogger("scraper_factory")


@dataclass
class ScraperOverview:
    """The seventeen operational facts about one generated scraper."""

    scraper_id: int
    name: str
    class_name: str
    module_name: str
    website_url: str
    country: str = ""

    # 1–8 health
    health_status: str = "UNKNOWN"
    last_run: Any = None
    last_successful_run: Any = None
    last_failure: Any = None
    failure_count: int = 0
    consecutive_failures: int = 0
    success_rate: float = 0.0
    is_scheduled: bool = False

    # 9–12 provenance
    generated_version: str = ""
    active_version: str = ""
    current_strategy: str = ""
    investigation_result: str = ""

    # 13–17 behaviour
    diagnostic_category: str = ""
    repair_count: int = 0
    average_execution_ms: int = 0
    last_extraction_count: int = 0
    last_ai_classification_count: int = 0

    # supporting detail
    total_runs: int = 0
    data_source: str = ""
    ai_relevant: int = 0
    ai_not_relevant: int = 0
    ai_unchecked: int = 0
    open_alerts: int = 0
    recommended_action: str = ""
    disabled_reason: str = ""

    def to_dict(self) -> dict:
        out = dict(self.__dict__)
        for key in ("last_run", "last_successful_run", "last_failure"):
            value = out.get(key)
            out[key] = value.isoformat() if value else None
        return out

    @property
    def health_class(self) -> str:
        """CSS hook — kept here so template and CLI agree on severity."""
        return {"HEALTHY": "ok", "DEGRADED": "warn",
                "DISABLED": "bad"}.get(self.health_status, "muted")


def scraper_overviews(*, only_installed: bool = True) -> list[ScraperOverview]:
    """Build the operational view for every generated scraper."""
    from tenders.models import ScraperSource

    from .models import (ExecutionMetric, FactoryAlert, FactoryStatus,
                         GeneratedScraper, InvestigationRecord, ScraperHealth)

    qs = GeneratedScraper.objects.select_related("country", "active_version")
    if only_installed:
        qs = qs.filter(status=FactoryStatus.INSTALLED)

    scrapers = list(qs)
    if not scrapers:
        return []

    ids = [s.pk for s in scrapers]
    health = {h.scraper_id: h for h in
              ScraperHealth.objects.filter(scraper_id__in=ids)}
    scheduled = set(ScraperSource.objects
                    .filter(scraper_class__in=[s.class_name for s in scrapers],
                            is_active=True)
                    .values_list("scraper_class", flat=True))

    # Latest investigation per scraper, and the newest metric per scraper.
    investigations: dict[int, InvestigationRecord] = {}
    for record in (InvestigationRecord.objects
                   .filter(scraper_id__in=ids)
                   .order_by("scraper_id", "-created_at")):
        investigations.setdefault(record.scraper_id, record)

    latest_metric: dict[int, ExecutionMetric] = {}
    for metric in (ExecutionMetric.objects
                   .filter(scraper_id__in=ids, phase="EXECUTION")
                   .order_by("scraper_id", "-created_at")):
        latest_metric.setdefault(metric.scraper_id, metric)

    aggregates = {
        row["scraper_id"]: row for row in
        ExecutionMetric.objects.filter(scraper_id__in=ids, phase="EXECUTION")
        .values("scraper_id")
        .annotate(runs=Count("id"),
                  avg_ms=Avg("execution_ms"),
                  repairs=Sum("repair_count"))
    }
    open_alerts = {
        row["scraper_id"]: row["n"] for row in
        FactoryAlert.objects.filter(scraper_id__in=ids, is_open=True)
        .values("scraper_id").annotate(n=Count("id"))
    }
    last_alert: dict[int, FactoryAlert] = {}
    for alert in (FactoryAlert.objects
                  .filter(scraper_id__in=ids, is_open=True)
                  .order_by("scraper_id", "-created_at")):
        last_alert.setdefault(alert.scraper_id, alert)

    out: list[ScraperOverview] = []
    for scraper in scrapers:
        h = health.get(scraper.pk)
        agg = aggregates.get(scraper.pk) or {}
        metric = latest_metric.get(scraper.pk)
        record = investigations.get(scraper.pk)
        alert = last_alert.get(scraper.pk)

        view = ScraperOverview(
            scraper_id=scraper.pk, name=scraper.name,
            class_name=scraper.class_name, module_name=scraper.module_name,
            website_url=scraper.website_url,
            country=getattr(scraper.country, "code", "") or "",
            is_scheduled=scraper.class_name in scheduled,
        )

        if h is not None:
            view.health_status = h.state
            view.last_successful_run = h.last_ok_at
            view.last_failure = h.last_failure_at
            view.failure_count = h.total_failures
            view.consecutive_failures = h.consecutive_failures
            view.total_runs = h.total_runs
            view.success_rate = h.success_rate
            view.last_extraction_count = h.last_tenders_found
            view.disabled_reason = h.disabled_reason
            diag = h.last_diagnosis or {}
            root = diag.get("root_cause") or {}
            if root:
                view.diagnostic_category = (
                    f"{root.get('subsystem', '')} → {root.get('category', '')}")
        # last_run is the most recent execution of either kind.
        view.last_run = max(
            [d for d in (getattr(h, "last_ok_at", None),
                         getattr(h, "last_failure_at", None)) if d],
            default=None)

        if metric is not None:
            view.last_run = view.last_run or metric.created_at
            view.last_extraction_count = (view.last_extraction_count
                                          or metric.tenders_total)
            view.ai_relevant = metric.ai_relevant
            view.ai_not_relevant = metric.ai_not_relevant
            view.ai_unchecked = metric.ai_unchecked
            view.last_ai_classification_count = (metric.ai_relevant
                                                 + metric.ai_not_relevant)

        view.average_execution_ms = int(agg.get("avg_ms") or 0)
        view.repair_count = int(agg.get("repairs") or 0)
        if not view.total_runs:
            view.total_runs = int(agg.get("runs") or 0)

        if record is not None:
            view.current_strategy = record.strategy
            view.data_source = record.data_source
            view.investigation_result = (
                f"{record.data_source} "
                f"({record.overall_confidence}% confident, tier {record.tier})"
                if record.data_source else
                f"{record.failure_category or 'incomplete'}")
        # Fall back to the strategy recorded on the metric when no
        # investigation row survives.
        if not view.current_strategy and metric is not None:
            view.current_strategy = metric.strategy

        active = getattr(scraper, "active_version", None)
        view.active_version = (f"v{active.version_number}" if active else "")
        view.generated_version = view.active_version or _first_version(scraper)

        view.open_alerts = int(open_alerts.get(scraper.pk) or 0)
        if alert is not None:
            view.recommended_action = (alert.details or {}).get(
                "recommended_action", "")

        out.append(view)

    # Worst first: a dashboard is for finding what is broken.
    severity = {"DISABLED": 0, "DEGRADED": 1, "UNKNOWN": 2, "HEALTHY": 3}
    out.sort(key=lambda v: (severity.get(v.health_status, 4),
                            -v.consecutive_failures, v.name.lower()))
    return out


def _first_version(scraper) -> str:
    version = (scraper.versions.order_by("-version_number").first()
               if hasattr(scraper, "versions") else None)
    return f"v{version.version_number}" if version else ""


# ── fleet-level summary ───────────────────────────────────────────────────────

def health_distribution() -> dict[str, int]:
    """How many generated scrapers sit in each health state."""
    from .models import FactoryStatus, GeneratedScraper, ScraperHealth

    installed = GeneratedScraper.objects.filter(status=FactoryStatus.INSTALLED)
    total = installed.count()
    counts = {row["state"]: row["n"] for row in
              ScraperHealth.objects.filter(scraper__in=installed)
              .values("state").annotate(n=Count("id"))}
    tracked = sum(counts.values())
    counts["UNKNOWN"] = max(0, total - tracked)     # installed but never run
    return counts


def fleet_summary(*, days: int = 7) -> dict:
    """Aggregate metrics for the dashboard header and the reports."""
    from .models import ExecutionMetric, FactoryAlert, ManualReviewItem, ReviewStatus

    since = timezone.now() - timedelta(days=days)
    metrics = ExecutionMetric.objects.filter(created_at__gte=since,
                                             phase="EXECUTION")
    totals = metrics.aggregate(
        runs=Count("id"),
        ok=Count("id", filter=Q(succeeded=True)),
        tenders=Sum("tenders_total"), valid=Sum("tenders_valid"),
        rejected=Sum("tenders_rejected"),
        relevant=Sum("ai_relevant"), not_relevant=Sum("ai_not_relevant"),
        avg_ms=Avg("execution_ms"), repairs=Sum("repair_count"))
    runs = totals["runs"] or 0

    root_causes = {
        row["failure_category"]: row["n"] for row in
        metrics.exclude(failure_category="")
        .values("failure_category").annotate(n=Count("id"))
        .order_by("-n")[:8]
    }
    strategies = {
        row["strategy"]: row["n"] for row in
        metrics.exclude(strategy="").values("strategy")
        .annotate(n=Count("id")).order_by("-n")
    }

    return {
        "window_days": days,
        "runs": runs,
        "successful_runs": totals["ok"] or 0,
        "success_rate": ((totals["ok"] or 0) / runs) if runs else 0.0,
        "tenders_total": totals["tenders"] or 0,
        "tenders_valid": totals["valid"] or 0,
        "tenders_rejected": totals["rejected"] or 0,
        "ai_relevant": totals["relevant"] or 0,
        "ai_not_relevant": totals["not_relevant"] or 0,
        "average_execution_ms": int(totals["avg_ms"] or 0),
        "repairs": totals["repairs"] or 0,
        "health": health_distribution(),
        "root_causes": root_causes,
        "strategies": strategies,
        "open_alerts": FactoryAlert.objects.filter(is_open=True).count(),
        "open_reviews": ManualReviewItem.objects.filter(
            status__in=[ReviewStatus.OPEN, ReviewStatus.IN_PROGRESS]).count(),
    }


def strategy_performance(*, days: int = 30) -> list[dict]:
    """Which strategies actually work, measured rather than assumed."""
    from .models import ExecutionMetric

    since = timezone.now() - timedelta(days=days)
    rows = (ExecutionMetric.objects
            .filter(created_at__gte=since, phase="EXECUTION")
            .exclude(strategy="")
            .values("strategy")
            .annotate(runs=Count("id"),
                      ok=Count("id", filter=Q(succeeded=True)),
                      tenders=Sum("tenders_valid"),
                      avg_ms=Avg("execution_ms"))
            .order_by("-runs"))
    out = []
    for row in rows:
        runs = row["runs"] or 0
        out.append({
            "strategy": row["strategy"], "runs": runs,
            "successful": row["ok"] or 0,
            "success_rate": ((row["ok"] or 0) / runs) if runs else 0.0,
            "tenders": row["tenders"] or 0,
            "average_ms": int(row["avg_ms"] or 0),
        })
    out.sort(key=lambda r: (-r["success_rate"], -r["runs"]))
    return out
