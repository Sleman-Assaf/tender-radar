# tenders/tasks.py

import logging
from django.utils import timezone
from celery import shared_task

logger = logging.getLogger('tenders')


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def run_scraper(self, source_id: int):
    """Run a single scraper. Passes the source object so the scraper
    can read its DB-managed TenderingURL records."""
    from .models import ScraperSource, ScrapeLog
    from .scrapers import get_scraper
    from .scrapers.base import tolerant_console
    from .services import save_tender_data

    try:
        source = ScraperSource.objects.get(pk=source_id, is_active=True)
    except ScraperSource.DoesNotExist:
        logger.warning(f'ScraperSource {source_id} not found or inactive')
        return

    log = ScrapeLog.objects.create(source=source)
    logger.info(f'Starting scraper: {source.name}')

    try:
        scraper        = get_scraper(source.scraper_class)
        scraper.source = source          # set as attribute so base scraper can read it
        # A worker has no console: without this, progress prints raise OSError
        # and the scrape is recorded as failed. See scrapers.base.tolerant_console.
        with tolerant_console():
            tender_data_list = scraper.scrape()

        new_count = updated_count = 0
        for td in tender_data_list:
            created, updated = save_tender_data(td, source)
            if created:        new_count += 1
            elif updated:      updated_count += 1

        source.last_scraped_at = timezone.now()
        source.save(update_fields=['last_scraped_at'])

        log.status          = ScrapeLog.RunStatus.SUCCESS
        log.tenders_found   = len(tender_data_list)
        log.tenders_new     = new_count
        log.tenders_updated = updated_count
        log.finished_at     = timezone.now()
        log.save()

        logger.info(
            f'[{source.name}] Done. Found={len(tender_data_list)} '
            f'New={new_count} Updated={updated_count}'
        )

    except Exception as exc:
        log.status        = ScrapeLog.RunStatus.FAILED
        log.error_message = str(exc)
        log.finished_at   = timezone.now()
        log.save()
        logger.error(f'[{source.name}] Scraper FAILED: {exc}')
        raise self.retry(exc=exc)


@shared_task
def run_all_scrapers():
    from .models import ScraperSource
    sources = ScraperSource.objects.filter(is_active=True)
    logger.info(f'Launching {sources.count()} scrapers')
    for source in sources:
        run_scraper.delay(source.pk)


# ── Auto-schedule: run ALL scrapers ─────────────────────────────────────────

@shared_task(bind=True, name='tenders.tasks.run_all_scrapers_task',
             max_retries=0, time_limit=7200, soft_time_limit=6900)
def run_all_scrapers_task(self):
    """
    Celery Beat task — fires every 6 hours and runs all active scrapers
    sequentially.  Registered by: python manage.py setup_schedule
    """
    from .models import ScraperSource
    from django.utils import timezone

    started_at = timezone.now()
    logger.info(f'[AutoSchedule] Starting full scraper run at {started_at:%Y-%m-%d %H:%M}')

    sources = ScraperSource.objects.filter(is_active=True).order_by('id')
    total   = sources.count()

    if total == 0:
        logger.warning('[AutoSchedule] No active ScraperSources found')
        return {'status': 'no sources', 'count': 0}

    logger.info(f'[AutoSchedule] {total} active sources to run')

    results = {'ok': 0, 'failed': 0, 'skipped': 0, 'sources': []}

    for source in sources:
        try:
            logger.info(f'[AutoSchedule] → {source.name} ({source.scraper_class})')
            # Reuse the existing run_scraper task logic directly (no sub-task overhead)
            run_scraper.apply(args=[source.pk])
            results['ok'] += 1
            results['sources'].append({'name': source.name, 'status': 'ok'})
        except Exception as exc:
            logger.error(f'[AutoSchedule] ✗ {source.name}: {exc}')
            results['failed'] += 1
            results['sources'].append({'name': source.name, 'status': 'failed', 'error': str(exc)})

    elapsed = (timezone.now() - started_at).total_seconds()
    logger.info(
        f'[AutoSchedule] Done in {elapsed:.0f}s — '
        f'OK={results["ok"]} FAILED={results["failed"]}'
    )
    return results


# ── AEC Classification via AJiBrain ─────────────────────────────────────────

@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def classify_tender_aec(self, tender_id: int):
    """Classify a single tender for AEC relevance via AJiBrain API."""
    from .models import Tender
    from .ajibrain_client import classify_and_translate_tender

    try:
        tender = Tender.objects.get(pk=tender_id)
    except Tender.DoesNotExist:
        logger.warning(f'[classify_aec] Tender {tender_id} not found')
        return

    updates = classify_and_translate_tender(tender)
    Tender.objects.filter(pk=tender_id).update(**updates)
    logger.info(
        f'[classify_aec] Tender #{tender_id} is_aec={updates.get("is_aec")} '
        f'title_ar_added={bool(updates.get("title_ar"))}'
    )


@shared_task
def classify_unchecked_tenders(batch_size: int = 50):
    """
    Find tenders not yet checked for AEC and dispatch individual classification tasks.
    Safe to run frequently — processes only unchecked rows.
    """
    from .models import Tender
    ids = list(
        Tender.objects.filter(is_aec_checked=False, is_relevant=True)
              .values_list('id', flat=True)[:batch_size]
    )
    logger.info(f'[classify_aec] Dispatching {len(ids)} tenders for AEC classification')
    for tid in ids:
        classify_tender_aec.delay(tid)
    return {'dispatched': len(ids)}


# ── Daily tender digest e-mail ──────────────────────────────────────────────

@shared_task(bind=True, name='tenders.tasks.send_daily_digest_task',
             max_retries=0, time_limit=1800, soft_time_limit=1700)
def send_daily_digest_task(self):
    """
    Celery Beat task — fires daily at the configured hour (default 9 AM) and
    e-mails each user the new tenders for their preferred countries.
    Registered by: python manage.py setup_schedule
    No-op while the digest toggle is disabled in Manage → Daily Digest.
    """
    from .models import IntegrationSettings
    from .digest import run_daily_digest

    cfg = IntegrationSettings.get()
    if not cfg.digest_enabled:
        logger.info('[Digest] skipped — digest disabled in settings.')
        return 'disabled'

    summary = run_daily_digest()
    return (f'sent={summary["sent"]} failed={summary["failed"]} '
            f'no_countries={summary["no_countries"]} no_tenders={summary["no_tenders"]}')


# ── Scheduled system backup ─────────────────────────────────────────────────

@shared_task(bind=True, name='tenders.tasks.run_scheduled_backup_task',
             max_retries=0, time_limit=3600, soft_time_limit=3500)
def run_scheduled_backup_task(self):
    """
    Celery Beat task — fires hourly and creates a backup when the schedule
    configured in System Dashboard → Backup & Restore is due.
    Registered by: python manage.py setup_schedule
    No-op while automatic backups are disabled.
    """
    from .models import BackupSchedule, SystemBackup
    from .backup import create_backup

    cfg = BackupSchedule.get()
    if not cfg.enabled:
        return 'disabled'

    now = timezone.localtime()
    if now.hour != cfg.hour:
        return 'not due (hour)'
    if cfg.frequency == BackupSchedule.Frequency.WEEKLY and now.weekday() != cfg.weekday:
        return 'not due (weekday)'
    if cfg.frequency == BackupSchedule.Frequency.MONTHLY and now.day != cfg.day_of_month:
        return 'not due (day of month)'

    # One run per slot, even if Beat fires twice within the hour.
    if cfg.last_run_at:
        last = timezone.localtime(cfg.last_run_at)
        if last.date() == now.date() and last.hour == now.hour:
            return 'already ran this hour'

    record = create_backup(
        None, trigger=SystemBackup.Trigger.SCHEDULED,
        include_database=cfg.includes_database,
        include_media=cfg.includes_media,
        include_config=cfg.includes_config,
        include_code=cfg.includes_code,
        include_docs=cfg.includes_docs,
        note='Automatic scheduled backup',
    )
    cfg.last_run_at = timezone.now()
    cfg.save(update_fields=['last_run_at'])

    # Retention is applied by create_backup itself, so manual and scheduled
    # runs are trimmed identically — pruning here as well would be a no-op.
    keep = cfg.retention_count
    logger.info(f'[Backup] scheduled run: {record.filename} ({record.status}), keep={keep}')
    return f'{record.status} {record.filename} keep={keep}'
