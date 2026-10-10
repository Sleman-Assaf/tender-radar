# tenders/scrapers/dgmarket.py
"""
DGMarket — Development Gateway Market — Global Procurement Notices
URL: https://www2.dgmarket.com/NoticeList

What is this?
-------------
DGMarket aggregates procurement notices from governments, international
organizations, and development banks worldwide. It covers tenders funded by
World Bank, UNDP, AfDB, IsDB, UNOPS, EU, GIZ, and many bilateral donors.

AJi Group focus: MENA countries (Jordan, Saudi Arabia, UAE, Iraq, Syria)
covering construction, infrastructure, engineering, consultancy, and services.

Technology: Spring MVC server-rendered HTML.
Filters passed as GET params; session also stores active filter set.
Cards rendered in div.card-body with table layout.

Confirmed card structure (from live page HTML):
    Container: div.card-body > div.row > div
    Title:     a.card-link[href="/Notice/N"] → title text
    Table rows (in order):
        tr[0]: colspan=2 → Buyer name (last span)
        tr[1]: td[0] = Create/Change Date, td[1] = Countries "[Jordan]"
        tr[2]: td[0] = Deadline, td[1] = Type
        tr[3]: td[0] = Original Language
        tr[4]: td[0] = Estimated Value "0 USD" or " ", td[1] = View Details link

Detail URL: https://www2.dgmarket.com/Notice/{id}

Pagination: GET params with &page=N (0-based, Spring MVC default)
    Confirmed pattern: /NoticeList?...&page=0 → page 1
                       /NoticeList?...&page=1 → page 2
    Page size: 25 (default, confirmed from pageSize selector)

Country filter (confirmed from live URL):
    country=iq&country=jo&country=sa&country=sy&country=ae
    + country=kw&country=bh&country=om&country=ye&country=ps
      (Kuwait, Bahrain, Oman, Yemen, Palestine — extended MENA)

Countries field format: "[Jordan]" "[United Arab Emirates]" "[]"
    → parse country name from brackets, e.g. [Jordan] -> JO

Country ISO mapping from display name in Countries field.

Total notices (confirmed): 2,578 for IQ+JO+SA+SY+AE filter
At 25/page = ~104 pages. MAX_PAGES=20 covers ~500 most recent.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www2.dgmarket.com'
LIST_URL  = f'{BASE_URL}/NoticeList'
PAGE_SIZE = 25
MAX_PAGES = 20   # 20 × 25 = 500 most recent tenders

# MENA + extended region countries
COUNTRIES = [
    'jo', 'sa', 'ae', 'iq', 'sy',   # confirmed in screenshot
    'kw', 'bh', 'om', 'ye', 'ps',   # Kuwait, Bahrain, Oman, Yemen, Palestine
    'eg', 'ma', 'tn', 'dz', 'ly',   # Egypt, Morocco, Tunisia, Algeria, Libya
    'lb', 'tr',                       # Lebanon, Turkey
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         LIST_URL,
}

# Country name → ISO code (from Countries field like "[Jordan]")
_COUNTRY_NAME_MAP = {
    'jordan': 'JO', 'saudi arabia': 'SA', 'united arab emirates': 'AE',
    'iraq': 'IQ', 'syria': 'SY', 'kuwait': 'KW', 'bahrain': 'BH',
    'oman': 'OM', 'yemen': 'YE', 'palestine': 'PS', 'egypt': 'EG',
    'morocco': 'MA', 'tunisia': 'TN', 'algeria': 'DZ', 'libya': 'LY',
    'lebanon': 'LB', 'turkey': 'TR', 'ghana': 'GH', 'kenya': 'KE',
    'ethiopia': 'ET', 'nigeria': 'NG', 'tanzania': 'TZ', 'uganda': 'UG',
    'senegal': 'SN', "cote d'ivoire": 'CI', 'ukraine': 'UA',
    'georgia': 'GE', 'india': 'IN', 'south africa': 'ZA',
    'congo, democratic republic': 'CD',
}


class DGMarketScraper(BaseScraper):
    """
    Scraper for DGMarket procurement notices filtered to MENA countries.

    Uses GET requests with country filter params.
    Pagination via &page=N (0-based Spring MVC).
    Parses Bootstrap card grid rendered server-side.
    """

    country_code = 'ZZ'   # multi-country
    source_name  = 'DGMarket — Development Gateway Market'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [DGMarket] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='DGMarketScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [DGMarket] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='DGMarketScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : DGMarketScraper')
        print(f'  MODE    : GET HTML (server-rendered, &page=N pagination)')
        print(f'  FILTER  : {len(COUNTRIES)} MENA countries')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # Build base params (repeated country params)
        base_params = [
            ('keywordsMatching', 'or'),
            ('keywords', ''),
            ('buyerKeywords', ''),
            ('procurementMethod', ''),
            ('startDate', ''),
            ('endDate', ''),
            ('deadlineStartDate', ''),
            ('deadlineEndDate', ''),
            ('estimatedAmountMin', ''),
            ('estimatedAmountMax', ''),
            ('currency', 'USD'),
            ('referenceNumber', ''),
            ('buyerId', ''),
            ('pageSize', str(PAGE_SIZE)),
        ]
        for cc in COUNTRIES:
            base_params.append(('country', cc))

        results:  list[TenderData] = []
        seen_ids: set[str]        = set()
        page      = 0   # 0-based

        while page < MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            params = base_params + [('page', str(page))]

            try:
                resp = self.session.get(
                    LIST_URL, params=params, timeout=self.timeout
                )
                resp.raise_for_status()
                resp.encoding = 'utf-8'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[DGMarket] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[DGMarket] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Debug page 0 — check total and URL actually used
            if page == 0:
                total_el = soup.find(
                    string=re.compile(r'\d[\d,]* notices found', re.I)
                )
                total_str = total_el.strip() if total_el else '?'
                print(f'\n  [DEBUG] {total_str}')
                print(f'  [DEBUG] final URL: {resp.url[:100]}')

                # Check pagination pattern — look for page links
                page_links = soup.select('a[href*="page="]')
                if page_links:
                    print(f'  [DEBUG] pagination: {page_links[0].get("href","")}')
                else:
                    # Try alternate: look for ul.pagination
                    pag = soup.find('ul', class_='pagination')
                    print(f'  [DEBUG] pagination el: {"found" if pag else "NOT found — check page 2 URL"}')
                print()

            # Find tender cards — filtered list uses col-12 single column
            # Featured section uses col-md-6 — exclude it
            # Cards are in the main content area (not featured section)
            cards = self._find_tender_cards(soup)

            if not cards:
                print('(no cards — stopping)')
                if page == 0:
                    print(f'  [DEBUG] HTML snippet: {repr(resp.text[2000:2200])}')
                break

            matched  = 0
            page_new = 0

            for card in cards:
                td = self._parse_card(card)
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

            print(f'-> {len(cards)} cards, {page_new} new, {matched} relevant')
            logger.info(f'[DGMarket] page {page}: {page_new} new, {matched} relevant')

            if len(cards) < PAGE_SIZE:
                print(f'       > Partial page ({len(cards)}) — end of results')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant DGMarket notice(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[DGMarket] complete — total={len(results)}')
        return results

    # ── Card finder ───────────────────────────────────────────────────────

    @staticmethod
    def _find_tender_cards(soup):
        """
        Find tender cards in the filtered results list.
        The page has two sections:
        - Featured/popular: div.container-fluid.mt-3 with col-md-6 cards
        - Main results: the filtered list (col-12 or different container)
        We want all cards that have a /Notice/ link.
        """
        # Find all card-body divs that contain a /Notice/ link
        cards = []
        for cb in soup.select('div.card-body'):
            a = cb.find('a', href=re.compile(r'^/Notice/\d+'))
            if a:
                cards.append(cb)
        # If we get more than 25, the featured section added extras
        # Deduplicate by notice ID
        seen = set()
        unique = []
        for cb in cards:
            a = cb.find('a', href=re.compile(r'^/Notice/\d+'))
            if a:
                nid = re.search(r'/Notice/(\d+)', a['href'])
                nid = nid.group(1) if nid else None
                if nid and nid not in seen:
                    seen.add(nid)
                    unique.append(cb)
        return unique

    # ── Card parser ───────────────────────────────────────────────────────

    def _parse_card(self, card) -> Optional[TenderData]:
        try:
            # Title and ID
            a = card.find('a', class_='card-link',
                          href=re.compile(r'^/Notice/\d+'))
            if not a:
                return None

            title     = ' '.join(a.get_text(strip=True).split())
            href      = a.get('href', '')
            m         = re.search(r'/Notice/(\d+)', href)
            notice_id = m.group(1) if m else ''
            detail_url = f'{BASE_URL}{href}' if href else LIST_URL

            # Parse table rows
            rows = card.select('table tr')
            buyer       = self._row_span(rows, 0, -1)   # last span in row 0
            pub_str     = self._cell_span(rows, 1, 0)   # tr[1] td[0]
            country_str = self._cell_span(rows, 1, 1)   # tr[1] td[1]
            deadline_str= self._cell_span(rows, 2, 0)   # tr[2] td[0]
            type_str    = self._cell_span(rows, 2, 1)   # tr[2] td[1]
            value_str   = self._cell_span(rows, 4, 0)   # tr[4] td[0]

            pub_date = self._parse_dg_date(pub_str)
            deadline = self._parse_dg_date(deadline_str)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            # Country from "[Jordan]" or "[United Arab Emirates]"
            country_cc = self._parse_country(country_str)

            # Estimated value
            est_value = self._parse_value(value_str)

            # Description
            description = f'{type_str} | {buyer}' if buyer else type_str

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = notice_id,
                reference_no        = notice_id,
                entity              = buyer or 'DGMarket',
                entity_ar           = buyer or 'DGMarket',
                description         = description[:300],
                category            = self.detect_category(
                                          f'{title} {type_str} {description}'
                                      ),
                status              = status,
                currency            = 'USD',
                estimated_value     = est_value,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
                country_code        = country_cc,
            )

        except Exception as exc:
            logger.warning(f'[DGMarket] parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _row_span(rows: list, row_idx: int, span_idx: int) -> str:
        """Get text of span[span_idx] in rows[row_idx]."""
        try:
            spans = rows[row_idx].find_all('span')
            return spans[span_idx].get_text(strip=True) if spans else ''
        except (IndexError, AttributeError):
            return ''

    @staticmethod
    def _cell_span(rows: list, row_idx: int, cell_idx: int) -> str:
        """Get last span text in rows[row_idx] td[cell_idx]."""
        try:
            tds = rows[row_idx].find_all('td')
            spans = tds[cell_idx].find_all('span')
            return spans[-1].get_text(strip=True) if spans else ''
        except (IndexError, AttributeError):
            return ''

    @staticmethod
    def _parse_dg_date(val: str) -> Optional[date]:
        """Parse 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD'."""
        val = val.strip()
        if not val:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(val[:19], fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_country(val: str) -> str:
        """Parse country string like [Jordan] to ISO code. Returns ZZ if unknown."""
        m = re.search(r'\[([^\]]+)\]', val)
        if not m:
            return 'ZZ'
        name = m.group(1).strip().lower()
        return _COUNTRY_NAME_MAP.get(name, 'ZZ')

    @staticmethod
    def _parse_value(val: str) -> Optional[float]:
        """Parse '0 USD' or '1500000 USD' → float. Returns None if 0 or empty."""
        m = re.search(r'[\d,]+', val.strip())
        if m:
            try:
                v = float(m.group().replace(',', ''))
                return v if v > 0 else None
            except ValueError:
                pass
        return None
