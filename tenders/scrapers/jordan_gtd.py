# tenders/scrapers/jordan_gtd.py
"""
Jordan — Government Tenders Directorate (GTD)
Portal : https://www.gtd.gov.jo
Page   : /AR/modules/tendersunderoffering

NOTES:
  - ASP.NET WebForms — requires a real browser-like session.
  - First request must be GET to the listing page to obtain cookies
    (__AntiXsrfToken, ASP.NET_SessionId, etc.) and VIEWSTATE.
  - Pagination is done via GET with ?Page=N — NOT a postback.
  - Server closes connections that look like bots (missing headers,
    no Referer, no Accept, suspiciously fast requests).

HTML structure (confirmed from live source 2026-03-29):
  table.table.table-tender
    thead > tr > th × 9:
      [0] رقم العطاء        → reference_no  +  hidden hfId → external_id
      [1] اسم العطاء        → title
      [2] تاريخ الطرح       → publication_date    "YYYY/MM/DD"
      [3] تاريخ الايداع     → submission_deadline "YYYY/MM/DD"
      [4] الوثائق           (usually empty links)
      [5] المرفقات          (usually empty links)
      [6] الملاحق           (usually empty links)
      [7] الاعلان           → PDF announcement link
      [8] التفاصيل          → a.tender-link → detail page
"""

import logging
import random
import re
import time
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Browser-realistic headers ─────────────────────────────────────────────────
_BASE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;'
        'q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,'
        'application/signed-exchange;v=b3;q=0.7'
    ),
    'Accept-Language': 'ar-JO,ar;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
    'DNT': '1',
}


class JordanGTDScraper(BaseScraper):
    country_code = 'JO'
    source_name  = 'Jordan GTD'
    base_url     = 'https://www.gtd.gov.jo'

    LISTING_URL  = 'https://www.gtd.gov.jo/AR/modules/tendersunderoffering'

    # CSS selectors confirmed from live HTML
    TABLE_SEL  = 'table.table-tender'
    ROW_SEL    = 'tbody tr'
    REF_SEL    = 'span.tender-number'
    TITLE_SEL  = 'span[id*="lblTitle"]'
    DETAIL_SEL = 'a.tender-link'

    def __init__(self):
        super().__init__()
        # Override session with full browser-like setup
        self.session = requests.Session()
        self.session.headers.update(_BASE_HEADERS)
        # Longer delays — ASP.NET portals are sensitive to fast requests
        self.page_delay_min = 3.0   # seconds minimum between pages
        self.page_delay_max = 6.0   # seconds maximum between pages
        self._session_primed = False

    # ── Session priming ───────────────────────────────────────────────────────

    def _prime_session(self) -> bool:
        """
        Visit the listing page once to collect cookies and establish a
        legitimate-looking session before paginating.
        Returns True on success.
        """
        try:
            print(f'       ↳ Priming session (GET {self.LISTING_URL}) ...', flush=True)
            resp = self.session.get(
                self.LISTING_URL,
                timeout=self.timeout,
                headers={**_BASE_HEADERS, 'Referer': self.base_url + '/'},
                allow_redirects=True,
            )
            resp.raise_for_status()
            cookies = dict(self.session.cookies)
            print(f'       ↳ Session primed — cookies: {list(cookies.keys())}')
            logger.info(f'[{self.source_name}] Session primed — cookies: {list(cookies.keys())}')
            self._session_primed = True
            # Polite pause after first hit
            time.sleep(random.uniform(2.0, 4.0))
            return True
        except Exception as exc:
            print(f'       ↳ ⚠ Session prime failed: {exc}')
            logger.warning(f'[{self.source_name}] Session prime failed: {exc}')
            return False

    # ── Override scrape_url to inject session priming ─────────────────────────

    def scrape_url(self, tendering_url) -> list[TenderData]:
        """
        Scrape all pages for the GTD URL.
        Primes the session first, then paginates with human-like delays.
        """
        from tenders.models import TenderingURL as TU

        # Prime session once per source run
        if not self._session_primed:
            self._prime_session()

        results: list[TenderData] = []
        max_pages = tendering_url.max_pages or 20

        for page in range(1, max_pages + 1):
            page_url = tendering_url.build_page_url(page)

            # Human-like delay between pages (except the very first)
            if page > 1:
                delay = random.uniform(self.page_delay_min, self.page_delay_max)
                print(f'       ↳ Waiting {delay:.1f}s before page {page}...', flush=True)
                time.sleep(delay)

            print(f'       › Page {page:>3}: {page_url}', end='  ', flush=True)
            logger.info(f'[{self.source_name}] Page {page}: {page_url}')

            try:
                resp = self.session.get(
                    page_url,
                    timeout=self.timeout,
                    headers={
                        **_BASE_HEADERS,
                        'Referer': tendering_url.url if page == 1
                                   else tendering_url.build_page_url(page - 1),
                    },
                    allow_redirects=True,
                )
                resp.raise_for_status()
                soup  = BeautifulSoup(resp.text, 'html.parser')
                items = self.parse_page(soup, tendering_url)

                if not items:
                    print('(no items — stopping)')
                    logger.info(f'[{self.source_name}] Page {page}: no items, stopping')
                    break

                # ── Page-level relevance check ─────────────────────────
                # If ANY keyword appears anywhere in the full page HTML,
                # all tenders on this page are taken. No per-row filtering.
                page_text = BeautifulSoup(resp.text, 'html.parser').get_text(separator=' ', strip=True)

                if self.detect_relevance(page_text):
                    for item in items:
                        if not item.category or item.category == 'OTHER':
                            item.category = self.detect_category(
                                self._build_match_text(item, page_text)
                            )
                    results.extend(items)
                    print(f'→ {len(items)} items, {len(items)} relevant (page hit)')
                    logger.info(
                        f'[{self.source_name}] Page {page}: '
                        f'{len(items)} items, page relevant — all taken'
                    )
                else:
                    print(f'→ {len(items)} items, 0 relevant (page not matched)')
                    logger.info(
                        f'[{self.source_name}] Page {page}: page not relevant — skipped'
                    )

            except requests.exceptions.ConnectionError as exc:
                print(f'✗ Connection reset — server rejected request: {exc}')
                logger.error(f'[{self.source_name}] Page {page} connection error: {exc}')
                # Back off and retry once
                print(f'       ↳ Backing off 15s then retrying once...')
                time.sleep(15)
                try:
                    resp = self.session.get(page_url, timeout=self.timeout, allow_redirects=True)
                    resp.raise_for_status()
                    soup  = BeautifulSoup(resp.text, 'html.parser')
                    items = self.parse_page(soup, tendering_url)
                    retry_page_text = BeautifulSoup(resp.text, 'html.parser').get_text(separator=' ', strip=True)
                    if self.detect_relevance(retry_page_text):
                        for i in items:
                            if not i.category or i.category == 'OTHER':
                                i.category = self.detect_category(self._build_match_text(i, retry_page_text))
                        results.extend(items)
                        print(f'       ↳ Retry OK: {len(items)} items, {len(items)} relevant (page hit)')
                    else:
                        print(f'       ↳ Retry OK: {len(items)} items, 0 relevant (page not matched)')
                except Exception as retry_exc:
                    print(f'       ↳ Retry also failed: {retry_exc} — stopping this URL')
                    logger.error(f'[{self.source_name}] Retry failed: {retry_exc}')
                    break

            except Exception as exc:
                print(f'✗ ERROR: {exc}')
                logger.error(f'[{self.source_name}] Page {page} error: {exc}', exc_info=True)
                break

        return results

    # ── Page parser ───────────────────────────────────────────────────────────

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        """
        Parse one listing page — confirmed column mapping from live HTML.
        Returns [] to signal end of pagination.
        """
        table = soup.select_one(self.TABLE_SEL)
        if not table:
            # Try any table that looks like a tender listing
            table = soup.find('table', attrs={'class': lambda c: c and 'table' in c})
        if not table:
            logger.warning(f'[{self.source_name}] Tender table not found — possible captcha/block')
            return []

        rows = table.select(self.ROW_SEL)
        if not rows:
            return []

        items: list[TenderData] = []
        for row in rows:
            td = self._parse_row(row, tendering_url)
            if td:
                items.append(td)
        return items

    # ── Row parser ────────────────────────────────────────────────────────────

    def _parse_row(self, row: Tag, tendering_url) -> Optional[TenderData]:
        cells = row.find_all('td')
        if len(cells) < 4:
            return None

        # col 0 — رقم العطاء + hidden DB id
        ref_span     = cells[0].select_one(self.REF_SEL)
        reference_no = ref_span.get_text(strip=True) if ref_span else cells[0].get_text(strip=True)
        hidden       = cells[0].find('input', {'type': 'hidden'})
        external_id  = hidden['value'] if hidden and hidden.get('value') else reference_no

        # col 1 — اسم العطاء
        title_span = cells[1].select_one(self.TITLE_SEL)
        title      = title_span.get_text(strip=True) if title_span else cells[1].get_text(strip=True)
        if not title:
            return None

        # col 2 — تاريخ الطرح
        pub_date = self._parse_date(cells[2].get_text(strip=True)) if len(cells) > 2 else None

        # col 3 — تاريخ الايداع (submission deadline)
        deadline = self._parse_date(cells[3].get_text(strip=True)) if len(cells) > 3 else None

        # col 7 — PDF announcement
        adv_link = None
        if len(cells) > 7:
            pdf_a = cells[7].find('a', href=True)
            if pdf_a:
                adv_link = urljoin(self.base_url, pdf_a['href'])

        # col 8 — detail page URL
        detail_url = tendering_url.url
        if len(cells) > 8:
            det_a = cells[8].select_one(self.DETAIL_SEL) or cells[8].find('a', href=True)
            if det_a and det_a.get('href'):
                detail_url = urljoin(self.base_url, det_a['href'])

        return TenderData(
            title               = title,
            source_url          = detail_url,
            external_id         = external_id,
            reference_no        = reference_no,
            entity              = '',
            status              = 'OPEN',
            publication_date    = pub_date,
            submission_deadline = deadline,
        )

    # ── Date parser ───────────────────────────────────────────────────────────

    def _parse_date(self, text: str) -> Optional[date]:
        """GTD format: 'YYYY/MM/DD'. Falls back to other common formats."""
        if not text:
            return None
        val = text.strip()
        if not val:
            return None
        if '.' in val and 'T' not in val:
            val = val[:val.index('.')]
        for fmt, length in [
            ('%Y-%m-%dT%H:%M:%S', 19),
            ('%Y-%m-%dT%H:%M',    16),
            ('%Y-%m-%d',          10),
            ('%Y/%m/%d',          10),
            ('%d/%m/%Y',          10),
            ('%d-%m-%Y',          10),
            ('%d %b %Y',          11),
            ('%B %d, %Y',         None),
        ]:
            try:
                sliced = val[:length] if length else val
                return datetime.strptime(sliced, fmt).date()
            except ValueError:
                continue
        m = re.search(r'(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})', val)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', val)
        if m:
            try:
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
        return None
