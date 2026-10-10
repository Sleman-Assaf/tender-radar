"""Internal developer review queue.

Lists builds the factory could not complete automatically. This is the only
place the internal detail (validation failures, sandbox errors, prompts,
generated code) is surfaced — and only to developers, never to the employee who
requested the site.

    python manage.py factory_queue
    python manage.py factory_queue --show 12      # full detail for one entry
    python manage.py factory_queue --code 12      # print the generated code
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from scraper_factory.models import (FactoryStatus, GeneratedScraper,
                                     GenerationRun, ScraperCodeVersion)


class Command(BaseCommand):
    help = "List or inspect builds queued for developer review."

    def add_arguments(self, parser):
        parser.add_argument("--show", type=int, default=None,
                            help="GeneratedScraper id to inspect in full.")
        parser.add_argument("--code", type=int, default=None,
                            help="GeneratedScraper id whose last code to print.")
        parser.add_argument("--all", action="store_true",
                            help="Include installed and retired entries.")

    def handle(self, *args, **opts):
        if opts["code"]:
            return self._print_code(opts["code"])
        if opts["show"]:
            return self._show(opts["show"])

        qs = GeneratedScraper.objects.all().order_by("-created_at")
        if not opts["all"]:
            qs = qs.filter(status=FactoryStatus.AWAITING_DEVELOPER)

        if not qs.exists():
            self.stdout.write(self.style.SUCCESS(
                "\n  Nothing awaiting developer review.\n"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  DEVELOPER REVIEW QUEUE — {qs.count()} entr(ies)\n"))
        for rec in qs[:50]:
            self.stdout.write(
                f"    #{rec.pk:<4} {rec.status:20} {rec.class_name:32} "
                f"{rec.website_url[:44]}")
            reason = (rec.review_reason or "").strip().splitlines()
            if reason:
                self.stdout.write(f"          {reason[0][:100]}")
        self.stdout.write(
            "\n  Inspect one with:  python manage.py factory_queue --show <id>\n")

    # ── detail ────────────────────────────────────────────────────────────────

    def _show(self, pk: int):
        rec = GeneratedScraper.objects.filter(pk=pk).first()
        if rec is None:
            self.stdout.write(self.style.ERROR(f"  No entry #{pk}."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  #{rec.pk} {rec.class_name}\n"))
        self.stdout.write(f"    url        : {rec.website_url}")
        self.stdout.write(f"    module     : tenders/scrapers/{rec.module_name}.py")
        self.stdout.write(f"    status     : {rec.status}")
        self.stdout.write(f"    family     : {rec.family or '-'}")
        self.stdout.write(f"    country    : {rec.country_id}")
        self.stdout.write(f"    created    : {rec.created_at:%Y-%m-%d %H:%M}")

        if rec.review_reason:
            self.stdout.write(self.style.WARNING("\n  WHY IT FAILED"))
            for line in rec.review_reason.splitlines()[:25]:
                self.stdout.write(f"    {line[:120]}")

        run = (GenerationRun.objects.filter(scraper=rec)
               .order_by("-created_at").first())
        if run is None:
            run = (GenerationRun.objects.filter(website_url=rec.website_url)
                   .order_by("-created_at").first())
        if run is not None:
            self.stdout.write(self.style.MIGRATE_HEADING("\n  GENERATION RUN"))
            self.stdout.write(
                f"    outcome={run.outcome} attempts={run.attempts} "
                f"repairs={run.repair_attempts} model={run.model_name}")
            self.stdout.write(
                f"    prompt v{getattr(run.prompt_version, 'version', '-')} "
                f"tokens~{run.prompt_tokens_est} "
                f"refs={[r['module'] for r in (run.reference_modules or [])]}")
            self.stdout.write("\n  ATTEMPT CHAIN")
            for entry in run.attempt_log or []:
                if "error" in entry:
                    self.stdout.write(
                        f"    #{entry['attempt']} {entry['kind']:8} "
                        f"ERROR {entry['error']}")
                    continue
                self.stdout.write(
                    f"    #{entry['attempt']} {entry['kind']:8} "
                    f"passed={str(entry['passed']):5} "
                    f"style={entry['style_score']:>3} "
                    f"hard={entry['hard_violations']}")
                for v in entry.get("violations", [])[:6]:
                    mark = "REJECT" if v["hard"] else "style "
                    where = f":{v['line']}" if v.get("line") else ""
                    self.stdout.write(
                        f"          [{mark}] {v['code']}{where} {v['message'][:88]}")

            bench = getattr(run, "benchmark", None)
            if bench is not None:
                self.stdout.write(self.style.MIGRATE_HEADING("\n  BENCHMARK"))
                self.stdout.write(
                    f"    overall={bench.overall_score}/100 "
                    f"exec={bench.execution_success} "
                    f"extraction={bench.extraction_accuracy:.0%} "
                    f"style={bench.style_similarity:.0%} "
                    f"repairs={bench.repair_iterations}")

        self.stdout.write(
            f"\n  Print the generated code:  "
            f"python manage.py factory_queue --code {rec.pk}\n")

    def _print_code(self, pk: int):
        version = (ScraperCodeVersion.objects.filter(scraper_id=pk)
                   .order_by("-version_number").first())
        if version is None:
            # A failed build may not have stored a version; fall back to the run.
            run = (GenerationRun.objects.filter(scraper_id=pk)
                   .order_by("-created_at").first())
            self.stdout.write(self.style.ERROR(
                "  No stored code version for this entry."
                + (" The generation run has the prompt and attempt log."
                   if run else "")))
            return
        self.stdout.write(version.source_code)
