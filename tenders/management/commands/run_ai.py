# tenders/management/commands/run_ai.py
# Path: TenderRadar/tenders/management/commands/run_ai.py
# Usage:
#   python manage.py run_ai                    -- process all unchecked tenders
#   python manage.py run_ai --source WorldBankScraper  -- one source only
#   python manage.py run_ai --force            -- re-classify already checked tenders
#   python manage.py run_ai --service openai   -- override settings, force OpenAI
#   python manage.py run_ai --service ajibrain -- override settings, force AJiBrain

from django.core.management.base import BaseCommand
from django.db.models import Q


class Command(BaseCommand):
    help = 'Run AI classification and translation on tenders'

    def add_arguments(self, parser):
        parser.add_argument('--source', type=str, default=None,
            help='Filter by scraper class name')
        parser.add_argument('--force', action='store_true',
            help='Re-classify tenders already marked as checked')
        parser.add_argument('--service', type=str, default=None,
            choices=['openai', 'ajibrain', 'ollama'],
            help='Override AI service (default: use SiteSettings)')
        parser.add_argument('--classify-only', action='store_true',
            help='Only classify, skip translation')

    def handle(self, *args, **options):
        from tenders.models import SiteSettings, Tender, ScraperSource

        cfg        = SiteSettings.get()
        ai_service = options['service'].upper() if options['service'] else cfg.ai_service
        force      = options['force']
        source_cls = options['source']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n⬡  TenderRadar — AI Processing | service={ai_service}\n'
        ))

        # Build queryset
        qs = Tender.objects.select_related('source', 'country')
        if source_cls:
            qs = qs.filter(source__scraper_class=source_cls)
        if not force:
            qs = qs.filter(is_aec_checked=False)

        total = qs.count()
        self.stdout.write(f'  Tenders to process: {total}')

        if total == 0:
            self.stdout.write(self.style.WARNING(
                '  Nothing to do. Use --force to re-classify already checked tenders.'
            ))
            return

        if ai_service in ('OPENAI', 'OLLAMA'):
            self._run_openai(qs, cfg, options['classify_only'], ai_service)
        else:
            self._run_ajibrain(qs)

    def _run_openai(self, qs, cfg, classify_only, ai_service='OPENAI'):
        if ai_service == 'OLLAMA':
            from tenders.ollama_client import classify_batch, translate_batch
            batch_c = cfg.ollama_classify_batch
            batch_t = cfg.ollama_translate_batch
        else:
            from tenders.openai_client import classify_batch, translate_batch
            batch_c = cfg.openai_classify_batch
            batch_t = cfg.openai_translate_batch
        import re as _re
        _arabic = _re.compile(r'[\u0600-\u06FF]')

        tenders  = list(qs)
        classified = aec_found = translated = 0

        # Step 1: classify
        self.stdout.write(f'\n  Step 1/2: Classifying {len(tenders)} tenders '
                          f'(batch={batch_c})...')
        from tenders.models import Tender as TM
        for i in range(0, len(tenders), batch_c):
            batch  = tenders[i:i + batch_c]
            titles = [t.title or t.title_ar or '' for t in batch]
            results = classify_batch(titles)

            to_update = []
            for tender, title in zip(batch, titles):
                tender.is_aec         = results.get(title, False)
                tender.is_aec_checked = True
                to_update.append(tender)
                classified += 1
                if tender.is_aec:
                    aec_found += 1

            TM.objects.bulk_update(to_update, ['is_aec', 'is_aec_checked'])
            self.stdout.write(f'    batch {i//batch_c + 1}: {len(batch)} classified')
            for tender, title in zip(batch, titles):
                icon   = self.style.SUCCESS('✓ AEC') if tender.is_aec else '✗    '
                short  = (title[:90] + '…') if len(title) > 90 else title
                self.stdout.write(f'      [{icon}] {short}')

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Classified: {classified} | AEC found: {aec_found}'
        ))

        if classify_only:
            return

        # Step 2: translate AEC tenders missing Arabic
        needs_tr = [
            t for t in tenders
            if t.is_aec
            and not _arabic.search(t.title_ar or '')
            and t.title
            and not _arabic.search(t.title)
        ]
        self.stdout.write(f'\n  Step 2/2: Translating {len(needs_tr)} AEC titles '
                          f'(batch={batch_t})...')

        for i in range(0, len(needs_tr), batch_t):
            batch   = needs_tr[i:i + batch_t]
            titles  = [t.title for t in batch]
            results = translate_batch(titles)

            to_update = []
            for tender in batch:
                arabic = results.get(tender.title, '')
                if arabic and _arabic.search(arabic):
                    tender.title_ar = arabic
                    to_update.append(tender)
                    translated += 1

            if to_update:
                TM.objects.bulk_update(to_update, ['title_ar'])
            self.stdout.write(f'    batch {i//batch_t + 1}: {len(to_update)}/{len(batch)} translated')
            for tender in batch:
                arabic = results.get(tender.title, '')
                if arabic and _arabic.search(arabic):
                    en = (tender.title[:55] + '…') if len(tender.title) > 55 else tender.title
                    ar = (arabic[:55]        + '…') if len(arabic)        > 55 else arabic
                    self.stdout.write(f'      EN: {en}')
                    self.stdout.write(f'      AR: {ar}')
                    self.stdout.write(f'      {"─"*60}')

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Translated: {translated}'
        ))

    def _run_ajibrain(self, qs):
        from tenders.services import run_ai_classification
        from tenders.models import Tender as TM
        classified = aec = translated = 0
        for tender in qs.iterator():
            try:
                updates = run_ai_classification(tender)
                if updates:
                    for k, v in updates.items():
                        setattr(tender, k, v)
                    tender.save(update_fields=list(updates.keys()))
                    classified += 1
                    if updates.get('is_aec'): aec += 1
                    if updates.get('title_ar'): translated += 1
            except Exception as exc:
                self.stderr.write(f'  ⚠ #{tender.pk}: {exc}')
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ AJiBrain: classified={classified} aec={aec} translated={translated}'
        ))
