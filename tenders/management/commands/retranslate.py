# tenders/management/commands/retranslate.py
# Path: TenderRadar/tenders/management/commands/retranslate.py
"""
Fill missing title_ar (Arabic) for tenders whose title is in English,
French, or any non-Arabic language.

The original title field is NEVER modified.

Usage:
  python manage.py retranslate              # all tenders missing title_ar
  python manage.py retranslate --limit 50   # first N
  python manage.py retranslate --dry-run    # preview only, no DB writes
"""
import re
from django.core.management.base import BaseCommand
from tenders.models import Tender
from tenders.ajibrain_client import translate_title

_ARABIC_RE = re.compile(r'[\u0600-\u06FF]')


def _is_arabic(text):
    return bool(_ARABIC_RE.search(text or ''))


class Command(BaseCommand):
    help = 'Translate title to Arabic and store in title_ar for all tenders missing it'

    def add_arguments(self, parser):
        parser.add_argument('--limit',   type=int, default=0,
                            help='Process at most N tenders (0 = all)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview without writing to DB')

    def handle(self, *args, **options):
        limit   = options['limit']
        dry_run = options['dry_run']

        # Find tenders with no Arabic in title_ar
        qs = Tender.objects.all().order_by('id')
        needs = [t for t in qs if t.title and not _is_arabic(t.title_ar or '')]

        if limit:
            needs = needs[:limit]

        self.stdout.write(
            f'Found {len(needs)} tenders missing Arabic title_ar'
            + (' (dry-run)' if dry_run else '')
        )

        updated = skipped = errors = 0
        for tender in needs:
            if dry_run:
                self.stdout.write(f'  #{tender.pk}: {tender.title[:80]}')
                continue

            # Classify first
            from tenders.ajibrain_client import classify_aec
            is_aec = classify_aec(tender.title or tender.title_ar or '')
            Tender.objects.filter(pk=tender.pk).update(is_aec=is_aec, is_aec_checked=True)

            if not is_aec:
                self.stdout.write(f'  #{tender.pk} [not AEC] {tender.title[:60]}')
                skipped += 1
                continue

            if _is_arabic(tender.title):
                Tender.objects.filter(pk=tender.pk).update(title_ar=tender.title)
                self.stdout.write(f'  #{tender.pk} [copied] {tender.title[:70]}')
                updated += 1
            else:
                arabic = translate_title(tender.title)
                if arabic and _is_arabic(arabic):
                    Tender.objects.filter(pk=tender.pk).update(title_ar=arabic)
                    self.stdout.write(f'  #{tender.pk} [translated] {arabic[:70]}')
                    updated += 1
                else:
                    self.stdout.write(
                        self.style.WARNING(f'  #{tender.pk} [failed] {tender.title[:60]}')
                    )
                    errors += 1

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Done: {updated} updated, {errors} failed, {skipped} skipped'
                )
            )
