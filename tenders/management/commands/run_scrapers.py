# tenders/management/commands/run_scrapers.py
# Path: TenderRadar/tenders/management/commands/run_scrapers.py

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from tenders.models import ScraperSource, ScrapeLog, SiteSettings, Tender
from tenders.scrapers import get_scraper
from tenders.scrapers.base import tolerant_console
from tenders.services import save_tender_data

# Used by _run_ajibrain's exception handler, which referenced `logger` without
# it existing — so a single AJiBrain failure raised NameError instead of being
# logged and skipped.
logger = logging.getLogger('tenders')


class Command(BaseCommand):
    help = 'Run scrapers — with optional OpenAI batch classify/translate per scraper'

    def add_arguments(self, parser):
        parser.add_argument('--source', type=str, default=None,
            help='Scraper class name, e.g. JordanGTDScraper. Runs all if omitted.')
        parser.add_argument('--dry-run', action='store_true',
            help='Scrape but do not save to database')
        parser.add_argument('--no-ai', action='store_true',
            help='Skip AI classification/translation even if configured')

    def handle(self, *args, **options):
        target  = options['source']
        dry     = options['dry_run']
        skip_ai = options['no_ai']

        # Read site settings once
        cfg        = SiteSettings.get()
        ai_service = cfg.ai_service   # 'AJIBRAIN' or 'OPENAI'

        qs = ScraperSource.objects.select_related('country').filter(is_active=True)
        if target:
            qs = qs.filter(scraper_class=target)
            if not qs.exists():
                self.stderr.write(self.style.ERROR(f'No active source: scraper_class="{target}"'))
                return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n⬡  TenderRadar — {"DRY RUN" if dry else "LIVE SCRAPE"}'
            f' | {qs.count()} source(s)'
            f' | AI: {"OFF (--no-ai)" if skip_ai else ai_service}\n'
        ))

        total_found = total_new = total_updated = total_classified = total_translated = 0

        for source in qs:
            url_count = source.tendering_urls.filter(is_active=True).count()
            self.stdout.write(
                f'\n▶ {source.country.flag_emoji} {source.name} '
                f'[{source.scraper_class}] — {url_count} URL(s)'
            )

            if url_count == 0:
                self.stdout.write(self.style.WARNING(
                    '  ⚠  No active TenderingURLs — run: python manage.py seed_data'
                ))
                continue

            log = ScrapeLog.objects.create(source=source) if not dry else None

            try:
                scraper        = get_scraper(source.scraper_class)
                scraper.source = source
                with tolerant_console():
                    tender_list = scraper.scrape()
                total_found   += len(tender_list)

                new_c = upd_c = 0
                saved_ids = []

                for td in tender_list:
                    if dry:
                        self.stdout.write(f'  [DRY] {td.title[:72]}')
                    else:
                        created, updated = save_tender_data(td, source)
                        if created:   new_c += 1
                        elif updated: upd_c += 1

                total_new     += new_c
                total_updated += upd_c

                # ── AI: classify + translate after each scraper ───────────
                if not dry and not skip_ai and (new_c + upd_c) > 0:
                    self._run_ai(source, ai_service, cfg,
                                 new_c, upd_c,
                                 total_classified, total_translated)

                if not dry:
                    source.last_scraped_at = timezone.now()
                    source.save(update_fields=['last_scraped_at'])
                    log.status          = ScrapeLog.RunStatus.SUCCESS
                    log.tenders_found   = len(tender_list)
                    log.tenders_new     = new_c
                    log.tenders_updated = upd_c
                    log.finished_at     = timezone.now()
                    log.save()

                self.stdout.write(self.style.SUCCESS(
                    f'  ✓  Found={len(tender_list)}  New={new_c}  Updated={upd_c}'
                ))

            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'  ✗  FAILED: {exc}'))
                import traceback; traceback.print_exc()
                if log:
                    log.status        = ScrapeLog.RunStatus.FAILED
                    log.error_message = str(exc)
                    log.finished_at   = timezone.now()
                    log.save()

        self.stdout.write(self.style.SUCCESS(
            f'\n{"━"*58}\n'
            f'  Done.  Found={total_found}  New={total_new}  Updated={total_updated}\n'
            f'{"━"*58}\n'
        ))

    def _run_ai(self, source, ai_service, cfg, new_c, upd_c,
                total_classified, total_translated):
        """Run AI classification and translation after a scraper finishes."""

        # Tenders from this source that need checking
        unchecked_qs = Tender.objects.filter(
            source=source, is_aec_checked=False
        )
        unchecked_count = unchecked_qs.count()

        if unchecked_count == 0:
            return

        self.stdout.write(
            f'  🤖 AI ({ai_service}): {unchecked_count} unchecked tender(s) ...'
        )

        if ai_service == 'OPENAI':
            self._run_openai(source, cfg, unchecked_qs)
        else:
            self._run_ajibrain(source, unchecked_qs)

    def _run_openai(self, source, cfg, unchecked_qs):
        """OpenAI batch classify then translate."""
        try:
            from tenders.openai_client import process_scraper_results
            summary = process_scraper_results(
                tenders_qs      = Tender.objects.filter(source=source),
                batch_classify  = cfg.openai_classify_batch,
                batch_translate = cfg.openai_translate_batch,
            )
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ OpenAI: classified={summary["classified"]} '
                f'aec={summary["aec_found"]} '
                f'translated={summary["translated"]}'
            ))
        except Exception as exc:
            self.stderr.write(self.style.WARNING(
                f'  ⚠ OpenAI batch failed: {exc} — falling back to AJiBrain'
            ))
            self._run_ajibrain(source, unchecked_qs)

    def _run_ajibrain(self, source, unchecked_qs):
        """AJiBrain one-by-one classify + translate (original behaviour)."""
        from tenders.services import run_ai_classification
        classified = aec = translated = 0
        for tender in unchecked_qs.select_related()[:500]:
            try:
                updates = run_ai_classification(tender)
                if updates:
                    fields = list(updates.keys())
                    for k, v in updates.items():
                        setattr(tender, k, v)
                    tender.save(update_fields=fields)
                    classified += 1
                    if updates.get('is_aec'):
                        aec += 1
                    if updates.get('title_ar'):
                        translated += 1
            except Exception as exc:
                logger.warning(f'[ajibrain] #{tender.pk} failed: {exc}')
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ AJiBrain: classified={classified} aec={aec} translated={translated}'
        ))
