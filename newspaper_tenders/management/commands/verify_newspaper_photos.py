# newspaper_tenders/management/commands/verify_newspaper_photos.py
"""
Sweep existing newspaper tender photos and clear the ones that do not belong to
their tender (the AI sometimes attached a neighbouring court/auction ad).

    # Preview only — changes nothing, prints what would be cleared:
    python manage.py verify_newspaper_photos

    # Apply — clears mismatched photos (row kept, image file removed):
    python manage.py verify_newspaper_photos --apply

OCR is slow on CPU (seconds per image); run the full sweep in the background.
Only the picture is ever removed — the tender text is never touched.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from newspaper_tenders import photo_verify
from newspaper_tenders.models import NewspaperTenderMeta


class Command(BaseCommand):
    help = 'Clear newspaper tender photos whose crop does not belong to the tender.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually clear mismatched photos (default: dry run).')
        parser.add_argument('--limit', type=int, default=0,
                            help='Only scan the first N photos (0 = all).')

    def handle(self, *args, **opts):
        if not photo_verify.available():
            self.stderr.write(self.style.ERROR(
                'EasyOCR is not installed — run: pip install easyocr'))
            return

        apply = opts['apply']
        qs = (NewspaperTenderMeta.objects
              .exclude(ad_image='')
              .exclude(ad_image__isnull=True)
              .select_related('tender')
              .order_by('tender_id'))
        if opts['limit']:
            qs = qs[:opts['limit']]

        total = qs.count() if not opts['limit'] else len(qs)
        self.stdout.write(f'Scanning {total} photo(s) '
                          f'({"APPLY — will clear" if apply else "dry run"})…')

        mroot = str(settings.MEDIA_ROOT)
        wrong = kept = errors = 0

        for m in qs:
            name = m.ad_image.name
            path = os.path.join(mroot, name.replace('/', os.sep))
            try:
                belongs = photo_verify.photo_belongs_to_tender(path)
            except Exception as exc:                              # pragma: no cover
                errors += 1
                self.stderr.write(f'  #{m.tender_id}: OCR error ({exc}) — kept')
                continue

            if belongs:
                kept += 1
                continue

            wrong += 1
            ref = getattr(m.tender, 'reference_no', '') or '—'
            self.stdout.write(self.style.WARNING(
                f'  MISMATCH  #{m.tender_id} ref={ref} — {name}'))
            if apply:
                shared = NewspaperTenderMeta.objects.filter(ad_image=name).count()
                m.ad_image = ''
                m.save(update_fields=['ad_image'])
                if shared <= 1 and os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        self.stdout.write('')
        style = self.style.SUCCESS if apply else self.style.NOTICE
        self.stdout.write(style(
            f'Done. Mismatched: {wrong} | kept: {kept} | OCR errors: {errors} | '
            f'{"CLEARED" if apply else "dry run — nothing changed"}.'))
        if wrong and not apply:
            self.stdout.write('Re-run with --apply to clear the mismatched photos.')
