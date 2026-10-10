# tenders/scrapers/umucyo_rwanda.py
# Path: TenderRadar/tenders/scrapers/umucyo_rwanda.py
"""
UMUCYO -- Rwanda Public Procurement Portal
https://www.umucyo.gov.rw/eb/bav/selectListAdvertisingListForGU.do

Structure:
  - POST form pagination via currentPageNo field
  - Tender data packed in radio input value:
    tendReferNo|title|entityCode|deadline|stageCd|method|typeCd|openDate|status
  - Detail URL: /eb/bav/selectAdvertisingDtlInfo.do (POST with tendReferNo+stageCd+tendTypeCd)
  - No login required (public portal)
"""

import logging
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.umucyo.gov.rw'
LIST_URL  = f'{BASE_URL}/eb/bav/selectListAdvertisingListForGU.do'
MAX_PAGES = 10

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/147.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         LIST_URL,
}

# Map type codes to categories
_TYPE_MAP = {
    'G':  'GOODS',
    'W':  'WORKS',
    'C':  'CONSULTANCY',
    'NC': 'SERVICES',
}

# Map status codes
_STATUS_MAP = {
    'ADV': 'OPEN',
    'OPE': 'OPEN',
    'EVA': 'OPEN',
    'SIG': 'CLOSED',
    'CAN': 'CLOSED',
    'A':   'OPEN',
    'P':   'OPEN',   # Published
}


class UmucyoRwandaScraper(BaseScraper):
    """Scraper for Rwanda UMUCYO public procurement portal — 10 pages."""

    source_name  = 'UMUCYO -- Rwanda Public Procurement Portal'
    country_code = 'RW'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.update(_HEADERS)

        results = []
        for page_num in range(1, MAX_PAGES + 1):
            tenders = self._fetch_page(session, page_num)
            if not tenders:
                logger.info(f'[UMUCYO] No results on page {page_num}, stopping')
                break
            results.extend(tenders)
            logger.info(f'[UMUCYO] Page {page_num}: {len(tenders)} tenders (total: {len(results)})')

        logger.info(f'[UMUCYO] Done -- {len(results)} tenders')
        return results

    def _fetch_page(self, session: requests.Session, page_num: int) -> list[TenderData]:
        """POST pagination form for page N."""
        data = {
            'currentPageNo':      str(page_num),
            'recordCountPerPage': '10',
            'tendTypeCd':         '',
            'tendMthodCd':        '',
            'tendStatCd':         'ADV',   # Advertised (open) only
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
            'searchConditions':   '',
        }
        try:
            r = session.post(LIST_URL, data=data, timeout=20)
            if r.status_code != 200:
                logger.warning(f'[UMUCYO] Page {page_num}: status {r.status_code}')
                return []
            soup = BeautifulSoup(r.text, 'html.parser')
            return self._parse_table(soup)
        except Exception as exc:
            logger.warning(f'[UMUCYO] Page {page_num} error: {exc}')
            return []

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        tenders = []
        for radio in soup.select('input[type="radio"][name="tenderNo"]'):
            td = self._parse_radio(radio, soup)
            if td:
                tenders.append(td)
        return tenders

    def _parse_radio(self, radio, soup) -> Optional[TenderData]:
        """
        Radio value format:
        tendReferNo|title|entityCode|deadline|stageCd|method|typeCd|openDate|status
        e.g.: 000002/C/NCB/2025/2026/1400000000|Consultancy for...|1400000000|10/06/2026|O|NCB|C|10/06/2026 15:00|P
        """
        val = radio.get('value', '')
        parts = val.split('|')
        if len(parts) < 7:
            return None

        tend_refer_no = parts[0].strip()
        title         = parts[1].strip()
        entity_code   = parts[2].strip()
        deadline_raw  = parts[3].strip()
        stage_cd      = parts[4].strip()
        method        = parts[5].strip()
        type_cd       = parts[6].strip()
        open_date_raw = parts[7].strip() if len(parts) > 7 else ''
        status_raw    = parts[8].strip() if len(parts) > 8 else 'P'

        if not title or len(title) < 5:
            return None

        # Find display tender number (e.g. 000002/C/NCB/2025/2026/MINEDUC)
        display_no = tend_refer_no  # fallback
        row = radio.find_parent('tr')
        if row:
            tds = row.find_all('td')
            if len(tds) >= 2:
                display_no = tds[1].get_text(strip=True) or tend_refer_no

        # Entity name from display number's last segment (e.g. MINEDUC, NAEB)
        entity = self._entity_name(entity_code, row)

        # Source URL: replace internal entity code in tendReferNo with display name
        # e.g. 000005/C/NCB/2025/2026/0902000000 → 000005/C/NCB/2025/2026/NAEB
        import re as _re
        display_last = _re.search(r'/([A-Z0-9]+)$', display_no)
        if display_last:
            # Replace numeric-only last segment with the display abbreviation
            pub_ref = _re.sub(r'/\d+$', f'/{display_last.group(1)}', tend_refer_no)
        else:
            pub_ref = tend_refer_no
        source_url = f'{BASE_URL}/eb/bav/selectListAdvertisingListForGU.do?tendReferNo={pub_ref}'

        # Dates
        deadline = self._parse_date(deadline_raw.split()[0] if deadline_raw else '')
        pub_date = None
        # Publication date: extract from row advertising date column (index 3)
        if row:
            tds = row.find_all('td')
            if len(tds) >= 4:
                pub_date = self._parse_date(tds[3].get_text(strip=True))

        # Status
        status = _STATUS_MAP.get(status_raw, 'OPEN')

        # Category from type code
        category = _TYPE_MAP.get(type_cd, 'SERVICES')

        # Method → description
        desc = f'Method: {method} | Stage: {stage_cd} | Type: {type_cd}'

        return TenderData(
            title               = title[:300],
            source_url          = source_url,
            external_id         = tend_refer_no,
            reference_no        = display_no[:100],
            entity              = entity[:200],
            entity_ar           = '',
            description         = desc,
            category            = category,
            status              = status,
            currency            = 'RWF',
            publication_date    = pub_date,
            submission_deadline = deadline,
            country_code        = 'RW',
        )

    @staticmethod
    def _entity_name(entity_code: str, row) -> str:
        """Try to find entity name from tender reference displayed number."""
        if row:
            tds = row.find_all('td')
            if len(tds) >= 2:
                # The display tender no ends with entity abbreviation e.g. /MINEDUC
                text = tds[1].get_text(strip=True)
                m = re.search(r'/([A-Z0-9]+)$', text)
                if m:
                    return m.group(1)
        return entity_code or 'Rwanda Government Entity'

    @staticmethod
    def _parse_date(val: str) -> Optional[datetime.date]:
        if not val:
            return None
        val = val.strip().split()[0]
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None
