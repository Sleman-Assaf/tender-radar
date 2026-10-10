"""Operational report over the persisted metrics.

    python manage.py factory_report                 # daily
    python manage.py factory_report --period weekly
    python manage.py factory_report --period monthly --json

Reads only tables the Factory owns, so it can be run any time — including from
cron — without touching production data or the scheduler.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

PERIODS = {"daily": 1, "weekly": 7, "monthly": 30}


class Command(BaseCommand):
    help = "Daily, weekly or monthly Factory operations report."

    def add_arguments(self, parser):
        parser.add_argument("--period", choices=sorted(PERIODS), default="daily")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        from ... import operations
        from ...models import BenchmarkRun, ExecutionMetric

        period = options["period"]
        days = PERIODS[period]
        summary = operations.fleet_summary(days=days)
        strategies = operations.strategy_performance(days=days)
        benchmarks = list(BenchmarkRun.objects.order_by("-created_at")[:5])

        if options["json"]:
            self.stdout.write(json.dumps({
                "period": period, "summary": summary, "strategies": strategies,
                "benchmark_trend": [
                    {"run": b.pk, "at": b.created_at.isoformat(),
                     "passed": b.passed, "total": b.total,
                     "success_rate": b.success_rate,
                     "regressions": len(b.regressions)} for b in benchmarks],
            }, indent=2, ensure_ascii=False, default=str))
            return

        w = 30
        out = self.stdout.write
        out("")
        out(f"FACTORY {period.upper()} REPORT — last {days} day(s)")
        out("=" * 74)

        out("")
        out("EXECUTION")
        out(f"  {'runs':<{w}} {summary['runs']}")
        out(f"  {'successful':<{w}} {summary['successful_runs']}")
        out(f"  {'success rate':<{w}} {summary['success_rate']:.0%}")
        out(f"  {'average execution':<{w}} {summary['average_execution_ms']} ms")
        out(f"  {'repairs performed':<{w}} {summary['repairs']}")

        out("")
        out("TENDERS")
        out(f"  {'extracted':<{w}} {summary['tenders_total']}")
        out(f"  {'valid (passed validation)':<{w}} {summary['tenders_valid']}")
        out(f"  {'rejected':<{w}} {summary['tenders_rejected']}")
        out(f"  {'AI relevant':<{w}} {summary['ai_relevant']}")
        out(f"  {'AI not relevant':<{w}} {summary['ai_not_relevant']}")

        out("")
        out("HEALTH DISTRIBUTION")
        health = summary["health"] or {}
        if any(health.values()):
            for state, count in sorted(health.items()):
                out(f"  {state:<{w}} {count}")
        else:
            out("  (no generated scrapers installed yet)")

        out("")
        out("MOST COMMON ROOT CAUSES")
        if summary["root_causes"]:
            for cause, n in summary["root_causes"].items():
                out(f"  {cause:<{w}} {n}")
        else:
            out("  (none recorded in this window)")

        out("")
        out("TOP PERFORMING STRATEGIES")
        if strategies:
            out(f"  {'strategy':<22}{'runs':>6}{'success':>10}{'tenders':>10}{'avg ms':>9}")
            for s in strategies:
                out(f"  {s['strategy']:<22}{s['runs']:>6}"
                    f"{s['success_rate']:>9.0%}{s['tenders']:>10}{s['average_ms']:>9}")
        else:
            out("  (no executions recorded in this window)")

        out("")
        out("BENCHMARK TREND")
        if benchmarks:
            out(f"  {'run':<7}{'when':<18}{'passed':>8}{'rate':>8}{'regressions':>13}")
            for b in benchmarks:
                flag = "  <-- baseline" if b.is_baseline else ""
                out(f"  #{b.pk:<6}{b.created_at:%Y-%m-%d %H:%M}  "
                    f"{b.passed:>3}/{b.total:<4}{b.success_rate:>7.0%}"
                    f"{len(b.regressions):>13}{flag}")
        else:
            out("  (no benchmark runs yet — python manage.py factory_benchmark_run)")

        out("")
        out("ATTENTION")
        out(f"  {'open alerts':<{w}} {summary['open_alerts']}")
        out(f"  {'open review items':<{w}} {summary['open_reviews']}")
        out("")
