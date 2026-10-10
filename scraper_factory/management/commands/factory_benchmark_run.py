"""Run a benchmark suite and report PASS / FAIL / UNKNOWN with root causes.

    python manage.py factory_benchmark_run
    python manage.py factory_benchmark_run --limit 20 --no-tier2
    python manage.py factory_benchmark_run --workers 8 --retries 2
    python manage.py factory_benchmark_run --mark-baseline

The first run of a suite has nothing to compare against. Mark it as the baseline
once its results are believed, and every later run reports regressions against
it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

DEFAULT_SUITE = "production-portals"


class Command(BaseCommand):
    help = "Execute a benchmark suite and report the results."

    def add_arguments(self, parser):
        parser.add_argument("--suite", default=DEFAULT_SUITE)
        parser.add_argument("--limit", type=int, default=0,
                            help="Run only the first N cases (a smoke subset).")
        parser.add_argument("--workers", type=int, default=0)
        parser.add_argument("--retries", type=int, default=-1)
        parser.add_argument("--no-tier2", action="store_true",
                            help="Never launch a browser; much faster, and enough "
                                 "for a regression subset.")
        parser.add_argument("--mark-baseline", action="store_true")
        parser.add_argument("--notes", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        import json

        from ...benchmarking import BenchmarkEngine, render_run
        from ...models import BenchmarkSuite

        try:
            suite = BenchmarkSuite.objects.get(name=options["suite"])
        except BenchmarkSuite.DoesNotExist:
            raise CommandError(
                f"no suite named '{options['suite']}'. Create one with:\n"
                f"  python manage.py factory_benchmark_suite --seed")

        engine = BenchmarkEngine(
            workers=options["workers"] or None,
            retries=(None if options["retries"] < 0 else options["retries"]),
            allow_tier2=(False if options["no_tier2"] else None))

        self.stdout.write(f"running '{suite.name}' with {engine.workers} worker(s), "
                          f"{engine.retries} retry/retries"
                          f"{', browser tier off' if options['no_tier2'] else ''} …")

        run = engine.run(suite, limit=options["limit"] or None,
                         mark_baseline=options["mark_baseline"],
                         notes=options["notes"])

        if options["json"]:
            self.stdout.write(json.dumps({
                "run_id": run.pk, "total": run.total, "passed": run.passed,
                "failed": run.failed, "unknown": run.unknown,
                "errored": run.errored, "success_rate": run.success_rate,
                "average_ms": run.average_ms,
                "regressions": run.regressions, "improvements": run.improvements,
                "root_causes": run.root_causes,
                "strategy_counts": run.strategy_counts,
                "results": run.results,
            }, indent=2, ensure_ascii=False))
        else:
            self.stdout.write("")
            self.stdout.write(render_run(run))

        if run.regressions:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(
                f"{len(run.regressions)} regression(s) — do not ship this change "
                f"until they are understood."))
        elif run.failed:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"{run.failed} case(s) fail, but none regressed against the "
                f"baseline."))
        else:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("no regressions."))
