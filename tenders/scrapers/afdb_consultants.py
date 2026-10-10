# tenders/scrapers/afdb_consultants.py
"""
AfDB — African Development Bank Consultants / EOI / AMI Vacancies
URL: https://www.afdb.org/en/about-us/careers/current-vacancies/consultants

What is this?
-------------
The AfDB Consultants page lists current Expressions of Interest (EOI) and
Appels à Manifestation d'Intérêt (AMI) for both Individual and Firm Consultants
hired directly by the Bank (not project procurement — that's the main AfDB scraper).

These cover:
  • EOI  — Expression of Interest (English)
  • AMI  — Appel à Manifestation d'Intérêt (French)
  • Types: Individual Consultant | Firm Consultant

Technology: Drupal 7 + Views module (same as main AfDB site)

HTML structure (from live page):
    table.vacancies-table-display tbody tr
    ├── td.views-field-title a[href]            → title + detail URL path
    ├── td.views-field-field-consultant-type    → "Individual Consultant" / "Firm Consultant"
    ├── td.views-field-created                  → publication date "12-Jun-2026"
    └── td.views-field-unpublish-on             → closing/unpublish date "19-Jun-2026"

View header: "Displaying 1 - 18 of 18" (currently single page, ~18 items)
Pagination: ?page=N (Drupal Views standard — added for future growth)

Detail URL: https://www.afdb.org + href
  e.g. https://www.afdb.org/en/consultants/eoi-recruitment-senior-consultant-...-94383

Date format: DD-Mon-YYYY e.g. "12-Jun-2026"

RSS feed also available (but blocked from this server):
    https://www.afdb.org/en/about-us/careers/current-vacancies/consultants/rss
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.afdb.org'
LIST_URL   = f'{BASE_URL}/en/about-us/careers/current-vacancies/consultants'
MAX_PAGES  = 20   # 20 × 18 = 360 items max (future-proofed)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}


class AfDBConsultantsScraper(BaseScraper):
    """
    Scraper for AfDB Consultants / EOI / AMI vacancy listings.

    Parses Drupal Views table at:
    https://www.afdb.org/en/about-us/careers/current-vacancies/consultants

    Yields Individual and Firm Consultant opportunities (EOI/AMI).
    Pagination via ?page=N (0-indexed Drupal style).
    """

    country_code = 'ZZ'   # multi-country / corporate AfDB
    source_name  = 'AfDB — Consultants'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [AfDBConsultants] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='AfDBConsultantsScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [AfDBConsultants] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='AfDBConsultantsScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : AfDBConsultantsScraper')
        print(f'  MODE    : HTML scraping (Drupal 7 Views table)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:    list[TenderData] = []
        seen_ids:   set[str]        = set()
        page        = 0
        total_items = None

        while True:
            url = LIST_URL if page == 0 else f'{LIST_URL}?page={page}'
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[AfDBConsultants] WAF block page {page}: {exc}')
                elif '404' in err:
                    print('(404 — end of pages)')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[AfDBConsultants] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            rows = soup.select('table.vacancies-table-display tbody tr')

            # Extract total from view header on page 0
            if total_items is None:
                vh = soup.select_one('.view-header')
                if vh:
                    m = re.search(r'of\s+([\d,]+)', vh.get_text())
                    if m:
                        total_items = int(m.group(1).replace(',', ''))
                        print(f'(total={total_items})', end='  ')

            if not rows:
                print('(empty — stopping)')
                break

            # Debug on page 0
            if page == 0 and rows:
                r0 = rows[0]
                title_a = r0.select_one('td.views-field-title a')
                con_t   = r0.select_one('td.views-field-field-consultant-type')
                pub     = r0.select_one('td.views-field-created')
                close   = r0.select_one('td.views-field-unpublish-on')
                print(f'\n  [DEBUG] title : {title_a.get_text(strip=True)[:65] if title_a else "?"}')
                print(f'  [DEBUG] type  : {con_t.get_text(strip=True) if con_t else "?"}')
                print(f'  [DEBUG] pub   : {pub.get_text(strip=True) if pub else "?"}')
                print(f'  [DEBUG] close : {close.get_text(strip=True) if close else "?"}')
                print()

            matched = 0
            page_new = 0
            for row in rows:
                td = self._parse_row(row)
                if not td:
                    continue
                if td.external_id and td.external_id in seen_ids:
                    continue
                if td.external_id:
                    seen_ids.add(td.external_id)
                page_new += 1
                combined = f'{td.title} {td.description} {td.entity}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(rows)} rows, {page_new} new, {matched} relevant')
            logger.info(
                f'[AfDBConsultants] page {page}: '
                f'{len(rows)} rows, {page_new} new, {matched} relevant'
            )

            if page_new == 0:
                print(f'       > No new items — stopping')
                break
            if len(rows) < 5:
                # Very thin page = end of data
                print(f'       > Thin page ({len(rows)}) — end of data')
                break
            if page >= MAX_PAGES - 1:
                print(f'       > Hit MAX_PAGES cap ({MAX_PAGES})')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant AfDB consultant notice(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[AfDBConsultants] complete — total={len(results)}')
        return results

    # ── Row parser ────────────────────────────────────────────────────────────

    def _parse_row(self, row) -> Optional[TenderData]:
        try:
            # Title + detail URL
            title_cell = row.select_one('td.views-field-title')
            if not title_cell:
                return None
            link    = title_cell.select_one('a')
            title   = link.get_text(strip=True) if link else title_cell.get_text(strip=True)
            href    = link.get('href', '') if link else ''

            if not title:
                return None

            # Build full URL
            if href.startswith('http'):
                source_url = href
            elif href.startswith('/'):
                source_url = f'{BASE_URL}{href}'
            else:
                source_url = LIST_URL

            # External ID from URL slug (numeric suffix e.g. -94383)
            ext_id = ''
            m = re.search(r'-(\d{4,6})$', href.rstrip('/').split('?')[0])
            if m:
                ext_id = m.group(1)

            # Consultant type
            ct_cell = row.select_one('td.views-field-field-consultant-type')
            con_type = ct_cell.get_text(strip=True) if ct_cell else ''

            # Publication date
            pub_cell = row.select_one('td.views-field-created')
            pub_str  = pub_cell.get_text(strip=True) if pub_cell else ''

            # Closing date
            close_cell = row.select_one('td.views-field-unpublish-on')
            close_str  = close_cell.get_text(strip=True) if close_cell else ''

            pub_date = self._parse_afdb_date(pub_str)
            deadline = self._parse_afdb_date(close_str)

            # Status
            today = date.today()
            if deadline and deadline < today:
                status = 'CLOSED'
            else:
                status = 'OPEN'

            # Category from title prefix (EOI/AMI/RFP etc.)
            category = self._cat_from_title(title)

            # Description
            desc_parts = [f'Type: {con_type}' if con_type else '']
            if close_str:
                desc_parts.append(f'Closing: {close_str}')
            description = ' | '.join(filter(None, desc_parts))

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ext_id,
                entity              = 'African Development Bank Group (AfDB)',
                entity_ar           = 'مجموعة بنك التنمية الأفريقي',
                description         = description,
                category            = category,
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[AfDBConsultants] parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_afdb_date(val: str) -> Optional[date]:
        """Parse AfDB date format: 'DD-Mon-YYYY' e.g. '12-Jun-2026'."""
        if not val:
            return None
        val = val.strip()
        for fmt in ('%d-%b-%Y', '%d/%b/%Y', '%d %b %Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _cat_from_title(title: str) -> str:
        """Derive category from title prefix."""
        prefix_map = {
            'EOI': 'CONSULTANCY',
            'AMI': 'CONSULTANCY',
            'RFP': 'CONSULTANCY',
            'RFQ': 'GOODS',
            'IFB': 'WORKS',
            'ICB': 'WORKS',
            'GPN': 'SERVICES',
        }
        prefix = title.split('-')[0].strip().upper()
        return prefix_map.get(prefix, 'CONSULTANCY')
