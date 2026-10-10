# tenders/scrapers/undp.py
"""
UNDP — United Nations Development Programme Procurement Notices scraper
Source: https://procurement-notices.undp.org/

─────────────────────────────────────────────────────────────────────────────
How the UNDP listing works
─────────────────────────────────────────────────────────────────────────────
This is a ColdFusion (.cfm) static-HTML site.  All tenders appear on a
*single* paginated listing at:

    GET https://procurement-notices.undp.org/index.cfm

The page renders a <div class="vacanciesTable"> block containing N rows,
each row is a clickable <a> anchor (.vacanciesTable__row) with six
<div class="vacanciesTable__cell"> children:

    [0] Title      — inner <span>
    [1] Ref No     — inner <span>
    [2] UNDP Office/Country — inner <span>
    [3] Process    — inner <span>  (ITB / RFP / RFQ / IC / EOI …)
    [4] Deadline   — inner <span> / <nobr>   (e.g. "05-May-26\n05:00 PM…")
    [5] Posted     — inner <span> / <nobr>   (e.g. "20-Apr-26")

Detail page: the <a> href is either
    • view_negotiation.cfm?nego_id=<int>   (Quantum eTendering notices)
    • view_notice.cfm?notice_id=<int>      (legacy notices)

Both live under the same base URL.

Pagination:
    The site dumps ALL current tenders on one page — typically 800-1200 rows.
    There is no standard ColdFusion startrow= parameter exposed in the URL;
    the page ships the full list.  We therefore use pagination_style=SINGLE
    (only one URL, max_pages=1) and parse every row in one pass.

Region/country filter:
    Each row has a CSS class like  region_RAB  (Arab States) or
    region_RAF  (Africa) plus  country_<n>  (process type).
    We parse region → country_code via a mapping dict.
    country_code stays 'UN' when we cannot determine the country.

Relevance/category:
    We do per-item keyword matching on the combined text of title,
    reference no, and country name.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://procurement-notices.undp.org'
LIST_URL    = f'{BASE_URL}/index.cfm'
DETAIL_BASE = BASE_URL

# UNDP region codes → country_code used by TenderRadar
# (we keep the UN umbrella for anything we cannot map more precisely)
REGION_TO_CC = {
    'RAB': 'UN',   # Arab States  — too many countries to pick one
    'RAF': 'UN',   # Africa
    'RAS': 'UN',   # Asia & Pacific
    'RER': 'UN',   # Europe & CIS
    'RLA': 'UN',   # Latin America & Caribbean
    '':    'UN',   # unspecified
}

# Rough process-type → TenderRadar category hint
PROCESS_CATEGORY = {
    'itb':  'CIVIL',   # Invitation to Bid  → usually civil works
    'rfp':  'ADV',     # Request for Proposal → consulting / advisory
    'rfq':  'CIVIL',   # Request for Quotation → goods/small works
    'ic':   'ADV',     # Individual Contractor → consulting
    'eoi':  'ADV',     # Expression of Interest
}

PROCESS_STATUS = {
    'itb': 'OPEN',
    'rfp': 'OPEN',
    'rfq': 'OPEN',
    'ic':  'OPEN',
    'eoi': 'OPEN',
}

# Approximate UNDP country-office code snippet → country_code
# Used if office text contains a recognisable ISO2 name
OFFICE_CC_MAP = {
    'JORDAN':       'JO',
    'JOR':          'JO',
    'EGYPT':        'EG',
    'EGY':          'EG',
    'SAUDI ARABIA': 'SA',
    'SAU':          'SA',
    'UAE':          'AE',
    'MOROCCO':      'MA',
    'MAR':          'MA',
    'IRAQ':         'IQ',
    'IRQ':          'IQ',
    'SOMALIA':      'UN',
    'SOM':          'UN',
    'SUDAN':        'UN',
    'SDN':          'UN',
    'SYRIA':        'UN',
    'SYR':          'UN',
    'LEBANON':      'UN',
    'LBN':          'UN',
    'YEMEN':        'UN',
    'YEM':          'UN',
    'PALESTINE':    'UN',
    'PAL':          'UN',
    'DJIBOUTI':     'DJ',
    'DJI':          'DJ',
    'ETHIOPIA':     'ET',
    'ETH':          'ET',
    'KENYA':        'KE',
    'KEN':          'KE',
    'BURUNDI':      'BI',
    'BDI':          'BI',
    'MOZAMBIQUE':   'MZ',
    'MOZ':          'MZ',
    'MALAWI':       'MW',
    'MWI':          'MW',
    'RWANDA':       'RW',
    'RWA':          'RW',
    'TANZANIA':     'TZ',
    'TZA':          'TZ',
    'UGANDA':       'UG',
    'UGA':          'UG',
    'ZIMBABWE':     'ZW',
    'ZWE':          'ZW',
    'COMOROS':      'KM',
    'COM':          'KM',
    'SOMALIA':      'UN',
    'ERITREA':      'ER',
    'ERI':          'ER',
    'MADAGASCAR':   'MG',
    'MDG':          'MG',
    'MAURITIUS':    'MU',
    'MUS':          'MU',
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection':      'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


class UNDPScraper(BaseScraper):
    """
    Scraper for UNDP Procurement Notices portal.

    The site delivers all open tenders in a single large HTML page (~800+ rows).
    We fetch it once, parse the .vacanciesTable__row <a> anchors, apply
    keyword relevance/category filters, and return matching TenderData items.

    TenderingURL in DB should be:
        url              = https://procurement-notices.undp.org/index.cfm
        pagination_style = SINGLE
        max_pages        = 1      (all tenders on one page)
    """

    country_code = 'UN'
    source_name  = 'UNDP'
    base_url     = BASE_URL

    # ── Main entry point ──────────────────────────────────────────────────────

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='UNDPScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [UNDP] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='UNDPScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : UNDPScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        # ── Fetch the listing page ────────────────────────────────────────────
        try:
            resp = self.get(LIST_URL)
        except Exception as exc:
            print(f'  ✗ [UNDP] GET {LIST_URL} failed: {exc}')
            logger.error(f'[UNDP] GET failed: {exc}', exc_info=True)
            return []

        html = resp.text
        soup = BeautifulSoup(html, 'html.parser')
        rows = self._parse_rows(soup)

        print(f'  [DEBUG] Response length  : {len(html):,} chars')
        print(f'  [DEBUG] Total rows found : {len(rows)}')
        if rows:
            sample = rows[0]
            print(f'  [DEBUG] Sample title     : {sample.title[:80]!r}')
            print(f'  [DEBUG] Sample ref       : {sample.reference_no!r}')
            print(f'  [DEBUG] Sample url       : {sample.source_url!r}')
            print(f'  [DEBUG] Sample country   : {sample.country_code!r}')
            print(f'  [DEBUG] Sample deadline  : {sample.submission_deadline!r}')
        print()

        # ── Relevance + category filtering ────────────────────────────────────
        all_results: list[TenderData] = []
        page_text = soup.get_text(separator=' ', strip=True)

        if self.detect_relevance(page_text):
            # Page-level hit: match every item individually for category only
            for item in rows:
                combined = self._item_text(item)
                if not item.category or item.category == 'OTHER':
                    item.category = self.detect_category(combined)
                all_results.append(item)
            print(f'  → Page-level keyword hit — processing {len(rows)} rows')
        else:
            # Per-item keyword filter
            for item in rows:
                combined = self._item_text(item)
                if self.detect_relevance(combined):
                    if not item.category or item.category == 'OTHER':
                        item.category = self.detect_category(combined)
                    all_results.append(item)
            print(f'  → Per-item filter: {len(all_results)}/{len(rows)} relevant')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant tender(s)  ←  UNDP')
        print(f'  {"═" * 58}\n')
        logger.info(f'[UNDP] scrape() complete — total={len(all_results)}')

        return all_results

    # ── HTML parser ───────────────────────────────────────────────────────────

    def _parse_rows(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse all .vacanciesTable__row anchor elements.

        Structure of each row:
            <a href="view_negotiation.cfm?nego_id=44672"
               class="vacanciesTableLink vacanciesTable__row region_RAF country_7">
                <div class="vacanciesTable__cell">
                    <div class="vacanciesTable__cell__label">Title</div>
                    <span>Software Developer (International IC)</span>
                </div>
                <div class="vacanciesTable__cell">…Ref No…</div>
                <div class="vacanciesTable__cell">…UNDP Office/Country…</div>
                <div class="vacanciesTable__cell">…Process…</div>
                <div class="vacanciesTable__cell">…Deadline…</div>
                <div class="vacanciesTable__cell">…Posted…</div>
            </a>
        """
        rows = soup.select('a.vacanciesTableLink.vacanciesTable__row')
        if not rows:
            # Fallback — broader selector
            rows = soup.select('a.vacanciesTable__row')

        items: list[TenderData] = []

        for row in rows:
            href = row.get('href', '')
            if not href:
                continue

            # Build absolute URL
            if href.startswith('http'):
                source_url = href
            else:
                source_url = f'{BASE_URL}/{href.lstrip("/")}'

            # Extract external id from URL
            ext_id = self._extract_id(href)
            if not ext_id:
                continue

            # Get all cells
            cells = row.select('div.vacanciesTable__cell')
            if len(cells) < 4:
                continue

            # Pull text from each cell — label div is the first child,
            # the content is the <span> or direct text that follows.
            title        = self._cell_span(cells, 0)
            reference_no = self._cell_span(cells, 1)
            office       = self._cell_span(cells, 2)
            process      = self._cell_span(cells, 3)
            deadline_raw = self._cell_span(cells, 4) if len(cells) > 4 else ''
            posted_raw   = self._cell_span(cells, 5) if len(cells) > 5 else ''

            if not title:
                continue

            # CSS classes encode region and process-type
            classes       = row.get('class', [])
            region_code   = self._extract_region(classes)
            country_code  = self._office_to_cc(office, region_code)

            # Process → category / status
            proc_key      = process.lower().split('-')[0].strip()  # e.g. "rfp"
            category      = PROCESS_CATEGORY.get(proc_key, 'OTHER')
            status        = PROCESS_STATUS.get(proc_key, 'OPEN')

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = reference_no,
                description         = (
                    f'{title}\n\nOffice: {office}\n'
                    f'Process: {process}\nRef: {reference_no}'
                ).strip(),
                entity              = office,
                category            = category,
                status              = status,
                currency            = 'USD',
                country_code        = country_code,
                publication_date    = self._parse_date(posted_raw),
                submission_deadline = self._parse_date(deadline_raw),
            ))

        return items

    # ── Cell helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _cell_span(cells: list, index: int) -> str:
        """Extract the visible text from a cell, skipping the label div."""
        if index >= len(cells):
            return ''
        cell = cells[index]
        # Try <span> first (most cells)
        span = cell.find('span')
        if span:
            return span.get_text(separator=' ', strip=True)
        # Try <nobr> (deadline / posted cells)
        nobr = cell.find('nobr')
        if nobr:
            return nobr.get_text(separator=' ', strip=True)
        # Fall back to full cell text minus the label
        label = cell.find(class_='vacanciesTable__cell__label')
        if label:
            label.extract()
        return cell.get_text(separator=' ', strip=True)

    @staticmethod
    def _extract_id(href: str) -> str:
        """
        Extract numeric id from:
            view_negotiation.cfm?nego_id=44672  → "44672"
            view_notice.cfm?notice_id=98814     → "98814"
        """
        m = re.search(r'(?:nego_id|notice_id)=(\d+)', href)
        if m:
            return m.group(1)
        # last resort — use entire query string
        if '?' in href:
            return href.split('?', 1)[1][:40]
        return ''

    @staticmethod
    def _extract_region(classes: list) -> str:
        """Return region suffix from class list, e.g. 'RAB' from 'region_RAB'."""
        for cls in classes:
            if cls.startswith('region_'):
                return cls[len('region_'):]
        return ''

    @staticmethod
    def _office_to_cc(office: str, region_code: str) -> str:
        """
        Try to derive a 2-letter country code from the office string.
        Falls back to region mapping, then 'UN'.

        Office examples:
            "UNDP-JOR/JORDAN"
            "UNDP-LBN/LEBANON"
            "UNDP-EGY/EGYPT"
            "UNDP-HQ/UNITED STATES OF AMERICA"
        """
        upper = office.upper()
        for keyword, cc in OFFICE_CC_MAP.items():
            if keyword in upper:
                return cc
        # Use region → cc mapping as fallback
        return REGION_TO_CC.get(region_code, 'UN')

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        """
        Parse UNDP date strings like:
            "05-May-26 05:00 PM (New York time)"
            "20-Apr-26"
            "07-Dec-26\n05:00 PM (New York time)"
        We only care about the date part.
        """
        if not text:
            return None
        # Take only the first token (date part before any space/newline)
        token = text.strip().split()[0]
        for fmt in ('%d-%b-%y', '%d-%b-%Y', '%d/%m/%Y', '%Y-%m-%d', '%d.%m.%Y'):
            try:
                return datetime.strptime(token, fmt).date()
            except ValueError:
                continue
        return None

    # ── Helper for keyword matching ───────────────────────────────────────────

    @staticmethod
    def _item_text(item: TenderData) -> str:
        """Build combined text for relevance/category keyword matching."""
        return ' '.join(filter(None, [
            item.title, item.reference_no, item.description, item.entity,
        ]))
