# newspaper_tenders/views.py
# عطاءات الجريدة — dedicated dashboard section.
#
# Data source: tenders.Tender rows that carry a NewspaperTenderMeta record.
# BUSINESS RULE (explicit requirement): only is_aec=True newspaper tenders are
# ever shown here — non-AEC extractions stay in the DB for auditing but never
# appear in the UI. Cards reuse the existing tender-card design; the detail
# link goes to the standard tenders:detail page, which shows newspaper
# provenance for these rows.

import logging
import threading

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.generic import ListView

logger = logging.getLogger('tenders')

from tenders.models import (Tender, TenderStatus,
                            TENDER_CATEGORY_FILTER_CHOICES)
from tenders.views import (annotate_bookmarked_for_user, annotate_new_for_user,
                           get_or_create_profile)

from . import runlock
from .models import (NewspaperEdition, NewspaperEditionFile,
                     NewspaperTenderMeta)
from .sources import NEWSPAPERS


def _apply_edition_filter(qs, ed):
    """Edition dropdown filter. Value is 'YYYY-MM-DD|SOURCE' (date+paper —
    two papers publish on the same date); a bare date is tolerated.
    A value whose date part is not a valid ISO date is ignored (hand-edited
    URLs must never 500)."""
    from datetime import date
    src = ''
    if '|' in ed:
        ed, src = ed.split('|', 1)
    try:
        d = date.fromisoformat(ed.strip())
    except ValueError:
        return qs
    qs = qs.filter(newspaper_meta__edition__edition_date=d)
    if src.strip():
        qs = qs.filter(newspaper_meta__edition__source=src.strip().upper())
    return qs


class NewspaperTenderListView(LoginRequiredMixin, ListView):
    model = Tender
    template_name = 'newspaper/list.html'
    context_object_name = 'tenders'
    paginate_by = 25

    def get_queryset(self):
        today = timezone.now().date()
        user  = self.request.user

        qs = (Tender.objects
              .select_related('country', 'source', 'newspaper_meta',
                              'newspaper_meta__edition')
              .filter(newspaper_meta__isnull=False)
              .filter(is_relevant=True)
              .filter(Q(submission_deadline__gte=today) |
                      Q(submission_deadline__isnull=True)))

        # Business rule: the newspaper section shows AEC tenders only.
        # NEWSPAPER_AEC_ONLY=False relaxes it (demo / review mode) so every
        # extracted tender is visible.
        #
        # "AEC" here is the classification service's is_aec, widened to include
        # tenders whose title says supervision/design — AJi's actual work, which
        # the service marks non-AEC when a tender bundles design with
        # construction. Display only: see tenders/aec_scope.py.
        from tenders.aec_scope import aec_display_q
        if getattr(settings, 'NEWSPAPER_AEC_ONLY', True):
            qs = qs.filter(aec_display_q())

        categories_sel = [c for c in self.request.GET.getlist('category') if c.strip()]
        status = self.request.GET.get('status', '').strip()
        q      = self.request.GET.get('q', '').strip()
        ed     = self.request.GET.get('edition', '').strip()
        paper  = self.request.GET.get('newspaper', '').strip().upper()

        if categories_sel:
            qs = qs.filter(category__in=categories_sel)
        if status:
            qs = qs.filter(status=status)
        if ed:
            qs = _apply_edition_filter(qs, ed)
        if paper:
            qs = qs.filter(newspaper_meta__edition__source=paper)
        if q:
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(title_ar__icontains=q) |
                Q(reference_no__icontains=q) |
                Q(entity__icontains=q) |
                Q(entity_ar__icontains=q) |
                Q(description__icontains=q) |
                Q(description_ar__icontains=q)
            )

        qs = annotate_new_for_user(qs, user).order_by('-newspaper_meta__edition__edition_date',
                                                      'newspaper_meta__page_number',
                                                      '-scraped_at')
        return annotate_bookmarked_for_user(qs, user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # Annotate each row with this user's AEC vote — same mechanism as the
        # main tender list, feeding the same accuracy-feedback report.
        from tenders.models import AECFeedback
        tenders = ctx.get('object_list', [])
        votes = {}
        aec_ids = [t.pk for t in tenders if getattr(t, 'is_aec', False)]
        if aec_ids:
            for fb in AECFeedback.objects.filter(tender_id__in=aec_ids,
                                                 user=self.request.user):
                votes[fb.tender_id] = fb.vote
        for t in tenders:
            t.user_aec_vote = votes.get(t.pk)

        # "Last scan": the most recent newspaper activity of any kind —
        # every run (scheduled, retry sweep, or manual button) touches the
        # edition row, so updated_at is an honest scan timestamp.
        last_edition = NewspaperEdition.objects.order_by('-updated_at').first()

        ctx.update({
            'profile':    get_or_create_profile(self.request.user),
            'categories': TENDER_CATEGORY_FILTER_CHOICES,
            'statuses':   TenderStatus.choices,
            'editions':   NewspaperEdition.objects.order_by('-edition_date')[:30],
            'last_scan':  last_edition,
            'next_run':   _next_scheduled_run(),
            'non_aec_mode': False,
            'newspapers': [(c['code'], c['display_en'])
                           for c in NEWSPAPERS.values()],
            'active_filters': {
                'category': self.request.GET.getlist('category'),
                'status':   self.request.GET.get('status', ''),
                'q':        self.request.GET.get('q', ''),
                'edition':  self.request.GET.get('edition', ''),
                'newspaper': self.request.GET.get('newspaper', ''),
            },
        })
        return ctx


def _next_scheduled_run():
    """
    When the 08:00 run is next due, or None if this process schedules nothing.

    None is the normal answer in a management command or under the test suite,
    so the page must render without it rather than raise.
    """
    try:
        from .apps import next_scheduled_run
        return next_scheduled_run()
    except Exception:                                       # pragma: no cover
        return None


# ── Manual "check today's edition" trigger ──────────────────────────────────
# Same pattern as tenders.views.trigger_all_scrapers: a background thread with
# a busy-lock, so two clicks never start two runs. The pipeline itself is
# idempotent, so even a race would be harmless — the lock just keeps the
# answer honest.
#
# The slot itself lives in runlock.py, not here: the 08:00 scheduler claims the
# same one, and apps.py must not import views to do it.


def _run_newspaper_now():
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(getattr(settings, 'NEWSPAPER_TIMEZONE', 'Asia/Amman'))
        target = timezone.now().astimezone(tz).date()
        from .pipeline import run_all
        results = run_all(target)   # every enabled newspaper
        logger.info(f'[newspaper] manual check finished: {results}')
    except Exception as exc:
        logger.error(f'[newspaper] manual check crashed: {exc}', exc_info=True)
    finally:
        runlock.release()


def _run_edition_retry(edition_pk: int):
    """Thread target: re-run ONE edition (retries failed/pending PDFs,
    re-downloads, picks up newly attached ones; DONE files are skipped)."""
    try:
        edition = NewspaperEdition.objects.get(pk=edition_pk)
        from .pipeline import run_for_date
        result = run_for_date(edition.edition_date, edition.source)
        logger.info(f'[newspaper] retry of {edition.source} '
                    f'{edition.edition_date} finished: {result}')
    except Exception as exc:
        logger.error(f'[newspaper] retry crashed: {exc}', exc_info=True)
    finally:
        runlock.release()


@login_required
def retry_edition(request, pk):
    """POST /newspaper-tenders/runs/<pk>/retry/ — re-run one edition now.
    Wrapped in admin_required at the URL level (Runs page is admin-only).
    Shares the busy-lock with the manual check so only one job runs at a
    time; the pipeline itself is idempotent either way.

    POST force=1 → FULL re-process: every page (including DONE ones) goes
    through the AI again. Existing tenders are not duplicated — the
    deterministic fingerprints match — they just gain whatever is new
    (e.g. the ad photo, when the AI service was upgraded after the edition
    was first processed)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        edition = NewspaperEdition.objects.get(pk=pk)
    except NewspaperEdition.DoesNotExist:
        return JsonResponse({'error': 'Edition not found'}, status=404)

    force = (request.POST.get('force') or '').strip() in ('1', 'true')

    if not runlock.claim():
        return JsonResponse({
            'status': 'busy',
            'message': 'A newspaper job is already running — try again '
                       'in a few minutes.',
        })

    if force:
        # Reset every page so the pipeline reworks the whole edition.
        edition.files.update(status=NewspaperEditionFile.Status.PENDING,
                             error_message='')
        edition.status = NewspaperEdition.Status.PROCESSING
        edition.save(update_fields=['status'])

    threading.Thread(target=_run_edition_retry, args=(edition.pk,),
                     name=f'newspaper-retry-{edition.pk}', daemon=True).start()
    verb = ('Re-processing ALL pages of' if force else 'Retrying')
    return JsonResponse({
        'status': 'started',
        'message': f'{verb} {edition.source_display_en} '
                   f'{edition.edition_date:%d %b %Y} — tenders update as '
                   'each page finishes (existing ones are never duplicated).',
    })


def _wipe_edition(edition) -> dict:
    """
    Throw away everything an edition produced, but KEEP the edition row.

    Deleting the row instead would take the Retry button with it — the button
    lives on the row — and for any date but today there is no way to bring an
    edition back from the UI. So Reset empties rather than deletes.

    Three things need doing by hand, and each one is a trap:

    * The TENDERS do not cascade. NewspaperTenderMeta points AT the tender
      (OneToOneField), so deleting the edition removes meta rows and leaves the
      tenders behind — invisible on the newspaper page, which requires a meta
      row, yet still present in All Tenders with no photo and no provenance.
    * The ad PHOTOS are FileFields. Dropping the row leaves the JPEG on disk as
      untracked media forever.
    * The file_hash must be CLEARED, not just the status. It is the key to the
      SHA256 skip in pipeline._process_file, and a non-empty hash left on a
      reset row can collide with the partial unique index the moment the
      re-read stores it again.
    """
    from tenders.models import Tender

    metas = NewspaperTenderMeta.objects.filter(edition=edition)
    tender_pks = list(metas.values_list('tender_id', flat=True))
    photos = 0

    with transaction.atomic():
        for meta in metas:
            if meta.ad_image:
                meta.ad_image.delete(save=False)
                photos += 1
        # Deleting the tenders cascades their meta rows away with them.
        Tender.objects.filter(pk__in=tender_pks).delete()
        files = edition.files.update(
            status=NewspaperEditionFile.Status.PENDING,
            error_message='',
            file_hash='',
            file_size=0,
            page_count=0,
            tenders_found=0,
            downloaded_at=None,
            processed_at=None,
        )
        edition.processed_at = None
        edition.mark(NewspaperEdition.Status.DISCOVERED,
                     'Reset by an administrator — nothing has been read yet. '
                     'Press Retry to download and read every page again.')

    return {'tenders': len(tender_pks), 'photos': photos, 'files': files}


@login_required
def reset_edition(request, pk):
    """
    POST /newspaper-tenders/runs/<pk>/reset/ — stop, then empty the edition.

    One button, two behaviours, because the safe action depends on whether
    anything is running:

      * A newspaper job IS running -> record a cancel request and stop there.
        Wiping now would race the thread: it would keep saving tenders for the
        page it is part-way through, straight into the rows we just cleared.
        The run halts at the next page boundary, and a second press does the
        wipe. Two presses, but never a half-deleted edition.
      * Nothing is running -> wipe immediately.

    The busy flag is global to the whole newspaper feature rather than per
    edition, so any running newspaper job defers the wipe. That is deliberate:
    the running job might be this very edition, and the flag cannot tell us.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        edition = NewspaperEdition.objects.get(pk=pk)
    except NewspaperEdition.DoesNotExist:
        return JsonResponse({'error': 'Edition not found'}, status=404)

    label = f'{edition.source_display_en} {edition.edition_date:%d %b %Y}'

    if runlock.is_running():
        from .pipeline import request_cancel
        request_cancel(edition.pk)
        logger.info(f'[newspaper] cancel requested for {label} (pk={edition.pk})')
        return JsonResponse({
            'status': 'stopping',
            'message': f'Stopping the run on {label}. It halts after the page '
                       'it is reading now, which can take a few minutes — '
                       'press Reset again once it has stopped to clear the '
                       'edition.',
        })

    result = _wipe_edition(edition)
    logger.info(f'[newspaper] reset {label} (pk={edition.pk}): '
                f'{result["tenders"]} tender(s), {result["photos"]} photo(s), '
                f'{result["files"]} page(s) back to pending')
    return JsonResponse({
        'status': 'reset',
        'message': f'{label} cleared — {result["tenders"]} tender(s) and '
                   f'{result["photos"]} photo(s) deleted, {result["files"]} '
                   'page(s) queued again. Press Retry to read it from scratch.',
    })


@login_required
def trigger_newspaper_check(request):
    """POST /newspaper-tenders/run/ — start checking today's Al-Rai edition."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not getattr(settings, 'NEWSPAPER_ENABLED', True):
        return JsonResponse({'status': 'disabled',
                             'message': 'Newspaper Tenders is disabled (NEWSPAPER_ENABLED).'})

    if not runlock.claim():
        return JsonResponse({
            'status': 'busy',
            'message': "A newspaper check is already running — tenders "
                       "appear as each PDF finishes.",
        })

    threading.Thread(target=_run_newspaper_now,
                     name='newspaper-manual-run', daemon=True).start()
    # Name the papers dynamically from the registry — never hardcode the list
    from .sources import enabled_sources
    papers = ', '.join(c['display_en'] for c in enabled_sources())
    return JsonResponse({'status': 'started',
                         'message': f"Checking today's newspaper editions ({papers}) — "
                                    "new tenders will appear here as pages are read."})


class NonAECNewspaperListView(NewspaperTenderListView):
    """
    System Dashboard audit view: newspaper tenders the AI classified as
    NON-AEC. Wrapped in admin_required at the URL level — regular users
    never see it. Uses the same card design as عطاءات الجريدة.
    """

    def get_queryset(self):
        today = timezone.now().date()
        user  = self.request.user

        qs = (Tender.objects
              .select_related('country', 'source', 'newspaper_meta',
                              'newspaper_meta__edition')
              .filter(newspaper_meta__isnull=False, is_aec=False)
              .filter(Q(submission_deadline__gte=today) |
                      Q(submission_deadline__isnull=True)))

        q     = self.request.GET.get('q', '').strip()
        ed    = self.request.GET.get('edition', '').strip()
        paper = self.request.GET.get('newspaper', '').strip().upper()
        categories_sel = [c for c in self.request.GET.getlist('category') if c.strip()]
        status = self.request.GET.get('status', '').strip()
        if ed:
            qs = _apply_edition_filter(qs, ed)
        if paper:
            qs = qs.filter(newspaper_meta__edition__source=paper)
        if categories_sel:
            qs = qs.filter(category__in=categories_sel)
        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(title_ar__icontains=q) |
                           Q(reference_no__icontains=q) |
                           Q(entity__icontains=q) | Q(entity_ar__icontains=q))

        qs = annotate_new_for_user(qs, user).order_by(
            '-newspaper_meta__edition__edition_date',
            'newspaper_meta__page_number', '-scraped_at')
        return annotate_bookmarked_for_user(qs, user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['non_aec_mode'] = True
        ctx['it_section'] = 'newspaper_non_aec'
        return ctx
