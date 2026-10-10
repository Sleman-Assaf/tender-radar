# newspaper_tenders/apps.py
import logging

from django.apps import AppConfig

logger = logging.getLogger('tenders')

#: The live BackgroundScheduler, or None when this process runs none.
_scheduler = None

_JOB_ID = 'newspaper_daily_run'


class NewspaperTendersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'newspaper_tenders'
    verbose_name = 'Newspaper Tenders — عطاءات الجريدة'

    def ready(self):
        if not _serves_requests():
            return
        _close_orphaned_editions()
        _maybe_start_scheduler()


def _maybe_start_scheduler():
    """
    Start the daily run in the processes that serve the site, and nowhere else.

    Two gates, deliberately separate from the one above. NEWSPAPER_ENABLED
    turns the whole feature off. NEWSPAPER_INPROCESS_SCHEDULER turns off only
    the automatic run — which is what a second web worker against the same
    database needs, so that just one process schedules.

    The process policy is tenders.apps._should_start_scheduler(), the same
    function the scrapers and the News sweep use, so `migrate`,
    `collectstatic`, the runserver parent and the test suite never schedule a
    scan.
    """
    from django.conf import settings

    if not getattr(settings, 'NEWSPAPER_ENABLED', True):
        return
    if not getattr(settings, 'NEWSPAPER_INPROCESS_SCHEDULER', True):
        return

    try:
        from tenders.apps import _should_start_scheduler
    except Exception as exc:                                # pragma: no cover
        logger.warning(f'[newspaper] cannot read scheduler policy: {exc}')
        return

    if not _should_start_scheduler():
        return

    _start_scheduler()


def _start_scheduler():
    """
    A daily APScheduler job inside the web process.

    Its own BackgroundScheduler rather than a job on the scrapers' one, for the
    same reason magazine_tenders keeps its own: this app has to be removable
    without touching the scrapers, and a crash in one scheduler must not take
    out the other.
    """
    global _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from django.conf import settings
    except ImportError:
        logger.warning('[newspaper] APScheduler not installed — no automatic run.')
        return

    raw = str(getattr(settings, 'NEWSPAPER_SCHEDULE_TIME', '08:00') or '08:00')
    try:
        hour_s, _, minute_s = raw.partition(':')
        hour, minute = int(hour_s), int(minute_s or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(raw)
    except ValueError:
        logger.warning(f'[newspaper] NEWSPAPER_SCHEDULE_TIME={raw!r} is not '
                       f'HH:MM — falling back to 08:00')
        hour, minute = 8, 0

    tz = getattr(settings, 'NEWSPAPER_TIMEZONE', 'Asia/Amman')

    scheduler = BackgroundScheduler(timezone=tz)
    job = scheduler.add_job(
        func=_run_todays_edition,
        trigger=CronTrigger(hour=hour, minute=minute),
        id=_JOB_ID,
        name=f'Newspaper run daily at {hour:02d}:{minute:02d} {tz}',
        replace_existing=True,
        max_instances=1,
        # An hour's grace: if the machine was asleep or the process was busy at
        # the fire time, the run still happens rather than being dropped. A
        # newspaper edition is only available for its own day, so a dropped
        # fire loses that day's tenders for good.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler          # keep a reference so it is not collected

    nxt = getattr(job, 'next_run_time', None)
    when = nxt.strftime('%Y-%m-%d %H:%M') if nxt else 'unknown'
    logger.info(f'[newspaper] scheduler started — daily '
                f'{hour:02d}:{minute:02d} {tz}; next run {when}')
    print(f'\n  [Timer]  Newspaper run scheduled — daily at '
          f'{hour:02d}:{minute:02d} ({tz}).'
          f'\n           Next automatic run: {when}\n')


def _run_todays_edition():
    """
    Scheduler entry point. Never raises — a crash must not kill the job.

    Claims the same slot the buttons claim, so an 08:00 fire while somebody is
    already running a check is skipped rather than doubled, and so Reset's
    guard can see this run.

    "Today" is read in NEWSPAPER_TIMEZONE, not the server's zone: the edition
    that exists at 08:00 Amman is Amman's date.
    """
    from . import runlock

    if not runlock.claim():
        logger.info('[newspaper] 08:00 run skipped — a run is already in progress')
        return
    try:
        import zoneinfo

        from django.conf import settings
        from django.utils import timezone

        from .pipeline import run_all

        tz_name = getattr(settings, 'NEWSPAPER_TIMEZONE', 'Asia/Amman')
        target = timezone.now().astimezone(zoneinfo.ZoneInfo(tz_name)).date()
        results = run_all(target)
        logger.info(f'[newspaper] scheduled run for {target} finished: {results}')
    except Exception as exc:                                # pragma: no cover
        logger.error(f'[newspaper] scheduled run crashed: {exc}', exc_info=True)
    finally:
        runlock.release()


def next_scheduled_run():
    """Datetime of the next automatic run, or None if not scheduled here."""
    if _scheduler is None:
        return None
    try:
        job = _scheduler.get_job(_JOB_ID)
        return getattr(job, 'next_run_time', None) if job else None
    except Exception:                                       # pragma: no cover
        return None


def _serves_requests() -> bool:
    """
    True in a process that answers HTTP, false in the tooling.

    Deliberately NOT tenders.apps._should_start_scheduler(), even though
    magazine_tenders.apps guards its identical sweep with it. That function
    answers a different question — "should the automatic scan run here?" — and
    obeys TENDERRADAR_SCHEDULER, so with the scheduler switched off it also
    switches off the sweep. Closing a row that a dead process left behind is a
    data-integrity fix, not a scan; it should happen in any web process,
    whatever the scheduler policy.

    What must be excluded is the tooling: `test` (it would fight the fixtures),
    and the migration commands (the table may not exist yet).
    """
    import sys

    argv = ' '.join(sys.argv).lower()
    if any(cmd in argv for cmd in (
            ' test', 'makemigrations', ' migrate', 'collectstatic',
            'showmigrations', 'flush', 'loaddata', 'dumpdata')):
        return False
    if 'manage.py' in argv:
        return 'runserver' in argv
    return any(name in argv for name in (
        'serve_http', 'serve_https', 'serve_desktop',
        'waitress', 'gunicorn', 'uwsgi', 'daphne', 'uvicorn', 'hypercorn',
    ))


def _close_orphaned_editions():
    """
    Close out editions still marked PROCESSING when a process starts.

    Nothing can be running in a process that has only just started, so no row
    matched here is live and its live-looking badge is always wrong.

    But — and this is why the wording below is careful — PROCESSING means
    two different things for a newspaper edition, unlike the RUNNING status
    this sweep is modelled on. run_for_date() ends a PARTIAL run at PROCESSING
    on purpose (see its status block: some pages failed, or some are still
    pending), so a row can be resting there having finished perfectly
    normally. This sweep cannot tell that case from a killed run, so it must
    not claim the edition was interrupted. It states only what is certainly
    true: the edition did not finish, and here is what to do about it.

    The pages themselves are deliberately untouched. They keep their own
    statuses, so Retry resumes exactly where the last run stopped.

    FAILED follows magazine_tenders.apps._close_orphaned_runs and
    tenders.apps._reset_orphaned_scrape_logs rather than adding a status
    value. If the difference between "interrupted" and "finished with pages
    left over" ever needs to be visible, that is what a dedicated status
    would buy.
    """
    try:
        from .models import NewspaperEdition

        stale = NewspaperEdition.objects.filter(
            status=NewspaperEdition.Status.PROCESSING)
        # processed_at is deliberately NOT set here: in this app it means
        # "finished successfully" - models.mark() writes it only for DONE -
        # so stamping it on a failed row would corrupt the field's meaning.
        count = stale.update(
            status=NewspaperEdition.Status.FAILED,
            error_message='This edition did not finish. It was either '
                          'interrupted, or some of its pages could not be '
                          'read. Press Retry to continue, or Reset to '
                          'start over.',
        )
        if count:
            logger.info(f'[newspaper] closed {count} interrupted edition(s) '
                        'left over from a previous process')
    except Exception as exc:                                # pragma: no cover
        # Never let a startup housekeeping task stop the site from booting.
        logger.warning(f'[newspaper] could not close interrupted editions: {exc}')
