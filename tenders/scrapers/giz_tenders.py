# tenders/scrapers/giz_tenders.py
"""
GIZ — Vergabemarktplatz (GIZ Procurement Portal)
URL: https://ausschreibungen.giz.de/Satellite/company/welcome.do

What is this?
-------------
The GIZ Vergabemarktplatz (procurement marketplace) is the official e-tendering
platform of Deutsche Gesellschaft für Internationale Zusammenarbeit (GIZ) GmbH,
the German development cooperation agency. GIZ implements projects worldwide and
publishes international procurement notices here covering all countries where
GIZ operates — including Jordan, MENA, Africa, and globally.

Technology: Java EE / cosinex DTVP platform. Static server-rendered HTML.
No authentication required for the public listing.

HTML structure (confirmed from live page):
    Table: table.csx-new-table
    thead: Published | Submission deadline | Short Description | Type | Contracting authority | Action
    tbody tr (one row per notice):
        td[0]: publication date
               <abbr title='DD.MM.YYYY at HH:MM o'clock'>DD.MM.YYYY</abbr>
        td[1]: submission deadline
               <abbr title='DD.MM.YYYY at HH:MM o'clock'>DD.MM.YYYY</abbr>
               OR <abbr title="There is no deadline...">n.a.</abbr>
        td[2]: title / short description (text, no link)
        td[3]: notice type e.g. "UVgO\nNotice", "VgV\nContract award notice"
        td[4]: contracting authority (always GIZ GmbH)
        td[5]: <a href="/Satellite/public/company/projectForwarding.do?pid=NNNNN">
               External ID: pid value from URL

    tfoot pagination:
        <div class='browsePages'>
            Page: 1 of 6 - Total amount of entries: 113
            <a class='browseForward' href='/Satellite/.../selectedTablePagePROJECT_RESULT=2'>
            <a class='browseLast' href='/Satellite/.../selectedTablePagePROJECT_RESULT=6'>

Pagination URL pattern:
    Page 1: https://ausschreibungen.giz.de/Satellite/company/welcome.do
    Page N: https://ausschreibungen.giz.de/Satellite/company/welcome.do
             ?method=showTable&fromSearch=1&selectedTablePagePROJECT_RESULT=N

Type filtering (td[3]):
    KEEP: "Notice", "CFC" (Call for Competition)  → OPEN
    SKIP: "Contract award notice"                  → already awarded
    SKIP: "Contract modification notice"           → modification of existing contract

Date format: "DD.MM.YYYY" e.g. "12.06.2026"
             Parsed from abbr title: "12.06.2026 at 21:54 o'clock"

Detail URL: https://ausschreibungen.giz.de/Satellite/public/company/projectForwarding.do?pid=NNNNN

Total notices: ~113 (6 pages of ~20)
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://ausschreibungen.giz.de'
LIST_URL  = f'{BASE_URL}/Satellite/company/welcome.do'
MAX_PAGES = 10   # safety cap; typically 6 pages

# Notice types to SKIP (awarded/modified = not open tenders)
SKIP_TYPES = {
    # English
    'contract award notice',
    'contract modification notice',
    # German
    'auftragsänderung',
    'bekanntmachung vergebener aufträge',
    'vergabebekanntmachung',
    'bekanntmachung über auftragsänderung',
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
    'Referer':         BASE_URL,
}


class GIZTendersScraper(BaseScraper):
    """
    Scraper for GIZ Vergabemarktplatz (GIZ procurement portal).

    Parses static HTML table from ausschreibungen.giz.de.
    Pagination via GET with selectedTablePagePROJECT_RESULT=N.
    Filters to only open tenders (skips award/modification notices).
    """

    country_code = 'ZZ'   # Multi-country; detected per tender from title
    source_name  = 'GIZ — Vergabemarktplatz'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [GIZTenders] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='GIZTendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [GIZTenders] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='GIZTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : GIZTendersScraper')
        print(f'  MODE    : HTML scraping (cosinex DTVP platform)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_pids: set[str]        = set()
        page       = 1
        max_page   = MAX_PAGES

        while page <= max_page:
            print(f'       > page {page:>3}', end='  ', flush=True)

            if page == 1:
                url = f'{LIST_URL}?lang=en'
            else:
                url = (
                    f'{LIST_URL}?lang=en&method=showTable&fromSearch=1'
                    f'&selectedTablePagePROJECT_RESULT={page}'
                )

            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                # GIZ site is ISO-8859-1
                resp.encoding = 'iso-8859-1'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[GIZTenders] WAF block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[GIZTenders] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Extract total pages from pagination on page 1
            if page == 1:
                max_page = self._get_max_page(soup)
                total    = self._get_total(soup)
                print(f'(total={total}, pages={max_page})', end='  ')
                max_page = min(max_page, MAX_PAGES)

            # Parse table
            table = soup.find('table', class_='csx-new-table')
            if not table:
                print('(table not found — stopping)')
                break

            rows = [
                r for r in table.find_all('tr')
                if r.find('td') and not r.find('th')
            ]
            # Skip tfoot row
            rows = [r for r in rows if 'browsePages' not in str(r)]

            if not rows:
                print('(no rows — stopping)')
                break

            # Debug on page 1
            if page == 1 and rows:
                cells = rows[0].find_all('td')
                print(f'\n  [DEBUG] pub:   {self._abbr_date(cells[0]) if cells else "?"}')
                print(f'  [DEBUG] ddl:   {self._abbr_date(cells[1]) if len(cells)>1 else "?"}')
                print(f'  [DEBUG] title: {cells[2].get_text(strip=True)[:60] if len(cells)>2 else "?"}')
                print(f'  [DEBUG] type:  {repr(cells[3].get_text(strip=True)) if len(cells)>3 else "?"}')
                link_el = cells[5].find('a') if len(cells) > 5 else None
                print(f'  [DEBUG] link:  {link_el["href"] if link_el else "?"}')
                print()

            matched  = 0
            page_new = 0
            skipped  = 0
            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 5:
                    continue

                title    = ' '.join(cells[2].get_text(strip=True).split())
                type_txt = ' '.join(cells[3].get_text(strip=True).split()).lower()

                # Skip award/modification notices
                if any(skip in type_txt for skip in SKIP_TYPES):
                    skipped += 1
                    continue

                if not title:
                    continue

                # PID from link
                pid = ''
                if len(cells) > 5:
                    link_el = cells[5].find('a')
                    if link_el:
                        m = re.search(r'pid=(\d+)', link_el.get('href', ''))
                        if m:
                            pid = m.group(1)

                if pid and pid in seen_pids:
                    continue
                if pid:
                    seen_pids.add(pid)
                page_new += 1

                # Dates
                pub_date = self._parse_dtvp_date(self._abbr_date(cells[0]))
                deadline = self._parse_dtvp_date(self._abbr_date(cells[1]))

                # Detail URL
                detail_url = (
                    f'{BASE_URL}/Satellite/public/company/projectForwarding.do?pid={pid}'
                    if pid else LIST_URL
                )

                today  = date.today()
                if deadline and deadline < today:
                    status        = 'CLOSED'
                    save_deadline = None
                else:
                    status        = 'OPEN'
                    save_deadline = deadline

                # Notice type label
                type_label = ' '.join(cells[3].get_text(strip=True).split())

                combined = f'{title} {type_label}'

                if self.detect_relevance(combined):
                    detected_cc = self._detect_country(title)
                    results.append(TenderData(
                        title               = title,
                        title_ar            = title,
                        source_url          = detail_url,
                        external_id         = pid,
                        reference_no        = pid,
                        entity              = 'GIZ — Deutsche Gesellschaft für Internationale Zusammenarbeit',
                        entity_ar           = 'GIZ — الشركة الألمانية للتعاون الدولي',
                        description         = type_label,
                        category            = self.detect_category(combined),
                        status              = status,
                        currency            = 'EUR',
                        country_code        = detected_cc,
                        estimated_value     = None,
                        publication_date    = pub_date,
                        submission_deadline = save_deadline,
                        opening_date        = None,
                    ))
                    matched += 1

            print(f'-> {len(rows)} rows, {page_new} new ({skipped} skipped), {matched} relevant')
            logger.info(
                f'[GIZTenders] page {page}: '
                f'{len(rows)} rows, {page_new} new, {skipped} skipped, {matched} relevant'
            )

            if page_new == 0 and page > 1:
                print(f'       > No new rows — stopping')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant GIZ tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[GIZTenders] complete — total={len(results)}')
        return results

    # ── Helpers ───────────────────────────────────────────────────────────

    # Country keyword → ISO alpha-2 mapping (ordered: longer/more specific first)
    _COUNTRY_MAP = {
        # MENA
        'jordan': 'JO', 'jordanien': 'JO',
        'egypt': 'EG', 'ägypten': 'EG',
        'morocco': 'MA', 'maroc': 'MA', 'marokko': 'MA', 'ourika': 'MA',
        'tunisia': 'TN', 'tunesien': 'TN', 'tunisie': 'TN',
        'algeria': 'DZ', 'algerian': 'DZ', 'algerien': 'DZ', 'algérie': 'DZ',
        'libya': 'LY', 'libyen': 'LY',
        'lebanon': 'LB', 'libanon': 'LB',
        'iraq': 'IQ', 'irak': 'IQ',
        'yemen': 'YE', 'jemen': 'YE',
        'saudi': 'SA',
        'oman': 'OM',
        'kuwait': 'KW',
        'bahrain': 'BH',
        'qatar': 'QA',
        'palestine': 'PS', 'palestinian': 'PS',
        'syria': 'SY', 'syrien': 'SY',
        # Africa
        'senegal': 'SN', 'dakar': 'SN',
        'ghana': 'GH',
        'nigeria': 'NG',
        'kenya': 'KE',
        'ethiopia': 'ET', 'horn of africa': 'ET',
        'tanzania': 'TZ',
        'uganda': 'UG',
        'rwanda': 'RW',
        'mozambique': 'MZ',
        'zambia': 'ZM',
        'zimbabwe': 'ZW',
        'malawi': 'MW',
        'somalia': 'SO',
        'sudan': 'SD',
        'south sudan': 'SS',
        'cameroon': 'CM', 'kamerun': 'CM',
        "cote d'ivoire": "CI", "ivory coast": "CI",
        'burkina faso': 'BF',
        'mali': 'ML',
        'niger': 'NE',
        'benin': 'BJ',
        'togo': 'TG',
        'liberia': 'LR',
        'sierra leone': 'SL',
        'guinea': 'GN',
        'madagascar': 'MG',
        'angola': 'AO',
        'namibia': 'NA',
        'botswana': 'BW',
        'south africa': 'ZA', 'südafrika': 'ZA',
        'djibouti': 'DJ',
        'eritrea': 'ER',
        'congo': 'CG',
        # Asia
        'india': 'IN', 'himachal': 'IN', 'rajasthan': 'IN',
        'indonesia': 'ID',
        'pakistan': 'PK',
        'bangladesh': 'BD',
        'vietnam': 'VN',
        'myanmar': 'MM',
        'cambodia': 'KH',
        'nepal': 'NP',
        'philippines': 'PH',
        'thailand': 'TH',
        'laos': 'LA',
        'mongolia': 'MN',
        'kyrgyzstan': 'KG',
        'tajikistan': 'TJ',
        'uzbekistan': 'UZ',
        'kazakhstan': 'KZ',
        'georgia': 'GE',
        'armenia': 'AM',
        'azerbaijan': 'AZ',
        # Europe/Balkans
        'ukraine': 'UA',
        'moldova': 'MD',
        'albania': 'AL', 'tirana': 'AL',
        'western balkans': 'RS', 'balkans': 'RS',
        'kosovo': 'XK',
        'serbia': 'RS',
        'north macedonia': 'MK',
        'bosnia': 'BA',
        'montenegro': 'ME',
        # Americas
        'colombia': 'CO',
        'peru': 'PE',
        'bolivia': 'BO',
        'ecuador': 'EC',
        'honduras': 'HN',
        'guatemala': 'GT',
        'haiti': 'HT',
        # DE internal (deprioritize)
        'eschborn': 'DE', 'bonn': 'DE',
    }

    @classmethod
    def _detect_country(cls, title: str) -> str:
        """Detect country ISO code from tender title keywords."""
        t = title.lower()
        # Check multi-word keys first
        for keyword in sorted(cls._COUNTRY_MAP, key=len, reverse=True):
            if keyword in t:
                return cls._COUNTRY_MAP[keyword]
        return 'ZZ'  # multi-country / unknown

    @staticmethod
    def _abbr_date(cell) -> str:
        """Extract date string from <abbr title='DD.MM.YYYY at ...'> in a td."""
        abbr = cell.find('abbr') if cell else None
        if not abbr:
            return ''
        title = abbr.get('title', '')
        # "12.06.2026 at 21:54 o'clock" → "12.06.2026"
        m = re.match(r'(\d{2}\.\d{2}\.\d{4})', title)
        return m.group(1) if m else ''

    @staticmethod
    def _parse_dtvp_date(val: str) -> Optional[date]:
        """Parse 'DD.MM.YYYY' → date."""
        if not val:
            return None
        try:
            return datetime.strptime(val.strip(), '%d.%m.%Y').date()
        except ValueError:
            return None

    @staticmethod
    def _get_max_page(soup) -> int:
        """Extract max page number from pagination div."""
        pages_div = soup.find('div', class_='browsePages')
        if not pages_div:
            return 1
        # Try browseLast link first: href="...&selectedTablePagePROJECT_RESULT=6"
        last_link = pages_div.find('a', class_='browseLast')
        if last_link:
            m = re.search(r'selectedTablePagePROJECT_RESULT=(\d+)', last_link.get('href', ''))
            if m:
                return int(m.group(1))
        # Fallback: text "Page: 1 of 6" or "Seite: 1 von 6"
        text = pages_div.get_text()
        m = re.search(r'(?:of|von)\s+(\d+)', text)
        return int(m.group(1)) if m else 1

    @staticmethod
    def _get_total(soup) -> int:
        """Extract total notice count from pagination div."""
        pages_div = soup.find('div', class_='browsePages')
        if not pages_div:
            return 0
        text = pages_div.get_text()
        m = re.search(r'(?:Total amount of entries|Anzahl der Eintr.ge):\s*(\d+)', text)
        return int(m.group(1)) if m else 0
