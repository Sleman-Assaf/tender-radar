# magazine_tenders/management/commands/run_magazines.py
"""
Run the magazine scrapers from the command line.

    manage.py run_magazines                        every enabled magazine
    manage.py run_magazines --source CBNME         one magazine
    manage.py run_magazines --source CBNME --dry-run    scrape + print, save nothing
    manage.py run_magazines --no-ai                save without classifying

`--dry-run` is the one to reach for while writing a scraper: it exercises the
fetch and the parse and shows exactly what would be saved, without touching the
database or spending an AI call.
"""
from django.core.management.base import BaseCommand, CommandError

from magazine_tenders import sources
from magazine_tenders.models import MagazineRun
from magazine_tenders.registry import get_magazine_scraper


class Command(BaseCommand):
    help = 'Scrape tender announcements from the registered magazine websites.'

    def add_arguments(self, parser):
        parser.add_argument('--source', default='',
                            help='Magazine code (default: every enabled magazine)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Scrape and print; save nothing')
        parser.add_argument('--no-ai', action='store_true',
                            help='Save without running classification')

    def handle(self, *args, **opts):
        code    = (opts.get('source') or '').strip().upper()
        dry_run = opts.get('dry_run')
        no_ai   = opts.get('no_ai')

        configured = sources.MAGAZINES
        if not configured:
            self.stdout.write(self.style.WARNING(
                'No magazines are configured in magazine_tenders/sources.py.'))
            return

        if code and code not in configured:
            raise CommandError(f'Unknown magazine {code!r}. '
                               f'Known: {", ".join(sorted(configured))}')

        codes = [code] if code else [c['code'] for c in sources.enabled_sources()]
        if not codes:
            self.stdout.write(self.style.WARNING(
                'Every configured magazine is disabled (MAGAZINE_<CODE>_ENABLED=0).'))
            return

        if dry_run:
            for c in codes:
                self._dry_run(c)
            return

        for c in codes:
            self._live_run(c, no_ai=no_ai)

    # ── modes ────────────────────────────────────────────────────────────────
    def _dry_run(self, code):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n[DRY RUN] {code}'))
        scraper = get_magazine_scraper(code)
        # A scraper refuses to run without `self.source` (the runner normally
        # supplies it). It only reads `source.name`, for the banner — so a dry
        # run hands it an UNSAVED ScraperSource. The scrape then works exactly
        # as it will in production while still writing nothing at all.
        from tenders.models import ScraperSource
        cfg = sources.get_source_config(code)
        scraper.source = ScraperSource(name=cfg['name'],
                                       base_url=cfg.get('base_url', ''),
                                       scraper_class=cfg['scraper_class'],
                                       is_active=False)
        items = scraper.scrape() or []
        self.stdout.write(f'  returned {len(items)} item(s)')
        for i, td in enumerate(items[:20], 1):
            self.stdout.write(
                f'  {i:3}. {td.title[:74]}\n'
                f'       entity={td.entity[:40]!r} ref={td.reference_no[:24]!r} '
                f'deadline={td.submission_deadline} status={td.status} '
                f'url={td.source_url[:60]}'
            )
        if len(items) > 20:
            self.stdout.write(f'  … and {len(items) - 20} more')
        self.stdout.write(self.style.SUCCESS(
            '  nothing saved (dry run); classification not called'))

    def _live_run(self, code, no_ai=False):
        from magazine_tenders import runner

        self.stdout.write(self.style.MIGRATE_HEADING(f'\n[RUN] {code}'))
        if no_ai:
            # Bypass classification by monkeypatching the save call for this
            # run only — used when the AJiAI service is down and the goal is
            # just to confirm the scrape/parse/save path.
            import magazine_tenders.runner as r
            original = r.save_tender_data
            r.save_tender_data = lambda td, src: original(td, src, enrich=False)
            try:
                run = runner.run_one(code, trigger=MagazineRun.Trigger.CLI)
            finally:
                r.save_tender_data = original
        else:
            run = runner.run_one(code, trigger=MagazineRun.Trigger.CLI)

        style = self.style.SUCCESS if run.status == MagazineRun.Status.SUCCESS else self.style.ERROR
        self.stdout.write(style(
            f'  {run.status}: found={run.found} new={run.saved_new} '
            f'updated={run.saved_updated} skipped={run.skipped} '
            f'aec={run.aec} non_aec={run.non_aec}'
        ))
        if run.error_message:
            self.stdout.write(self.style.ERROR(f'  error: {run.error_message[:300]}'))
