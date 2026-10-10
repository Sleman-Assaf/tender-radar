# tenders/management/commands/setup_schedule.py
"""
Management command to register the 6-hour Celery Beat schedule.

Usage:
    python manage.py setup_schedule

This creates (or updates) a PeriodicTask in django-celery-beat that
fires run_all_scrapers_task every 6 hours automatically.

Run this once after deployment (or add to seed_data flow).
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Register the 6-hour periodic scraper schedule in Celery Beat'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours', type=int, default=6,
            help='Interval between full scraper runs (default: 6)'
        )
        parser.add_argument(
            '--disable', action='store_true',
            help='Disable the periodic task instead of enabling it'
        )

    def handle(self, *args, **options):
        try:
            from django_celery_beat.models import PeriodicTask, IntervalSchedule
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'django-celery-beat not installed. Run: pip install django-celery-beat'
            ))
            return

        import json

        hours   = options['hours']
        disable = options['disable']

        # Create or get an interval schedule of N hours
        schedule, created = IntervalSchedule.objects.get_or_create(
            every  = hours,
            period = IntervalSchedule.HOURS,
        )
        if created:
            self.stdout.write(f'  Created interval: every {hours} hours')

        task_name = 'TenderRadar — Run All Scrapers (every 6h)'

        if disable:
            updated = PeriodicTask.objects.filter(name=task_name).update(enabled=False)
            self.stdout.write(self.style.WARNING(
                f'  Disabled periodic task: {task_name} ({updated} updated)'
            ))
            return

        # Create or update the periodic task
        task, created = PeriodicTask.objects.update_or_create(
            name     = task_name,
            defaults = {
                'task':        'tenders.tasks.run_all_scrapers_task',
                'interval':    schedule,
                'enabled':     True,
                'kwargs':      json.dumps({}),
                'description': (
                    f'Automatically runs all active scrapers every {hours} hours. '
                    'Managed by: python manage.py setup_schedule'
                ),
            }
        )

        action = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'\n✓  {action} periodic task: {task_name}\n'
            f'   Task   : tenders.tasks.run_all_scrapers_task\n'
            f'   Every  : {hours} hours\n'
            f'   Enabled: {task.enabled}\n'
            f'   Next run: {task.last_run_at or "on next Celery Beat tick"}\n'
        ))

        # ── AEC classification task: every 30 minutes ────────────────────────
        aec_schedule, _ = IntervalSchedule.objects.get_or_create(
            every=30, period=IntervalSchedule.MINUTES,
        )
        aec_task_name = 'TenderRadar — Classify Unchecked Tenders (AEC, every 30m)'
        aec_task, aec_created = PeriodicTask.objects.update_or_create(
            name=aec_task_name,
            defaults={
                'task':        'tenders.tasks.classify_unchecked_tenders',
                'interval':    aec_schedule,
                'enabled':     True,
                'kwargs':      json.dumps({}),
                'description': (
                    'Sends unchecked tenders to AJiBrain for AEC classification. '
                    'Runs every 30 minutes.'
                ),
            }
        )
        aec_action = 'Created' if aec_created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'✓  {aec_action} periodic task: {aec_task_name}\n'
        ))

        # ── Daily tender digest e-mail: every day at digest_hour (default 9 AM) ──
        from django_celery_beat.models import CrontabSchedule
        from django.conf import settings as dj_settings

        digest_hour = 9
        try:
            from tenders.models import IntegrationSettings
            digest_hour = IntegrationSettings.get().digest_hour
        except Exception:
            pass

        digest_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0', hour=str(digest_hour),
            day_of_week='*', day_of_month='*', month_of_year='*',
            timezone=getattr(dj_settings, 'CELERY_TIMEZONE', 'UTC'),
        )
        digest_task_name = 'TenderRadar — Daily Tender Digest E-mail'
        digest_task, digest_created = PeriodicTask.objects.update_or_create(
            name=digest_task_name,
            defaults={
                'task':        'tenders.tasks.send_daily_digest_task',
                'crontab':     digest_schedule,
                'interval':    None,
                'enabled':     True,
                'kwargs':      json.dumps({}),
                'description': (
                    f'E-mails each user the new tenders for their preferred countries, '
                    f'daily at {digest_hour:02d}:00 ({getattr(dj_settings, "CELERY_TIMEZONE", "UTC")}). '
                    'The task itself is a no-op while the digest toggle in '
                    'Manage → Daily Digest is disabled.'
                ),
            }
        )
        digest_action = 'Created' if digest_created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'✓  {digest_action} periodic task: {digest_task_name} (daily {digest_hour:02d}:00)\n'
        ))

        # ── System backup: hourly tick, the task itself decides if it is due ──
        backup_schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='5', hour='*',
            day_of_week='*', day_of_month='*', month_of_year='*',
            timezone=getattr(dj_settings, 'CELERY_TIMEZONE', 'UTC'),
        )
        backup_task_name = 'TenderRadar — Scheduled System Backup'
        backup_task, backup_created = PeriodicTask.objects.update_or_create(
            name=backup_task_name,
            defaults={
                'task':        'tenders.tasks.run_scheduled_backup_task',
                'crontab':     backup_schedule,
                'interval':    None,
                'enabled':     True,
                'kwargs':      json.dumps({}),
                'description': (
                    'Ticks hourly and creates a full-system backup when the schedule '
                    'in IT Dashboard → Backup & Restore is due. No-op while automatic '
                    'backups are disabled there.'
                ),
            }
        )
        backup_action = 'Created' if backup_created else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'✓  {backup_action} periodic task: {backup_task_name} (hourly tick)\n'
        ))

        self.stdout.write(
            '\nTo start the scheduler, run these two processes in separate terminals:\n'
            '  celery -A tender_radar worker -l info\n'
            '  celery -A tender_radar beat   -l info\n'
        )
