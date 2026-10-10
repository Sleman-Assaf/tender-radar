"""Seed and inspect benchmark suites.

    python manage.py factory_benchmark_suite --seed
    python manage.py factory_benchmark_suite --list
    python manage.py factory_benchmark_suite --add <url> --label "Name" [--country JO]

Seeding builds the suite from the portals already configured in production, and
from the TenderingURL rows attached to them. That is the honest starting point:
these are the sites the business actually depends on, so a regression on any of
them matters more than a regression on a portal nobody uses.

Expectations are left blank by seeding. A case with no declared expectation
scores PASS when it is plannable and UNKNOWN when it is not — never FAIL — so a
freshly seeded suite cannot manufacture failures. Declared expectations are
added deliberately, one portal at a time, as their correct behaviour is
confirmed.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

DEFAULT_SUITE = "production-portals"


class Command(BaseCommand):
    help = "Seed, list or extend a Factory benchmark suite."

    def add_arguments(self, parser):
        parser.add_argument("--suite", default=DEFAULT_SUITE)
        parser.add_argument("--seed", action="store_true",
                            help="Populate from the configured production sources.")
        parser.add_argument("--list", action="store_true")
        parser.add_argument("--add", metavar="URL")
        parser.add_argument("--label", default="")
        parser.add_argument("--country", default="")
        parser.add_argument("--expect-source", default="",
                            help="HTML | API | GRAPHQL | JS_STATE")
        parser.add_argument("--expect-strategy", default="")
        parser.add_argument("--expect-technology", default="")
        parser.add_argument("--min-rows", type=int, default=0)
        parser.add_argument("--include-urls", action="store_true",
                            help="Also add each source's TenderingURL rows, which "
                                 "is how the suite passes 100 cases.")

    def handle(self, *args, **options):
        from ...models import BenchmarkCase, BenchmarkSuite

        suite, created = BenchmarkSuite.objects.get_or_create(
            name=options["suite"],
            defaults={"description": "Portals TenderRadar depends on in production."})
        if created:
            self.stdout.write(self.style.SUCCESS(f"created suite '{suite.name}'"))

        if options["add"]:
            self._add(suite, options)
            return
        if options["seed"]:
            self._seed(suite, include_urls=options["include_urls"])
        if options["list"] or not (options["seed"] or options["add"]):
            self._list(suite)

    # ── actions ───────────────────────────────────────────────────────────────

    def _add(self, suite, options):
        from ...models import BenchmarkCase

        url = options["add"]
        if not url.lower().startswith(("http://", "https://")):
            raise CommandError("URL must start with http:// or https://")
        case, created = BenchmarkCase.objects.update_or_create(
            suite=suite, url=url[:500],
            defaults={
                "label": (options["label"] or url)[:120],
                "country_code": options["country"][:4],
                "expected_data_source": options["expect_source"][:20],
                "expected_strategy": options["expect_strategy"][:20],
                "expected_technology": options["expect_technology"][:20],
                "min_rows": max(0, options["min_rows"]),
                "is_active": True,
            })
        verb = "added" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"{verb}: {case.label}"))

    def _seed(self, suite, *, include_urls: bool) -> None:
        from tenders.models import ScraperSource, TenderingURL

        from ...models import BenchmarkCase

        added = skipped = 0
        for source in ScraperSource.objects.select_related("country").order_by("name"):
            url = (getattr(source, "base_url", "") or "").strip()
            if not url:
                # Fall back to the source's first tendering URL.
                first = TenderingURL.objects.filter(source=source).first()
                url = (getattr(first, "url", "") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                skipped += 1
                continue
            _, created = BenchmarkCase.objects.get_or_create(
                suite=suite, url=url[:500],
                defaults={"label": source.name[:120],
                          "country_code": (getattr(source.country, "code", "") or "")[:4],
                          "notes": f"seeded from ScraperSource #{source.pk}"})
            added += 1 if created else 0

        if include_urls:
            for turl in (TenderingURL.objects.select_related("source", "source__country")
                         .order_by("id")):
                url = (turl.url or "").strip()
                if not url.lower().startswith(("http://", "https://")):
                    continue
                label = f"{turl.source.name} — {getattr(turl, 'label', '') or turl.pk}"
                _, created = BenchmarkCase.objects.get_or_create(
                    suite=suite, url=url[:500],
                    defaults={"label": label[:120],
                              "country_code": (getattr(turl.source.country, "code", "") or "")[:4],
                              "notes": f"seeded from TenderingURL #{turl.pk}"})
                added += 1 if created else 0

        total = BenchmarkCase.objects.filter(suite=suite).count()
        self.stdout.write(self.style.SUCCESS(
            f"seeded {added} new case(s); suite now holds {total} "
            f"({skipped} source(s) had no usable URL)"))
        if total < 100:
            self.stdout.write(self.style.WARNING(
                f"  {total} cases — below the 100 target. Re-run with "
                f"--include-urls, or add portals with --add."))

    def _list(self, suite) -> None:
        from ...models import BenchmarkCase

        cases = BenchmarkCase.objects.filter(suite=suite).order_by("label")
        declared = cases.exclude(expected_data_source="").count()
        self.stdout.write("")
        self.stdout.write(f"suite '{suite.name}': {cases.count()} case(s), "
                          f"{cases.filter(is_active=True).count()} active, "
                          f"{declared} with declared expectations")
        self.stdout.write("")
        for case in cases[:200]:
            expect = " ".join(filter(None, [
                case.expected_data_source, case.expected_strategy,
                case.expected_technology,
                f"min{case.min_rows}" if case.min_rows else ""]))
            flag = " " if case.is_active else "x"
            self.stdout.write(f"  {flag} {case.label[:44]:<46} "
                              f"{(expect or '(no expectations)'):<34} "
                              f"{case.url[:46]}")
