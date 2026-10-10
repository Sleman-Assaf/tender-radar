# newspaper_tenders/tasks.py
# Celery task for the daily Newspaper Tenders run.
#
# Scheduling (registered by `manage.py setup_newspaper_schedule`):
#   * main run    — daily at NEWSPAPER_SCHEDULE_TIME (default 06:00) in
#                   NEWSPAPER_TIMEZONE (default Asia/Amman);
#   * retry sweep — hourly during NEWSPAPER_RETRY_HOURS (default 7-18): a
#                   cheap no-op when the edition is already DONE, otherwise it
#                   picks up late-attached PDFs and retries failed files.
# Idempotency lives in the pipeline, so overlapping/duplicate fires are safe.

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('tenders')


@shared_task(bind=True, name='newspaper_tenders.tasks.run_newspaper_task',
             max_retries=0, time_limit=7200, soft_time_limit=6900)
def run_newspaper_task(self, target_date: str = ''):
    """
    Run newspaper processing for `target_date` (ISO string) or, by default,
    for "today" in the configured newspaper timezone.

    Never lets an exception escape: a newspaper failure must never break the
    Celery worker or anything else in TenderRadar.
    """
    if not getattr(settings, 'NEWSPAPER_ENABLED', True):
        logger.info('[newspaper] skipped — NEWSPAPER_ENABLED is off')
        return 'disabled'

    from datetime import date

    try:
        if target_date:
            target = date.fromisoformat(target_date)
        else:
            import zoneinfo
            tz_name = getattr(settings, 'NEWSPAPER_TIMEZONE', 'Asia/Amman')
            target = timezone.now().astimezone(
                zoneinfo.ZoneInfo(tz_name)).date()
    except Exception as exc:
        logger.error(f'[newspaper] bad target date {target_date!r}: {exc}')
        return 'bad-date'

    try:
        from .pipeline import run_all
        results = run_all(target)   # every enabled newspaper (Al-Rai, Al-Ghad…)
        return ' | '.join(f'{k}:{v}' for k, v in results.items())
    except Exception as exc:
        logger.error(f'[newspaper] run for {target} crashed: {exc}',
                     exc_info=True)
        return 'crashed'
