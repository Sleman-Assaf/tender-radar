# tenders/management/commands/classify_aec.py
# Path: TenderRadar/tenders/management/commands/classify_aec.py

from django.core.management.base import BaseCommand
from tenders.models import Tender
from tenders.ajibrain_client import classify_and_translate_tender


class Command(BaseCommand):
    help = 'Classify tenders as AEC-related via AJiBrain API. Processes unchecked tenders by default.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all', action='store_true',
            help='Re-classify ALL tenders (not just unchecked ones)',
        )
        parser.add_argument(
            '--limit', type=int, default=0,
            help='Maximum number of tenders to process (0 = no limit)',
        )

    def handle(self, *args, **options):
        qs = Tender.objects.filter(is_relevant=True)
        if not options['all']:
            qs = qs.filter(is_aec_checked=False)

        if options['limit']:
            qs = qs[:options['limit']]

        total = qs.count()
        self.stdout.write(f'Processing {total} tenders...')

        ok = failed = 0
        for tender in qs.iterator():
            try:
                updates = classify_and_translate_tender(tender)
                Tender.objects.filter(pk=tender.pk).update(**updates)
                status = '✓ AEC' if updates.get('is_aec') else '✗ NOT AEC'
                self.stdout.write(f'  [{status}] #{tender.pk} {tender.title[:70]}')
                ok += 1
            except Exception as exc:
                self.stderr.write(f'  [ERROR] #{tender.pk}: {exc}')
                failed += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. OK={ok} Failed={failed}'
        ))
