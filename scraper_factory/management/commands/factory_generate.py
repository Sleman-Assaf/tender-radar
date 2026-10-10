"""Generate a scraper for a URL (analysis -> retrieval -> model -> verify).

The generated file is written to the STAGING directory only. Nothing is
installed and no scraper is registered: that is the installer's job, behind
employee and manager approval.

    python manage.py factory_generate https://portal.example/tenders \
        --module example_portal --class ExamplePortalScraper \
        --country JO --name "Example Portal"

    # offline dry run of the whole pipeline, no model server needed

    # skip the live sandbox execution (validation only)
    python manage.py factory_generate <url> ... --no-sandbox
"""

from __future__ import annotations

import pathlib
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from scraper_factory.generation.orchestrator import GenerationOrchestrator


class Command(BaseCommand):
    help = ("Generate a scraper file for a tender portal URL and verify it. "
            "Writes to the staging directory; never installs.")

    def add_arguments(self, parser):
        parser.add_argument("url")
        parser.add_argument("--module", required=True,
                            help="Module file name without .py, e.g. example_portal")
        parser.add_argument("--class", dest="class_name", required=True,
                            help="Scraper class name, must end with 'Scraper'")
        parser.add_argument("--country", required=True,
                            help="Country code as used in CountryConfig, e.g. JO")
        parser.add_argument("--name", dest="source_name", required=True,
                            help="Human-readable source name")
        parser.add_argument("--no-sandbox", action="store_true",
                            help="Skip live execution (gate E).")

    def handle(self, *args, **opts):
        module_name = opts["module"]
        class_name = opts["class_name"]

        if not re.fullmatch(r"[a-z][a-z0-9_]*", module_name):
            raise CommandError("--module must be lower_snake_case, "
                               "matching the corpus convention.")
        if not class_name.endswith("Scraper"):
            raise CommandError("--class must end with 'Scraper' (invariant I5).")

        model = None

        orch = GenerationOrchestrator(model=model)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  Scraper Factory — generating\n"
            f"    url    : {opts['url']}\n"
            f"    file   : tenders/scrapers/{module_name}.py\n"
            f"    class  : {class_name}\n"))

        result = orch.generate(
            opts["url"], module_name=module_name, class_name=class_name,
            country_code=opts["country"].upper(), source_name=opts["source_name"],
            run_sandbox=not opts["no_sandbox"])

        self._report(result, module_name)

    # ── output ────────────────────────────────────────────────────────────────

    def _report(self, result, module_name: str):
        p = result.profile
        if p is not None:
            self.stdout.write("  ANALYSIS")
            self.stdout.write(
                f"    reachable={p.reachable} status={p.status_code} "
                f"family={p.family} framework={p.framework or '-'}")
            self.stdout.write(
                f"    pagination={p.pagination or '-'} js={p.js_required} "
                f"login={p.login_required} anti_bot={p.anti_bot} "
                f"lang={p.language or '-'}")
            if p.api_endpoints:
                self.stdout.write(f"    api endpoints: {p.api_endpoints[:3]}")
            self.stdout.write(f"    signature={p.site_signature[:16]}")

        if result.reference_modules:
            self.stdout.write("\n  RETRIEVED REFERENCES (F6)")
            run = result.generation_run
            for ref in (run.reference_modules if run else []):
                self.stdout.write(
                    f"    {ref['module']:28} score={ref['score']:>5.1f}  "
                    f"{'; '.join(ref['reasons'][:3])}")

        run = result.generation_run
        if run is not None:
            self.stdout.write("\n  PROMPT (F5/F12)")
            self.stdout.write(
                f"    version={getattr(run.prompt_version, 'version', '-')} "
                f"est_tokens={run.prompt_tokens_est} "
                f"fragments={run.model_params.get('fragments', [])}")

            self.stdout.write("\n  ATTEMPTS (F7)")
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
                    f"hard={entry['hard_violations']} "
                    f"({entry['duration_ms']} ms)")

        if result.report is not None and not result.report.passed:
            self.stdout.write(self.style.ERROR("\n  VALIDATION FAILURES"))
            for v in result.report.violations:
                where = f":{v.line}" if v.line else ""
                kind = "REJECT" if v.hard else "style "
                self.stdout.write(f"    [{kind}] {v.code}{where} {v.message[:110]}")

        if result.test_outcome is not None:
            t = result.test_outcome
            self.stdout.write("\n  SANDBOX (gate E)")
            self.stdout.write(
                f"    ok={t.ok} tenders={t.tenders_found} "
                f"runtime={t.runtime_ms} ms category={t.error_category or '-'}")
            if t.error_message:
                self.stdout.write(f"    {t.error_message[:160]}")
            for row in (t.sample_rows or [])[:5]:
                self.stdout.write(
                    f"      - {row['title'][:46]:48} "
                    f"deadline={row['submission_deadline'] or '-':12} "
                    f"{row['source_url'][-28:]}")

        b = result.benchmark
        if b is not None:
            self.stdout.write("\n  BENCHMARK (F11)")
            self.stdout.write(
                f"    overall={b.overall_score}/100  exec={b.execution_success}  "
                f"extraction={b.extraction_accuracy:.0%}  "
                f"style={b.style_similarity:.0%}  repairs={b.repair_iterations}")
            if b.field_accuracy:
                fields = "  ".join(f"{k}={v:.0%}" for k, v in
                                   list(b.field_accuracy.items())[:6])
                self.stdout.write(f"    fields: {fields}")
            self.stdout.write(
                f"    duplicates={b.duplicate_rate:.0%}  "
                f"generation={b.generation_ms} ms  "
                f"stability={b.runtime_stability:.0%}")

        # Write the candidate to staging for review.
        if result.source_code:
            staging = pathlib.Path(getattr(settings, "FACTORY_STAGING_DIR"))
            staging.mkdir(parents=True, exist_ok=True)
            path = staging / f"{module_name}.py"
            path.write_text(result.source_code, encoding="utf-8")
            self.stdout.write(f"\n  candidate written to {path}")
            self.stdout.write("  (staging only — not installed, not registered)")

        style = self.style.SUCCESS if result.ok else self.style.ERROR
        self.stdout.write(style(
            f"\n  OUTCOME: {result.outcome} — {result.message}\n"))
