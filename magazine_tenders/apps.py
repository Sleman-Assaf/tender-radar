# magazine_tenders/apps.py
"""
App config plus the daily in-process sweep.

Why in-process APScheduler rather than Celery Beat: in this deployment
(Waitress, run as a Windows service) the APScheduler job inside the web process
is what actually drives scraping — the newspaper runs in the logs are all
"manual check", with no Beat activity. A `shared_task` exists in tasks.py for
environments that do run Beat, but it is not the primary path.

The scheduler is deliberately its OWN `BackgroundScheduler` rather than a job
added to `tenders.apps._scheduler`: this app has to be removable without
touching the scrapers' scheduler, and a crash in one must not take out the
other.
"""
import logging

from django.apps import AppConfig

logger = logging.getLogger('tenders')

#: The live BackgroundScheduler, or None when this process does not run one.
_scheduler = None

_JOB_ID = 'magazine_sweep'


class MagazineTendersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'magazine_tenders'
    verbose_name = 'News — الأخبار'

    def ready(self):
        from django.conf import settings

        if not getattr(settings, 'MAGAZINE_ENABLED', True):
            return
        if not getattr(settings, 'MAGAZINE_INPROCESS_SCHEDULER', True):
            return

        # Exactly the same process-detection rule the scrapers use, so the job
        # starts in the processes that serve the site and nowhere else: not in
        # `migrate`, not in `collectstatic`, not in the runserver parent that
        # only spawns the reloader, and not under `manage.py test`.
        try:
            from tenders.apps import _should_start_scheduler
        except Exception as exc:                                # pragma: no cover
            logger.warning(f'[magazine] cannot read scheduler policy: {exc}')
            return

        if not _should_start_scheduler():
            return

        _close_orphaned_runs()
        _start_scheduler()


def _close_orphaned_runs():
    """
    Mark runs left RUNNING by a previous process as FAILED.

    A sweep killed mid-flight — service restart, power loss — never reaches the
    line that writes its final status, so its row would read as "a sweep is
    happening right now" forever and the busy-lock UI would look stuck.
    """
    try:
        from django.utils import timezone

        from .models import MagazineRun

        stale = MagazineRun.objects.filter(status=MagazineRun.Status.RUNNING)
        n = stale.count()
        if n:
            stale.update(status=MagazineRun.Status.FAILED,
                         finished_at=timezone.now(),
                         error_message='Interrupted — the process running this '
                                       'sweep exited before it finished.')
            logger.info(f'[magazine] closed {n} orphaned RUNNING run(s)')
    except Exception as exc:                                    # pragma: no cover
        # A missing table (first boot, before migrate) must not break startup.
        logger.debug(f'[magazine] orphan cleanup skipped: {exc}')


def _start_scheduler():
    global _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from django.conf import settings
    except ImportError:
        logger.warning('[magazine] APScheduler not installed — no automatic sweep.')
        return

    raw = str(getattr(settings, 'MAGAZINE_SCHEDULE_TIME', '07:00') or '07:00')
    try:
        hour_s, _, minute_s = raw.partition(':')
        hour, minute = int(hour_s), int(minute_s or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(raw)
    except ValueError:
        logger.warning(f'[magazine] MAGAZINE_SCHEDULE_TIME={raw!r} is not HH:MM '
                       f'— falling back to 07:00')
        hour, minute = 7, 0

    tz = getattr(settings, 'MAGAZINE_TIMEZONE', 'Asia/Amman')

    scheduler = BackgroundScheduler(timezone=tz)
    job = scheduler.add_job(
        func=_run_sweep,
        trigger=CronTrigger(hour=hour, minute=minute),
        id=_JOB_ID,
        name=f'Magazine sweep daily at {hour:02d}:{minute:02d} {tz}',
        replace_existing=True,
        max_instances=1,
        # An hour's grace: if the machine was asleep or the process was busy at
        # the fire time, the sweep still happens rather than being dropped.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler          # keep a reference so it is not collected

    nxt = getattr(job, 'next_run_time', None)
    when = nxt.strftime('%Y-%m-%d %H:%M') if nxt else 'unknown'
    logger.info(f'[magazine] scheduler started — daily {hour:02d}:{minute:02d} '
                f'{tz}; next run {when}')
    print(f'\n  [Timer]  Magazine sweep scheduled — daily at '
          f'{hour:02d}:{minute:02d} ({tz}).'
          f'\n           Next automatic run: {when}\n')


def _run_sweep():
    """Scheduler entry point. Never raises — a crash must not kill the job."""
    try:
        from .models import MagazineRun
        from .runner import run_all
        run_all(trigger=MagazineRun.Trigger.SCHEDULE)
    except Exception as exc:                                    # pragma: no cover
        logger.error(f'[magazine] scheduled sweep crashed: {exc}')


def next_scheduled_run():
    """Datetime of the next automatic sweep, or None if not scheduled here."""
    if _scheduler is None:
        return None
    try:
        job = _scheduler.get_job(_JOB_ID)
        return getattr(job, 'next_run_time', None) if job else None
    except Exception:                                           # pragma: no cover
        return None
