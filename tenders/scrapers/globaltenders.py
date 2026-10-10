# tenders/scrapers/globaltenders.py
"""
GlobalTenders scraper — https://www.globaltenders.com

Filter URL (as provided by user):
    https://www.globaltenders.com/gtsearch
        ?status=menu
        &limit=0                          ← pagination offset (0, 20, 40 ...)
        &sector[]=32                      ← Engineering
        &sector[]=11                      ← Architecture/Construction
        &region_name[]=REG0102            ← Middle East
        &region_name[]=REG0103            ← Africa
        &region_name[]=REG0308            ← East Africa
        &notice_type=gpn,pp,spn,rei,ppn,acn,rfc
        &tender_type=live
        &est_cost_condition=greater_equal

Pagination: limit param increments by page_size per page (0, 20, 40 ...)
HTML structure (typical GlobalTenders layout):
    table or div listing, each row/card contains:
    - title + href to detail page
    - entity/organization
    - country
    - deadline/closing date
    - reference number

DEBUG mode on first page prints raw structure to help fix selectors.
"""

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

# Map country name (as shown by GlobalTenders) → ISO code
# Covers all countries in the Middle East + Africa filter
COUNTRY_MAP = {
    # GCC / Middle East
    'saudi arabia':       'SA', 'uae':                'AE',
    'united arab emirates': 'AE', 'kuwait':           'KW',
    'qatar':              'QA', 'bahrain':            'BH',
    'oman':               'OM', 'jordan':             'JO',
    'iraq':               'IQ', 'egypt':              'EG',
    'syria':              'SY', 'lebanon':            'LB',
    'yemen':              'YE', 'libya':              'LY',
    'tunisia':            'TN', 'algeria':            'DZ',
    'morocco':            'MA', 'sudan':              'SD',
    # East Africa
    'ethiopia':           'ET', 'kenya':              'KE',
    'tanzania':           'TZ', 'uganda':             'UG',
    'rwanda':             'RW', 'burundi':            'BI',
    'somalia':            'SO', 'djibouti':           'DJ',
    'eritrea':            'ER', 'south sudan':        'SS',
    'comoros':            'KM', 'madagascar':         'MG',
    'mozambique':         'MZ', 'malawi':             'MW',
    'zimbabwe':           'ZW', 'zambia':             'ZM',
    'mauritius':          'MU', 'seychelles':         'SC',
}

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.globaltenders.com'
# Real AJAX endpoint discovered from solr_search_new.js:
#   path = site_url + "solr_tender_new/advanceSearch/" + page
#   page = limit offset (0, 20, 40 ...)
AJAX_URL   = f'{BASE_URL}/solr_tender_new/advanceSearch'
MAX_PAGES  = 10
PAGE_SIZE  = 100      # items per page (limit increment)

# These match the hidden form fields on the search page
# (hiddenFields + advanceFields forms serialized)
FIXED_PARAMS = [
    ('status',               'menu'),
    ('region_name[]',        'REG0102'),   # Middle East
    ('region_name[]',        'REG0103'),   # Africa
    ('region_name[]',        'REG0308'),   # East Africa
    ('notice_type',          'gpn,pp,spn,rei,ppn,acn,rfc'),
    ('tender_type',          'live'),
    ('sector[]',             '32'),        # Engineering
    ('sector[]',             '11'),        # Architecture/Construction
    ('bidding_type',         ''),
    ('cpv',                  ''),
    ('est_cost_condition',   'greater_equal'),
    ('est_cost',             ''),
    ('q',                    '0'),
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'en-US,en;q=0.9',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer':         f'{BASE_URL}/gtsearch',
}


class GlobalTendersScraper(BaseScraper):
    """
    Scraper for GlobalTenders.com — engineering & construction tenders
    in Middle East, Africa, and East Africa.
    """

    country_code = 'JO'   # multi-region — default JO
    source_name  = 'GlobalTenders'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='GlobalTendersScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [GlobalTenders] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='GlobalTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)
        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : GlobalTendersScraper')
        print(f'  FILTERS : Engineering(32) + Arch/Const(11)')
        print(f'            Middle East + Africa + East Africa')
        print(f'  PAGES   : {MAX_PAGES} × {PAGE_SIZE} = up to {MAX_PAGES * PAGE_SIZE}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        results: list[TenderData] = []

        for page in range(MAX_PAGES):
            offset = page * PAGE_SIZE
            print(f'       > page {page + 1:>2}/{MAX_PAGES}  (limit={offset})',
                  end='  ', flush=True)

            params = list(FIXED_PARAMS) + [('limit', str(offset))]

            # AJAX URL: /solr_tender_new/advanceSearch/{offset}
            ajax_url = f'{AJAX_URL}/{offset}'
            params   = list(FIXED_PARAMS) + [('limit', str(offset))]

            try:
                resp = self.get(ajax_url, params=params)
                resp.raise_for_status()
            except Exception as exc:
                print(f'✗ ERROR: {exc}')
                logger.error(f'[GlobalTenders] page {page + 1}: {exc}')
                break

            # Response is JSON: {data: "<html>", links: "<html>", h1: "N Results"}
            try:
                body    = resp.json()
                html    = body.get('data', '') or body.get('html', '') or ''
                h1_text = body.get('h1', '')
                if page == 0:
                    print(f'\n  [DEBUG] JSON keys={list(body.keys())}')
                    print(f'  [DEBUG] h1={h1_text!r}  html_len={len(html)}')
                    print()
            except Exception:
                # Fallback: maybe plain HTML
                html = resp.text
                if page == 0:
                    print(f'\n  [DEBUG] Not JSON — plain HTML len={len(html)}')

            soup  = BeautifulSoup(html, 'html.parser')

            # ── DEBUG on first page ────────────────────────────────────────
            if page == 0:
                self._debug_structure(soup)

            items = self._parse_page(soup)

            if not items:
                print('(empty — stopping)')
                break

            matched = 0
            for t in items:
                combined = f"{t.title} {t.description or ''} {t.entity or ''}"
                if self.detect_relevance(combined):
                    results.append(t)
                    matched += 1

            print(f'→ {len(items)} rows, {matched} relevant')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant tender(s)  ←  GlobalTenders')
        print(f'  {"═" * 58}\n')
        return results

    # ── Page parser ───────────────────────────────────────────────────────────
    def _parse_page(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse GlobalTenders AJAX response HTML.
        Each tender is a div.tender-wrap with id=tender_XXXXXXX.
        Uses schema.org microdata attributes for reliable extraction.

        Structure (confirmed from live HTML):
            <div class="tender-wrap" id="tender_133005541">
              <span itemprop="name">Title here</span>
              <a href="...tender-detail/slug" itemprop="url">View Detail</a>
              <span itemprop="address">Saudi Arabia</span>
              <div itemprop="startDate" content="2026-04-08T00:00:00">08 Apr 2026</div>
              <div itemprop="endDate"   content="2026-04-16T23:59:59">16 Apr 2026</div>
            </div>
        """
        items = []
        cards = soup.select('div.tender-wrap')

        for card in cards:
            # ── ext_id from div id attr ────────────────────────────────────
            card_id = card.get('id', '')
            ext_id  = card_id.replace('tender_', '') if card_id else ''

            # ── title ──────────────────────────────────────────────────────
            title_el = card.select_one('[itemprop="name"], [itemprop="summary"]')
            title    = title_el.get_text(strip=True) if title_el else ''
            if not title or len(title) < 5:
                continue

            # ── source_url ─────────────────────────────────────────────────
            link_el    = card.select_one('a[itemprop="url"], a.btn-new')
            if not link_el:
                link_el = card.find('a', href=True)
            href       = link_el.get('href', '') if link_el else ''
            source_url = (urljoin(BASE_URL, href)
                         if href and href.startswith('/') else href or BASE_URL)

            # ── country ────────────────────────────────────────────────────
            country_el = card.select_one('[itemprop="address"], [itemprop="name"]'
                                         ' span[itemprop="address"]')
            country    = country_el.get_text(strip=True) if country_el else ''

            # ── dates from content attribute (ISO format) ──────────────────
            pub_el   = card.select_one('[itemprop="startDate"]')
            dead_el  = card.select_one('[itemprop="endDate"]')
            pub_raw  = (pub_el.get('content', '') or
                        pub_el.get_text(strip=True)) if pub_el else ''
            dead_raw = (dead_el.get('content', '') or
                        dead_el.get_text(strip=True)) if dead_el else ''

            # Map country name → ISO code
            country_code = COUNTRY_MAP.get(country.lower().strip(), '')

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                country_code        = country_code,
                description         = '',    # no description available
                category            = self.detect_category(title),
                status              = 'OPEN',
                currency            = 'USD',
                publication_date    = self._parse_date(pub_raw),
                submission_deadline = self._parse_date(dead_raw),
            ))

        return items

    # ── Debug helper ──────────────────────────────────────────────────────────
    @staticmethod
    def _debug_structure(soup: BeautifulSoup):
        """Print page structure hints to help fix selectors."""
        print()
        # Count tables
        tables = soup.find_all('table')
        print(f'  [DEBUG] tables={len(tables)}')
        for i, t in enumerate(tables[:3]):
            rows = t.find_all('tr')
            t_id = t.get('id', '') or t.get('class', [''])[0]
            print(f'    table[{i}] id/class={t_id!r}  rows={len(rows)}')
            if rows:
                first_data = next((r for r in rows if r.find('td')), None)
                if first_data:
                    cells = first_data.find_all('td')
                    print(f'    → {len(cells)} cells in first data row')
                    for ci, cell in enumerate(cells[:4]):
                        link = cell.find('a', href=True)
                        txt  = cell.get_text(strip=True)[:60]
                        print(f'      col[{ci}]: {txt!r}'
                              + (f'  href={link["href"][:60]}' if link else ''))

        # Count divs with tender-like classes
        tender_divs = soup.select(
            '[class*=tender], [class*=result], [class*=search], [id*=tender]'
        )
        print(f'  [DEBUG] tender-like divs={len(tender_divs)}')
        if tender_divs:
            el = tender_divs[0]
            print(f'    first: <{el.name} class={el.get("class")} id={el.get("id")}>')
        print()

    # ── Date parser ───────────────────────────────────────────────────────────
    @staticmethod
    def _parse_date(text: str) -> Optional[datetime.date]:
        if not text:
            return None
        val = re.sub(r'[^\d/\-\s\w]', '', text).strip()
        if not val:
            return None
        for fmt, length in [
            ('%d/%m/%Y',  10),
            ('%d-%m-%Y',  10),
            ('%Y-%m-%d',  10),
            ('%d %b %Y',  11),
            ('%d/%m/%y',   8),
            ('%b %d, %Y', None),
            ('%d %B %Y',  None),
        ]:
            try:
                sliced = val[:length] if length else val
                return datetime.strptime(sliced, fmt).date()
            except ValueError:
                continue
        return None
