# tenders/scrapers/base.py

import contextlib
import hashlib
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger('tenders')


# ── Console safety ────────────────────────────────────────────────────────────

class _TolerantStream:
    """
    A stdout proxy that never lets a progress line kill a scrape.

    Every scraper reports progress with print() and box-drawing characters.
    Celery installs colorama on Windows, so those prints go through colorama's
    console converter — and when the process has no real console (a Windows
    service, output redirected to a file, a detached background process) that
    converter raises `OSError: [Errno 22] Invalid argument` on write or flush.
    The exception surfaces inside scrape(), so the source is recorded as FAILED
    even though the fetching and parsing were fine. In one run on 2026-08-13
    this alone failed 33 of the 40 sources attempted.

    Writes are attempted normally and only the console-level failures are
    swallowed, so a real terminal still shows the same output as before.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, text):
        try:
            return self._wrapped.write(text)
        except (OSError, ValueError, UnicodeEncodeError):
            # ValueError covers "I/O operation on closed file".
            return len(text)

    def flush(self):
        try:
            self._wrapped.flush()
        except (OSError, ValueError):
            pass

    def isatty(self):
        try:
            return self._wrapped.isatty()
        except (OSError, ValueError, AttributeError):
            return False

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


_console_lock  = threading.Lock()
_console_depth = 0
_console_saved = None


@contextlib.contextmanager
def tolerant_console():
    """
    Run a block with scraper progress output that cannot raise.

    Wrap every call to `scraper.scrape()` in this: a failure to print must not
    be reported as a failure to scrape.

    Reference-counted under a lock rather than a plain contextlib.redirect_stdout,
    because two scrapes can overlap — a per-source Run while a different source is
    being scanned — and each runs in its own thread. Two threads swapping one
    global sys.stdout restore it in whatever order they happen to finish, which
    can put back a stale stream or leave a wrapper installed for the life of the
    process. Here the first thread in installs the wrapper, the last one out
    restores the original, and anything in between just shares it.
    """
    global _console_depth, _console_saved
    with _console_lock:
        if _console_depth == 0:
            _console_saved = sys.stdout
            sys.stdout     = _TolerantStream(sys.stdout)
        _console_depth += 1
    try:
        yield
    finally:
        with _console_lock:
            _console_depth -= 1
            if _console_depth == 0:
                sys.stdout, _console_saved = _console_saved, None


# ── TenderData ────────────────────────────────────────────────────────────────

@dataclass
class TenderData:
    """Raw data container returned by every scraper."""
    title:               str
    source_url:          str
    external_id:         str            = ''
    title_ar:            str            = ''
    reference_no:        str            = ''
    description:         str            = ''
    description_ar:      str            = ''
    entity:              str            = ''
    entity_ar:           str            = ''
    category:            str            = 'OTHER'
    status:              str            = 'UNKNOWN'
    estimated_value:     Optional[float]= None
    currency:            str            = ''
    publication_date:    Optional[date] = None
    submission_deadline: Optional[date] = None
    opening_date:        Optional[date] = None
    country_code:        str            = ''   # per-tender country override
    sort_order:          int            = 0    # used by WorldBank to put unknown deadlines last
    force_relevant:      bool           = False # bypass keyword filter on save


# ── Keyword loaders (always fresh from DB) ────────────────────────────────────

def _fetch_relevance_keywords() -> tuple[list[str], str]:
    """
    Query DB for active RelevanceKeyword rows.
    Returns (keyword_list, 'DB') on success, or (fallback_list, 'FALLBACK') on failure.
    Called fresh before every URL scrape — never cached on the instance.
    """
    try:
        from tenders.models import RelevanceKeyword
        kws = list(
            RelevanceKeyword.objects
            .filter(is_active=True)
            .values_list('keyword', flat=True)
        )
        if kws:
            return [k.lower() for k in kws], 'DB'
        logger.warning('RelevanceKeyword table is empty — using fallback list')
    except Exception as exc:
        logger.warning(f'Cannot read RelevanceKeyword from DB: {exc}')

    fallback = [
        # English
        'engineer', 'engineering', 'design', 'supervision', 'consultant',
        'consultancy', 'architect', 'architecture', 'structural', 'civil',
        'mep', 'mechanical', 'electrical', 'plumbing', 'urban', 'planning',
        'infrastructure', 'project management', 'pmc', 'environmental',
        'healthcare', 'hospital', 'transportation', 'road', 'bridge',
        'feasibility', 'master plan', 'construction management',
        'geotechnical', 'quantity surveying',
        'technical assistance', 'rehabilitation', 'reconstruction',
        'capacity building', 'procurement', 'assessment', 'institutional',
        'laboratory', 'smart city', 'smart cities',
        # Arabic — core AEC terms
        'استشارات', 'تصميم', 'إشراف', 'هندسة', 'معماري', 'مدني',
        'بنية تحتية', 'تخطيط', 'رقابة', 'دراسة جدوى', 'مشروع',
        'إنشاء', 'تطوير', 'صحة', 'مستشفى', 'نقل', 'طرق', 'جسور',
        'إدارة مشاريع', 'كهرباء', 'ميكانيكا', 'بيئة', 'تخطيط عمراني',
        # Arabic — Syria/NGO context (renovation, WASH, construction)
        'ترميم', 'تأهيل', 'إعادة تأهيل', 'اعادة ترميم', 'بناء',
        'مياه', 'صرف صحي', 'شبكات مياه', 'شبكة كهرباء',
        'طاقة شمسية', 'مولدة', 'محطة', 'مضخة',
        'مقاول', 'مقاولة', 'أعمال مدنية', 'أعمال إنشائية',
        'مباني', 'مبنى', 'منشآت', 'مرفق', 'بنية',
        'تركيب', 'توريد وتركيب', 'توريد وتنفيذ',
        'خدمات هندسية', 'أعمال هندسية', 'مشاريع إنشاء',
        'إعادة إعمار', 'إعمار', 'تعمير',
        'مخطط', 'رسومات هندسية', 'دراسة', 'تقييم',
        'الصرف الصحي', 'أعمال المياه', 'توريد', 'تنفيذ', 'مقاولة',
    ]
    return fallback, 'FALLBACK'


def _fetch_category_keywords() -> tuple[dict[str, list[str]], str]:
    """
    Query DB for active CategoryKeyword rows grouped by category.
    Returns (category_dict, 'DB') or (fallback_dict, 'FALLBACK').
    Called fresh before every URL scrape — never cached on the instance.
    """
    try:
        from tenders.models import CategoryKeyword
        rows = list(
            CategoryKeyword.objects
            .filter(is_active=True)
            .values('category', 'keyword')
        )
        if rows:
            result: dict[str, list[str]] = {}
            for r in rows:
                result.setdefault(r['category'], []).append(r['keyword'].lower())
            return result, 'DB'
        logger.warning('CategoryKeyword table is empty — using fallback dict')
    except Exception as exc:
        logger.warning(f'Cannot read CategoryKeyword from DB: {exc}')

    # Three-category system: HEALTH is checked first (health-care buildings
    # take priority over generic BUILDING), then INFRA, then BUILDING.
    # This is only an initial guess — the AJiAI API (/api/classify-tender/)
    # sets the final category during AI enrichment.
    fallback = {
        'HEALTH':   ['health', 'hospital', 'clinic', 'medical', 'laboratory',
                     'healthcare', 'wellness',
                     'صحي', 'مستشفى', 'عيادة', 'طبي', 'مختبر', 'مرافق صحية'],
        'INFRA':    ['infrastructure', 'road', 'highway', 'bridge', 'tunnel',
                     'dam', 'utility', 'sewer', 'water supply', 'wastewater',
                     'drainage', 'irrigation', 'transport', 'metro', 'rail',
                     'railway', 'airport', 'port', 'pavement',
                     'بنية تحتية', 'طرق', 'جسور', 'أنفاق', 'سد', 'صرف صحي',
                     'مياه', 'شبكات', 'ري', 'مواصلات', 'نقل', 'مطار', 'قطار',
                     'رصف'],
        'BUILDING': ['building', 'architect', 'architectural', 'structural',
                     'mep', 'mechanical', 'electrical', 'plumbing', 'hvac',
                     'interior design', 'school', 'university', 'hotel',
                     'residential', 'housing', 'office', 'mosque', 'warehouse',
                     'مبنى', 'مباني', 'معماري', 'تصميم معماري', 'إنشائي',
                     'ميكانيكا', 'كهرباء', 'تكييف', 'مدرسة', 'جامعة', 'فندق',
                     'سكني', 'إسكان', 'مسجد', 'مستودع'],
    }
    return fallback, 'FALLBACK'


# ── BaseScraper ───────────────────────────────────────────────────────────────

class BaseScraper:
    """
    Base class for all TenderRadar scrapers.

    Subclasses implement parse_page(soup, tendering_url) → list[TenderData].
    API-based scrapers (e.g. Saudi Etimad) override scrape() instead.

    Keywords are reloaded fresh from DB before every URL is scraped
    so any admin changes take effect immediately without restarting.
    """

    country_code: str = ''
    source_name:  str = ''
    base_url:     str = ''

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent':     settings.SCRAPER_USER_AGENT,
            'Accept-Language':'ar,en;q=0.9',
        })
        self.timeout  = settings.SCRAPER_REQUEST_TIMEOUT
        self.delay    = settings.SCRAPER_DELAY_BETWEEN_REQUESTS
        self.retries  = settings.SCRAPER_MAX_RETRIES

        # Proxy support (optional) — set SCRAPER_PROXY in settings or env
        proxy = getattr(settings, 'SCRAPER_PROXY', '')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            self.session.trust_env = False  # don't inherit system proxy settings

        # NOTE: do NOT cache keywords here — they are loaded fresh per URL
        # in _reload_keywords_from_db() called inside scrape_url() / scrape()

    # ── Keyword management ────────────────────────────────────────────────────

    def _reload_keywords_from_db(self) -> tuple[str, str]:
        """
        Reload both keyword sets fresh from DB.
        Prints and logs what was loaded and from where.
        Returns (relevance_source, category_source) e.g. ('DB', 'DB').
        """
        rel_kws,  rel_src  = _fetch_relevance_keywords()
        cat_kws,  cat_src  = _fetch_category_keywords()

        self._relevance_keywords: list[str]            = rel_kws
        self._category_keywords:  dict[str, list[str]] = cat_kws

        return rel_src, cat_src

    # ── Classification ────────────────────────────────────────────────────────

    def detect_relevance(self, text: str) -> bool:
        """
        Returns True if ANY relevance keyword appears in the given text.
        Short-circuits to True when admin set scraping mode to ALL,
        so every scraper respects the global setting automatically.
        """
        # Global mode check — single DB read via SiteSettings
        try:
            from tenders.services import scraping_mode_is_all
            if scraping_mode_is_all():
                return True
        except Exception:
            pass  # fallback: use keyword filtering

        text_lower = text.lower()
        matched = [kw for kw in self._relevance_keywords if kw in text_lower]
        if matched:
            preview = text.strip().replace("\n", " ")[:80]
            print(f"       ✓ MATCH {matched}")
            print(f"         text : {preview}")
        return bool(matched)

    def detect_category(self, text: str) -> str:
        """Returns best-matching category code, or 'OTHER'."""
        text_lower = text.lower()
        for cat, keywords in self._category_keywords.items():
            if any(kw in text_lower for kw in keywords):
                return cat
        return 'OTHER'

    def _build_match_text(self, item, page_text: str = '') -> str:
        """
        Build the widest possible text string for keyword matching.

        Concatenates ALL structured TenderData fields PLUS the raw
        page_text from soup.get_text().  This means keywords are checked
        against EVERYTHING on the page — title, description, entity name,
        category label, any embedded Arabic/English text — for ALL scrapers
        and ALL countries, not just the fields the parser managed to extract.

        Args:
            item:       TenderData instance returned by parse_page()
            page_text:  soup.get_text(separator=' ', strip=True) of the
                        listing page.  Pass '' if not available (e.g. APIs).
        """
        parts = [
            item.title       or '',
            item.title_ar    or '',
            item.description or '',
            item.description_ar or '',
            item.entity      or '',
            item.entity_ar   or '',
            item.reference_no or '',
            page_text,              # ← full page text as safety net
        ]
        return ' '.join(p for p in parts if p)

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> requests.Response:
        for attempt in range(1, self.retries + 1):
            try:
                time.sleep(self.delay)
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.exceptions.ConnectionError as exc:
                logger.warning(f'[{self.source_name}] Connection reset attempt {attempt}: {exc}')
                if attempt == self.retries:
                    raise
                backoff = self.delay * (attempt * 3)   # 6s, 12s, 18s...
                logger.info(f'[{self.source_name}] Backing off {backoff}s before retry')
                time.sleep(backoff)
            except requests.RequestException as exc:
                logger.warning(f'[{self.source_name}] GET attempt {attempt}: {exc}')
                if attempt == self.retries:
                    raise
                time.sleep(self.delay * attempt)

    def post(self, url: str, **kwargs) -> requests.Response:
        for attempt in range(1, self.retries + 1):
            try:
                time.sleep(self.delay)
                resp = self.session.post(url, timeout=self.timeout, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                logger.warning(f'[{self.source_name}] POST attempt {attempt}: {exc}')
                if attempt == self.retries:
                    raise
                time.sleep(self.delay * attempt)

    def soup(self, url: str, **kwargs) -> BeautifulSoup:
        resp = self.get(url, **kwargs)
        return BeautifulSoup(resp.text, 'html.parser')

    def compute_hash(self, td: TenderData) -> str:
        content = f'{td.title}|{td.reference_no}|{td.submission_deadline}'
        return hashlib.sha256(content.encode()).hexdigest()

    # ── DB-driven URL helpers ─────────────────────────────────────────────────

    def get_tendering_urls(self, source) -> list:
        """Return all active TenderingURL records for this source, ordered."""
        return list(
            source.tendering_urls
            .filter(is_active=True)
            .order_by('sort_order', 'id')
        )

    # ── Core scrape loop ──────────────────────────────────────────────────────

    def scrape(self) -> list[TenderData]:
        """
        Iterate every active TenderingURL for self.source.
        Keywords are reloaded from DB before each URL.
        Console output shows source, URL label, per-page progress,
        keyword source (DB/FALLBACK), and counts.
        """
        source = getattr(self, 'source', None)
        if source is None:
            print(f'  ✗  [{self.source_name}] self.source not set — skipping')
            logger.error(f'[{self.source_name}] scrape() called without self.source')
            return []

        urls = self.get_tendering_urls(source)

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : {self.__class__.__name__}')
        print(f'  URLs    : {len(urls)} active URL(s)')
        print(f'{"─" * 62}')

        if not urls:
            print(f'  ⚠  No active TenderingURLs — add via Admin → Scraper Sources')
            logger.warning(f'[{self.source_name}] No active TenderingURLs')
            return []

        all_results: list[TenderData] = []

        for idx, tendering_url in enumerate(urls, 1):
            # ── Reload keywords fresh from DB for THIS URL ────────────────────
            rel_src, cat_src = self._reload_keywords_from_db()

            rel_count = len(self._relevance_keywords)
            cat_count = sum(len(v) for v in self._category_keywords.values())

            print(f'\n  [{idx}/{len(urls)}] {tendering_url.label}')
            print(f'       URL      : {tendering_url.url}')
            print(f'       Mode     : {tendering_url.get_pagination_style_display()}'
                  f'  |  Max pages: {tendering_url.max_pages}')
            print(f'       Keywords : {rel_count} relevance [{rel_src}]'
                  f'  |  {cat_count} category [{cat_src}]')

            logger.info(
                f'[{self.source_name}] URL {idx}/{len(urls)}: {tendering_url.label} | '
                f'relevance={rel_count}[{rel_src}] category={cat_count}[{cat_src}]'
            )

            url_results = self.scrape_url(tendering_url)
            all_results.extend(url_results)

            print(f'       ✓ {len(url_results)} relevant tender(s) from this URL')
            logger.info(f'[{self.source_name}] URL "{tendering_url.label}": {len(url_results)} relevant')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant tender(s)  ←  {source.name}')
        print(f'  {"═" * 58}\n')
        logger.info(f'[{self.source_name}] scrape() complete — total={len(all_results)}')

        return all_results

    def scrape_url(self, tendering_url) -> list[TenderData]:
        """
        Paginate through all pages of one TenderingURL.
        Uses self._relevance_keywords / self._category_keywords which were
        reloaded from DB immediately before this call in scrape().
        """
        from tenders.models import TenderingURL as TU

        results:  list[TenderData] = []
        is_single = tendering_url.pagination_style == TU.PaginationStyle.SINGLE
        if is_single:
            max_pages = 1
        else:
            max_pages = tendering_url.max_pages or 10

        for page in range(1, max_pages + 1):
            page_url = tendering_url.build_page_url(page)
            print(f'       › Page {page:>3}: {page_url}', end='  ', flush=True)
            logger.info(f'[{self.source_name}] Scraping page {page}: {page_url}')

            try:
                s     = self.soup(page_url)
                items = self.parse_page(s, tendering_url)

                if not items:
                    print('(no items — stopping)')
                    logger.info(f'[{self.source_name}] Page {page}: no items, stopping')
                    break

                # ── Page-level relevance check ─────────────────────────────
                # Check the FULL page HTML text for any keyword occurrence.
                # If ANY keyword appears anywhere on the page, ALL tenders on
                # that page are considered relevant and are saved.
                # This mirrors how a human analyst would work:
                #   "Does this listing page mention engineering/consulting?" → yes → take all rows.
                # We do NOT filter row-by-row because tender titles alone are
                # often too short or abbreviated to contain a keyword match.
                page_text = s.get_text(separator=' ', strip=True)

                # ── Respect global scraping mode ───────────────────────────
                from tenders.services import scraping_mode_is_all
                take_all = scraping_mode_is_all()

                if take_all or self.detect_relevance(page_text):
                    # Take all items on this page
                    for item in items:
                        if not item.category or item.category == 'OTHER':
                            item.category = self.detect_category(
                                self._build_match_text(item, page_text)
                            )
                    results.extend(items)
                    mode_tag = 'ALL mode' if take_all else 'page hit'
                    print(f'→ {len(items)} items scraped, {len(items)} relevant ({mode_tag})')
                    logger.info(
                        f'[{self.source_name}] Page {page}: '
                        f'{len(items)} scraped, all taken ({mode_tag})'
                    )
                else:
                    print(f'→ {len(items)} items scraped, 0 relevant (page not matched)')
                    logger.info(
                        f'[{self.source_name}] Page {page}: '
                        f'{len(items)} scraped, page not relevant — skipped'
                    )

            except Exception as exc:
                print(f'✗ ERROR: {exc}')
                logger.error(f'[{self.source_name}] Page {page} error: {exc}', exc_info=True)
                break

            if is_single:
                break

        return results

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        """
        Override in subclass to parse one listing page.
        Return an empty list to stop pagination.
        """
        return []
