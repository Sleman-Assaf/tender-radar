# newspaper_tenders/pipeline.py
# Orchestration of one Newspaper Tenders run — عطاءات الجريدة.
#
# THE most important property of this module: it produces tenders through the
# EXISTING TenderRadar machinery, not a parallel one.
#   * tenders are TenderData objects saved via tenders.services.save_tender_data
#     (same past-deadline policy, same country/currency resolution, same
#     content-hash update semantics, same (source, external_id) dedup);
#   * classification/translation arrive from the AJiAI Newspaper API, which
#     internally runs the same run_classify_tender / run_translate_title code
#     as /api/classify-tender/ and /api/translate-title/ — so enrichment is
#     not re-run here (save_tender_data(..., enrich=False)) and the result is
#     applied exactly the way tenders/ajibrain_client.py would apply it.
#
# Idempotency:
#   * edition:  unique (source, edition_date)
#   * file:     unique SHA256 file_hash (+ unique (edition, pdf_url))
#   * tender:   deterministic external_id = sha256(title|entity|deadline)
#               under the dedicated (inactive, UI-hidden) ScraperSource;
#               a list ad (one ad, many tender numbers) merges into ONE row
#               via merge_list_ads() with the numbers kept in the description.

import hashlib
import logging
import re
import threading
from datetime import date, datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tenders.models import CountryConfig, ScraperSource, Tender
from tenders.scrapers.base import TenderData
from tenders.services import save_tender_data

from . import ai_client, alrai, intake, page_gate, sources
from .models import NewspaperEdition, NewspaperEditionFile, NewspaperTenderMeta

logger = logging.getLogger('tenders')

# Kept for backward compatibility (tests, admin filters). scraper_class
# sentinels are deliberately NOT registered in tenders/scrapers/__init__.py —
# newspapers are not scrapers. The Scrapers UI hides NEWSPAPER_* rows.
NEWSPAPER_SCRAPER_CLASS = 'NEWSPAPER_ALRAI'

VALID_CATEGORIES = {'BUILDING', 'INFRA', 'HEALTH', 'OTHER'}

_WS_RE = re.compile(r'\s+')
_DIACRITICS_RE = re.compile(r'[ً-ْـ]')


def get_newspaper_source(source_code: str = 'ALRAI') -> ScraperSource:
    """The newspaper's dedicated ScraperSource row (created on first use,
    is_active=False so run_all_scrapers never picks it up)."""
    cfg = sources.get_source_config(source_code)
    country, _ = CountryConfig.objects.get_or_create(
        code='JO',
        defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                  'flag_emoji': '🇯🇴', 'currency': 'JOD'},
    )
    source, created = ScraperSource.objects.get_or_create(
        scraper_class=cfg['scraper_class'],
        defaults={
            'name':        cfg['name'],
            'name_ar':     cfg['name_ar'],
            'country':     country,
            'base_url':    cfg['base_url'],
            'is_active':   False,   # never picked up by run_all_scrapers
            'description': 'Internal source row for the Newspaper Tenders '
                           'feature. Managed by the newspaper_tenders app — '
                           'not a scraper.',
        },
    )
    if created:
        logger.info(f'[newspaper] created dedicated ScraperSource row '
                    f'({source_code})')
    return source


def _normalize_for_id(text: str) -> str:
    text = _DIACRITICS_RE.sub('', text or '')
    return _WS_RE.sub(' ', text).strip().lower()


_ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')


def _normalize_ref_for_id(reference_no: str) -> str:
    """Reference numbers are essentially numeric (2026/54, ٢٠٢٦/٢٠٦٤, 'عطاء
    رقم 12/2026') but arrive with varying prefixes and digit scripts. Reduce
    to the digit sequence so the same tender number always yields the same
    key regardless of wording; fall back to normalized text for the rare
    ref with no digits."""
    ref = (reference_no or '').translate(_ARABIC_DIGITS)
    digits = re.sub(r'\D', '', ref)
    return digits or _normalize_for_id(ref)


def make_external_id(title: str, entity: str, deadline,
                     reference_no: str = '', prefix: str = 'alrai') -> str:
    """
    Deterministic tender identity: normalized title + entity + deadline.
    Deliberately independent of the PDF URL (URLs change; content identity
    should not). Same ad on another page / in a repeat edition → same id →
    existing (source, external_id) dedup handles it.

    reference_no is NOT part of the identity the pipeline uses (it passes
    the default '') — a multi-tender list ad is merged into ONE row by
    merge_list_ads() before saving, per the 24 Aug 2026 management decision.
    The parameter survives for migration 0003, which stamped ref-included
    ids before that decision (0004 recomputes them back).
    """
    key = '|'.join([
        _normalize_for_id(title),
        _normalize_for_id(entity),
        str(deadline or ''),
        _normalize_ref_for_id(reference_no),
    ])
    return f'{prefix}-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:56]


# Fabricated-tender detection. The AJiAI extraction invents placeholder tenders
# from non-tender content. Court-notice pages are caught by the crop OCR check;
# the other pattern is generic TEMPLATE companies named after a foreign / Gulf
# capital in parentheses — "شركة (دبي) للمقاولات", "شركة (الرياض) للتقنية" — a
# form no real Jordanian tender issuer carries. The model also re-uses a few
# invented names (e.g. شركة البتراء للخدمات الطبية); those are listed in the
# configurable NEWSPAPER_FAKE_ENTITIES setting.
_FABRICATED_CITY_RE = re.compile(
    r'\(\s*(أبوظبي|دبي|الشارقة|عجمان|الرياض|جدة|مكة|الدمام|الكويت|مسقط|'
    r'الدوحة|المنامة|القاهرة|الإسكندرية|بيروت|دمشق|بغداد|صنعاء)\s*\)')


def is_fabricated_entity(entity: str) -> bool:
    """
    True when `entity` matches a known AJiAI hallucination pattern: a template
    company named after a parenthesised foreign city, or a name the model is
    known to invent (NEWSPAPER_FAKE_ENTITIES). These rows are not real tenders.
    """
    e = (entity or '').strip()
    if not e:
        return False
    if _FABRICATED_CITY_RE.search(e):
        return True
    for name in getattr(settings, 'NEWSPAPER_FAKE_ENTITIES', ()):
        if name and name.strip() and name.strip() in e:
            return True
    return False


# How many days two deadlines may differ and still be treated as the same
# ref-less announcement (OCR / publication-day variance across newspapers).
DEDUP_DEADLINE_DAYS = 3


def cross_source_duplicate(cleaned: dict, source):
    """
    Return an existing newspaper Tender from a DIFFERENT source that is the same
    tender as `cleaned`, or None. The same government tender is advertised in
    several newspapers; the per-source unique key cannot see across papers, so
    this is what stops each paper's copy becoming a separate row.

    A duplicate must match on ALL of: same ENTITY, same TITLE, same REFERENCE
    (or both ref-less), and deadlines within DEDUP_DEADLINE_DAYS. This is
    deliberately conservative — over-merging deletes a real tender, which is
    far worse than leaving a duplicate:

        * ENTITY alone is not enough — one body runs many tenders;
        * REFERENCE alone is not enough — two bodies reuse the same annual
          number (٢٥/٢٠٢٦ appears for both a hospital and a council), and one
          body's sub-tender numbers reduce to the same digits for DIFFERENT
          tenders (12/فرعية "postage stamps" vs 12/ل/فرعية "laser printers");
        * so the TITLE must also agree. The same advert reprinted in another
          paper keeps its entity, title, number and deadline; a genuinely
          different tender differs in at least one.

    The cost is missing a duplicate whose headline the second paper reworded —
    a leftover row, never a deleted tender.
    """
    entity_n = _normalize_for_id(cleaned.get('entity') or '')
    title_n  = _normalize_for_id(cleaned.get('title') or '')
    if not entity_n or not title_n:
        return None                      # nothing solid to anchor on — never merge
    ref_n    = _normalize_ref_for_id(cleaned.get('reference_no') or '')
    deadline = cleaned.get('submission_deadline')

    qs = (Tender.objects
          .filter(newspaper_meta__isnull=False)
          .exclude(source=source))
    if deadline:
        from datetime import timedelta
        window = timedelta(days=DEDUP_DEADLINE_DAYS)
        qs = qs.filter(Q(submission_deadline__isnull=True) |
                       Q(submission_deadline__range=(deadline - window,
                                                     deadline + window)))

    for other in qs.only('id', 'entity', 'reference_no', 'title',
                         'submission_deadline', 'source_id'):
        if _normalize_for_id(other.entity or '') != entity_n:
            continue
        if _normalize_for_id(other.title or '') != title_n:
            continue                     # different tender text → keep both
        if _normalize_ref_for_id(other.reference_no or '') == ref_n:
            return other                 # same entity + title + reference (or both ref-less)
    return None


def merge_list_ads(cleaned_tenders: list) -> list:
    """
    One newspaper ad frequently lists MANY tenders sharing title/entity/
    deadline and differing only by tender number (NEPCO: 36 numbers in one
    ad; Income Tax Dept: 35 invitations). Management decision (24 Aug 2026):
    such an ad is ONE tender row — the individual numbers are preserved in
    the description, never as separate look-alike rows.

    Groups by the same normalized (title|entity|deadline) key external_id
    uses (reference_no deliberately excluded here AND at the call site's
    make_external_id call), so grouping and dedup always agree.
    """
    groups, order = {}, []
    for c in cleaned_tenders:
        key = (_normalize_for_id(c['title']), _normalize_for_id(c['entity']),
               str(c['submission_deadline'] or ''))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    merged = []
    for key in order:
        items = groups[key]
        if len(items) == 1:
            merged.append(items[0])
            continue
        base = dict(items[0])
        refs = [i['reference_no'].strip() for i in items
                if (i['reference_no'] or '').strip()]
        lines = []
        for i in items:
            ref  = (i['reference_no'] or '').strip()
            desc = (i['description_ar'] or i['description'] or '').strip()
            line = ' — '.join(x for x in (ref, desc) if x)
            if line and line not in lines:
                lines.append(line)
        listing = (f'إعلان واحد يتضمن {len(items)} عطاء — '
                   f'one ad listing {len(items)} tenders:\n'
                   + '\n'.join(f'• {l}' for l in lines))
        base['description']    = listing
        base['description_ar'] = listing
        if refs:
            base['reference_no'] = (refs[0] if len(refs) == 1 else
                                    f'{refs[0]} … {refs[-1]} ({len(refs)} عطاء)')[:200]

        # Photo for a merged list ad. A single crop cannot represent many
        # tenders, and the AI's per-sub-item crops for a bundled ad are
        # unreliable — it may hand back a NEIGHBOURING ad's photo (seen live:
        # the Income-&-Sales-Tax 35-tender list carried a court-notice crop).
        # So keep a crop only when every sub-item that has one agrees on the
        # SAME image (a single whole-ad crop); otherwise attach none. A wrong
        # photo is worse than no photo — the merged row then shows text only.
        crops = {i['ad_image_b64'] for i in items if i.get('ad_image_b64')}
        base['ad_image_b64'] = crops.pop() if len(crops) == 1 else ''

        merged.append(base)
    return merged


# Fields the zoom re-read may correct. The photo and page number always stay
# from the page pass (the crop IS the photo; its position never changes).
_REREAD_FIELDS = ('title', 'title_ar', 'entity', 'entity_ar', 'reference_no',
                  'description', 'description_ar', 'submission_deadline',
                  'opening_date')


def _zoom_reread(rec: dict, *, edition_date: str, run_id: str) -> dict:
    """
    Second AI pass at full resolution: the page-level read can misread a
    decorative headline (استئجار مبنى بديل → استشاري مبدئي لبدائل, live on
    24 Aug 2026) because one ad is a small part of the page image. The ad's
    own high-res crop — which the AI already returned for the photo — is sent
    back to the SAME extract endpoint (it accepts image uploads; zero AJiAI
    changes) and its answer overrides the page-pass fields. Any failure keeps
    the page-pass record — this pass can only improve, never lose, a tender.
    """
    img = rec.get('ad_image_b64') or ''
    if not img:
        return rec
    try:
        items = ai_client.extract_from_image(
            img, edition_date=edition_date,
            page_number=rec.get('page_number') or 1)
    except ai_client.AIServiceError as exc:
        logger.warning(f'[newspaper:{run_id}] zoom re-read failed '
                       f'({exc}) — keeping page-pass record')
        return rec
    if len(items) != 1:
        # 0 = crop unreadable; >1 = a list ad (already merged into one row) —
        # either way the page-pass record stands.
        return rec
    reread = validate_ai_tender(items[0])
    if reread is None:
        return rec
    merged = dict(rec)
    changed = [f for f in _REREAD_FIELDS
               if reread.get(f) and reread.get(f) != rec.get(f)]
    for f in changed:
        merged[f] = reread[f]
    if changed:
        logger.info(f'[newspaper:{run_id}] zoom re-read corrected '
                    f'{changed}: {rec.get("title", "")[:45]!r} -> '
                    f'{merged["title"][:45]!r}')
    return merged


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ── Non-tender ad rejection (deterministic, runs AFTER the AI) ─────────────────
# Newspapers print AUCTIONS and ASSET SALES beside real tenders, and the AI
# vision pass sometimes returns those as "tenders". This gate drops them before
# they are saved. It does NOT touch AI classification (is_aec / category) — it
# only decides whether an extracted ad is a procurement tender at all.
#
# Precision first — it must never drop a real tender — informed by an
# adversarial review of the Jordanian-newspaper ad taxonomy:
#
#   * KEEP-FIRST: any title that itself announces a procurement (عطاء /
#     مناقصة) OR describes buying (توريد supply / تركيب install / شراء
#     purchase) is always kept. This protects supply tenders whose text
#     contains a sale noun — e.g. "توريد أجهزة بيع التذاكر" (ticket-VENDING
#     machines), "نقاط بيع" (POS) — and asset-disposal tenders issued AS a
#     عطاء, which are genuine tender processes.
#   * AUCTIONS (مزاد / مزادة / مزايدة / مزاودة) are rejected — EXCEPT a reverse
#     auction (مزاد عكسي / المزايدة العكسية), which is a BUYING method and is
#     kept.
#   * SALE word بيع is matched as a token allowing Arabic proclitics
#     (للبيع / البيع / لبيع), so private "for sale" ads are caught, while
#     المبيعات ("Income & Sales Tax Dept", a real tender issuer) never matches
#     — its بيع is followed by ات, and stripping ال leaves مبيعات ≠ بيع.
#
# Rent words (استئجار / إيجار / تأجير) are DELIBERATELY excluded: a public body
# routinely tenders to RENT a building or vehicles ("طلب استئجار مبنى بديل"),
# a genuine tender. The occasional private rent-OUT ad is accepted as harmless
# rather than risk dropping a real rent-in tender.
#
# Text is normalised first (tatweel + diacritics stripped) so OCR/typesetting
# elongation like "مـزاد" cannot slip through.

_TATWEEL   = 'ـ'
_HARAKAT   = re.compile('[ً-ْٰ]')
_ARABIC_RE = re.compile(r'[؀-ۿ]+')

_AUCTION_ROOTS   = ('مزاد', 'مزايد', 'مزاود')
_SALE_ROOTS      = ('بيع',)
_KEEP_MARKERS    = ('عطاء', 'مناقصة', 'مناقصه', 'توريد', 'تركيب', 'شراء')
_REVERSE_AUCTION = ('عكسي', 'العكسية')
# Longest first, so "وال" is stripped before "و".
_PROCLITICS      = ('وال', 'فال', 'بال', 'كال', 'لل', 'ال', 'و', 'ف', 'ب', 'ك', 'ل')


def _normalize_ar(text: str) -> str:
    t = (text or '').replace(_TATWEEL, '')
    t = _HARAKAT.sub('', t)
    return ' '.join(t.split())


def _is_sale_token(token: str) -> bool:
    """
    True when `token` is a sale root, with or without an Arabic proclitic.

    Checked BOTH ways on purpose: the sale root بيع itself begins with ب —
    one of the proclitics — so stripping first would turn بيع into يع and miss
    the bare word. The bare token is tested first, then the proclitic-stripped
    form (which is what catches للبيع / البيع / لبيع). المبيعات survives both:
    it is not بيع, and stripping ال leaves مبيعات ≠ بيع.
    """
    if token in _SALE_ROOTS:
        return True
    for p in _PROCLITICS:
        if token.startswith(p) and len(token) > len(p) + 1:
            return token[len(p):] in _SALE_ROOTS
    return False


def non_tender_reason(title: str) -> str:
    """
    Return a short reason ('auction' / 'sale') when `title` is a non-tender
    advertisement that must not be saved, or '' when it is (or may be) a tender.
    See the block comment above for the full rationale and safety guarantees.
    """
    t = _normalize_ar(title)
    if any(m in t for m in _KEEP_MARKERS):
        return ''
    if any(r in t for r in _AUCTION_ROOTS) and not any(x in t for x in _REVERSE_AUCTION):
        return 'auction'
    if any(_is_sale_token(tok) for tok in _ARABIC_RE.findall(t)):
        return 'sale'
    return ''


def validate_ai_tender(raw: dict) -> dict | None:
    """
    Final deterministic gate on one AI-extracted tender (protects the
    production DB from bad AI output). Returns cleaned dict or None.

    is_aec/category are NOT expected here: classification is done by the
    standard TenderRadar enrichment after save, exactly like scraped tenders.
    If the AI service sends a category anyway it is only a provisional guess
    (same role as the scrapers' keyword guess) and is validated below.
    """
    if not isinstance(raw, dict):
        return None
    title = ' '.join(str(raw.get('title') or '').split())
    if not title:
        return None
    category = str(raw.get('category') or 'OTHER').strip().upper()
    if category not in VALID_CATEGORIES:
        category = 'OTHER'
    confidence = raw.get('confidence')
    if confidence is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None
    try:
        page_number = max(1, int(raw.get('page_number') or 1))
    except (TypeError, ValueError):
        page_number = 1

    return {
        'title':               title,
        'title_ar':            str(raw.get('title_ar') or '').strip(),
        'entity':              str(raw.get('entity') or '').strip()[:5000],
        'entity_ar':           str(raw.get('entity_ar') or '').strip()[:5000],
        'description':         str(raw.get('description') or '').strip(),
        'description_ar':      str(raw.get('description_ar') or '').strip(),
        'reference_no':        str(raw.get('reference_no') or '').strip()[:200],
        'submission_deadline': _parse_iso_date(raw.get('submission_deadline')),
        'opening_date':        _parse_iso_date(raw.get('opening_date')),
        'category':            category,
        'confidence':          confidence,
        'page_number':         page_number,
        'raw_text':            str(raw.get('raw_text') or '').strip(),
        # The ad photo (base64 JPEG cropped by the AI service). MUST survive
        # this whitelist — dropping it here was the bug that left every
        # pipeline-saved tender photoless while direct API tests had photos.
        'ad_image_b64':        str(raw.get('ad_image_b64') or ''),
    }


# ── Cancelling a run ─────────────────────────────────────────────────────────
# A run is a daemon thread inside the web process, and Python cannot kill a
# thread. So stopping is COOPERATIVE: the Stop/Reset endpoint records the
# edition here and the per-PDF loop in run_for_date() checks it between pages.
#
# The consequence is honest and worth stating in the UI: a stop lands at the
# next page boundary, not instantly. With a page costing minutes of AI time,
# that can mean a wait.
#
# Process-local by design, exactly like the busy flag in views.py. The thread
# that must see the flag lives in this process, so a database column would buy
# nothing and cost a write per page.
_cancel_lock = threading.Lock()
_cancel_requested: set = set()


def request_cancel(edition_pk: int) -> None:
    """Ask the run working on this edition to stop at the next page."""
    with _cancel_lock:
        _cancel_requested.add(int(edition_pk))


def cancel_requested(edition_pk: int) -> bool:
    with _cancel_lock:
        return int(edition_pk) in _cancel_requested


def clear_cancel(edition_pk: int) -> None:
    """
    Forget a cancel request.

    Called at the START of every run as well as the end: a request that arrives
    after the loop has already finished would otherwise sit in the set and kill
    the NEXT run of that edition before it read a single page.
    """
    with _cancel_lock:
        _cancel_requested.discard(int(edition_pk))


class RunCounters:
    def __init__(self):
        self.cancelled = False     # stopped by an administrator mid-run
        self.pdfs_discovered = 0
        self.pdfs_processed = 0
        self.pdfs_skipped = 0
        self.court_pages_skipped = 0   # headed إعلانات قضائية — never sent to the AI
        self.pdfs_failed = 0
        self.ai_tenders = 0
        self.rejected_invalid = 0
        self.rejected_non_tender = 0
        self.hallucinated_rejected = 0
        self.photos_dropped = 0
        self.dropped_non_aec_no_photo = 0
        self.dropped_ids = set()   # external_ids dropped this run (whole-row),
        # so the same tender in a later file of the edition is not re-created.
        self.cross_source_dupes = 0
        self.aec = 0
        self.non_aec = 0
        self.duplicates = 0
        self.saved = 0

    def summary(self, edition_date, status):
        return (f'Newspaper run: Edition: {edition_date} | '
                f'PDFs discovered: {self.pdfs_discovered} | '
                f'Processed: {self.pdfs_processed} | '
                f'Skipped: {self.pdfs_skipped} | '
                f'Court pages skipped: {self.court_pages_skipped} | '
                f'Failed: {self.pdfs_failed} | '
                f'AI tenders: {self.ai_tenders} | '
                f'Invalid rejected: {self.rejected_invalid} | '
                f'Non-tender rejected: {self.rejected_non_tender} | '
                f'Fabricated rejected: {self.hallucinated_rejected} | '
                f'Non-AEC dropped (no photo): {self.dropped_non_aec_no_photo} | '
                f'Cross-source dupes: {self.cross_source_dupes} | '
                f'AEC: {self.aec} | Non-AEC: {self.non_aec} | '
                f'Duplicates: {self.duplicates} | Saved: {self.saved} | '
                f'Status: {status}')


def run_all(target: date) -> dict:
    """Run every enabled newspaper for one date. Failures in one newspaper
    never stop the others."""
    results = {}
    for cfg in sources.enabled_sources():
        try:
            results[cfg['code']] = run_for_date(target, cfg['code'])
        except Exception as exc:
            logger.error(f'[newspaper] {cfg["code"]} run crashed: {exc}',
                         exc_info=True)
            results[cfg['code']] = 'crashed'
    return results


def run_for_date(target: date, source_code: str = 'ALRAI') -> str:
    """
    Complete newspaper processing for one date + newspaper. Idempotent —
    safe to call any number of times. Returns a short status string.
    """
    cfg = sources.get_source_config(source_code)
    run_id = f'{source_code}-{target.isoformat()}-{timezone.now():%H%M%S}'
    logger.info(f'[newspaper:{run_id}] run started for {target}')
    counters = RunCounters()

    edition, _ = NewspaperEdition.objects.get_or_create(
        source=source_code, edition_date=target)

    if edition.status == NewspaperEdition.Status.DONE:
        # Even a DONE edition gets a light re-check: Al-Rai sometimes appends
        # PDFs later in the day. New URLs become new file rows; existing
        # hashes are skipped, so this stays cheap and duplicate-free.
        logger.info(f'[newspaper:{run_id}] edition already DONE — re-checking for new PDFs')

    # ── Discovery ───────────────────────────────────────────────────────────
    try:
        result = sources.discover_for(cfg, target)
    except Exception as exc:
        edition.mark(NewspaperEdition.Status.FAILED, f'Discovery failed: {exc}')
        logger.error(f'[newspaper:{run_id}] discovery failed: {exc}')
        return 'discovery-failed'

    if result is None:
        logger.info(f'[newspaper:{run_id}] No {cfg["display_en"]} edition found '
                    f'for {target}. This is a normal outcome (weekend / not '
                    'yet published).')
        if edition.status not in (NewspaperEdition.Status.DONE,):
            edition.mark(NewspaperEdition.Status.DISCOVERED)
        return 'no-edition'

    edition.article_url = result.article.url
    if not edition.discovered_at:
        edition.discovered_at = timezone.now()

    if not result.pdf_urls:
        edition.mark(NewspaperEdition.Status.PENDING_PDFS)
        logger.info(f'[newspaper:{run_id}] article exists but no PDFs attached '
                    'yet — edition marked PENDING_PDFS, will retry later.')
        return 'pending-pdfs'

    counters.pdfs_discovered = len(result.pdf_urls)
    edition.pdf_count = len(result.pdf_urls)
    edition.status = NewspaperEdition.Status.PROCESSING
    edition.save()

    # ── Register file rows (idempotent on (edition, pdf_url)) ───────────────
    for url in result.pdf_urls:
        NewspaperEditionFile.objects.get_or_create(edition=edition, pdf_url=url)

    source = get_newspaper_source(source_code)

    # ── Per-PDF processing: only PENDING/DOWNLOADED/FAILED rows are worked ──
    for file_row in edition.files.exclude(status=NewspaperEditionFile.Status.DONE):
        # The only cancellation point. Deliberately BEFORE the work rather than
        # inside it: a page is either read completely or not at all, so a stop
        # can never leave one half-processed.
        if cancel_requested(edition.pk):
            counters.cancelled = True
            logger.info(f'[newspaper:{run_id}] stopped by request — '
                        f'{file_row.pdf_url} and any pages after it were not read')
            break
        _process_file(run_id, edition, file_row, source, counters, cfg)

    clear_cancel(edition.pk)

    # ── Edition status ──────────────────────────────────────────────────────
    if counters.cancelled:
        # FAILED rather than a new STOPPED value: it is what this project
        # already does for a run that did not finish (see
        # magazine_tenders.apps._close_orphaned_runs and
        # tenders.apps._reset_orphaned_scrape_logs), and the reason below is
        # rendered on the runs page, so nothing is lost by not adding a status.
        edition.mark(NewspaperEdition.Status.FAILED,
                     'Stopped by an administrator. Pages already read are kept '
                     '— press Retry to finish the rest, or Reset to start over.')
        logger.info(f'[newspaper:{run_id}] ' + counters.summary(target, 'STOPPED'))
        return 'stopped'

    statuses = set(edition.files.values_list('status', flat=True))
    if statuses and statuses <= {NewspaperEditionFile.Status.DONE}:
        # mark() only ever WRITES error_message, never clears it, so a
        # note left by an earlier interrupted or reset run would sit on
        # a healthy DONE row for ever. Clear it on the clean path.
        edition.error_message = ''
        edition.mark(NewspaperEdition.Status.DONE)
        status = 'done'
    elif NewspaperEditionFile.Status.FAILED in statuses:
        edition.mark(NewspaperEdition.Status.PROCESSING,
                     'Some PDFs failed — will retry on next run')
        status = 'partial'
    else:
        edition.mark(NewspaperEdition.Status.PROCESSING)
        status = 'partial'

    if counters.ai_tenders == 0 and status == 'done':
        logger.info(f'[newspaper:{run_id}] {cfg["display_en"]} edition '
                    'processed successfully — zero tenders found.')

    logger.info(f'[newspaper:{run_id}] ' + counters.summary(target, status.upper()))
    return status


def court_gate_applies(cfg: dict) -> bool:
    """The header gate runs for sources that declare it (Al-Rai) while the
    NEWSPAPER_SKIP_COURT_PAGES setting is on."""
    return bool(cfg.get('page_header_gate')) and page_gate.enabled()


def _process_file(run_id, edition, file_row, source, counters, cfg=None):
    """Download → validate → hash-dedup → AI extract → final gate → save.

    cfg['split_pages'] (Al-Ghad): the file is the WHOLE newspaper — its text
    layer selects the tender pages, and each page is sent to the AI as its
    own single-page PDF, exactly like one Al-Rai ads PDF.
    """
    cfg = cfg or sources.get_source_config(edition.source)
    # ── Download + validate ─────────────────────────────────────────────────
    try:
        data = intake.download_pdf(file_row.pdf_url,
                                   referer=cfg['base_url'] + '/')
        intake.validate_pdf(data)
    except (intake.DownloadError, intake.InvalidPdfError) as exc:
        file_row.mark(NewspaperEditionFile.Status.FAILED, str(exc))
        counters.pdfs_failed += 1
        logger.warning(f'[newspaper:{run_id}] {file_row.pdf_url}: {exc}')
        return

    file_hash = intake.sha256_hex(data)

    # ── Hash idempotency: identical bytes already processed anywhere? ───────
    dupe = (NewspaperEditionFile.objects
            .filter(file_hash=file_hash,
                    status=NewspaperEditionFile.Status.DONE)
            .exclude(pk=file_row.pk)
            .first())
    if dupe:
        file_row.file_hash = ''   # keep the hash on the DONE row only
        file_row.mark(NewspaperEditionFile.Status.DONE,
                      f'Duplicate of file #{dupe.pk} (same SHA256) — skipped')
        counters.pdfs_skipped += 1
        logger.info(f'[newspaper:{run_id}] {file_row.pdf_url}: duplicate PDF '
                    f'(hash {file_hash[:12]}…) — skipped')
        return

    file_row.file_hash = file_hash
    file_row.file_size = len(data)
    file_row.downloaded_at = timezone.now()
    file_row.status = NewspaperEditionFile.Status.DOWNLOADED
    file_row.save()

    # ── Page gate: is this page worth reading at all? ───────────────────────
    # Al-Rai heads its court-notice pages "إعلانات قضائية". The vision model
    # invents whole lists of tenders from them (36 fake "دعوة عطاء" rows from
    # one page on 5 Oct 2026), so such a page is closed here, unread. Positive
    # match only — an unreadable header means the page is read as before.
    if court_gate_applies(cfg) and page_gate.classify_page(data) == page_gate.COURT:
        file_row.tenders_found = 0
        file_row.mark(NewspaperEditionFile.Status.DONE, page_gate.SKIP_NOTE)
        counters.court_pages_skipped += 1
        logger.info(f'[newspaper:{run_id}] {file_row.pdf_url}: court-notices '
                    f'page — not sent to the AI')
        return

    # ── AI extraction ───────────────────────────────────────────────────────
    # Payloads: [(pdf_bytes, base_page_number), ...] — one entry for a
    # single-page ads PDF (Al-Rai), or one per tender-candidate page cut out
    # of a full-newspaper PDF (Al-Ghad).
    if cfg.get('split_pages'):
        from . import splitter
        try:
            payloads = [(page_bytes, page_no)
                        for page_no, page_bytes in splitter.find_tender_pages(data)]
        except splitter.SplitError as exc:
            file_row.mark(NewspaperEditionFile.Status.FAILED, str(exc))
            counters.pdfs_failed += 1
            logger.warning(f'[newspaper:{run_id}] split failed for '
                           f'{file_row.pdf_url}: {exc}')
            return
        if not payloads:
            file_row.tenders_found = 0
            file_row.mark(NewspaperEditionFile.Status.DONE,
                          'No tender-candidate pages in this edition')
            counters.pdfs_processed += 1
            return
    else:
        payloads = [(data, 1)]

    ai_tenders = []
    try:
        for page_bytes, base_page in payloads:
            # Second checkpoint, and for two of the three newspapers the
            # only one that matters: Al-Ghad and Ad-Dustour publish a
            # single file per edition, so the per-file check in
            # run_for_date never comes round again and a stop would
            # otherwise have to wait for the whole edition. Pages already
            # read are kept, and the row stays non-DONE so Retry resumes.
            if cancel_requested(edition.pk):
                logger.info(f'[newspaper:{run_id}] stopped by request part-way '
                            f'through {file_row.pdf_url} (page {base_page})')
                break
            for t in ai_client.extract_tenders(
                    page_bytes,
                    edition_date=edition.edition_date.isoformat(),
                    source_url=edition.article_url,
                    pdf_url=file_row.pdf_url,
                    page_number=base_page):
                # Anchor to the real newspaper page (single-page payloads
                # come back as page 1 relative to themselves).
                if cfg.get('split_pages'):
                    t['page_number'] = base_page
                ai_tenders.append(t)
    except ai_client.AIServiceError as exc:
        # Retryable → stay DOWNLOADED (next run re-sends without re-download
        # decisions); permanent → FAILED (still retried next run, but flagged).
        # Tenders already saved from earlier pages are dedup-safe on retry.
        status = (NewspaperEditionFile.Status.DOWNLOADED if exc.retryable
                  else NewspaperEditionFile.Status.FAILED)
        file_row.mark(status, str(exc))
        counters.pdfs_failed += 1
        logger.warning(f'[newspaper:{run_id}] AI failed for {file_row.pdf_url}: {exc}')
        return

    counters.pdfs_processed += 1
    counters.ai_tenders += len(ai_tenders)

    # ── Final deterministic gate + save through the EXISTING pipeline ──────
    validated = []
    for raw in ai_tenders:
        cleaned = validate_ai_tender(raw)
        if cleaned is None:
            counters.rejected_invalid += 1
            logger.info(f'[newspaper:{run_id}] rejected invalid AI record: '
                        f'{str(raw)[:120]}')
            continue
        validated.append(cleaned)

    records = merge_list_ads(validated)
    if getattr(settings, 'NEWSPAPER_ZOOM_REREAD', True):
        records = [_zoom_reread(rec, edition_date=str(edition.edition_date),
                                run_id=run_id) for rec in records]

    saved_for_file = 0
    for cleaned in records:
        # Deterministic non-tender gate: drop auction / asset-sale ads the AI
        # returned as tenders, before they reach the database. Runs after the
        # AI, changes no classification — see non_tender_reason() above.
        reason = non_tender_reason(cleaned['title'])
        if reason:
            counters.rejected_non_tender += 1
            logger.info(f'[newspaper:{run_id}] rejected non-tender ({reason}): '
                        f'{cleaned["title"][:70]}')
            continue

        # Reject fabricated ENTITIES (template companies the AJiAI model invents,
        # e.g. "شركة (دبي) للمقاولات", or known invented names). Not real tenders.
        if is_fabricated_entity(cleaned.get('entity', '')):
            counters.hallucinated_rejected += 1
            logger.info(f'[newspaper:{run_id}] rejected fabricated entity: '
                        f'{(cleaned.get("entity") or "")[:50]}')
            continue

        # Reject fabricated tenders. The AJiAI extraction sometimes INVENTS
        # tenders from a court / legal-notice page ("إعلانات قضائية") — the
        # fake row then carries that page's crop, which OCRs as a court notice.
        # We already OCR the crop for photo verification; if it reads as a
        # non-tender notice, the row is not a real tender, so drop the WHOLE row
        # (not just the photo). Fires only on positive court/auction evidence —
        # an OCR failure keeps the tender (photo_belongs_to_tender is
        # conservative), and a genuine tender ad OCRs as a tender, not a notice.
        crop_b64 = cleaned.get('ad_image_b64', '') or ''
        if crop_b64 and getattr(settings, 'NEWSPAPER_PHOTO_VERIFY', True):
            try:
                from . import photo_verify
                if (photo_verify.available()
                        and not photo_verify.photo_belongs_to_tender(crop_b64)):
                    counters.hallucinated_rejected += 1
                    logger.info(f'[newspaper:{run_id}] rejected fabricated tender — '
                                f'crop is a court/auction notice: '
                                f'{cleaned["title"][:60]}')
                    continue
            except Exception as exc:
                logger.warning(f'[newspaper:{run_id}] crop check skipped: {exc}')

        external_id = make_external_id(cleaned['title'], cleaned['entity'],
                                       cleaned['submission_deadline'],
                                       prefix=cfg['id_prefix'])
        already = Tender.objects.filter(source=source,
                                        external_id=external_id).exists()

        # Already dropped earlier in THIS run (same tender in another file of the
        # edition)? The whole-row delete removed it, so the `already` check above
        # cannot see it — count this copy as a duplicate and skip re-creating and
        # re-dropping it (which would double the drop counters and repeat the
        # classify+delete). Mirrors the saved path's saved=1 + duplicates=1.
        if external_id in counters.dropped_ids:
            counters.duplicates += 1
            logger.info(f'[newspaper:{run_id}] duplicate of a tender already '
                        f'dropped this run — skipped: {cleaned["title"][:60]}')
            continue

        # Cross-newspaper de-duplication: the same tender runs in several papers.
        # Only for tenders NOT already known to THIS source (a re-run must still
        # update its own row, not skip it). Runs after the AI, changes nothing
        # about classification — it only decides whether to add a second copy.
        if not already and getattr(settings, 'NEWSPAPER_CROSS_SOURCE_DEDUP', True):
            dup = cross_source_duplicate(cleaned, source)
            if dup is not None:
                counters.cross_source_dupes += 1
                logger.info(f'[newspaper:{run_id}] cross-source duplicate of '
                            f'tender #{dup.pk} ({dup.source.name}) — skipped: '
                            f'{cleaned["title"][:60]}')
                continue

        td = TenderData(
            title=cleaned['title'],
            title_ar=cleaned['title_ar'],
            source_url=file_row.pdf_url,
            external_id=external_id,
            reference_no=cleaned['reference_no'],
            description=cleaned['description'],
            description_ar=cleaned['description_ar'],
            entity=cleaned['entity'],
            entity_ar=cleaned['entity_ar'],
            category=cleaned['category'],
            status='OPEN',
            publication_date=edition.edition_date,
            submission_deadline=cleaned['submission_deadline'],
            opening_date=cleaned['opening_date'],
            country_code='JO',
            # The AI already decided tender-ness; keyword relevance filtering
            # would only lose Arabic tenders whose title misses a keyword.
            force_relevant=True,
        )

        with transaction.atomic():
            # Default enrichment ON: the standard TenderRadar classification
            # service (tenders/ajibrain_client.py → /api/classify-tender/ +
            # /api/translate-title/) sets is_aec, category and title_ar —
            # EXACTLY the same flow as every scraped tender.
            created, updated = save_tender_data(td, source)

            tender = Tender.objects.filter(source=source,
                                           external_id=external_id).first()
            if tender is None:
                # save_tender_data skipped it (e.g. past deadline — the same
                # policy every tender obeys).
                logger.info(f'[newspaper:{run_id}] skipped by save policy: '
                            f'{cleaned["title"][:80]}')
                continue

            # Photo of the specific announcement (cropped by the AI service).
            # Kept out of raw_extracted_data — it is a file, not audit JSON.
            # The crop was already OCR-verified before saving: a court/auction
            # crop rejects the whole tender (see above), so a tender that
            # reaches here has a genuine tender-ad crop.
            ad_image_b64 = cleaned.pop('ad_image_b64', '') or ''

            # Is the crop a degenerate (thin-strip) half-photo? The AJiAI box
            # captured only a sliver of the ad and we cannot re-crop it (the
            # service returns no coordinates). Decided BEFORE counting, because
            # the consequence differs by AEC status (see below).
            degenerate = False
            if ad_image_b64 and getattr(settings, 'NEWSPAPER_PHOTO_VERIFY', True):
                try:
                    from . import photo_verify
                    degenerate = photo_verify.crop_is_degenerate(ad_image_b64)
                except Exception as exc:
                    logger.warning(f'[newspaper:{run_id}] crop shape check skipped: {exc}')

            # A Non-AEC tender with NO usable photo is dropped whole (text + row)
            # and left out of the saved count — whether the AI returned NO crop at
            # all, or only a degenerate half-strip we cannot show. AEC tenders are
            # ALWAYS kept (text-only) even without a photo — they are what matters.
            #
            # CRUCIAL guard: only drop when classification actually RAN
            # (is_aec_checked) and returned non-AEC. Under the AJIBRAIN service
            # classification is inline, so is_aec is accurate here — but under
            # OPENAI/OLLAMA it is DEFERRED to the run_ai batch, leaving a fresh
            # row at the model default is_aec=False / is_aec_checked=False. Without
            # this guard every photoless tender (AEC included) would be deleted
            # before the batch could classify it, destroying real AEC tenders.
            # When unchecked we keep the row and let the batch classify it — never
            # delete an unclassified tender. Only rows created in THIS run are
            # dropped; an existing row (re-run) may hold a good stored photo, so it
            # falls through to the keep-existing logic below.
            no_usable_photo = degenerate or not ad_image_b64
            drop_whole_row = (
                no_usable_photo and created
                and tender.is_aec_checked and not tender.is_aec
                and getattr(settings, 'NEWSPAPER_DROP_NONAEC_WITHOUT_PHOTO', True)
            )
            if drop_whole_row:
                if degenerate:
                    counters.photos_dropped += 1
                counters.dropped_non_aec_no_photo += 1
                counters.dropped_ids.add(external_id)   # so a copy of the same
                # tender in a LATER file of this edition is counted as a
                # duplicate, not re-created and re-dropped (see the early guard).
                reason = 'half crop' if degenerate else 'no crop extracted'
                logger.info(f'[newspaper:{run_id}] dropped NON-AEC tender #{tender.pk} '
                            f'— {reason}, no photo to show: {cleaned["title"][:60]}')
                tender.delete()          # OneToOne CASCADE removes any meta row
                continue

            if tender.is_aec:
                counters.aec += 1
            else:
                counters.non_aec += 1

            # AEC (or a pre-existing non-AEC row): keep the text, drop only the
            # half-photo so we never show a misleading sliver.
            if degenerate and ad_image_b64:
                counters.photos_dropped += 1
                logger.info(f'[newspaper:{run_id}] dropped degenerate (half) '
                            f'crop for tender #{tender.pk} — kept text-only')
                ad_image_b64 = ''

            meta, _ = NewspaperTenderMeta.objects.update_or_create(
                tender=tender,
                defaults={
                    'edition':          edition,
                    'edition_file':     file_row,
                    'page_number':      cleaned['page_number'],
                    'ai_confidence':    cleaned['confidence'],
                    'original_pdf_url': file_row.pdf_url,
                    'raw_extracted_data': {
                        k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                        for k, v in cleaned.items()
                    },
                },
            )

            # Store the ad photo for every newspaper tender (AEC and
            # non-AEC alike) — the Non-AEC audit page shows it too.
            # A fresh photo REPLACES an existing one (re-processing an
            # edition after a quality upgrade refreshes the photos); when
            # the AI returns no photo, an existing one is kept.
            if ad_image_b64:
                try:
                    import base64 as _b64
                    from django.core.files.base import ContentFile
                    if meta.ad_image:
                        meta.ad_image.delete(save=False)
                    meta.ad_image.save(f'{tender.external_id[:64]}.jpg',
                                       ContentFile(_b64.b64decode(ad_image_b64)),
                                       save=True)
                except Exception as exc:
                    logger.warning(f'[newspaper:{run_id}] could not store ad '
                                   f'image for #{tender.pk}: {exc}')

        if created:
            counters.saved += 1
            saved_for_file += 1
        elif already:
            counters.duplicates += 1

    file_row.tenders_found = len(ai_tenders)
    file_row.mark(NewspaperEditionFile.Status.DONE)
    logger.info(f'[newspaper:{run_id}] {file_row.pdf_url}: '
                f'{len(ai_tenders)} AI tender(s), {saved_for_file} new saved')
