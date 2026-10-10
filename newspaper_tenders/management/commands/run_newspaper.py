# newspaper_tenders/management/commands/run_newspaper.py
"""
Manual Newspaper Tenders run (no Celery needed).

Usage:
    python manage.py run_newspaper                # today (Asia/Amman)
    python manage.py run_newspaper --date 2026-08-18
"""

from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Run the Newspaper Tenders pipeline once (synchronously)'

    def add_arguments(self, parser):
        parser.add_argument('--date', default='',
                            help='Edition date YYYY-MM-DD (default: today in NEWSPAPER_TIMEZONE)')
        parser.add_argument('--source', default='',
                            help='Newspaper code (ALRAI, ALGHAD). Default: all enabled.')

    def handle(self, *args, **options):
        if options['date']:
            try:
                target = date.fromisoformat(options['date'])
            except ValueError:
                raise CommandError(f'Bad --date {options["date"]!r} — use YYYY-MM-DD')
        else:
            import zoneinfo
            tz_name = getattr(settings, 'NEWSPAPER_TIMEZONE', 'Asia/Amman')
            target = timezone.now().astimezone(zoneinfo.ZoneInfo(tz_name)).date()

        if options['source']:
            from newspaper_tenders.pipeline import run_for_date
            result = run_for_date(target, options['source'].upper())
            self.stdout.write(self.style.SUCCESS(
                f'Newspaper run for {target} [{options["source"].upper()}]: {result}'))
        else:
            from newspaper_tenders.pipeline import run_all
            results = run_all(target)
            for code, result in results.items():
                self.stdout.write(self.style.SUCCESS(
                    f'Newspaper run for {target} [{code}]: {result}'))
