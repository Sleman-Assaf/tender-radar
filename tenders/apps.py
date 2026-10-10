# tenders/apps.py
import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger('tenders')

# ── Run guard ─────────────────────────────────────────────────────────────────
# A full run walks every active source and takes many minutes. It can now be
# started from three places at once — the 6-hourly scheduler, the "Run All
# Scanners" button (any signed-in user), and a per-source Run button — so
# without a guard two runs interleave: the same portals fetched twice
# simultaneously, and two ScrapeLog rows per source for a single pass.
#
# One lock for the full run, plus a set of the source IDs currently being
# scraped so a single source cannot be started twice either.
_full_run_lock  = threading.Lock()
_active_sources: set[int] = set()
_active_lock    = threading.Lock()


def full_run_is_active() -> bool:
    """True while a full scan is in progress (scheduled or manual)."""
    return _full_run_lock.locked()


def source_run_is_active(source_id: int) -> bool:
    """True while this one source is being scraped."""
    with _active_lock:
        return int(source_id) in _active_sources


def _claim_source(source_id: int) -> bool:
    """Reserve a source for scraping. False when it is already reserved."""
    with _active_lock:
        sid = int(source_id)
        if sid in _active_sources:
            return False
        _active_sources.add(sid)
        return True


def _release_source(source_id: int) -> None:
    with _active_lock:
        _active_sources.discard(int(source_id))


# ── Starting a scan ───────────────────────────────────────────────────────────
# These claim the slot *before* answering the caller. Asking
# full_run_is_active() and then spawning a thread is two steps: two people
# pressing "Run All Scanners" in the same second both saw False, both were told
# "started", and only one run actually happened.

def start_full_run() -> bool:
    """
    Start a full scan in a background thread. Returns whether it started.

    False means a scan was already running, so nothing new was launched.
    """
    if not _full_run_lock.acquire(blocking=False):
        return False

    def _worker():
        try:
            _do_full_run()
        finally:
            _full_run_lock.release()

    threading.Thread(target=_worker, daemon=True, name='scraper-full-run').start()
    return True


def start_source_run(source_id: int) -> bool:
    """
    Start a scan of one source in a background thread. Returns whether it started.

    False means either that source or a full scan is already running.
    """
    sid = int(source_id)
    if _full_run_lock.locked():
        return False
    if not _claim_source(sid):
        return False

    def _worker():
        # The slot is already claimed, so the scrape must not try to claim it
        # again — it releases it when it finishes.
        _run_single_scraper(sid, claimed=True)

    threading.Thread(target=_worker, daemon=True,
                     name=f'scraper-source-{sid}').start()
    return True


class TendersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name               = 'tenders'

    def ready(self):
        from django.db.models.signals import post_save
        from django.contrib.auth import get_user_model
        User = get_user_model()
        post_save.connect(create_user_profile, sender=User)

        # Wire login signal
        import tenders.signals  # noqa

        if not _should_start_scheduler():
            return
        _reset_orphaned_scrape_logs()
        _start_scheduler()


def _should_start_scheduler() -> bool:
    """
    True in exactly the processes that serve the site.

    The automatic 6-hourly run lives inside the web process (APScheduler, not
    Celery), so it has to start there and nowhere else: not in `migrate`, not in
    `collectstatic`, and not in the runserver parent that only spawns the
    reloader — each of those would either run scrapers during a deploy step or
    run them twice.

    Set TENDERRADAR_SCHEDULER=0 to keep the scheduler off in a process that
    would otherwise start it (a second web worker against the same database), or
    =1 to force it on.
    """
    import sys

    override = os.environ.get('TENDERRADAR_SCHEDULER', '').strip().lower()
    if override in ('0', 'false', 'no', 'off'):
        return False
    if override in ('1', 'true', 'yes', 'on'):
        return True

    argv = ' '.join(sys.argv).lower()

    # manage.py: only the development server, and only inside the reloader's
    # child process (RUN_MAIN), so the scheduler is not started twice.
    if 'manage.py' in argv:
        return 'runserver' in argv and os.environ.get('RUN_MAIN') == 'true'

    # The production entry points. serve_http.py / serve_https.py are how this
    # project is actually started (start_http.bat), and they were missing from
    # the previous check — which is why the automatic run never happened in
    # production: the command line is `python serve_http.py`, and nothing in it
    # says "waitress", so the scheduler silently never started.
    return any(name in argv for name in (
        'serve_http', 'serve_https', 'serve_desktop',
        'waitress', 'gunicorn', 'uwsgi', 'daphne', 'uvicorn', 'hypercorn',
    ))


def _reset_orphaned_scrape_logs():
    """
    Close out ScrapeLog rows left in RUNNING by a previous process.

    A run that is killed — server restart, power loss, crash — never reaches the
    line that writes its final status, so its row stays RUNNING forever. Those
    rows then read as "a scan is happening right now" on the dashboard and in
    System Logs. Nothing can still be running in a process that has only just
    started, so every one of them belongs to a run that died.
    """
    from django.utils import timezone
    try:
        from .models import ScrapeLog
        stale = ScrapeLog.objects.filter(status=ScrapeLog.RunStatus.RUNNING)
        count = stale.update(
            status=ScrapeLog.RunStatus.FAILED,
            finished_at=timezone.now(),
            error_message='Interrupted — the server stopped while this run was in progress.',
        )
        if count:
            logger.info(f'[Scheduler] Closed {count} interrupted scrape log(s) from a previous run')
    except Exception as exc:                                  # pragma: no cover
        # Never block startup for this — an unmigrated database is the usual
        # cause, and the next start will tidy up instead.
        logger.warning(f'[Scheduler] Could not close interrupted scrape logs: {exc}')


def _start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron         import CronTrigger
        from apscheduler.triggers.interval     import IntervalTrigger
        from django.conf import settings
    except ImportError:
        logger.warning('[Scheduler] APScheduler not installed.')
        return

    interval_hours = getattr(settings, 'SCRAPER_SCHEDULE_HOURS', 6) or 6

    # A fixed clock — 00:00, 06:00, 12:00, 18:00 Asia/Amman — rather than "six
    # hours after this process started". With the interval trigger, every
    # restart pushed the next run a further six hours out, so on a machine that
    # is restarted during the working day the automatic run could keep being
    # deferred and never actually arrive.
    if 24 % interval_hours == 0:
        trigger  = CronTrigger(hour=f'*/{interval_hours}', minute=0)
        schedule = f'every {interval_hours}h on the clock (00:00, ' \
                   f'{interval_hours:02d}:00, … Asia/Amman)'
    else:
        trigger  = IntervalTrigger(hours=interval_hours)
        schedule = f'every {interval_hours}h from startup'

    scheduler = BackgroundScheduler(timezone='Asia/Amman')
    job = scheduler.add_job(
        func=_run_all_scrapers,
        trigger=trigger,
        id='run_all_scrapers',
        name=f'Run all scrapers {schedule}',
        replace_existing=True,
        max_instances=1,
        # An hour's grace: if the machine was asleep or the process was busy at
        # the fire time, the run still happens rather than being dropped.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()

    # Keep a reference so the scheduler is not garbage-collected, and so the
    # status endpoint can report when the next automatic run is due.
    global _scheduler
    _scheduler = scheduler

    nxt = getattr(job, 'next_run_time', None)
    when = nxt.strftime('%Y-%m-%d %H:%M') if nxt else 'unknown'
    logger.info(f'[Scheduler] Started — {schedule}; next run {when}')
    print(f'\n  [Timer]  Scraper scheduler started — {schedule}.'
          f'\n           Next automatic run: {when}\n')


#: The live BackgroundScheduler, or None when this process does not run one.
_scheduler = None


def next_scheduled_run():
    """Datetime of the next automatic full run, or None if not scheduled here."""
    if _scheduler is None:
        return None
    try:
        job = _scheduler.get_job('run_all_scrapers')
        return getattr(job, 'next_run_time', None) if job else None
    except Exception:                                          # pragma: no cover
        return None


def _run_ai_enrichment(source) -> None:
    """
    Batch AEC classification + Arabic translation for one source's new tenders.

    Only the batch services are handled here. AJIBRAIN classifies inline, one
    tender at a time, inside save_tender_data().
    """
    from .models import SiteSettings, Tender as TenderModel

    cfg        = SiteSettings.get()
    ai_service = cfg.ai_service
    if ai_service not in ('OPENAI', 'OLLAMA'):
        return
    if not TenderModel.objects.filter(source=source, is_aec_checked=False).exists():
        return

    try:
        b_classify  = (cfg.openai_classify_batch  if ai_service == 'OPENAI'
                       else cfg.ollama_classify_batch)
        b_translate = (cfg.openai_translate_batch if ai_service == 'OPENAI'
                       else cfg.ollama_translate_batch)
        from .openai_client import process_scraper_results
        process_scraper_results(
            tenders_qs=TenderModel.objects.filter(source=source),
            batch_classify=b_classify,
            batch_translate=b_translate,
            ai_service=ai_service,
        )
    except Exception as exc:
        logger.error(f'[{ai_service}] enrichment failed for {source.name}: {exc}',
                     exc_info=True)


def _scrape_one_source(source, claimed: bool = False) -> bool:
    """
    Scrape one source: fetch, save, enrich, and write its ScrapeLog row.

    Returns True on success. Never raises — one dead portal must not abandon the
    rest of a full run. The single shared implementation for both entry points:
    the per-source Run button and the full run, which used to carry two copies
    of this logic that had already drifted apart in their log messages.

    `claimed` says the caller already reserved this source (start_source_run
    does, so it can tell the user whether the scan really started). Either way
    the reservation is released here when the scrape finishes.
    """
    import traceback
    from django.utils import timezone
    from .models   import ScrapeLog
    from .scrapers import get_scraper
    from .scrapers.base import tolerant_console
    from .services import save_tender_data

    sid = source.pk
    if not claimed and not _claim_source(sid):
        logger.info(f'[Scrape] {source.name} is already being scraped — skipped')
        return False

    log = None
    try:
        log            = ScrapeLog.objects.create(source=source)
        scraper        = get_scraper(source.scraper_class)
        scraper.source = source
        with tolerant_console():
            tender_list = scraper.scrape()

        new_count = updated_count = 0
        for td in tender_list:
            created, updated = save_tender_data(td, source)
            if created:    new_count     += 1
            elif updated:  updated_count += 1

        _run_ai_enrichment(source)

        source.last_scraped_at = timezone.now()
        source.save(update_fields=['last_scraped_at'])

        log.status          = ScrapeLog.RunStatus.SUCCESS
        log.tenders_found   = len(tender_list)
        log.tenders_new     = new_count
        log.tenders_updated = updated_count
        log.finished_at     = timezone.now()
        log.save()
        logger.info(f'[Scrape] {source.name}: found={len(tender_list)} '
                    f'new={new_count} updated={updated_count}')
        return True

    except Exception as exc:
        logger.error(f'[Scrape] [FAIL] {source.name}: {exc}\n{traceback.format_exc()}')
        if log:
            try:
                log.status        = ScrapeLog.RunStatus.FAILED
                log.error_message = str(exc)[:2000]
                log.finished_at   = timezone.now()
                log.save()
            except Exception:
                pass
        return False

    finally:
        _release_source(sid)


def _run_single_scraper(source_id: int, claimed: bool = False):
    """Scrape one source by id — target of the per-source Run button."""
    from .models import ScraperSource
    try:
        source = ScraperSource.objects.select_related('country').get(
            pk=source_id, is_active=True)
    except ScraperSource.DoesNotExist:
        logger.warning(f'[Manual] ScraperSource {source_id} not found or inactive')
        if claimed:
            _release_source(source_id)      # nothing will run, so free the slot
        return
    _scrape_one_source(source, claimed=claimed)


def _do_full_run():
    """
    Scrape every active source in turn. Callers must hold _full_run_lock.

    Use _run_all_scrapers() (the scheduled job) or start_full_run() (the button)
    rather than calling this directly.
    """
    from django.utils import timezone
    from .models import ScraperSource

    started = timezone.now()
    logger.info(f'[Scrape] > Starting full run at {started:%Y-%m-%d %H:%M}')
    try:
        sources = list(
            ScraperSource.objects.select_related('country')
                                 .filter(is_active=True).order_by('id')
        )
    except Exception as exc:
        logger.error(f'[Scrape] Cannot query ScraperSource: {exc}')
        return

    ok = sum(1 for source in sources if _scrape_one_source(source))

    elapsed = (timezone.now() - started).total_seconds()
    logger.info(f'[Scrape] Done in {elapsed:.0f}s — {ok}/{len(sources)} OK, '
                f'{len(sources) - ok} failed')


def _run_all_scrapers():
    """
    The 6-hourly scheduled job: take the run slot, sweep every source, release.

    Returns without doing anything when a scan is already in progress, so a
    scheduled fire during a manual run does not scrape every portal twice.
    """
    if not _full_run_lock.acquire(blocking=False):
        logger.info('[Scrape] A full run is already in progress — this call was skipped')
        return
    try:
        _do_full_run()
    finally:
        _full_run_lock.release()


def create_user_profile(sender, instance, created, **kwargs):
    if created:
        from .models import UserProfile
        UserProfile.objects.get_or_create(user=instance)
