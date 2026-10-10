# bd/management/commands/backfill_cv_photos.py
# Path: TenderRadar/bd/management/commands/backfill_cv_photos.py
#
# Re-extracts profile photos for engineers that were imported before the
# photo feature (or whose photo failed to convert).
#
#   python manage.py backfill_cv_photos            # engineers without a photo
#   python manage.py backfill_cv_photos --all      # re-extract for everyone

from django.core.management.base import BaseCommand

from bd.models import Engineer
from bd.views import _reextract_photo


class Command(BaseCommand):
    help = 'Re-extract CV photos from the stored original CV files.'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true',
                            help='Re-extract even for engineers that already have a photo')

    def handle(self, *args, **opts):
        qs = Engineer.objects.all()
        if not opts['all']:
            qs = qs.filter(photo='') | qs.filter(photo__isnull=True)
            qs = qs.distinct()

        ok = failed = 0
        for e in qs:
            err = _reextract_photo(e)
            if err:
                failed += 1
                self.stdout.write(self.style.WARNING(f'  ✗ {e.full_name}: {err}'))
            else:
                ok += 1
                self.stdout.write(f'  ✓ {e.full_name}')

        self.stdout.write(self.style.SUCCESS(
            f'Done — photos extracted: {ok}, failed/none: {failed}'))
