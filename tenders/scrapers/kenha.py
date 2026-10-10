# tenders/scrapers/kenha.py
# Path: TenderRadar/tenders/scrapers/kenha.py
"""
Kenya National Highways Authority (KeNHA) -- Consultancy Services Tenders
URL: https://kenha.co.ke/tenders-consultancy-services/

The page uses the 'Document Library Pro' WordPress plugin, which renders
tenders via DataTables. Data is loaded asynchronously by POSTing to:
  https://kenha.co.ke/wp-admin/admin-ajax.php
  action=dlp_load_posts

The response is a DataTables JSON object:
  {
    "data": [
      {
        "cf:tender_no":   "<raw HTML>",
        "cf:tender_description": "<raw HTML>",
        "cf:Addendum_Clarification_Tender_Notice": "<raw HTML>",
        "link": "<a href='...'>...</a>"
      },
      ...
    ],
    "recordsTotal": N,
    "recordsFiltered": N
  }

We paginate by incrementing the 'start' parameter (DataTables offset).
"""

import logging
import re
import time
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL     = 'https://kenha.co.ke'
PAGE_URL     = 'https://kenha.co.ke/tenders-consultancy-services/'
AJAX_URL     = 'https://kenha.co.ke/wp-admin/admin-ajax.php'
PAGE_ID      = '14513'       # WordPress post ID visible in page HTML
TABLE_ID     = 'dlp_46f880ecd090d038_2'  # data-table id from HTML
PAGE_LENGTH  = 25            # matches the pageLength in page config

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         PAGE_URL,
    'Origin':          BASE_URL,
    'X-Requested-With': 'XMLHttpRequest',
}


def _strip_html(html: str) -> str:
    """Extract visible text from an HTML fragment."""
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(separator=' ', strip=True)


def _extract_link(html: str) -> str:
    """Extract href from an <a> tag HTML fragment."""
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    a = soup.find('a', href=True)
    if a:
        href = a['href']
        if href.startswith('http'):
            return href
        return BASE_URL + href
    return ''


def _make_payload(start: int, nonce: str) -> dict:
    """Build the DataTables AJAX POST payload."""
    return {
        'action':             'dlp_load_posts',
        'nonce':              nonce,
        'post_id':            PAGE_ID,
        'table_id':           TABLE_ID,
        'draw':               str(start // PAGE_LENGTH + 1),
        'start':              str(start),
        'length':             str(PAGE_LENGTH),
        'search[value]':      '',
        'search[regex]':      'false',
        'order[0][column]':   '0',
        'order[0][dir]':      'asc',
        # Column definitions matching the thead
        'columns[0][data]':   'cf:tender_no',
        'columns[0][name]':   'tender_no',
        'columns[0][searchable]': 'true',
        'columns[0][orderable]':  'false',
        'columns[0][search][value]': '',
        'columns[0][search][regex]': 'false',
        'columns[1][data]':   'cf:tender_description',
        'columns[1][name]':   'tender_description',
        'columns[1][searchable]': 'true',
        'columns[1][orderable]':  'false',
        'columns[1][search][value]': '',
        'columns[1][search][regex]': 'false',
        'columns[2][data]':   'cf:Addendum_Clarification_Tender_Notice',
        'columns[2][name]':   'Addendum_Clarification_Tender_Notice',
        'columns[2][searchable]': 'true',
        'columns[2][orderable]':  'false',
        'columns[2][search][value]': '',
        'columns[2][search][regex]': 'false',
        'columns[3][data]':   'link',
        'columns[3][name]':   'link',
        'columns[3][searchable]': 'true',
        'columns[3][orderable]':  'false',
        'columns[3][search][value]': '',
        'columns[3][search][regex]': 'false',
    }


class KeNHAScraper(BaseScraper):
    """
    Scraper for KeNHA (Kenya National Highways Authority) consultancy tenders.
    Uses the Document Library Pro DataTables AJAX endpoint.
    """

    source_name  = 'KeNHA -- Kenya National Highways Authority'
    country_code = 'KE'
    base_url     = BASE_URL
    MAX_PAGES    = 20

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.update(_HEADERS)

        logger.info(f'[KeNHA] Starting scrape -- {PAGE_URL}')

        # Step 1: GET the page to obtain nonce and cookies
        try:
            resp = session.get(PAGE_URL, timeout=30)
            resp.raise_for_status()
            logger.info(f'[KeNHA] Page GET HTTP {resp.status_code} ({len(resp.content)} bytes)')
        except requests.RequestException as exc:
            logger.error(f'[KeNHA] Page GET failed: {exc}')
            return []

        # Extract nonce from page JS: ajax_nonce":"a22fb4387d"
        nonce_match = re.search(r'"ajax_nonce"\s*:\s*"([^"]+)"', resp.text)
        nonce = nonce_match.group(1) if nonce_match else ''
        logger.info(f'[KeNHA] nonce={nonce!r}')
        if not nonce:
            logger.warning('[KeNHA] No nonce found -- AJAX calls may fail')

        all_results: list[TenderData]  = []
        seen_ids:    set[str]          = set()
        start = 0

        for page_num in range(1, self.MAX_PAGES + 1):
            payload = _make_payload(start, nonce)

            try:
                r = session.post(
                    AJAX_URL,
                    data=payload,
                    headers={**_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
                    timeout=30,
                )
                r.raise_for_status()
                data = r.json()
                logger.info(
                    f'[KeNHA] Page {page_num} (start={start}): '
                    f'HTTP {r.status_code}, '
                    f'{len(data.get("data", []))} rows, '
                    f'total={data.get("recordsFiltered", "?")}'
                )
            except requests.RequestException as exc:
                logger.error(f'[KeNHA] AJAX page {page_num} failed: {exc}')
                break
            except ValueError as exc:
                logger.error(f'[KeNHA] JSON parse error page {page_num}: {exc}')
                break

            rows = data.get('data', [])
            if not rows:
                logger.info(f'[KeNHA] No rows on page {page_num} -- done')
                break

            for row in rows:
                tender = self._parse_row(row)
                if tender and tender.external_id not in seen_ids:
                    seen_ids.add(tender.external_id)
                    all_results.append(tender)

            logger.info(
                f'[KeNHA] Page {page_num}: {len(rows)} rows parsed, '
                f'total so far={len(all_results)}'
            )

            total_records = int(data.get('recordsFiltered', 0))
            start += PAGE_LENGTH
            if start >= total_records:
                logger.info(f'[KeNHA] Reached end ({start} >= {total_records})')
                break

            time.sleep(1.0)

        logger.info(f'[KeNHA] Scrape complete -- {len(all_results)} unique tenders')
        return all_results

    def _parse_row(self, row: dict) -> Optional[TenderData]:
        """Parse one DataTables row dict into a TenderData."""
        tender_no   = _strip_html(row.get('cf:tender_no', ''))
        description = _strip_html(row.get('cf:tender_description', ''))
        addendum    = _strip_html(row.get('cf:Addendum_Clarification_Tender_Notice', ''))
        link_html   = row.get('link', '')
        source_url  = _extract_link(link_html) or PAGE_URL

        title = description or tender_no
        if not title:
            return None

        # Build external_id from tender number or URL slug
        if tender_no:
            external_id = re.sub(r'\s+', '_', tender_no.strip())[:200]
        else:
            external_id = source_url.rstrip('/').split('/')[-1][:200]

        # Build description combining addendum info if available
        full_description = description
        if addendum:
            full_description = f'{description} | Addendum: {addendum}'.strip(' |')

        logger.debug(
            f'[KeNHA] Row: no={tender_no!r} '
            f'desc={description[:60]!r} url={source_url}'
        )

        return TenderData(
            title                = title,
            source_url           = source_url,
            external_id          = external_id,
            reference_no         = tender_no[:100] if tender_no else '',
            description          = full_description,
            entity               = 'Kenya National Highways Authority',
            category             = 'OTHER',
            status               = 'OPEN',
            country_code         = 'KE',
        )
