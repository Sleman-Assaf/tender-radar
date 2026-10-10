# bd/tasks.py
# Path: TenderRadar/bd/tasks.py
#
# Bulk CV analysis queue. Items are processed strictly one at a time —
# AJiAI/Ollama is a single-GPU queue, so parallel calls would only contend.
# Runs as a Celery task when a worker is available, with an automatic
# in-process thread fallback so the feature works even without Celery.

import json
import logging
import threading

from django.utils import timezone

logger = logging.getLogger('tenders')

try:
    from celery import shared_task
    _HAS_CELERY = True
except ImportError:                                     # pragma: no cover
    _HAS_CELERY = False

    def shared_task(*a, **k):                           # no-op decorator
        def deco(fn):
            return fn
        return deco if not (a and callable(a[0])) else a[0]


def _process_item(item) -> None:
    """Extract → AJiAI parse → create/update Engineer. Raises on failure."""
    from .ajiai_client import extract_cv
    from .models import CVBatchItem, CVDocument, Engineer
    from .text_extract import extract_text

    item.status = CVBatchItem.Status.PROCESSING
    item.error = ''
    item.save(update_fields=['status', 'error'])

    with item.file.open('rb') as fh:
        fh.name = item.original_name or item.file.name   # extension routing
        cv_text = extract_text(fh)
    if not cv_text:
        raise ValueError('No text could be extracted (scanned or unsupported file).')

    # Profile photo (if the CV contains one)
    from .photo_extract import extract_photo
    with item.file.open('rb') as fh:
        fh.name = item.original_name or item.file.name
        photo_bytes, photo_ext = extract_photo(fh)

    parsed = extract_cv(cv_text)
    if not parsed or not (parsed.get('full_name') or '').strip():
        raise ValueError('AJiAI could not parse this CV (no name detected).')

    full_name = str(parsed['full_name']).strip()[:200]
    memberships = parsed.get('memberships') or []
    if isinstance(memberships, list):
        memberships = '\n'.join(str(m) for m in memberships)

    years = parsed.get('years_experience')
    try:
        years = int(years) if years not in (None, '', 0) else None
    except (TypeError, ValueError):
        years = None

    discipline = str(parsed.get('discipline') or 'OTHER').upper()
    valid_disciplines = {c for c, _ in Engineer._meta.get_field('discipline').choices}
    if discipline not in valid_disciplines:
        discipline = 'OTHER'

    def dumps(key):
        data = parsed.get(key) or []
        return json.dumps(data if isinstance(data, list) else [],
                          ensure_ascii=False)

    fields = dict(
        current_position = str(parsed.get('current_position') or '')[:200],
        profession       = str(parsed.get('profession') or '')[:200],
        nationality      = str(parsed.get('nationality') or '')[:100],
        date_of_birth    = str(parsed.get('date_of_birth') or '')[:40],
        years_with_firm  = str(parsed.get('years_with_firm') or '')[:40],
        memberships      = memberships,
        countries_experience = str(parsed.get('countries_experience') or '')[:400],
        synopsis         = str(parsed.get('synopsis') or ''),
        years_experience = years,
        discipline       = discipline,
        education_json      = dumps('education'),
        employment_json     = dumps('employment'),
        experience_json     = dumps('experience'),
        certifications_json = dumps('certifications'),
        languages_json      = dumps('languages'),
        cv_text          = cv_text[:60000],
        ai_parsed        = True,
        needs_review     = True,
    )

    engineer = Engineer.objects.filter(full_name__iexact=full_name).first()
    if engineer:
        for k, v in fields.items():
            setattr(engineer, k, v)
        if photo_bytes:
            from django.core.files.base import ContentFile
            engineer.photo = ContentFile(
                photo_bytes,
                name=f"{full_name.replace(' ', '_')}_photo.{photo_ext or 'png'}")
        engineer.save()
        item.was_update = True
    else:
        engineer = Engineer.objects.create(
            full_name=full_name, created_by=item.batch.created_by, **fields)
        if photo_bytes:
            from django.core.files.base import ContentFile
            engineer.photo = ContentFile(
                photo_bytes,
                name=f"{full_name.replace(' ', '_')}_photo.{photo_ext or 'png'}")
            engineer.save(update_fields=['photo'])

    # Attach the original file to the engineer
    with item.file.open('rb') as fh:
        from django.core.files.base import ContentFile
        CVDocument.objects.create(
            engineer=engineer, kind=CVDocument.Kind.ORIGINAL,
            original_name=item.original_name,
            uploaded_by=item.batch.created_by,
            file=ContentFile(fh.read(), name=item.original_name or 'cv.docx'),
        )

    item.engineer     = engineer
    item.status       = CVBatchItem.Status.DONE
    item.processed_at = timezone.now()
    item.save(update_fields=['engineer', 'status', 'processed_at', 'was_update'])
    logger.info(f'[bd.batch] item #{item.pk} → {full_name!r} '
                f'({"updated" if item.was_update else "created"})')


def _run_batch(batch_id: int) -> None:
    from .models import CVBatch, CVBatchItem

    try:
        batch = CVBatch.objects.get(pk=batch_id)
    except CVBatch.DoesNotExist:
        return

    batch.status = CVBatch.Status.PROCESSING
    batch.started_at = timezone.now()
    batch.save(update_fields=['status', 'started_at'])
    logger.info(f'[bd.batch] Batch #{batch_id} started — '
                f'{batch.items.count()} CVs')

    for item in batch.items.filter(status__in=[CVBatchItem.Status.PENDING,
                                               CVBatchItem.Status.PROCESSING]):
        # ── Cancellation check between items ────────────────────────────────
        try:
            current = CVBatch.objects.only('status').get(pk=batch_id).status
        except CVBatch.DoesNotExist:
            logger.info(f'[bd.batch] Batch #{batch_id} deleted mid-run — stopping.')
            return
        if current == CVBatch.Status.CANCELLED:
            batch.items.filter(status=CVBatchItem.Status.PENDING).update(
                status=CVBatchItem.Status.CANCELLED)
            CVBatch.objects.filter(pk=batch_id).update(finished_at=timezone.now())
            logger.info(f'[bd.batch] Batch #{batch_id} cancelled — '
                        f'remaining items skipped.')
            return

        try:
            _process_item(item)
        except Exception as exc:
            item.status = CVBatchItem.Status.FAILED
            item.error = str(exc)[:500]
            item.processed_at = timezone.now()
            item.save(update_fields=['status', 'error', 'processed_at'])
            logger.warning(f'[bd.batch] item #{item.pk} '
                           f'({item.original_name}) failed: {exc}')

        # ── Breathing gap between items ─────────────────────────────────────
        # Ollama serves one request at a time — this pause lets interactive
        # requests (AJi Hub assist, chat, classify) slip into the queue
        # instead of being starved by a long CV batch.
        import time
        from django.conf import settings as dj_settings
        time.sleep(int(getattr(dj_settings, 'BD_BATCH_ITEM_DELAY', 8)))

    # Batch may have been cancelled/deleted while the last item was running
    try:
        batch.refresh_from_db()
    except CVBatch.DoesNotExist:
        return
    if batch.status == CVBatch.Status.CANCELLED:
        batch.items.filter(status=CVBatchItem.Status.PENDING).update(
            status=CVBatchItem.Status.CANCELLED)
        batch.finished_at = timezone.now()
        batch.save(update_fields=['finished_at'])
        return

    counts = batch.counts
    batch.status = (CVBatch.Status.DONE if counts['done'] else CVBatch.Status.FAILED)
    batch.finished_at = timezone.now()
    batch.save(update_fields=['status', 'finished_at'])
    logger.info(f'[bd.batch] Batch #{batch_id} finished — '
                f"done={counts['done']} failed={counts['failed']}")


@shared_task(name='bd.tasks.process_cv_batch')
def process_cv_batch(batch_id: int):
    _run_batch(batch_id)


def enqueue_batch(batch_id: int) -> str:
    """Queue the batch on Celery; fall back to an in-process daemon thread
    when no broker/worker is reachable. Returns 'celery' or 'thread'."""
    if _HAS_CELERY:
        try:
            process_cv_batch.delay(batch_id)
            return 'celery'
        except Exception as exc:
            logger.warning(f'[bd.batch] Celery unavailable ({exc}) — '
                           f'processing batch #{batch_id} in a thread')
    threading.Thread(target=_run_batch, args=(batch_id,),
                     name=f'cv-batch-{batch_id}', daemon=True).start()
    return 'thread'
