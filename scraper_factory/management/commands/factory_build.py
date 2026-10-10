"""One-click Scraper Factory build — the MVP entry point.

The employee supplies a URL and nothing else. The command analyses the site,
writes a scraper, verifies it, tests it, repairs it if needed, installs it,
registers it, runs it once and saves the tenders.

    python manage.py factory_build https://portal.example/tenders

Output is intentionally minimal: progress and a single outcome line. Prompts,
generated code, validation and benchmark reports and extracted rows are stored
for developers but never printed here.

    --verbose      also print the internal detail (developers only)
    --dry-run      generate and test but do NOT install
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from scraper_factory.generation.pipeline import ScraperFactoryPipeline


class Command(BaseCommand):
    help = ("Add a tender website in one step: generate, verify, install, "
            "register and run its scraper automatically.")

    def add_arguments(self, parser):
        parser.add_argument("url")
        parser.add_argument("--country", default="",
                            help="Optional country code override, e.g. JO.")
        parser.add_argument("--name", default="",
                            help="Optional display name for the source.")
        parser.add_argument(
            "--mode", choices=["install", "dry_run"], default="install",
            help=("install: real transactional installation. "
                  "dry_run: run the identical pipeline (including registration "
                  "and execution) then roll everything back."))
        parser.add_argument("--dry-run", action="store_true",
                            help="Shorthand for --mode dry_run.")
        parser.add_argument("--with-save", action="store_true",
                            help="In dry_run, also re-run through the full save "
                                 "pipeline (slow: pays AI enrichment cost).")
        parser.add_argument("--verbose", action="store_true",
                            help="Print internal detail (developers only).")

    def handle(self, *args, **opts):
        model = None

        mode = "dry_run" if opts["dry_run"] else opts["mode"]
        header = ("Testing (dry run)" if mode == "dry_run" else "Adding")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  {header} {opts['url']}\n"))

        # Always state which backend will write the code.
        if model is None:
            from scraper_factory.models_ai.auto import active_model_summary
            info = active_model_summary()
            self.stdout.write(f"    model: {info['name']}\n")
        else:
            self.stdout.write(f"    model: {getattr(model, 'name', '?')}\n")

        def progress(stage: str, label: str):
            self.stdout.write(f"    {label} ...")

        pipeline = ScraperFactoryPipeline(model=model)
        outcome = pipeline.build(
            opts["url"], country_code=opts["country"],
            source_name=opts["name"], progress=progress, mode=mode,
            with_save=opts["with_save"])

        style = self.style.SUCCESS if outcome.ok else (
            self.style.WARNING if outcome.needs_developer else self.style.ERROR)
        self.stdout.write(style(f"\n  {outcome.headline}"))
        if outcome.detail:
            self.stdout.write(f"  {outcome.detail}")
        self.stdout.write(f"  ({outcome.duration_ms} ms)\n")

        if opts["verbose"] and outcome.internal:
            self.stdout.write(self.style.MIGRATE_HEADING(
                "\n  INTERNAL DETAIL (developers only)"))
            for key, value in outcome.internal.items():
                text = str(value)
                if len(text) > 700:
                    text = text[:700] + " ..."
                self.stdout.write(f"    {key}: {text}")
            self.stdout.write("")

        if outcome.needs_developer:
            self.stdout.write(
                "  A developer can inspect it with:\n"
                "    python manage.py factory_queue\n")
