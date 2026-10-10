"""Analytics — the six statistic families, computed from Factory-owned tables.

Read-only aggregation. Nothing here writes, and nothing reads a production table
except to count rows, so a report can be run at any time without risk.

Each family answers a question somebody actually asks:

    execution      is it collecting, and how fast?
    repair         which repair strategies genuinely work?
    benchmark      is the Factory getting better or worse?
    investigation  what kinds of portal are we meeting, and where do we fail?
    strategy       which generation strategies survive contact with reality?
    validation     is generated code passing the gates, and on which attempt?

`repair` is the one that could not be answered before Phase 4: the data lived in
GenerationRun.attempt_log JSON, so "does REPAIR_SELECTORS work?" required parsing
every run. With RepairAttempt as a table it is a group-by.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Avg, Count, Max, Q, Sum
from django.utils import timezone

logger = logging.getLogger("scraper_factory")


def _since(days: int):
    return timezone.now() - timedelta(days=days)


def _rate(part: int, whole: int) -> float:
    return (part / whole) if whole else 0.0


# ── 1. execution ──────────────────────────────────────────────────────────────

def execution_statistics(*, days: int = 30) -> dict:
    from .models import ExecutionHistory, ExecutionMetric

    since = _since(days)
    metrics = ExecutionMetric.objects.filter(created_at__gte=since,
                                             phase="EXECUTION")
    agg = metrics.aggregate(
        runs=Count("id"), ok=Count("id", filter=Q(succeeded=True)),
        tenders=Sum("tenders_total"), valid=Sum("tenders_valid"),
        rejected=Sum("tenders_rejected"),
        relevant=Sum("ai_relevant"), not_relevant=Sum("ai_not_relevant"),
        unchecked=Sum("ai_unchecked"),
        avg_exec=Avg("execution_ms"), avg_extract=Avg("extraction_ms"))
    runs = agg["runs"] or 0

    history = ExecutionHistory.objects.filter(created_at__gte=since)
    retries = history.filter(attempt__gt=1).count()
    attempts = history.count()

    return {
        "window_days": days,
        "runs": runs,
        "successful": agg["ok"] or 0,
        "failed": runs - (agg["ok"] or 0),
        "success_rate": _rate(agg["ok"] or 0, runs),
        "failure_rate": _rate(runs - (agg["ok"] or 0), runs),
        "tenders_extracted": agg["tenders"] or 0,
        "tenders_valid": agg["valid"] or 0,
        "tenders_rejected": agg["rejected"] or 0,
        "ai_relevant": agg["relevant"] or 0,
        "ai_not_relevant": agg["not_relevant"] or 0,
        "ai_unchecked": agg["unchecked"] or 0,
        "average_execution_ms": int(agg["avg_exec"] or 0),
        "average_extraction_ms": int(agg["avg_extract"] or 0),
        "scheduled_attempts": attempts,
        "retried_attempts": retries,
        "retry_rate": _rate(retries, attempts),
        "failure_categories": _counts(metrics.exclude(failure_category=""),
                                      "failure_category"),
    }


# ── 2. repair ─────────────────────────────────────────────────────────────────

def repair_statistics(*, days: int = 90) -> dict:
    """Which repairs work. The whole point of storing attempts as rows."""
    from .models import RepairAttempt

    since = _since(days)
    attempts = RepairAttempt.objects.filter(created_at__gte=since)
    total = attempts.count()
    decided = attempts.exclude(worked=None)

    by_strategy = []
    for row in (decided.values("strategy")
                .annotate(n=Count("id"), fixed=Count("id", filter=Q(worked=True)),
                          avg_ms=Avg("duration_ms"))
                .order_by("-n")):
        by_strategy.append({
            "strategy": row["strategy"] or "(none)",
            "attempts": row["n"], "fixed": row["fixed"],
            "success_rate": _rate(row["fixed"], row["n"]),
            "average_ms": int(row["avg_ms"] or 0),
        })
    by_strategy.sort(key=lambda r: (-r["success_rate"], -r["attempts"]))

    by_category = []
    for row in (decided.values("failure_category")
                .annotate(n=Count("id"), fixed=Count("id", filter=Q(worked=True)))
                .order_by("-n")[:12]):
        by_category.append({
            "category": row["failure_category"] or "(none)",
            "attempts": row["n"], "fixed": row["fixed"],
            "success_rate": _rate(row["fixed"], row["n"]),
        })

    fixed = decided.filter(worked=True).count()
    return {
        "window_days": days,
        "attempts": total,
        "decided": decided.count(),
        "fixed": fixed,
        "success_rate": _rate(fixed, decided.count()),
        "with_prior_art": attempts.exclude(prior_art_used="").count(),
        "average_ms": int(attempts.aggregate(a=Avg("duration_ms"))["a"] or 0),
        "by_strategy": by_strategy,
        "by_category": by_category,
        "best_strategy": (by_strategy[0]["strategy"] if by_strategy else ""),
    }


# ── 3. benchmark ──────────────────────────────────────────────────────────────

def benchmark_statistics(*, limit: int = 20) -> dict:
    from .models import BenchmarkProject, BenchmarkRun, BenchmarkSuite

    runs = list(BenchmarkRun.objects.select_related("suite")
                .order_by("-created_at")[:limit])
    trend = [{
        "run": r.pk, "suite": r.suite.name, "at": r.created_at,
        "total": r.total, "passed": r.passed, "failed": r.failed,
        "unknown": r.unknown, "success_rate": r.success_rate,
        "average_ms": r.average_ms, "regressions": len(r.regressions or []),
        "improvements": len(r.improvements or []),
        "is_baseline": r.is_baseline,
    } for r in runs]

    latest = runs[0] if runs else None
    baseline = next((r for r in runs if r.is_baseline), None)
    direction = ""
    if latest is not None and baseline is not None and latest.pk != baseline.pk:
        delta = latest.success_rate - baseline.success_rate
        direction = ("improving" if delta > 0.01 else
                     "regressing" if delta < -0.01 else "flat")

    return {
        "runs_recorded": BenchmarkRun.objects.count(),
        "suites": BenchmarkSuite.objects.count(),
        "projects": BenchmarkProject.objects.count(),
        "cases_total": sum(s.cases.count() for s in BenchmarkSuite.objects.all()),
        "latest": trend[0] if trend else None,
        "baseline": ({"run": baseline.pk, "success_rate": baseline.success_rate}
                     if baseline else None),
        "direction": direction,
        "open_regressions": len((latest.regressions or []) if latest else []),
        "trend": trend,
    }


# ── 4. investigation ──────────────────────────────────────────────────────────

def investigation_statistics(*, days: int = 90) -> dict:
    from .models import InvestigationRecord

    since = _since(days)
    records = InvestigationRecord.objects.filter(created_at__gte=since)
    total = records.count()
    tier2 = records.filter(tier=2).count()
    complete = records.filter(complete=True).count()

    return {
        "window_days": days,
        "investigations": total,
        "complete": complete,
        "completion_rate": _rate(complete, total),
        "tier2_escalations": tier2,
        "tier2_rate": _rate(tier2, total),
        "average_confidence": int(
            records.aggregate(a=Avg("overall_confidence"))["a"] or 0),
        "average_duration_ms": int(
            records.aggregate(a=Avg("duration_ms"))["a"] or 0),
        "data_sources": _counts(records.exclude(data_source=""), "data_source"),
        "failure_categories": _counts(records.exclude(failure_category=""),
                                      "failure_category"),
        "root_cause_stages": _counts(records.exclude(root_cause_stage=""),
                                     "root_cause_stage"),
        "viable_plans": records.filter(plan_viable=True).count(),
        "viability_rate": _rate(records.filter(plan_viable=True).count(), total),
    }


# ── 5. strategy ───────────────────────────────────────────────────────────────

def strategy_statistics(*, days: int = 90) -> dict:
    """Which strategies get chosen, and which survive execution.

    Chosen and successful are different questions, and the gap between them is
    the interesting number: a strategy picked often but succeeding rarely means
    the Planner is over-confident about that shape.
    """
    from .models import ExecutionMetric, InvestigationRecord

    since = _since(days)
    chosen = _counts(
        InvestigationRecord.objects.filter(created_at__gte=since)
        .exclude(strategy=""), "strategy")

    rows = []
    for row in (ExecutionMetric.objects
                .filter(created_at__gte=since, phase="EXECUTION")
                .exclude(strategy="")
                .values("strategy")
                .annotate(runs=Count("id"), ok=Count("id", filter=Q(succeeded=True)),
                          tenders=Sum("tenders_valid"), avg_ms=Avg("execution_ms"))
                .order_by("-runs")):
        rows.append({
            "strategy": row["strategy"], "runs": row["runs"],
            "successful": row["ok"] or 0,
            "success_rate": _rate(row["ok"] or 0, row["runs"]),
            "tenders": row["tenders"] or 0,
            "average_ms": int(row["avg_ms"] or 0),
            "times_chosen": chosen.get(row["strategy"], 0),
        })
    rows.sort(key=lambda r: (-r["success_rate"], -r["runs"]))
    return {
        "window_days": days,
        "chosen": chosen,
        "performance": rows,
        "best": rows[0]["strategy"] if rows else "",
        "worst": rows[-1]["strategy"] if len(rows) > 1 else "",
    }


# ── 6. validation ─────────────────────────────────────────────────────────────

def validation_statistics(*, days: int = 90) -> dict:
    """Gate outcomes, and how many attempts code needs to pass."""
    from .models import GenerationRun, RepairAttempt

    since = _since(days)
    runs = GenerationRun.objects.filter(created_at__gte=since)
    total = runs.count()
    by_outcome = _counts(runs, "outcome")

    first_pass = runs.filter(outcome=GenerationRun.Outcome.PASSED,
                             repair_attempts=0).count()
    passed = runs.filter(outcome=GenerationRun.Outcome.PASSED).count()

    violations: dict[str, int] = {}
    for attempt in (RepairAttempt.objects.filter(created_at__gte=since)
                    .exclude(violations_before=[])[:400]):
        for code in (attempt.violations_before or [])[:10]:
            key = code if isinstance(code, str) else str(code)
            violations[key] = violations.get(key, 0) + 1

    return {
        "window_days": days,
        "generation_runs": total,
        "passed": passed,
        "pass_rate": _rate(passed, total),
        "first_pass": first_pass,
        "first_pass_rate": _rate(first_pass, total),
        "failed_validation": by_outcome.get("FAILED_VALIDATION", 0),
        "failed_sandbox": by_outcome.get("FAILED_SANDBOX", 0),
        "model_unreachable": by_outcome.get("MODEL_UNREACHABLE", 0),
        "errors": by_outcome.get("ERROR", 0),
        "by_outcome": by_outcome,
        "average_attempts": round(
            runs.aggregate(a=Avg("attempts"))["a"] or 0, 2),
        "average_repairs": round(
            runs.aggregate(a=Avg("repair_attempts"))["a"] or 0, 2),
        "common_violations": dict(sorted(violations.items(),
                                         key=lambda kv: -kv[1])[:10]),
    }


def field_statistics(*, days: int = 30) -> dict:
    """Per-field extraction coverage across recent runs.

    Answers the question a whole-source health state cannot: which column stopped
    working. A source whose deadline column silently went empty still reports
    HEALTHY, because it returns rows and they are genuine tenders — the loss only
    shows up field by field. Sparse fields are listed separately from core ones,
    since most portals legitimately omit several (see FieldTelemetry).
    """
    from .models import ExecutionMetric

    since = _since(days)
    rows = list(ExecutionMetric.objects
                .filter(created_at__gte=since, phase="EXECUTION")
                .exclude(details__field_coverage__isnull=True)
                .order_by("-created_at")
                .values_list("scraper__class_name", "details")[:1000])

    totals: dict[str, list[float]] = {}
    missing_core: dict[str, int] = {}
    sparse: dict[str, int] = {}
    dateless = date_ok = date_bad = 0
    per_scraper: dict[str, dict] = {}

    for class_name, details in rows:
        cov = (details or {}).get("field_coverage") or {}
        if not cov:
            continue
        for name, value in cov.items():
            totals.setdefault(name, []).append(float(value))
        for name in (details or {}).get("missing_core") or []:
            missing_core[name] = missing_core.get(name, 0) + 1
        for name in (details or {}).get("sparse_fields") or []:
            sparse[name] = sparse.get(name, 0) + 1

        if (details or {}).get("dates_expected"):
            rate = float((details or {}).get("date_parse_rate") or 0)
            if rate >= 0.5:
                date_ok += 1
            else:
                date_bad += 1
        else:
            dateless += 1

        # Keep the most recent observation per scraper, so the worst field of a
        # given source is visible without averaging away a recent break. Only
        # named when something is actually below full coverage — reporting a
        # "worst field" at 100% invites a developer to investigate nothing.
        if class_name and class_name not in per_scraper:
            worst = min(cov.items(), key=lambda kv: kv[1]) if cov else None
            if worst is not None and worst[1] < 1.0:
                per_scraper[class_name] = {"worst_field": worst[0],
                                           "worst_coverage": round(worst[1], 3)}

    average = {name: round(sum(v) / len(v), 3)
               for name, v in sorted(totals.items())}
    return {
        "window_days": days,
        "runs_measured": len(rows),
        "average_coverage": average,
        "weakest_fields": dict(sorted(average.items(), key=lambda kv: kv[1])[:6]),
        "missing_core_counts": dict(sorted(missing_core.items(),
                                           key=lambda kv: -kv[1])),
        "sparse_counts": dict(sorted(sparse.items(), key=lambda kv: -kv[1])[:10]),
        "sources_publishing_no_dates": dateless,
        "sources_parsing_dates": date_ok,
        "sources_failing_dates": date_bad,
        "per_scraper_worst": dict(sorted(
            per_scraper.items(), key=lambda kv: kv[1]["worst_coverage"])[:15]),
    }


# ── everything at once ────────────────────────────────────────────────────────

def all_statistics(*, days: int = 30) -> dict:
    """Every family, for the dashboard and the report command."""
    from . import scheduling
    return {
        "execution": execution_statistics(days=days),
        "repair": repair_statistics(days=max(days, 90)),
        "benchmark": benchmark_statistics(),
        "investigation": investigation_statistics(days=max(days, 90)),
        "strategy": strategy_statistics(days=max(days, 90)),
        "validation": validation_statistics(days=max(days, 90)),
        "field": field_statistics(days=days),
        "queue": scheduling.queue_summary(),
    }


def _counts(queryset, field: str, limit: int = 12) -> dict:
    return {
        (row[field] or "(none)"): row["n"] for row in
        queryset.values(field).annotate(n=Count("id")).order_by("-n")[:limit]
    }
