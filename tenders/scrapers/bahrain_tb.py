# tenders/scrapers/bahrain_tb.py
# Path: TenderRadar/tenders/scrapers/bahrain_tb.py
"""
Bahrain Tender Board (BTB) -- Published Tenders
URL: https://www.tenderboard.gov.bh/Tenders/PublicTenders/

The page uses ASP.NET WebService endpoints:
  POST /Templates/TenderBoardWebService.aspx/GetCurrentPublicTenderPageCount
    -> {"d": N}            -- total pages

  POST /Templates/TenderBoardWebService.aspx/GetCurrentPublicTenderByPage
    -> {"d": "<html>"}     -- rendered HTML table rows for page N

Both POSTs use:
  Content-Type: application/json; charset=utf-8
  Body: single-quoted JSON string (ASP.NET AJAX convention)
    {'tenderNumber':'','ministry':'0','category':'0','tendertype':'0',
     'closingDate_filter':'','publicTenderOnly':'true',
     'prequalificationOnly':'false','auctionOnly':'true',
     'sortingType':'0','listPage':'mainList','Page':'N',
     'smeTendersOnly':'false','sectionName':''}

The HTML response contains a table with columns:
  Tender No | Tender Subject | Purchasing Authority |
  Purchase Before | Closing Date | (details link)
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

BASE_URL   = 'https://www.tenderboard.gov.bh'
PAGE_URL   = 'https://www.tenderboard.gov.bh/Tenders/PublicTenders/'
WS_URL     = 'https://www.tenderboard.gov.bh/Templates/TenderBoardWebService.aspx'
COUNT_URL  = WS_URL + '/GetCurrentPublicTenderPageCount'
PAGE_URL_  = WS_URL + '/GetCurrentPublicTenderByPage'

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':           '*/*',
    'Accept-Language':  'en-US,en;q=0.9',
    'Content-Type':     'application/json; charset=utf-8',
    'Referer':          PAGE_URL,
    'X-Requested-With': 'XMLHttpRequest',
}


def _ws_body(**kwargs) -> str:
    """
    Build the ASP.NET AJAX single-quoted JSON body.
    e.g.  {'tenderNumber':'','ministry':'0','Page':'1'}
    """
    defaults = {
        'tenderNumber':      '',
        'ministry':          '0',
        'category':          '0',
        'tendertype':        '0',
        'closingDate_filter': '',
        'publicTenderOnly':  'true',
        'prequalificationOnly': 'false',
        'auctionOnly':       'true',
        'sortingType':       '0',
        'listPage':          'mainList',
        'Page':              '-1',
        'smeTendersOnly':    'false',
        'sectionName':       '',
    }
    defaults.update(kwargs)
    parts = ["'{k}':'{v}'".format(k=k, v=v) for k, v in defaults.items()]
    return '{' + ','.join(parts) + '}'


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    text = text.strip()
    for fmt in ('%d/%m/%Y', '%d %b %Y', '%d-%m-%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class BahrainTBScraper(BaseScraper):
    """
    Scraper for Bahrain Tender Board (BTB) published tenders.
    Bahrain government central procurement portal.
    """

    source_name  = 'Bahrain -- Tender Board (Published Tenders)'
    country_code = 'BH'
    base_url     = BASE_URL
    MAX_PAGES    = 200

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.update(_HEADERS)

        logger.info(f'[BTB] Starting scrape -- {PAGE_URL}')

        # Warm up: GET page to collect session cookies
        try:
            r = session.get(PAGE_URL, timeout=20)
            logger.info(f'[BTB] Page GET HTTP {r.status_code}')
            if r.status_code == 403:
                logger.error('[BTB] 403 geo-blocked')
                return []
        except requests.RequestException as exc:
            logger.error(f'[BTB] Page GET failed: {exc}')
            return []

        # Step 1: get total page count
        total_pages = self._get_page_count(session)
        if total_pages == 0:
            logger.warning('[BTB] 0 pages returned -- no tenders found')
            return []
        logger.info(f'[BTB] Total pages: {total_pages}')

        # Step 2: scrape each page
        all_results: list[TenderData] = []
        seen_ids: set[str] = set()

        for page_num in range(1, min(total_pages, self.MAX_PAGES) + 1):
            html = self._get_page_html(session, page_num)
            if not html:
                logger.warning(f'[BTB] Empty HTML on page {page_num}')
                break

            tenders = self._parse_html(html, page_num)
            logger.info(f'[BTB] Page {page_num}/{total_pages}: {len(tenders)} tenders')

            for t in tenders:
                if t.external_id not in seen_ids:
                    seen_ids.add(t.external_id)
                    all_results.append(t)

            if page_num < total_pages:
                time.sleep(0.5)

        logger.info(f'[BTB] Scrape complete -- {len(all_results)} unique tenders')
        return all_results

    def _get_page_count(self, session) -> int:
        try:
            body = _ws_body(Page='-1')
            resp = session.post(COUNT_URL, data=body, timeout=30)
            logger.info(f'[BTB] PageCount: HTTP {resp.status_code} ({len(resp.content)}B) '
                        f'preview={resp.text[:100]!r}')
            if resp.status_code != 200:
                return 0
            data = resp.json()
            return int(data.get('d', 0))
        except Exception as exc:
            logger.error(f'[BTB] PageCount failed: {exc}')
            return 0

    def _get_page_html(self, session, page: int) -> str:
        try:
            body = _ws_body(Page=str(page))
            resp = session.post(PAGE_URL_, data=body, timeout=30)
            logger.debug(f'[BTB] Page {page}: HTTP {resp.status_code} ({len(resp.content)}B)')
            if resp.status_code != 200:
                return ''
            data = resp.json()
            return data.get('d', '') or ''
        except Exception as exc:
            logger.error(f'[BTB] Page {page} HTML failed: {exc}')
            return ''

    def _parse_html(self, html: str, page_num: int) -> list[TenderData]:
        """Parse the rendered HTML table rows returned by the WebService."""
        soup = BeautifulSoup(html, 'html.parser')
        results: list[TenderData] = []

        # The response is rendered as a table or div cards
        # Try table rows first
        rows = soup.find_all('tr')
        if rows:
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 4:
                    continue
                t = self._parse_table_row(cols)
                if t:
                    results.append(t)
        else:
            # Try card/div layout
            cards = (
                soup.find_all(class_=re.compile(r'tender', re.I)) or
                soup.find_all('div', class_=re.compile(r'item|card|row', re.I))
            )
            if not cards and page_num == 1:
                logger.warning(f'[BTB] No rows/cards found. HTML preview: {html[:400]!r}')
            for card in cards:
                t = self._parse_card(card)
                if t:
                    results.append(t)

        return results

    def _parse_table_row(self, cols) -> Optional[TenderData]:
        """Parse a <tr> with table data cells."""
        texts = [c.get_text(strip=True) for c in cols]
        if not any(texts):
            return None

        # Typical BTB columns: Tender No | Subject | Entity | Purchase Before | Closing Date | ...
        tender_no   = texts[0] if len(texts) > 0 else ''
        subject     = texts[1] if len(texts) > 1 else ''
        entity      = texts[2] if len(texts) > 2 else ''
        purchase_dt = texts[3] if len(texts) > 3 else ''
        closing_dt  = texts[4] if len(texts) > 4 else ''

        if not subject or subject.lower() in ('tender subject', 'subject', ''):
            return None  # header row

        # Detail URL from any link in the row
        link = None
        for col in cols:
            a = col.find('a', href=True)
            if a:
                href = a['href']
                if not href.startswith('http'):
                    href = BASE_URL + '/' + href.lstrip('/')
                link = href
                break

        ext_id = tender_no or (link.rstrip('/').split('/')[-1] if link else subject[:80])

        logger.debug(f'[BTB] Row: no={tender_no!r} subj={subject[:40]!r} entity={entity!r}')

        return TenderData(
            title               = subject,
            source_url          = link or PAGE_URL,
            external_id         = ext_id[:200],
            reference_no        = tender_no[:100],
            entity              = entity,
            category            = 'OTHER',
            status              = 'OPEN',
            publication_date    = _parse_date(purchase_dt),
            submission_deadline = _parse_date(closing_dt),
            country_code        = 'BH',
        )

    def _parse_card(self, card) -> Optional[TenderData]:
        """Parse a div-based tender card."""
        text = card.get_text(' ', strip=True)
        if not text or len(text) < 10:
            return None

        a = card.find('a', href=True)
        url = a['href'] if a else ''
        if url and not url.startswith('http'):
            url = BASE_URL + '/' + url.lstrip('/')

        title = ''
        for el in card.find_all(['h3', 'h4', 'h5', 'strong', 'b']):
            t = el.get_text(strip=True)
            if t and len(t) > 5:
                title = t
                break
        if not title:
            title = (a.get_text(strip=True) if a else '') or text[:100]

        # Extract tender number
        ref_m = re.search(r'\b(\d{4,}[-/]\d{1,4}|\w+-\d{4,})\b', text)
        ref   = ref_m.group(0) if ref_m else ''

        # Extract dates
        dates = re.findall(r'\d{1,2}/\d{1,2}/\d{4}', text)

        ext_id = ref or url.rstrip('/').split('/')[-1][:200] or title[:80]

        return TenderData(
            title               = title,
            source_url          = url or PAGE_URL,
            external_id         = ext_id,
            reference_no        = ref[:100],
            category            = 'OTHER',
            status              = 'OPEN',
            publication_date    = _parse_date(dates[0]) if dates else None,
            submission_deadline = _parse_date(dates[-1]) if len(dates) > 1 else None,
            country_code        = 'BH',
        )
