# tenders/scrapers/tenderjo.py
"""
TenderJO scraper — www.tenderjo.com (Jordan tenders portal)

Strategy:
  - Hits the internal JSON API: GET /api/HomePage/GetValue
  - Scrapes ALL categories (catidvalue=0 = all) for country=jo
  - Also sweeps the AEC-relevant category IDs individually for completeness
  - Pagination via IDFrom / IDTo (PAGE_SIZE records per call)
  - Per-tender relevance check (not batch) so we never miss individual AEC tenders
    mixed inside a general-category page
  - Country hardcoded to JO
"""

import logging
import time
from datetime import date, datetime

import requests

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenderjo')

# ── All category IDs to sweep (0 = all categories) ─────────────────────────
# 0 covers everything; specific IDs are added as a second pass for AEC focus
ALL_CATEGORIES     = [0]          # 0 = no category filter → returns all tenders
AEC_CATEGORIES     = [35, 45, 46, 48, 49, 238, 255, 272]
CONSULTANCY_CATEGORIES = AEC_CATEGORIES   # alias kept for tendersa.py back-compat   # consultancy / studies / engineering
PAGE_SIZE          = 15
MAX_PAGES_PER_CAT  = 50  # AEC category sweep
MAX_PAGES_PASS1    = 20  # all-categories pass           # safety cap (50 × 15 = 750 tenders per category)


class TenderJOScraper(BaseScraper):
    """
    Full-coverage scraper for www.tenderjo.com.
    Fetches all active Jordanian tenders via the site's internal JSON API.
    """

    source_name = 'TenderJO'
    BASE_URL     = 'https://www.tenderjo.com'
    API_ENDPOINT = '/api/HomePage/GetValue'

    HEADERS = {
        'User-Agent':       (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept':           'application/json, text/javascript, */*; q=0.01',
        'Accept-Language':  'ar,en;q=0.9',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer':          'https://www.tenderjo.com/Default.aspx?lang=ar&country=jo',
    }

    def __init__(self):
        super().__init__()
        self.session.headers.update(self.HEADERS)

    # ── Main entry point ────────────────────────────────────────────────────

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(scraper_class='TenderJOScraper', is_active=True)
        except ScraperSource.DoesNotExist:
            logger.error('[TenderJO] ScraperSource not found in DB')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='TenderJOScraper', is_active=True
            ).first()

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 64}')
        print(f'  SOURCE  : {source.name}')
        print(f'  URL     : {self.BASE_URL}/Default.aspx?lang=ar&country=jo')
        print(f'  MODE    : JSON API — all categories + AEC sweep')
        print(f'  Keywords: {rel_count} relevance [{rel_src}] | {cat_count} category [{cat_src}]')
        print(f'{"─" * 64}')

        seen_ids: set[str] = set()
        all_results: list[TenderData] = []

        # Pass 1 — all categories (catidvalue=0), per-tender relevance filter, max 10 pages
        print(f'\n  ▶  All categories (relevance-filtered, max {MAX_PAGES_PASS1} pages)')
        for item in self._fetch_category(cat_id=0, country='jo', filter_relevant=True, max_pages=MAX_PAGES_PASS1):
            key = item.external_id or item.title
            if key not in seen_ids:
                seen_ids.add(key)
                all_results.append(item)

        print(f'\n  {"═" * 60}')
        print(f'  TOTAL : {len(all_results)} tender(s)  ←  TenderJO')
        print(f'  {"═" * 60}\n')
        logger.info(f'[TenderJO] scrape() done — {len(all_results)} tenders')

        return all_results

    # ── Paginated category fetcher ──────────────────────────────────────────

    def _fetch_category(
        self,
        cat_id: int,
        country: str = 'jo',
        filter_relevant: bool = True,
        max_pages: int = MAX_PAGES_PER_CAT,
    ) -> list[TenderData]:
        """
        Fetch all pages for one category ID.
        If filter_relevant=True, only keep tenders that match relevance keywords.
        If filter_relevant=False, keep every tender (used for AEC-specific categories).
        """
        results: list[TenderData] = []
        id_from = 0
        id_to   = PAGE_SIZE
        page    = 1

        cat_label = f'cat={cat_id}' if cat_id else 'all-categories'

        while page <= max_pages:
            params = {
                'id':             1,
                'Country_Code':   country,
                'catidvalue':     cat_id,
                'buyerid':        '',
                'cityid':         '',
                'tendertypeid':   '',
                'tenderstatusid': '',
                'sortbyid':       '',
                'tendernameid':   '',
                'IDFrom':         id_from,
                'IDTo':           id_to,
                'User':           0,
                'startDate':      '',
                'endDate':        '',
                'ComeFrom':       2,
                'lang':           'ar',
            }

            print(
                f'     [{cat_label}] page {page:>3} '
                f'IDFrom={id_from} IDTo={id_to}',
                end='  ', flush=True
            )

            try:
                resp = self.session.get(
                    f'{self.BASE_URL}{self.API_ENDPOINT}',
                    params=params,
                    timeout=self.timeout,
                )
                resp.raise_for_status()

                try:
                    items_raw = resp.json()
                except ValueError:
                    print('✗ invalid JSON')
                    logger.error(f'[TenderJO] {cat_label} page {page}: invalid JSON')
                    break

                if not items_raw:
                    print('(end)')
                    break

                # Total count is in first row
                total_count = 0
                try:
                    total_count = int(items_raw[0].get('TenderCount', 0) or 0)
                except (IndexError, TypeError, ValueError):
                    pass

                # Per-tender relevance check or take all
                taken = 0
                for raw in items_raw:
                    tender = self._parse_item(raw)
                    if filter_relevant:
                        combined = self._item_text(raw)
                        if not self.detect_relevance(combined):
                            continue
                    results.append(tender)
                    taken += 1

                print(f'→ {len(items_raw)} fetched, {taken} kept (total={total_count})')
                logger.info(
                    f'[TenderJO] {cat_label} page {page}: '
                    f'fetched={len(items_raw)} kept={taken} total={total_count}'
                )

                # End of this category?
                if total_count and id_to >= total_count:
                    break
                if len(items_raw) < PAGE_SIZE:
                    break

                id_from  = id_to + 1
                id_to   += PAGE_SIZE
                page    += 1
                time.sleep(self.delay)

            except requests.RequestException as exc:
                print(f'✗ {exc}')
                logger.error(f'[TenderJO] {cat_label} page {page} request error: {exc}')
                break
            except Exception as exc:
                print(f'✗ parse error: {exc}')
                logger.error(
                    f'[TenderJO] {cat_label} page {page} error: {exc}',
                    exc_info=True,
                )
                break

        return results

    # ── Item parser ─────────────────────────────────────────────────────────

    def _item_text(self, raw: dict) -> str:
        """Flatten all API fields into a single string for keyword matching."""
        return ' '.join(str(v) for v in raw.values() if v)

    def _parse_item(self, t: dict) -> TenderData:
        tdc_id   = str(t.get('tdc_id', '') or '')
        title    = (t.get('tnd_name', '') or '').strip()
        entity   = (t.get('tpb_name', '') or '').strip()
        cat_name = (t.get('cat_Name', '') or '').strip()

        pub_date = self._parse_date(t.get('tnd_publish_date'))
        end_date = self._parse_date(t.get('tnd_buy_tender_date'))

        combined = self._item_text(t)
        category = self.detect_category(combined) or 'ADV'
        status   = self._map_status(t.get('tnd_sts', ''))

        # Build full detail URL
        source_url = (
            f'{self.BASE_URL}/TenderDetails.aspx?tdc_id={tdc_id}&lang=ar'
            if tdc_id else self.BASE_URL
        )

        return TenderData(
            title               = title or cat_name or 'Untitled',
            title_ar            = title or cat_name or '',
            description         = cat_name,
            description_ar      = cat_name,
            entity              = entity or cat_name,
            entity_ar           = entity or cat_name,
            reference_no        = tdc_id,
            external_id         = f'tenderjo-{tdc_id}' if tdc_id else '',
            source_url          = source_url,
            category            = category,
            status              = status,
            publication_date    = pub_date,
            submission_deadline = end_date,
            country_code        = 'JO',
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _parse_date(self, value) -> date | None:
        if not value:
            return None
        val = str(value).strip()
        if '.' in val:
            val = val[:val.index('.')]
        for fmt, length in [
            ('%Y-%m-%dT%H:%M:%S', 19),
            ('%Y-%m-%dT%H:%M',    16),
            ('%Y-%m-%d',          10),
            ('%Y/%m/%d',          10),
            ('%d/%m/%Y',          10),
            ('%d-%m-%Y',          10),
        ]:
            try:
                return datetime.strptime(val[:length], fmt).date()
            except ValueError:
                continue
        logger.debug(f'[TenderJO] unparseable date: {value!r}')
        return None

    def _map_status(self, sts_text: str) -> str:
        if not sts_text:
            return 'OPEN'
        t = str(sts_text).lower()
        if any(w in t for w in ('منته', 'closed', 'expired', 'مغلق', 'انتهت')):
            return 'CLOSED'
        if any(w in t for w in ('محال', 'awarded', 'فائز', 'ترسية')):
            return 'AWARDED'
        return 'OPEN'

    # parse_page not used — API-based scraper
    def parse_page(self, soup, tendering_url):
        return []
