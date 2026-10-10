# tenders/scrapers/ungm.py
"""
UNGM — United Nations Global Marketplace — Procurement Opportunities
URL: https://www.ungm.org/Public/Notice

What is this?
-------------
UNGM (United Nations Global Marketplace) is the official procurement portal
aggregating tenders from 40+ UN agencies: UNDP, UNICEF, WFP, WHO, UNIDO,
UNHCR, FAO, ILO, IOM, UNOPS, ITC, IAEA, and many more.

Replaces the direct UNIDO scraper (which is Cloudflare-blocked).
UNGM includes ALL UNIDO tenders plus tenders from every other UN body.
Filtered to MENA countries for AJi Group relevance.

API (confirmed from browser Network tab):
    POST https://www.ungm.org/Public/Notice/Search
    Content-Type: application/json
    Response: HTML fragments (not JSON), injected into #tblNotices

Confirmed request payload:
    {
        "PageIndex": 1,           ← 1-based page number
        "PageSize": 15,           ← items per page (we use 25)
        "Title": "",
        "Description": "",
        "Reference": "",
        "PublishedFrom": "",
        "PublishedTo": "",
        "DeadlineFrom": "",
        "DeadlineTo": "",
        "IsActive": true,         ← only active notices
        "IsSustainable": false,
        "SortField": "DatePublished",
        "SortAscending": false,   ← newest first
        "Agencies": [],           ← empty = all agencies
        "Countries": [2395, 2471, ...],  ← MENA country IDs
        "NoticeTypes": [],
        "UNSPSCs": [],
        "TypeOfCompetitions": [],
        "NoticeDisplayType": null,
        "NoticeSearchTotalLabelId": "noticeSearchTotal",
        "isPicker": false
    }

Country IDs (from select options on /Public/Notice):
    2395=Jordan, 2471=Saudi Arabia, 2505=UAE, 2389=Iraq,
    2490=Syria, 2353=Egypt, 2431=Morocco, 2498=Tunisia,
    2408=Libya, 2405=Lebanon, 2401=Kuwait, 2308=Bahrain,
    2446=Oman, 2518=Yemen, 2522=Palestine

Response HTML (confirmed from live page, doc 10):
    Rows: div[role=row][data-noticeid="N"] .tableRow.dataRow
    Each row cells (in order):
        [0] Save/EOI buttons (skip)
        [1] div.tableCell.resultTitle > span.ungm-title → title
              + a[href='/Public/Notice/N'] → detail link
        [2] div.tableCell.deadline span → "18-Jun-2026 15:00 (GMT -4.00)"
        [3] div.tableCell span → "22-May-2026" (published date)
        [4] div.tableCell.resultAgency span → "UNDP"
        [5] div.tableCell label → type e.g. "Request for quotation"
        [6] div.tableCell.resultInfo1[data-description=Reference] span → ref
        [7] div.tableCell span → country "Jordan"

    Total: var noticeTotal = "1641"; (at end of response)

Detail URL: https://www.ungm.org/Public/Notice/{noticeid}

Date format: "18-Jun-2026 15:00 (GMT -4.00)" → parse first part
"""

import json
import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.ungm.org'
LIST_URL   = f'{BASE_URL}/Public/Notice'
SEARCH_URL = f'{BASE_URL}/Public/Notice/Search'

PAGE_SIZE  = 15   # UNGM server enforces max 15 per page
MAX_PAGES  = 20   # 20 × 15 = 300 most recent MENA notices   # 20 × 25 = 500 most recent MENA notices

# Country IDs (from select#selNoticeCountry, confirmed from live page)
# MENA core + Africa + Central Asia (AJi Group regional focus)
MENA_COUNTRY_IDS = [
    # MENA core
    2395,  # Jordan
    2471,  # Saudi Arabia
    2505,  # United Arab Emirates
    2389,  # Iraq
    2490,  # Syrian Arab Republic
    2353,  # Egypt
    2431,  # Morocco
    2498,  # Tunisia
    2408,  # Libya
    2405,  # Lebanon
    2401,  # Kuwait
    2308,  # Bahrain
    2446,  # Oman
    2518,  # Yemen
    2522,  # Palestine, State of
    # Africa (AJi active markets)
    2358,  # Ethiopia
    2397,  # Kenya
    2370,  # Ghana
    2443,  # Nigeria
    2462,  # Rwanda
    2472,  # Senegal
    2484,  # Sudan
    2432,  # Mozambique
    2503,  # Uganda
    2507,  # Tanzania
    2481,  # South Africa
    2327,  # Cameroon
    2314,  # Benin
    # Eastern Europe / Central Asia
    2504,  # Ukraine
    2368,  # Georgia
    2510,  # Uzbekistan
    2492,  # Tajikistan
    2396,  # Kazakhstan
]

# Country name (from row) → ISO code
_CC_MAP = {
    'jordan': 'JO', 'saudi arabia': 'SA', 'united arab emirates': 'AE',
    'iraq': 'IQ', 'syrian arab republic': 'SY', 'syria': 'SY',
    'egypt': 'EG', 'morocco': 'MA', 'tunisia': 'TN', 'libya': 'LY',
    'lebanon': 'LB', 'kuwait': 'KW', 'bahrain': 'BH', 'oman': 'OM',
    'yemen': 'YE', 'palestine, state of': 'PS', 'palestine': 'PS',
    'ethiopia': 'ET', 'kenya': 'KE', 'ghana': 'GH', 'nigeria': 'NG',
    'rwanda': 'RW', 'senegal': 'SN', 'sudan': 'SD', 'mozambique': 'MZ',
    'uganda': 'UG', 'tanzania, united republic of': 'TZ', 'tanzania': 'TZ',
    'south africa': 'ZA', 'cameroon': 'CM', 'benin': 'BJ',
    'ukraine': 'UA', 'georgia': 'GE', 'uzbekistan': 'UZ',
    'tajikistan': 'TJ', 'kazakhstan': 'KZ',
    'multiple destinations': 'ZZ', 'global': 'ZZ',
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/149.0.0.0 Safari/537.36'
    ),
    'Accept':          '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Content-Type':    'application/json',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
    'X-Requested-With': 'XMLHttpRequest',
}


class UNGMScraper(BaseScraper):
    """
    Scraper for UNGM (UN Global Marketplace) procurement opportunities.

    Uses the JSON POST API at /Public/Notice/Search filtered to MENA countries.
    Response is HTML fragments (not JSON) containing table rows.
    Covers UNIDO + UNDP + UNICEF + WFP + WHO + FAO + ILO + UNOPS + 35 more.
    """

    country_code = 'ZZ'
    source_name  = 'UNGM — United Nations Global Marketplace'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [UNGM] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='UNGMScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [UNGM] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='UNGMScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : UNGMScraper')
        print(f'  MODE    : POST JSON API → HTML response')
        print(f'  API     : {SEARCH_URL}')
        print(f'  FILTER  : {len(MENA_COUNTRY_IDS)} countries (MENA+Africa+CentralAsia), IsActive=true')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # First: GET /Public/Notice to establish session + get cookies
        try:
            self.session.get(LIST_URL, timeout=self.timeout)
        except Exception:
            pass  # cookies best-effort

        results:  list[TenderData] = []
        seen_ids: set[str]        = set()
        page      = 1
        total     = None

        while page <= MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            payload = {
                'PageIndex':                 page,
                'PageSize':                  PAGE_SIZE,
                'Title':                     '',
                'Description':               '',
                'Reference':                 '',
                'PublishedFrom':             '',
                'PublishedTo':               '',
                'DeadlineFrom':              '',
                'DeadlineTo':                '',
                'IsActive':                  True,
                'IsSustainable':             False,
                'SortField':                 'DatePublished',
                'SortAscending':             False,
                'Agencies':                  [],
                'Countries':                 MENA_COUNTRY_IDS,
                'NoticeTypes':               [],
                'UNSPSCs':                   [],
                'TypeOfCompetitions':        [],
                'NoticeDisplayType':         None,
                'NoticeSearchTotalLabelId':  'noticeSearchTotal',
                'isPicker':                  False,
            }

            try:
                resp = self.session.post(
                    SEARCH_URL,
                    data=json.dumps(payload),
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                resp.encoding = 'utf-8'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[UNGM] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[UNGM] page {page}: {exc}')
                break

            html = resp.text

            # Extract total from: var noticeTotal = "1641";
            if total is None:
                m = re.search(r'var\s+noticeTotal\s*=\s*["\'](\d+)["\']', html)
                if m:
                    total = int(m.group(1))
                    total_pages = -(-total // PAGE_SIZE)
                    print(f'\n  [DEBUG] total={total}, pages≤{min(total_pages, MAX_PAGES)}')
                    print(f'  [DEBUG] response size={len(html)}')

            soup = BeautifulSoup(html, 'html.parser')
            rows = soup.find_all('div', attrs={'role': 'row', 'data-noticeid': True})

            if not rows:
                print('(no rows — stopping)')
                if page == 1:
                    print(f'  [DEBUG] HTML snippet: {repr(html[:300])}')
                break

            if page == 1 and rows:
                r0 = rows[0]
                nid = r0.get('data-noticeid', '?')
                title_el = r0.select_one('span.ungm-title')
                deadline_el = r0.select_one('.deadline span')
                agency_el = r0.select_one('.resultAgency span')
                print(f'  [DEBUG] row[0] id={nid}')
                print(f'  [DEBUG] title:    {title_el.get_text(strip=True)[:60] if title_el else "?"}')
                print(f'  [DEBUG] deadline: {deadline_el.get_text(strip=True) if deadline_el else "?"}')
                print(f'  [DEBUG] agency:   {agency_el.get_text(strip=True) if agency_el else "?"}')
                print()

            matched  = 0
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
                combined = f'{td.title} {td.description}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(rows)} rows, {page_new} new, {matched} relevant')
            logger.info(f'[UNGM] page {page}: {page_new} new, {matched} relevant')

            if len(rows) < PAGE_SIZE:
                print(f'       > Partial page ({len(rows)}) — end of results')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant UNGM notice(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[UNGM] complete — total={len(results)}')
        return results

    # ── Row parser ────────────────────────────────────────────────────────

    def _parse_row(self, row) -> Optional[TenderData]:
        try:
            notice_id = row.get('data-noticeid', '')

            # Title
            title_el = row.select_one('span.ungm-title')
            title = ' '.join(title_el.get_text(strip=True).split()) if title_el else ''
            if not title:
                return None

            # Detail URL
            detail_link = row.select_one('a[href^="/Public/Notice/"]')
            detail_url = (
                f'{BASE_URL}{detail_link["href"]}' if detail_link
                else f'{BASE_URL}/Public/Notice/{notice_id}'
            )

            # Deadline
            deadline_el = row.select_one('.deadline span')
            deadline_str = deadline_el.get_text(strip=True) if deadline_el else ''
            deadline = self._parse_ungm_date(deadline_str)

            # Published date (2nd non-deadline data cell)
            cells = row.select('div[role=cell]')
            pub_date = None
            if len(cells) > 3:
                pub_str = cells[3].get_text(strip=True)
                pub_date = self._parse_ungm_date(pub_str)

            # Agency
            agency_el = row.select_one('.resultAgency span')
            agency = agency_el.get_text(strip=True) if agency_el else 'UNGM'

            # Type
            type_el = row.select_one('div[role=cell] label')
            type_str = type_el.get_text(strip=True) if type_el else ''

            # Reference
            ref_el = row.select_one('[data-description="Reference"] span')
            ref = ref_el.get_text(strip=True) if ref_el else ''

            # Country (last cell)
            country_str = ''
            if cells:
                country_str = cells[-1].get_text(strip=True)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            country_cc = _CC_MAP.get(country_str.lower(), 'ZZ')
            description = f'{type_str} | {agency}' if agency else type_str

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = notice_id,
                reference_no        = ref or notice_id,
                entity              = agency,
                entity_ar           = agency,
                description         = description[:300],
                category            = self.detect_category(
                                          f'{title} {type_str} {description}'
                                      ),
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
                country_code        = country_cc,
            )

        except Exception as exc:
            logger.warning(f'[UNGM] row parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ungm_date(val: str) -> Optional[date]:
        """
        Parse UNGM date formats:
          '18-Jun-2026 15:00 (GMT -4.00)' → strip timezone, parse
          '22-May-2026'
        """
        val = val.strip()
        if not val:
            return None
        # Strip timezone: " (GMT ...)"
        val = re.sub(r'\s*\(GMT[^)]*\)', '', val).strip()
        for fmt in ('%d-%b-%Y %H:%M', '%d-%b-%Y', '%d-%b-%y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None
