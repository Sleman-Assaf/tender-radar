"""Show the tenders a generated scraper actually extracted.

A dry run deliberately discards everything it saved, so the extracted rows are
not in the Tender table afterwards. They are, however, captured by the sandbox
and stored on the test run — which is what this command displays. It is how you
judge whether a generation was any good.

    python manage.py factory_tenders                 # the latest test run
    python manage.py factory_tenders --id 12         # a specific scraper
    python manage.py factory_tenders --all           # every recent test run
    python manage.py factory_tenders --code          # also print the scraper
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from scraper_factory.models import (FactoryTestRun, GeneratedScraper,
                                     ScraperCodeVersion)


class Command(BaseCommand):
    help = "Display the tenders extracted during a generation's sandbox test."

    def add_arguments(self, parser):
        parser.add_argument("--id", type=int, default=None,
                            help="GeneratedScraper id.")
        parser.add_argument("--run", type=int, default=None,
                            help="FactoryTestRun id.")
        parser.add_argument("--all", action="store_true",
                            help="List every recent test run.")
        parser.add_argument("--code", action="store_true",
                            help="Also print the generated scraper source.")

    def handle(self, *args, **opts):
        qs = FactoryTestRun.objects.select_related("scraper").order_by("-created_at")
        if opts["run"]:
            qs = qs.filter(pk=opts["run"])
        elif opts["id"]:
            qs = qs.filter(scraper_id=opts["id"])
        elif not opts["all"]:
            qs = qs[:1]

        runs = list(qs[:20])
        if not runs:
            self.stdout.write(self.style.WARNING(
                "\n  No sandbox test runs recorded yet.\n"
                "  Run a build first:\n"
                "    python manage.py factory_build <url> --mode dry_run\n"))
            return

        for run in runs:
            self._show(run, opts["code"])

    # ── output ────────────────────────────────────────────────────────────────

    def _show(self, run: FactoryTestRun, with_code: bool):
        scraper = run.scraper
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  {scraper.name if scraper else '(unknown)'} — "
            f"{scraper.class_name if scraper else ''}"))
        self.stdout.write(
            f"    website : {scraper.website_url if scraper else '-'}")
        self.stdout.write(
            f"    test run: #{run.pk}  status={run.status}  "
            f"found={run.tenders_found}  runtime={run.runtime_ms} ms  "
            f"{run.created_at:%Y-%m-%d %H:%M}")
        if run.error_message:
            self.stdout.write(self.style.ERROR(
                f"    error   : [{run.error_category}] {run.error_message[:150]}"))

        rows = run.sample_rows or []
        if not rows:
            self.stdout.write("    (no rows captured)\n")
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n    EXTRACTED TENDERS ({len(rows)} shown of {run.tenders_found})\n"))

        for i, row in enumerate(rows, 1):
            title = (row.get("title") or "").strip()
            self.stdout.write(f"    {i:>2}. {title[:88]}")
            bits = []
            if row.get("reference_no"):
                bits.append(f"ref={row['reference_no']}")
            if row.get("submission_deadline"):
                bits.append(f"deadline={row['submission_deadline']}")
            if row.get("publication_date"):
                bits.append(f"published={row['publication_date']}")
            if row.get("status"):
                bits.append(f"status={row['status']}")
            if bits:
                self.stdout.write("        " + "  ".join(bits))
            if row.get("entity"):
                self.stdout.write(f"        entity: {row['entity'][:80]}")
            if row.get("source_url"):
                self.stdout.write(f"        link  : {row['source_url'][:100]}")
            self.stdout.write("")

        # Field completeness — the quick quality read.
        fields = ("title", "source_url", "reference_no", "submission_deadline",
                  "entity", "publication_date")
        summary = []
        for f in fields:
            filled = sum(1 for r in rows if str(r.get(f) or "").strip())
            summary.append(f"{f}={filled * 100 // len(rows)}%")
        self.stdout.write("    field completeness: " + "  ".join(summary) + "\n")

        if with_code and scraper is not None:
            version = (ScraperCodeVersion.objects.filter(scraper=scraper)
                       .order_by("-version_number").first())
            if version is not None:
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f"\n    GENERATED SCRAPER "
                    f"(v{version.version_number}, {version.origin}, "
                    f"style {version.style_score}/100)\n"))
                self.stdout.write(version.source_code)
