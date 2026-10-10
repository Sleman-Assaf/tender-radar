# tenders/scrapers/ashghal.py
# Path: TenderRadar/tenders/scrapers/ashghal.py
"""
Qatar -- Ashghal (Public Works Authority) e-Tenders
URL: https://www.ashghal.gov.qa/en/tenders/pages/erptenders.aspx

Structure (SharePoint-based site):
  - Main listing page renders ALL open tenders in a single HTML table (no AJAX, no pagination).
  - Closed tenders on a separate URL with same table structure.
  - Table columns: Tender No. | Type | Tender Title | Issuing Date | Closing Date | Category

  Open:   /en/Tenders/pages/ERPTenderDetailes.aspx?Status=Open
  Closed: /en/Tenders/pages/ERPTenderDetailes.aspx?Status=Closed

Per user instruction: scrape first 10 pages max (each page = one full HTTP GET,
but the site returns all rows on a single page, so we collect up to MAX_ROWS rows
across Open + Closed tabs to stay within the 10-page intent).

Strategy:
  1. GET Open tenders page  → parse table
  2. GET Closed tenders page → parse table, limit to first 9 pages worth (≈ 10 total fetches)
"""

import logging
import re
import time
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.ashghal.gov.qa'
PAGE_URL   = 'https://www.ashghal.gov.qa/en/tenders/pages/erptenders.aspx'
OPEN_URL   = 'https://www.ashghal.gov.qa/en/Tenders/pages/ERPTenderDetailes.aspx?Status=Open'
CLOSED_URL = 'https://www.ashghal.gov.qa/en/Tenders/pages/ERPTenderDetailes.aspx?Status=Closed'

# The site renders pages of closed tenders via ASP.NET postback.
# Each page fetch = 1 "page" toward the 10-page limit.
# Open tenders are always one single page.
MAX_PAGES = 10

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest':  'document',
    'Sec-Fetch-Mode':  'navigate',
    'Sec-Fetch-Site':  'same-origin',
}


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    text = text.strip()
    # "29 April 2026" or "09 April 2026"
    for fmt in ('%d %B %Y', '%d %b %Y', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _category_from_type(tender_type: str) -> str:
    t = tender_type.strip().upper()
    if t in ('GTC',):
        return 'WORKS'        # General Tender Contract (construction)
    if t in ('STC',):
        return 'CONSULTANCY'  # Specific Tender Contract (consultancy/services)
    return 'OTHER'


def _extract_table_rows(html: str) -> list[dict]:
    """Parse the tender table from the page HTML."""
    soup = BeautifulSoup(html, 'html.parser')

    # Find the tenders table -- it has headers: Tender No. | Type | Tender Title | ...
    target_table = None
    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        if any('tender' in h for h in headers) and any('type' in h for h in headers):
            target_table = table
            break

    if not target_table:
        # Try div-based rows
        logger.warning('[Ashghal] No tender table found, attempting div fallback')
        return []

    rows = []
    for tr in target_table.find_all('tr'):
        cols = tr.find_all('td')
        if len(cols) < 4:
            continue

        # Col 0: Tender No (often a link)
        td_no   = cols[0]
        td_type = cols[1]
        td_title = cols[2]
        td_issue = cols[3]
        td_close = cols[4] if len(cols) > 4 else None
        td_cat   = cols[5] if len(cols) > 5 else None

        tender_no = td_no.get_text(strip=True)
        if not tender_no or tender_no.lower() in ('tender no.', 'no.', ''):
            continue  # skip header rows

        # Extract detail URL
        a = td_no.find('a', href=True)
        url = ''
        if a:
            href = a['href']
            url = href if href.startswith('http') else BASE_URL + href

        # TenderID from URL
        tid_m = re.search(r'TenderID=(\d+)', url)
        tid   = tid_m.group(1) if tid_m else tender_no

        rows.append({
            'tender_no':    tender_no,
            'tender_type':  td_type.get_text(strip=True),
            'title':        td_title.get_text(strip=True),
            'issue_date':   td_issue.get_text(strip=True),
            'close_date':   td_close.get_text(strip=True) if td_close else '',
            'category':     td_cat.get_text(strip=True) if td_cat else '',
            'url':          url,
            'tender_id':    tid,
        })

    return rows


def _extract_viewstate(html: str) -> dict:
    """Extract ASP.NET hidden fields for postback pagination."""
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for field in ('__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION', '__EVENTTARGET', '__EVENTARGUMENT'):
        el = soup.find('input', {'name': field})
        if el:
            fields[field] = el.get('value', '')
    return fields


def _find_next_page_link(html: str, current_page: int) -> Optional[str]:
    """Find the next page link/postback target in pager."""
    soup = BeautifulSoup(html, 'html.parser')
    # Look for pager links with page numbers
    next_page = current_page + 1
    for a in soup.find_all('a', href=True):
        txt = a.get_text(strip=True)
        if txt == str(next_page):
            return a['href']  # may be javascript:__doPostBack(...)
    return None


class AshghalScraper(BaseScraper):
    """
    Scraper for Qatar Public Works Authority (Ashghal) e-Tenders.
    Covers both Open and Closed tenders, limited to first 10 page fetches total.
    """

    source_name  = 'Qatar -- Ashghal (Public Works Authority e-Tenders)'
    country_code = 'QA'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.update(_HEADERS)

        logger.info('[Ashghal] Starting scrape')
        all_results: list[TenderData] = []
        seen_ids: set[str] = set()
        pages_fetched = 0

        # --- Open Tenders (single page, always fetch) ---
        logger.info(f'[Ashghal] Fetching Open tenders: {OPEN_URL}')
        open_html = self._fetch(session, OPEN_URL)
        pages_fetched += 1

        if open_html:
            rows = _extract_table_rows(open_html)
            logger.info(f'[Ashghal] Open: {len(rows)} rows')
            for row in rows:
                t = self._row_to_tender(row, status='OPEN')
                if t and t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    all_results.append(t)

        # --- Closed Tenders (paginated, up to remaining page budget) ---
        remaining = MAX_PAGES - pages_fetched
        if remaining > 0:
            closed = self._scrape_closed(session, remaining, seen_ids)
            all_results.extend(closed)

        logger.info(f'[Ashghal] Done -- {len(all_results)} unique tenders')
        return all_results

    def _scrape_closed(self, session, max_fetches: int, seen_ids: set) -> list[TenderData]:
        results: list[TenderData] = []
        url      = CLOSED_URL
        fetches  = 0
        page_num = 1

        while fetches < max_fetches:
            logger.info(f'[Ashghal] Fetching Closed page {page_num}: {url}')
            html = self._fetch(session, url)
            fetches += 1

            if not html:
                break

            rows = _extract_table_rows(html)
            logger.info(f'[Ashghal] Closed page {page_num}: {len(rows)} rows')

            if not rows:
                break

            for row in rows:
                t = self._row_to_tender(row, status='CLOSED')
                if t and t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    results.append(t)

            # Check for next page via ASP.NET postback pager
            next_target = self._find_postback_next(html, page_num)
            if not next_target:
                logger.info(f'[Ashghal] No more closed pages after page {page_num}')
                break

            # Build postback POST to load next page
            viewstate = _extract_viewstate(html)
            next_html, next_url = self._postback(session, url, viewstate, next_target)
            if not next_html:
                break

            url = next_url or url
            html = next_html
            page_num += 1

            # Process the postback response immediately
            rows = _extract_table_rows(html)
            fetches += 1
            logger.info(f'[Ashghal] Closed page {page_num} (postback): {len(rows)} rows')

            if not rows:
                break

            for row in rows:
                t = self._row_to_tender(row, status='CLOSED')
                if t and t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    results.append(t)

            next_target = self._find_postback_next(html, page_num)
            if not next_target:
                break

            # Set up for next iteration
            viewstate  = _extract_viewstate(html)
            next_html, next_url = self._postback(session, url, viewstate, next_target)
            if not next_html:
                break
            url  = next_url or url
            html = next_html
            page_num += 1
            fetches += 1

            time.sleep(0.5)

        return results

    def _fetch(self, session, url: str) -> str:
        try:
            r = session.get(url, timeout=25)
            logger.info(f'[Ashghal] GET {url} -> HTTP {r.status_code} ({len(r.content)}B)')
            if r.status_code == 200:
                return r.text
            logger.error(f'[Ashghal] Non-200: {r.status_code}')
            return ''
        except requests.RequestException as exc:
            logger.error(f'[Ashghal] Fetch error: {exc}')
            return ''

    def _find_postback_next(self, html: str, current_page: int) -> Optional[str]:
        """
        Find the __doPostBack target for the next page in the pager.
        Returns the eventTarget string or None.
        """
        soup = BeautifulSoup(html, 'html.parser')
        next_page_str = str(current_page + 1)

        for a in soup.find_all('a', href=re.compile(r'__doPostBack', re.I)):
            if a.get_text(strip=True) == next_page_str:
                # href="javascript:__doPostBack('ctl00$...$Pager','3')"
                m = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", a['href'])
                if m:
                    return m.group(1)  # eventTarget

        # Also check onclick
        for a in soup.find_all('a'):
            if a.get_text(strip=True) == next_page_str:
                onclick = a.get('onclick', '') or a.get('href', '')
                m = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", onclick)
                if m:
                    return m.group(1)

        return None

    def _postback(self, session, current_url: str, viewstate: dict, event_target: str):
        """Submit ASP.NET __doPostBack form for pagination."""
        data = {
            '__EVENTTARGET':        event_target,
            '__EVENTARGUMENT':      '',
            '__VIEWSTATE':          viewstate.get('__VIEWSTATE', ''),
            '__VIEWSTATEGENERATOR': viewstate.get('__VIEWSTATEGENERATOR', ''),
            '__EVENTVALIDATION':    viewstate.get('__EVENTVALIDATION', ''),
        }
        try:
            r = session.post(current_url, data=data, timeout=25, headers={
                **_HEADERS,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Referer': current_url,
            })
            logger.info(f'[Ashghal] Postback -> HTTP {r.status_code} ({len(r.content)}B)')
            if r.status_code == 200:
                return r.text, r.url
            return '', current_url
        except requests.RequestException as exc:
            logger.error(f'[Ashghal] Postback error: {exc}')
            return '', current_url

    def _row_to_tender(self, row: dict, status: str) -> Optional[TenderData]:
        title = row['title']
        if not title or len(title) < 3:
            return None

        tender_no = row['tender_no']
        ext_id    = row['tender_id'] or tender_no

        return TenderData(
            title               = title,
            source_url          = row['url'] or OPEN_URL,
            external_id         = ext_id[:200],
            reference_no        = tender_no[:100],
            entity              = 'Public Works Authority (Ashghal)',
            entity_ar           = 'أشغال - هيئة الأشغال العامة',
            category            = _category_from_type(row['tender_type']),
            status              = status,
            publication_date    = _parse_date(row['issue_date']),
            submission_deadline = _parse_date(row['close_date']),
            country_code        = 'QA',
        )
