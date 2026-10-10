# newspaper_tenders/management/commands/setup_newspaper_schedule.py
"""
Register the Newspaper Tenders schedule in Celery Beat.

Usage:
    python manage.py setup_newspaper_schedule
    python manage.py setup_newspaper_schedule --disable

Creates (or updates) two PeriodicTasks:
  1. Daily main run at NEWSPAPER_SCHEDULE_TIME (default 06:00) in
     NEWSPAPER_TIMEZONE (default Asia/Amman).
  2. Hourly retry sweep during NEWSPAPER_RETRY_HOURS (default '7-18') — a
     cheap no-op when the edition is already DONE; otherwise it picks up
     late-attached PDFs and retries failed files.

Mirrors tenders/management/commands/setup_schedule.py conventions.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand

MAIN_TASK_NAME  = 'Newspaper Tenders — Daily Al-Rai run (عطاءات الجريدة)'
RETRY_TASK_NAME = 'Newspaper Tenders — Retry sweep (late PDFs / failures)'
TASK_PATH       = 'newspaper_tenders.tasks.run_newspaper_task'


class Command(BaseCommand):
    help = 'Register the daily Newspaper Tenders schedule in Celery Beat'

    def add_arguments(self, parser):
        parser.add_argument('--disable', action='store_true',
                            help='Disable the periodic tasks instead of enabling them')

    def handle(self, *args, **options):
        try:
            from django_celery_beat.models import CrontabSchedule, PeriodicTask
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'django-celery-beat not installed. Run: pip install django-celery-beat'))
            return

        if options['disable']:
            updated = PeriodicTask.objects.filter(
                name__in=[MAIN_TASK_NAME, RETRY_TASK_NAME]).update(enabled=False)
            self.stdout.write(self.style.WARNING(
                f'  Disabled {updated} newspaper periodic task(s)'))
            return

        tz_name = getattr(settings, 'NEWSPAPER_TIMEZONE', 'Asia/Amman')
        sched_time = getattr(settings, 'NEWSPAPER_SCHEDULE_TIME', '06:00')
        try:
            hour, minute = sched_time.split(':')
            hour, minute = str(int(hour)), str(int(minute))
        except ValueError:
            self.stderr.write(self.style.ERROR(
                f'Bad NEWSPAPER_SCHEDULE_TIME {sched_time!r} — use HH:MM'))
            return
        retry_hours = getattr(settings, 'NEWSPAPER_RETRY_HOURS', '7-18')

        main_cron, _ = CrontabSchedule.objects.get_or_create(
            minute=minute, hour=hour, day_of_week='*',
            day_of_month='*', month_of_year='*', timezone=tz_name)
        retry_cron, _ = CrontabSchedule.objects.get_or_create(
            minute=minute, hour=retry_hours, day_of_week='*',
            day_of_month='*', month_of_year='*', timezone=tz_name)

        _, created = PeriodicTask.objects.update_or_create(
            name=MAIN_TASK_NAME,
            defaults={'task': TASK_PATH, 'crontab': main_cron,
                      'interval': None, 'enabled': True,
                      'kwargs': json.dumps({})})
        self.stdout.write(self.style.SUCCESS(
            f'  {"Created" if created else "Updated"}: {MAIN_TASK_NAME} '
            f'@ {hour.zfill(2)}:{minute.zfill(2)} {tz_name}'))

        _, created = PeriodicTask.objects.update_or_create(
            name=RETRY_TASK_NAME,
            defaults={'task': TASK_PATH, 'crontab': retry_cron,
                      'interval': None, 'enabled': True,
                      'kwargs': json.dumps({})})
        self.stdout.write(self.style.SUCCESS(
            f'  {"Created" if created else "Updated"}: {RETRY_TASK_NAME} '
            f'@ hours {retry_hours} {tz_name}'))
