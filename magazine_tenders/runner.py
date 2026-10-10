# magazine_tenders/runner.py
"""
Scrape → save → classify, one magazine at a time.

This mirrors `tenders.apps._scrape_one_source` step for step, because that is
the proven runner for the 80 existing web scrapers: create a run row, call the
scraper inside `tolerant_console()`, save every item through
`tenders.services.save_tender_data`, then call the batch AI step. The only
additions are the `MagazineTenderMeta` provenance row and the `MagazineRun`
counters.

Two invariants worth stating because breaking either would be silent:

* The scraper never persists anything — if a scraper is found writing to the
  database, that is the bug, not this file.
* Classification happens in exactly one place: inside `save_tender_data`, which
  calls the shared AJiAI client. Nothing here inspects a title to decide whether
  a tender is AEC.
"""
import hashlib
import logging
import re
import threading
import traceback
import unicodedata

from django.utils import timezone

from tenders.models import Tender
from tenders.scrapers.base import tolerant_console
from tenders.services import save_tender_data

from . import dedupe
from . import freshness
from . import summaries

from . import sources
from .anchor import ensure_countries, get_magazine_source
from . import quarters
from .models import MagazineRun, MagazineTenderMeta
from .registry import get_magazine_scraper

logger = logging.getLogger('tenders')


# ── Concurrency guard ─────────────────────────────────────────────────────────
# One magazine sweep at a time. The runs are idempotent (stable external_id), so
# a race would be harmless — the lock exists so the UI can answer "already
# running" honestly instead of starting a second sweep behind the user's back.
_run_lock = threading.Lock()
_running = False


def is_running() -> bool:
    return _running


# ── external_id ───────────────────────────────────────────────────────────────
_ARABIC_DIACRITICS = re.compile(r'[ً-ٰٟۖ-ۭ]')
_A2L = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')


def _normalize_for_id(text: str) -> str:
    """
    Fold a string to a stable comparison key.

    The same announcement must hash identically on every run, or
    `save_tender_data` would insert a duplicate instead of updating. Arabic
    diacritics and Arabic-Indic digits are the two things that actually vary
    between renderings of the same title, so both are normalised away.
    """
    s = unicodedata.normalize('NFKC', (text or '')).translate(_A2L)
    s = _ARABIC_DIACRITICS.sub('', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()


def make_external_id(title: str, entity: str, deadline, prefix: str) -> str:
    """
    Fallback id for a site that exposes no id of its own.

    Preference order is documented in the plan: the site's own item id or
    detail-URL slug first, then the reference number, and only then this hash.
    A scraper that can supply either of the first two should set `external_id`
    itself and never reach this.
    """
    basis = '|'.join((
        _normalize_for_id(title),
        _normalize_for_id(entity),
        deadline.isoformat() if deadline else '',
    ))
    digest = hashlib.sha256(basis.encode('utf-8')).hexdigest()[:56]
    return f'{prefix}-{digest}'


# ── One magazine ──────────────────────────────────────────────────────────────

def run_one(code: str, trigger: str = MagazineRun.Trigger.MANUAL) -> MagazineRun:
    """
    Scrape one magazine and persist whatever it returns.

    Never raises: a dead site marks its own run FAILED and returns, so one
    broken magazine cannot abandon the rest of a sweep.
    """
    code = (code or '').strip().upper()
    cfg = sources.get_source_config(code)
    run = MagazineRun.objects.create(magazine=code, trigger=trigger)

    try:
        source = get_magazine_source(code)

        scraper = get_magazine_scraper(code)
        scraper.source = source          # the anchor row is inactive; scrapers that
                                         # query is_active=True must not be used here
        with tolerant_console():
            tender_list = scraper.scrape() or []

        run.found = len(tender_list)

        # A magazine may cover several countries (Gulf Construction spans six).
        # save_tender_data resolves td.country_code against CountryConfig and
        # falls back to the SOURCE's country when the row is missing — which
        # would file a Qatar notice under Bahrain. Create the rows first.
        ensure_countries(getattr(td, 'country_code', '') for td in tender_list)

        new_count = updated_count = skipped = 0
        for td in tender_list:
            # Layer two of the freshness window: the backstop, for sources the
            # publisher cannot filter. DevelopmentAid is the one that matters —
            # its API has no date field and sorts by last-modified, so a 2017
            # procurement plan re-edited last week arrives looking recent.
            #
            # Dropped HERE, before save_tender_data, because that is where
            # classification happens: a stale item must not cost an AI call.
            # Undated items are always kept (see freshness.py).
            if freshness.is_stale(getattr(td, 'publication_date', None)):
                skipped += 1
                logger.info(f'[magazine:{code}] stale, outside the '
                            f'{freshness.max_age_months()}-month window: '
                            f'{td.title[:80]}')
                continue

            # Defaults the Dashboard depends on. DashboardView filters on
            # is_relevant + is_aec + status='OPEN', so a magazine tender that
            # left any of these unset would be classified and then invisible.
            if not getattr(td, 'status', '') or td.status == 'UNKNOWN':
                td.status = 'OPEN'
            if not getattr(td, 'country_code', ''):
                td.country_code = cfg.get('country_code', 'JO')
            if not getattr(td, 'external_id', ''):
                td.external_id = make_external_id(
                    td.title, td.entity, td.submission_deadline,
                    cfg.get('id_prefix', f'mag-{code.lower()}'),
                )
            # The AI is the AEC gate. The keyword relevance filter would hide
            # announcements whose wording misses a keyword, and is_relevant=True
            # is required for the Dashboard to show them at all.
            td.force_relevant = True

            created, updated = save_tender_data(td, source)   # ← classification happens here

            tender = Tender.objects.filter(source=source,
                                           external_id=td.external_id).first()
            if tender is None:
                # save_tender_data declined it — e.g. the deadline has passed.
                # Same policy every tender in the system obeys.
                skipped += 1
                logger.info(f'[magazine:{code}] skipped by save policy: {td.title[:80]}')
                continue

            if created:
                new_count += 1
            elif updated:
                updated_count += 1

            meta, _ = MagazineTenderMeta.objects.update_or_create(
                tender=tender,
                defaults={
                    'magazine':    code,
                    'run':         run,
                    'listing_url': getattr(td, 'source_url', '') or '',
                    'page_number': getattr(td, 'page_number', 1) or 1,
                    'raw_snippet': (getattr(td, 'raw_snippet', '') or ''
                                    )[:MagazineTenderMeta.RAW_SNIPPET_CAP],
                    'image_url':   (getattr(td, 'image_url', '') or '')[:2000],
                    'image_url_large': (getattr(td, 'image_url_large', '') or '')[:2000],
                    # For a forthcoming project, when the work is expected.
                    # The deadline on a forecast notice is the donor's own
                    # estimate of publication, not a bid date, which is why it
                    # is the right fallback here and nowhere else.
                    'expected_quarter': quarters.quarter_key(
                        getattr(td, 'description', '') or '',
                        getattr(td, 'submission_deadline', None)),
                },
            )

            # Is this the same story another source already carried? Done
            # HERE, after the meta row exists, because the comparison is
            # against magazine rows and this one is not one until then.
            # A duplicate is marked, never dropped — see dedupe.py.
            try:
                dedupe.link_if_duplicate(tender)
            except Exception as exc:
                # Deduplication is a nicety. A failure in it must not cost
                # the run the article it just saved.
                logger.warning(f'[magazine:{code}] dedupe failed for '
                               f'#{tender.pk}: {exc}')

            # The card's summary is NOT asked for here. Asking inline, right
            # after the classifier's call for the same row, turned a sweep
            # into a burst of back-to-back requests the AI host could not
            # take. run_all asks for a paced batch once the sweep is over.

        # Batch classification step for the OPENAI/OLLAMA services. A no-op
        # under AJIBRAIN, which classifies inline inside save_tender_data.
        from tenders.apps import _run_ai_enrichment
        _run_ai_enrichment(source)

        saved_ids = MagazineTenderMeta.objects.filter(run=run).values_list('tender_id', flat=True)
        run.aec     = Tender.objects.filter(id__in=saved_ids, is_aec=True).count()
        run.non_aec = Tender.objects.filter(id__in=saved_ids, is_aec=False).count()

        source.last_scraped_at = timezone.now()
        source.save(update_fields=['last_scraped_at'])

        run.saved_new     = new_count
        run.saved_updated = updated_count
        run.skipped       = skipped
        run.status        = MagazineRun.Status.SUCCESS
        run.finished_at   = timezone.now()
        run.save()

        logger.info(f'[magazine:{code}] found={run.found} new={new_count} '
                    f'updated={updated_count} skipped={skipped} '
                    f'aec={run.aec} non_aec={run.non_aec}')
        return run

    except Exception as exc:
        logger.error(f'[magazine:{code}] FAILED: {exc}\n{traceback.format_exc()}')
        run.status        = MagazineRun.Status.FAILED
        run.error_message = str(exc)[:2000]
        run.finished_at   = timezone.now()
        run.save()
        return run


# ── Every magazine ────────────────────────────────────────────────────────────

def run_all(trigger: str = MagazineRun.Trigger.MANUAL) -> dict:
    """
    Run every enabled magazine, sequentially. Returns {code: MagazineRun}.

    Sequential on purpose: these are polite scrapers against small publisher
    sites, and the existing scraper runner is sequential too.
    """
    global _running

    if not _run_lock.acquire(blocking=False):
        logger.info('[magazine] a sweep is already running — skipped')
        return {}


    _running = True
    results = {}
    try:
        configured = sources.enabled_sources()
        if not configured:
            logger.info('[magazine] no magazines configured — nothing to do')
            return {}

        logger.info(f'[magazine] sweep starting — {len(configured)} magazine(s), '
                    f'trigger={trigger}')
        for cfg in configured:
            results[cfg['code']] = run_one(cfg['code'], trigger=trigger)

        # The summaries, after the sweep and its classification calls are
        # done: a capped batch, one article at a time with a pause between,
        # stopping at the first failure. Whatever is left waits for the next
        # sweep or for `manage.py summarise_news`. See summaries.py.
        try:
            summaries.summarise_backlog()
        except Exception as exc:
            logger.warning(f'[magazine] summary batch failed: {exc}')
        return results
    finally:
        _running = False
        _run_lock.release()
