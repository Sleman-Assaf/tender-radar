# tenders/scrapers/rwanda.py
"""
Rwanda UMUCYO / RPPA e-Procurement scraper
Source: https://www.umucyo.gov.rw/eb/bav/selectListAdvertisingListForGU.do

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from actual full-listing HTML)
─────────────────────────────────────────────────────────────────────────────
URL:    POST /eb/bav/selectListAdvertisingListForGU.do
Method: POST (form submission with pagination)

Key POST fields:
    currentPageNo      = 1, 2, 3 …
    recordCountPerPage = 50
    tendTypeCd         = '' (All) | G | C | NC | W
    tendStatCd         = '' (All)
    (all other search fields empty)

Response: full HTML page with <table class="article_table">

Table columns (8):
    [0]  radio button  → value = pipe-separated data string
    [1]  Tender Name   → <a href="javascript:ViewDetail(...)"> full title </a>
    [2]  Tender No     → visible text "000003/C/NCB/2025/2026/MoE"  (entity = last segment)
                         + hidden <input value="000003/C/NCB/2025/2026/2900000000"> (real ref no)
    [3]  Status        → Published / Amended / Cancelled / Opened …
    [4]  Advertising Date  → DD/MM/YYYY
    [5]  Deadline of Submitting → "11/05/2026 16:00"  (or plain "11/05/2026 16:00" text)
    [6]  Planned Open Date
    [7]  Stage Type    → one stage / 1/2 stage

Radio value format (9 pipe-separated fields):
    tendReferNo | tendName | peCode | submDlineDt | tendStageCd |
    tendMthodCd | tendTypeCd | submDlineDtWithTime | tendStatCd
    e.g. "000003/C/NCB/2025/2026/2900000000|Hiring a consulting firm...|2900000000|11/05/2026|O|NCB|C|11/05/2026 16:00|P"

Detail URL:
    POST /eb/bav/selectAdvertisingDtlInfo.do
    with: tendReferNo, tendStageCd, tendTypeCd
    We store it as a constructed URL (not clickable, for reference).

Pagination:
    37,636 results / 753 pages at 50/page.
    We scrape max_pages (default 10) = 500 tenders per run.

Type → category:
    /C/  Consultant Services        → ADV
    /NC/ Non-Consultant Services    → ADV
    /W/  Works                      → CIVIL
    /G/  Goods                      → OTHER

Status → TenderRadar status:
    Published / Advertised / Amended  → OPEN
    Opened / Evaluation / Signed / Cancelled → CLOSED
"""

import logging
import re
from datetime import datetime, date
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.umucyo.gov.rw'
LIST_URL   = f'{BASE_URL}/eb/bav/selectListAdvertisingListForGU.do'
DETAIL_URL = f'{BASE_URL}/eb/bav/selectAdvertisingDtlInfo.do'

PER_PAGE   = 50   # match site's 50-records option

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Content-Type':    'application/x-www-form-urlencoded',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
}

# tendTypeCd → TenderRadar category
TYPE_CATEGORY = {
    'C':  'ADV',    # Consultant Services
    'NC': 'ADV',    # Non-Consultant Services
    'W':  'CIVIL',  # Works
    'G':  'OTHER',  # Goods
}

# Status text → TenderRadar status
def _status_from_text(text: str) -> str:
    t = text.strip().lower()
    if any(k in t for k in ('cancel', 'opened', 'evaluation', 'signed', 'closed')):
        return 'CLOSED'
    return 'OPEN'   # Published, Amended, Advertised

DATE_FMTS = (
    '%d/%m/%Y %H:%M',   # "11/05/2026 16:00"
    '%d/%m/%Y',         # "11/05/2026"
    '%Y/%m/%d',
    '%Y-%m-%d',
)


def _parse_date(text: str) -> Optional[date]:
    text = (text or '').strip()
    if not text or text in ('-', 'N/A', ''):
        return None
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Try just the date part if there's a time component
    text = text.split()[0]
    for fmt in ('%d/%m/%Y', '%Y/%m/%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _effective_max_pages(url_obj, default: int = 10) -> int:
    """Return max_pages from DB setting (scraping mode does NOT affect page count)."""
    return url_obj.max_pages if (url_obj and url_obj.max_pages) else default


class RwandaUMUCYOScraper(BaseScraper):
    """
    Scraper for Rwanda UMUCYO / RPPA full tender listing.

    Fetches POST /eb/bav/selectListAdvertisingListForGU.do
    with 50 records per page, up to max_pages pages.

    TenderingURL in DB:
        url              = https://www.umucyo.gov.rw/eb/bav/selectListAdvertisingListForGU.do
        pagination_style = SINGLE   (we drive pagination via POST)
        max_pages        = 10       (10 × 50 = 500 tenders per run)
    """

    country_code = 'RW'
    source_name  = 'Rwanda UMUCYO'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='RwandaUMUCYOScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗ [Rwanda] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='RwandaUMUCYOScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = _effective_max_pages(url_obj, default=10)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : RwandaUMUCYOScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  PAGES   : up to {max_pages} × {PER_PAGE} rows = {max_pages*PER_PAGE} tenders')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:   set[str]          = set()

        for page in range(1, max_pages + 1):
            print(f'       › Page {page:>2}/{max_pages}', end='  ', flush=True)
            logger.info(f'[Rwanda] Scraping page {page}/{max_pages}')

            payload = self._build_payload(page)
            try:
                resp = self.post(LIST_URL, data=payload)
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[Rwanda] POST page {page} failed: {exc}')
                break

            soup  = BeautifulSoup(resp.text, 'html.parser')
            items = self._parse_table(soup)

            if not items:
                print('(empty — stopping)')
                logger.info(f'[Rwanda] page {page}: no items')
                break

            if page == 1:
                print()
                print(f'  [DEBUG] Response length  : {len(resp.text):,} chars')
                print(f'  [DEBUG] Rows parsed      : {len(items)}')
                # Show total from pagination text
                pg_text = soup.get_text(separator=' ')
                m = re.search(r'(\d[\d,]+)\s+Results', pg_text)
                if m:
                    print(f'  [DEBUG] Total on site    : {m.group(1)} tenders')
                if items:
                    s = items[0]
                    print(f'  [DEBUG] Sample title     : {s.title[:65]!r}')
                    print(f'  [DEBUG] Sample ext_id    : {s.external_id!r}')
                    print(f'  [DEBUG] Sample entity    : {s.entity!r}')
                    print(f'  [DEBUG] Sample deadline  : {s.submission_deadline!r}')
                    print(f'  [DEBUG] Sample category  : {s.category!r}')
                print()

            # Relevance + category
            matched = 0
            page_text = soup.get_text(separator=' ', strip=True)

            if self.detect_relevance(page_text):
                for item in items:
                    if item.external_id not in seen_ids:
                        seen_ids.add(item.external_id)
                        combined = f'{item.title} {item.description}'
                        if not item.category or item.category == 'OTHER':
                            item.category = self.detect_category(combined)
                        all_results.append(item)
                        matched += 1
                print(f'→ {len(items)} rows, {matched} relevant (page hit)')
            else:
                for item in items:
                    if item.external_id not in seen_ids:
                        seen_ids.add(item.external_id)
                        combined = f'{item.title} {item.description}'
                        if self.detect_relevance(combined):
                            if not item.category or item.category == 'OTHER':
                                item.category = self.detect_category(combined)
                            all_results.append(item)
                            matched += 1
                print(f'→ {len(items)} rows, {matched} relevant')

            # Early stop if page wasn't full
            if len(items) < PER_PAGE:
                logger.info(f'[Rwanda] page {page}: partial page — last page')
                break

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  Rwanda')
        print(f'  {"═" * 58}\n')
        logger.info(f'[Rwanda] scrape() complete — total={len(all_results)}')
        return all_results

    # ── Table parser ──────────────────────────────────────────────────────────

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse <table class="article_table"> rows.

        Columns (0-indexed):
            0  radio   → value = pipe-separated metadata
            1  Tender Name → <a> title text
            2  Tender No   → visible text (with entity) + hidden input (real ref no)
            3  Status
            4  Advertising Date
            5  Deadline of Submitting
            6  Planned Open Date
            7  Stage Type
        """
        table = soup.find('table', class_='article_table')
        if not table:
            logger.warning('[Rwanda] article_table not found')
            return []

        items: list[TenderData] = []

        for row in table.select('tbody tr'):
            cells = row.find_all('td')
            if len(cells) < 6:
                continue

            # ── col 0: radio — extract metadata from value attribute ──────────
            radio = cells[0].find('input', type='radio')
            if not radio:
                continue
            radio_val = radio.get('value', '')
            rf = radio_val.split('|')
            # rf[0]=tendReferNo, rf[1]=title, rf[2]=peCode, rf[3]=submDlineDt(date only)
            # rf[4]=tendStageCd, rf[5]=tendMthodCd, rf[6]=tendTypeCd
            # rf[7]=submDlineDtWithTime, rf[8]=tendStatCd
            ref_no    = rf[0] if len(rf) > 0 else ''
            type_cd   = rf[6] if len(rf) > 6 else ''
            stage_cd  = rf[4] if len(rf) > 4 else 'O'
            stat_cd   = rf[8] if len(rf) > 8 else ''

            if not ref_no:
                continue

            ext_id = ref_no   # e.g. "000003/C/NCB/2025/2026/2900000000"

            # ── col 1: tender name ────────────────────────────────────────────
            a_tag = cells[1].find('a')
            title = a_tag.get_text(strip=True) if a_tag else cells[1].get_text(strip=True)

            # ── col 2: entity (from visible text, last segment after last /) ──
            # Visible text: "000003/C/NCB/2025/2026/MoE"
            # Hidden input: "000003/C/NCB/2025/2026/2900000000" (real ref no)
            tender_no_cell = cells[2]
            hidden = tender_no_cell.find('input', type='hidden')
            visible_no = tender_no_cell.get_text(separator=' ', strip=True)
            if hidden:
                visible_no = visible_no.replace(hidden.get('value',''), '').strip()
            # entity = last slash-segment of the visible text
            entity = visible_no.rsplit('/', 1)[-1].strip() if '/' in visible_no else visible_no

            # ── col 3: status ─────────────────────────────────────────────────
            status_text = cells[3].get_text(strip=True)
            status = _status_from_text(status_text)

            # ── col 4: advertising date ───────────────────────────────────────
            pub_date = _parse_date(cells[4].get_text(strip=True))

            # ── col 5: deadline ───────────────────────────────────────────────
            # May contain a clickable link (fn_DetailOfOpeningPop) or plain text
            dead_cell = cells[5]
            dead_a = dead_cell.find('a')
            dead_raw = dead_a.get_text(strip=True) if dead_a else dead_cell.get_text(strip=True)
            deadline = _parse_date(dead_raw)

            # ── category from type code ───────────────────────────────────────
            category = TYPE_CATEGORY.get(type_cd, 'OTHER')

            # ── detail URL ────────────────────────────────────────────────────
            source_url = (
                f'{DETAIL_URL}'
                f'?tendReferNo={ref_no}&tendStageCd={stage_cd}&tendTypeCd={type_cd}'
            )

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ext_id,
                description         = (
                    f'{title}\n\n'
                    f'Entity: {entity}\n'
                    f'Status: {status_text}\n'
                    f'Method: {rf[5] if len(rf) > 5 else ""}\n'
                    f'Type: {type_cd}\n'
                    f'Ref: {ext_id}'
                ).strip(),
                entity              = entity,
                category            = category,
                status              = status,
                currency            = 'RWF',
                country_code        = 'RW',
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_payload(page: int) -> dict:
        """Build POST payload for the full listing search."""
        return {
            'currentPageNo':      str(page),
            'recordCountPerPage': str(PER_PAGE),
            'tendTypeCd':         '',
            'tendMthodCd':        '',
            'tendStatCd':         '',
            'simpldYn':           '',
            'peCd':               '',
            'peCdNm':             '',
            'pubcatFromDt':       '',
            'pubcatToDt':         '',
            'bidopenDt':          '',
            'bidopenHr':          '',
            'bidopenMm':          '',
            'tendReferNo':        '',
            'tendNm':             '',
            'searchConditions':   (
                '[sep]tendTypeCd[sep][sep]tendMthodCd[sep][sep]tendStatCd[sep]'
                '[sep]simpldYn[sep][sep]peCd[sep][sep]peCdNm[sep]'
                '[sep]pubcatFromDt[sep][sep]pubcatToDt[sep]'
                '[sep]bidopenDt[sep][sep]bidopenHr[sep][sep]bidopenMm[sep]'
                '[sep]tendReferNo[sep][sep]tendNm[sep]'
                f'[sep]recordCountPerPage[sep]{PER_PAGE}'
            ),
        }
