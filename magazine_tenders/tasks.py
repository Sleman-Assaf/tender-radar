# magazine_tenders/tasks.py
"""
Celery wrappers, for parity with the newspaper app and for CLI/Beat use.

Not the primary path: in the current Waitress deployment the in-process
APScheduler job in apps.py is what actually fires the daily sweep. These tasks
exist so an environment that does run Celery Beat can schedule the same work —
see `manage.py setup_magazine_schedule`.
"""
import logging

from celery import shared_task

logger = logging.getLogger('tenders')


@shared_task(name='magazine_tenders.run_magazine')
def run_magazine_task(code: str = '') -> dict:
    """
    Run one magazine (`code` given) or every enabled one (`code` empty).

    Returns a small JSON-safe summary rather than model instances, so the result
    is readable in the Celery results table.
    """
    from .models import MagazineRun
    from .runner import run_all, run_one

    if code:
        run = run_one(code, trigger=MagazineRun.Trigger.CLI)
        return {code: _summary(run)}

    runs = run_all(trigger=MagazineRun.Trigger.CLI)
    return {c: _summary(r) for c, r in runs.items()}


def _summary(run) -> dict:
    return {
        'status':    run.status,
        'found':     run.found,
        'new':       run.saved_new,
        'updated':   run.saved_updated,
        'skipped':   run.skipped,
        'aec':       run.aec,
        'non_aec':   run.non_aec,
        'error':     run.error_message[:200],
    }
