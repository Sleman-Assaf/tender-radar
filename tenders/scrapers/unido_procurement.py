# tenders/scrapers/unido_procurement.py
"""
UNIDO — United Nations Industrial Development Organization — Procurement Opportunities
URL: https://www.unido.org/get-involved/procurement/procurement-opportunities

What is this?
-------------
UNIDO is the UN specialized agency for industrial development. It publishes
procurement notices for goods, services, and works related to its projects
globally — including engineering assessments, equipment supply, capacity
building, consultancy, and construction services in developing countries.

Covers MENA, Africa, Asia, and global projects. Many notices involve
engineering, industrial, and infrastructure services relevant to AEC firms.

Technology: Drupal 10 static HTML page.
Single GET, no pagination, no JavaScript required.

Confirmed HTML structure (from live page):
    Two sections on the page:

    1. "Current Opportunities" table:
       selector: table.table.table-bordered.table-striped tbody tr
       Columns (0-indexed):
           td[0]: Title text (may be truncated, no detail link)
           td[1]: Beneficiary Country ("Jordan", "Iraq Iraq", "Global", " Global")
           td[2]: Deadline "DD.MM.YYYY HH:MM:SS CET"
           td[3]: Type ("Request f. Quotation", "Inv. to Bid (1 env.)", "RFP (1 env.)", etc.)
           td[4]: Event Number ("7000008516")
           td[5]: Registration link (all point to login portal — not a detail URL)
       Detail URL: https://www.unido.org/get-involved/procurement/procurement-opportunities
       (no per-tender URLs in Current Opportunities section)

    2. "Additional Notices" table:
       selector: div.procurement-table table tbody tr
       Columns: Title | Country | Deadline | Type | Event Number | Contact | Documents
       The Documents column may contain a "View" link → per-tender PDF/page
       Note: tbody was empty in confirmed HTML — may be populated at runtime

Sample notices (confirmed from live page):
    7000008516 - Engineering Assessment Services for Post-Harvest Facility...  | Jordan
    7000008563 - PURCHASE OF TOOLS AND EQUIPMENT FOR THE ESTABLISHMENT...      | Saudi Arabia
    7000008589 - SERVICES RELATED TO BUILDING THE CAPACITY OF CEMENT...        | Egypt
    7000008584 - Capacity building training and mentorship to local trainers   | Iraq

Date format: DD.MM.YYYY HH:MM:SS CET  e.g. "02.07.2026 17:00:00 CET"
Country: free text, may duplicate ("Iraq Iraq"), "Global" → ZZ
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.unido.org'
LIST_URL  = f'{BASE_URL}/get-involved/procurement/procurement-opportunities'
PORTAL_URL = f'{BASE_URL}/get-involved/procurement/login-unidos-e-procurement-portal'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/149.0.0.0 Safari/537.36'
    ),
    'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language':           'en-US,en;q=0.9',
    'Accept-Encoding':           'gzip, deflate, br',
    'Cache-Control':             'no-cache',
    'Pragma':                    'no-cache',
    'Upgrade-Insecure-Requests': '1',
    'sec-ch-ua':                 '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    'sec-ch-ua-mobile':          '?0',
    'sec-ch-ua-platform':        '"Windows"',
    'sec-fetch-dest':            'document',
    'sec-fetch-mode':            'navigate',
    'sec-fetch-site':            'none',
    'sec-fetch-user':            '?1',
}

# Country name → ISO-2 (from Beneficiary Country field)
_CC_MAP = {
    'jordan': 'JO', 'saudi arabia': 'SA', 'united arab emirates': 'AE',
    'uae': 'AE', 'iraq': 'IQ', 'syria': 'SY', 'egypt': 'EG',
    'morocco': 'MA', 'tunisia': 'TN', 'algeria': 'DZ', 'libya': 'LY',
    'lebanon': 'LB', 'kuwait': 'KW', 'bahrain': 'BH', 'oman': 'OM',
    'yemen': 'YE', 'palestine': 'PS', 'turkey': 'TR', 'türkiye': 'TR',
    'ghana': 'GH', 'kenya': 'KE', 'ethiopia': 'ET', 'nigeria': 'NG',
    'liberia': 'LR', 'senegal': 'SN', 'sudan': 'SD', 'gambia': 'GM',
    'mozambique': 'MZ', 'south africa': 'ZA', 'ukraine': 'UA',
    'india': 'IN', 'indonesia': 'ID', 'vietnam': 'VN', 'viet nam': 'VN',
    'bangladesh': 'BD', 'pakistan': 'PK', 'colombia': 'CO',
    'mexico': 'MX', 'brazil': 'BR', 'philippines': 'PH',
    'austria': 'AT', 'global': 'ZZ', '': 'ZZ',
}


class UNIDOProcurementScraper(BaseScraper):
    """
    Scraper for UNIDO procurement opportunities page.

    Single GET, parses two HTML tables:
    1. Current Opportunities (table.table.table-bordered.table-striped)
    2. Additional Notices (div.procurement-table table) — may be empty

    No pagination — all active tenders on one page.
    """

    country_code = 'ZZ'
    source_name  = 'UNIDO — United Nations Industrial Development Organization'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [UNIDO] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='UNIDOProcurementScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [UNIDO] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='UNIDOProcurementScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : UNIDOProcurementScraper')
        print(f'  MODE    : Single GET (Drupal static page, no pagination)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # Debug: confirm proxy
        proxy = self.session.proxies.get('https') or self.session.proxies.get('http') or '(none)'
        print(f'  PROXY   : {proxy}')

        try:
            # Prime session with homepage first to get cookies (Drupal sets session)
            self.session.get(BASE_URL, timeout=self.timeout)
            import time; time.sleep(1)
            resp = self.session.get(LIST_URL, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
        except Exception as exc:
            print(f'  X  [UNIDO] Error: {exc}')
            logger.error(f'[UNIDO] fetch error: {exc}')
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        results:  list[TenderData] = []
        seen_ids: set[str]        = set()

        # ── Section 1: Current Opportunities ─────────────────────────────
        current_table = soup.select_one(
            'table.table.table-bordered.table-striped'
        )
        current_rows = current_table.select('tbody tr') if current_table else []
        print(f'       > Current Opportunities: {len(current_rows)} row(s)')

        if current_rows:
            r0 = current_rows[0]
            cells = r0.find_all('td')
            print(f'  [DEBUG] row[0] cols={len(cells)}')
            if cells:
                print(f'  [DEBUG] title:   {cells[0].get_text(strip=True)[:60]}')
                print(f'  [DEBUG] country: {cells[1].get_text(strip=True) if len(cells)>1 else "?"}')
                print(f'  [DEBUG] deadline:{cells[2].get_text(strip=True) if len(cells)>2 else "?"}')
                print(f'  [DEBUG] event:   {cells[4].get_text(strip=True) if len(cells)>4 else "?"}')
            print()

        matched = 0
        for row in current_rows:
            td = self._parse_current_row(row)
            if not td:
                continue
            if td.external_id and td.external_id in seen_ids:
                continue
            if td.external_id:
                seen_ids.add(td.external_id)
            combined = f'{td.title} {td.description}'
            if self.detect_relevance(combined):
                results.append(td)
                matched += 1

        print(f'       > Current: {len(current_rows)} rows, {matched} relevant')

        # ── Section 2: Additional Notices ─────────────────────────────────
        additional_table = soup.select_one('div.procurement-table table')
        additional_rows  = additional_table.select('tbody tr') if additional_table else []
        print(f'       > Additional Notices: {len(additional_rows)} row(s)')

        matched2 = 0
        for row in additional_rows:
            td = self._parse_additional_row(row)
            if not td:
                continue
            if td.external_id and td.external_id in seen_ids:
                continue
            if td.external_id:
                seen_ids.add(td.external_id)
            combined = f'{td.title} {td.description}'
            if self.detect_relevance(combined):
                results.append(td)
                matched2 += 1

        if additional_rows:
            print(f'       > Additional: {len(additional_rows)} rows, {matched2} relevant')

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant UNIDO procurement(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[UNIDO] complete — total={len(results)}')
        return results

    # ── Row parsers ───────────────────────────────────────────────────────

    def _parse_current_row(self, row) -> Optional[TenderData]:
        """Parse a row from the Current Opportunities table."""
        try:
            cells = row.find_all('td')
            if len(cells) < 5:
                return None

            title      = ' '.join(cells[0].get_text(strip=True).split())
            country_raw= cells[1].get_text(strip=True)
            deadline_s = cells[2].get_text(strip=True)
            type_str   = cells[3].get_text(strip=True)
            event_no   = cells[4].get_text(strip=True)

            if not title or not event_no:
                return None

            deadline   = self._parse_unido_date(deadline_s)
            today      = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            country_cc = self._parse_country(country_raw)

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = LIST_URL,
                external_id         = event_no,
                reference_no        = event_no,
                entity              = 'UNIDO',
                entity_ar           = 'منظمة الأمم المتحدة للتنمية الصناعية',
                description         = f'{type_str} | Event {event_no}',
                category            = self.detect_category(f'{title} {type_str}'),
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = None,
                submission_deadline = save_deadline,
                opening_date        = None,
                country_code        = country_cc,
            )

        except Exception as exc:
            logger.warning(f'[UNIDO] current row parse error: {exc}')
            return None

    def _parse_additional_row(self, row) -> Optional[TenderData]:
        """
        Parse a row from the Additional Notices table.
        Columns: Title | Country | Deadline | Type | Event Number | Contact | Documents
        Documents column may contain a direct View link.
        """
        try:
            cells = row.find_all('td')
            if len(cells) < 5:
                return None

            title      = ' '.join(cells[0].get_text(strip=True).split())
            country_raw= cells[1].get_text(strip=True) if len(cells) > 1 else ''
            deadline_s = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            type_str   = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            event_no   = cells[4].get_text(strip=True) if len(cells) > 4 else ''

            # Documents column may have a direct link
            detail_url = LIST_URL
            if len(cells) > 6:
                doc_link = cells[6].find('a', href=True)
                if doc_link:
                    href = doc_link['href']
                    detail_url = (
                        f'{BASE_URL}{href}' if href.startswith('/') else href
                    )

            if not title:
                return None

            deadline   = self._parse_unido_date(deadline_s)
            today      = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            country_cc = self._parse_country(country_raw)

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = event_no or title[:30],
                reference_no        = event_no,
                entity              = 'UNIDO',
                entity_ar           = 'منظمة الأمم المتحدة للتنمية الصناعية',
                description         = f'{type_str} | Additional Notice',
                category            = self.detect_category(f'{title} {type_str}'),
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = None,
                submission_deadline = save_deadline,
                opening_date        = None,
                country_code        = country_cc,
            )

        except Exception as exc:
            logger.warning(f'[UNIDO] additional row parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_unido_date(val: str) -> Optional[date]:
        """
        Parse UNIDO deadline format: 'DD.MM.YYYY HH:MM:SS CET'
        e.g. '02.07.2026 17:00:00 CET'
        """
        val = val.strip()
        if not val:
            return None
        # Strip timezone suffix
        val = re.sub(r'\s+[A-Z]{2,4}$', '', val).strip()
        for fmt in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_country(val: str) -> str:
        """
        Parse country name to ISO-2.
        Handles duplicates like 'Iraq Iraq', 'Morocco Morocco'.
        """
        # Deduplicate repeated words: "Iraq Iraq" → "Iraq"
        words = val.strip().split()
        seen_w, unique_words = set(), []
        for w in words:
            lw = w.lower()
            if lw not in seen_w:
                seen_w.add(lw)
                unique_words.append(w)
        clean = ' '.join(unique_words).strip().lower()

        # Direct lookup
        cc = _CC_MAP.get(clean)
        if cc:
            return cc

        # Partial match for longer names
        for key, iso in _CC_MAP.items():
            if key and key in clean:
                return iso

        return 'ZZ'
