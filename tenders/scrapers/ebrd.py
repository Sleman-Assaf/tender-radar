# tenders/scrapers/ebrd.py
"""
EBRD — European Bank for Reconstruction and Development
Source: https://ecepp.ebrd.com/delta/noticeSearchResults.html

─────────────────────────────────────────────────────────────────────────────
How the EBRD ECEPP listing works
─────────────────────────────────────────────────────────────────────────────
The page is server-rendered HTML.  The entire notice table is delivered in
one page as a <table id="noticeResultsTable">.  DataTables runs client-side
filtering/sorting on top of this static HTML — there is no API call for
pagination.  We therefore fetch the page ONCE and parse up to MAX_ROWS rows.

Row structure (10 <td> per <tr>):

    col 0  — <a href="viewNotice.html?displayNoticeId=NNNNN">Country: Title</a>
    col 1  — Notice type  (visible)
    col 2  — Procurement Exercise Title (visible)
    col 3  — Published date display  "21/04/2026 07:50 UK Time" (visible)
    col 4  — Closing date display    "02/06/2026 16:00 UK Time" / "N/A" (visible)
    col 5  — Current state           "Open" / "Preliminary" / "Information Only" (visible)
    col 6  — Published date short    "21/04/2026"  (hidden, used by extractor)
    col 7  — Published sortable      "202604210750" (hidden)
    col 8  — Closing sortable        "202606021600" (hidden, empty = N/A)
    col 9  — Metadata array (hidden):
             "[project_name, project_id, country, proc_title,
               notice_id, goods_type, method, entity, sector, notice_type]"

External ID:  displayNoticeId from the href in col 0.
Detail URL:   https://ecepp.ebrd.com/delta/viewNotice.html?displayNoticeId=NNNNN

Country:      col 9 index 2 (plain name, e.g. "Ukraine", "Turkey").
              Also prefixed in the title text as "Country: Title".

Goods type → category:
    Works / Civil         → CIVIL
    Consultancy           → ADV
    Goods                 → OTHER
    (default)             → OTHER

Notice type → status:
    Invitation For Tenders … → OPEN
    Contract Award Notice     → CLOSED (information only)
    Addendum / Amendment      → CLOSED (information only)
    Preliminary               → OPEN  (upcoming)
    (default)                 → OPEN

Pagination:
    pagination_style = SINGLE  (one fetch, MAX_ROWS cap, no looping)
    max_pages        = 1
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://ecepp.ebrd.com'
LIST_URL    = f'{BASE_URL}/delta/noticeSearchResults.html'
DETAIL_BASE = f'{BASE_URL}/delta/viewNotice.html'

MAX_ROWS = 200   # cap — EBRD page can have 500+ rows, we only want the freshest

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}

# ── Notice type → status ──────────────────────────────────────────────────────
def _notice_to_status(notice_type: str) -> str:
    nt = notice_type.lower()
    if any(k in nt for k in ('award', 'addendum', 'amendment', 'information only')):
        return 'CLOSED'
    return 'OPEN'

# ── Goods type → category ─────────────────────────────────────────────────────
def _goods_to_category(goods_type: str) -> str:
    gt = goods_type.lower()
    if 'works' in gt or 'civil' in gt:
        return 'CIVIL'
    if 'consult' in gt:
        return 'ADV'
    return 'OTHER'

# ── Date parsers ──────────────────────────────────────────────────────────────
def _parse_display_date(text: str) -> Optional[date]:
    """Parse "21/04/2026 07:50" or "21/04/2026" or "N/A"."""
    if not text or text.strip().upper() == 'N/A':
        return None
    token = text.strip().split()[0]   # "21/04/2026"
    try:
        return datetime.strptime(token, '%d/%m/%Y').date()
    except ValueError:
        return None

def _parse_sortable_date(text: str) -> Optional[date]:
    """Parse "202604210750" → date(2026,4,21)."""
    t = (text or '').strip()
    if len(t) >= 8:
        try:
            return datetime.strptime(t[:8], '%Y%m%d').date()
        except ValueError:
            pass
    return None

# ── Country name table (same as country_extractor but local for speed) ────────
_COUNTRY_NAME_TO_CC: dict[str, str] = {
    'afghanistan': 'AF', 'albania': 'AL', 'algeria': 'DZ', 'armenia': 'AM',
    'azerbaijan': 'AZ', 'bahrain': 'BH', 'bangladesh': 'BD', 'belarus': 'BY',
    'benin': 'BJ', 'bosnia': 'BA', 'bosnia and herzegovina': 'BA',
    'bulgaria': 'BG', 'burkina faso': 'BF', 'cameroon': 'CM', 'chad': 'TD',
    'comoros': 'KM', "cote d'ivoire": 'CI', 'croatia': 'HR', 'cyprus': 'CY',
    'czech republic': 'CZ', 'djibouti': 'DJ', 'egypt': 'EG', 'estonia': 'EE',
    'ethiopia': 'ET', 'gabon': 'GA', 'gambia': 'GM', 'georgia': 'GE',
    'ghana': 'GH', 'guinea': 'GN', 'guyana': 'GY', 'hungary': 'HU',
    'indonesia': 'ID', 'iran': 'IR', 'iraq': 'IQ', 'jordan': 'JO',
    'kazakhstan': 'KZ', 'kenya': 'KE', 'kosovo': 'XK', 'kyrgyz': 'KG',
    'kyrgyz republic': 'KG', 'kyrgyzstan': 'KG', 'latvia': 'LV',
    'lebanon': 'LB', 'libya': 'LY', 'lithuania': 'LT', 'macedonia': 'MK',
    'north macedonia': 'MK', 'madagascar': 'MG', 'malawi': 'MW',
    'malaysia': 'MY', 'maldives': 'MV', 'mali': 'ML', 'mauritania': 'MR',
    'mauritius': 'MU', 'moldova': 'MD', 'mongolia': 'MN', 'montenegro': 'ME',
    'morocco': 'MA', 'mozambique': 'MZ', 'niger': 'NE', 'nigeria': 'NG',
    'oman': 'OM', 'pakistan': 'PK', 'palestine': 'PS', 'poland': 'PL',
    'qatar': 'QA', 'romania': 'RO', 'russia': 'RU', 'saudi arabia': 'SA',
    'senegal': 'SN', 'serbia': 'RS', 'sierra leone': 'SL', 'slovakia': 'SK',
    'slovenia': 'SI', 'somalia': 'SO', 'sudan': 'SD', 'suriname': 'SR',
    'syria': 'SY', 'tajikistan': 'TJ', 'togo': 'TG', 'tunisia': 'TN',
    'turkey': 'TR', 'turkmenistan': 'TM', 'türkiye': 'TR', 'ukraine': 'UA',
    'uganda': 'UG', 'united arab emirates': 'AE', 'uzbekistan': 'UZ',
    'west bank': 'PS', 'west bank and gaza': 'PS', 'yemen': 'YE',
    'zambia': 'ZM', 'zimbabwe': 'ZW',
}

# Extra countries EBRD operates in that may not be in country_extractor
_EXTRA_COUNTRY_META: dict[str, tuple] = {
    'AM': ('Armenia',        'أرمينيا',             '🇦🇲', 'AMD', 200),
    'BY': ('Belarus',        'بيلاروسيا',           '🇧🇾', 'BYR', 201),
    'BG': ('Bulgaria',       'بلغاريا',             '🇧🇬', 'BGN', 202),
    'HR': ('Croatia',        'كرواتيا',             '🇭🇷', 'HRK', 203),
    'CY': ('Cyprus',         'قبرص',                '🇨🇾', 'EUR', 204),
    'CZ': ('Czech Republic', 'التشيك',              '🇨🇿', 'CZK', 205),
    'EE': ('Estonia',        'إستونيا',             '🇪🇪', 'EUR', 206),
    'GE': ('Georgia',        'جورجيا',              '🇬🇪', 'GEL', 207),
    'HU': ('Hungary',        'المجر',               '🇭🇺', 'HUF', 208),
    'XK': ('Kosovo',         'كوسوفو',              '🇽🇰', 'EUR', 209),
    'LV': ('Latvia',         'لاتفيا',              '🇱🇻', 'EUR', 210),
    'LT': ('Lithuania',      'ليتوانيا',            '🇱🇹', 'EUR', 211),
    'MK': ('North Macedonia','مقدونيا الشمالية',    '🇲🇰', 'MKD', 212),
    'MD': ('Moldova',        'مولدوفا',             '🇲🇩', 'MDL', 213),
    'MN': ('Mongolia',       'منغوليا',             '🇲🇳', 'MNT', 214),
    'ME': ('Montenegro',     'الجبل الأسود',        '🇲🇪', 'EUR', 215),
    'PL': ('Poland',         'بولندا',              '🇵🇱', 'PLN', 216),
    'RO': ('Romania',        'رومانيا',             '🇷🇴', 'RON', 217),
    'RU': ('Russia',         'روسيا',               '🇷🇺', 'RUB', 218),
    'RS': ('Serbia',         'صربيا',               '🇷🇸', 'RSD', 219),
    'SK': ('Slovakia',       'سلوفاكيا',            '🇸🇰', 'EUR', 220),
    'SI': ('Slovenia',       'سلوفينيا',            '🇸🇮', 'EUR', 221),
    'UA': ('Ukraine',        'أوكرانيا',            '🇺🇦', 'UAH', 222),
    'XK': ('Kosovo',         'كوسوفو',              '🇽🇰', 'EUR', 223),
    'ZM': ('Zambia',         'زامبيا',              '🇿🇲', 'ZMW', 224),
}


def _cc_from_name(name: str) -> str:
    """Map country name → ISO2 code."""
    return _COUNTRY_NAME_TO_CC.get(name.strip().lower(), 'EU')


def _ensure_country(code: str):
    """Get or auto-create CountryConfig for EBRD-specific countries."""
    from tenders.models import CountryConfig
    from tenders.country_extractor import ensure_country, COUNTRY_META

    code = code.upper()
    try:
        return CountryConfig.objects.get(code=code)
    except CountryConfig.DoesNotExist:
        pass

    # Check both meta tables
    meta = COUNTRY_META.get(code) or _EXTRA_COUNTRY_META.get(code)
    if not meta:
        logger.warning(f'[EBRD] Unknown country code {code!r}')
        return None

    name_en, name_ar, flag, currency, sort_order = meta
    obj, created = CountryConfig.objects.get_or_create(
        code=code,
        defaults={
            'name_en':    name_en,
            'name_ar':    name_ar,
            'flag_emoji': flag,
            'currency':   currency,
            'sort_order': sort_order,
            'is_active':  True,
        }
    )
    if created:
        logger.info(f'[EBRD] Auto-created CountryConfig: {flag} {name_en} ({code})')
    return obj


class EBRDScraper(BaseScraper):
    """
    Scraper for EBRD ECEPP (e-Procurement Portal).

    Fetches the full notice listing in one request (server-rendered HTML table)
    and parses up to MAX_ROWS (200) rows ordered by publication date descending
    (default DataTables ordering).

    TenderingURL in DB:
        url              = https://ecepp.ebrd.com/delta/noticeSearchResults.html
        pagination_style = SINGLE
        max_pages        = 1
    """

    country_code = 'EU'
    source_name  = 'EBRD'
    base_url     = BASE_URL

    # ── Main entry point ──────────────────────────────────────────────────────

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='EBRDScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [EBRD] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='EBRDScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : EBRDScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  MAX ROWS: {MAX_ROWS}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        try:
            resp = self.get(LIST_URL)
        except Exception as exc:
            print(f'  ✗ [EBRD] GET failed: {exc}')
            logger.error(f'[EBRD] GET failed: {exc}', exc_info=True)
            return []

        soup  = BeautifulSoup(resp.text, 'html.parser')
        items = self._parse_table(soup)

        print(f'  [DEBUG] Response length : {len(resp.text):,} chars')
        print(f'  [DEBUG] Rows parsed     : {len(items)}')
        if items:
            s = items[0]
            print(f'  [DEBUG] Sample title    : {s.title[:70]!r}')
            print(f'  [DEBUG] Sample ext_id   : {s.external_id!r}')
            print(f'  [DEBUG] Sample country  : {s.country_code!r}')
            print(f'  [DEBUG] Sample deadline : {s.submission_deadline!r}')
        print()

        # ── Relevance + category ──────────────────────────────────────────────
        all_results: list[TenderData] = []
        page_text = soup.get_text(separator=' ', strip=True)

        if self.detect_relevance(page_text):
            for item in items:
                combined = f'{item.title} {item.description}'
                if not item.category or item.category == 'OTHER':
                    kw_cat = self.detect_category(combined)
                    if kw_cat != 'OTHER':
                        item.category = kw_cat
                all_results.append(item)
            print(f'  → Page-level keyword hit — {len(items)} rows processed')
        else:
            for item in items:
                combined = f'{item.title} {item.description}'
                if self.detect_relevance(combined):
                    if not item.category or item.category == 'OTHER':
                        kw_cat = self.detect_category(combined)
                        if kw_cat != 'OTHER':
                            item.category = kw_cat
                    all_results.append(item)
            print(f'  → Per-item filter: {len(all_results)}/{len(items)} relevant')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant tender(s)  ←  EBRD')
        print(f'  {"═" * 58}\n')
        logger.info(f'[EBRD] scrape() complete — total={len(all_results)}')

        return all_results

    # ── HTML parser ───────────────────────────────────────────────────────────

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse <table id="noticeResultsTable"> rows.

        Column layout (0-indexed):
            0  title + href
            1  notice type
            2  procurement exercise title
            3  published display
            4  closing date display
            5  current state
            6  published short (hidden)   ← used for date parsing
            7  published sortable (hidden)
            8  closing sortable (hidden)
            9  metadata array (hidden)    ← country, entity, sector, goods type
        """
        table = soup.find('table', id='noticeResultsTable')
        if not table:
            logger.warning('[EBRD] noticeResultsTable not found in response')
            return []

        rows  = table.select('tbody tr')
        items: list[TenderData] = []

        for row in rows[:MAX_ROWS]:
            cells = row.find_all('td')
            if len(cells) < 10:
                continue

            # ── col 0: title + href ───────────────────────────────────────────
            a_tag = cells[0].find('a')
            if not a_tag:
                continue

            raw_title  = a_tag.get_text(strip=True)
            href       = a_tag.get('href', '')
            notice_id  = self._extract_notice_id(href)
            if not notice_id:
                continue

            source_url = f'{DETAIL_BASE}?displayNoticeId={notice_id}'

            # Strip leading "Country: " prefix from title
            title = self._strip_country_prefix(raw_title)

            # ── col 1: notice type ────────────────────────────────────────────
            notice_type = cells[1].get_text(strip=True)

            # ── col 5: current state ──────────────────────────────────────────
            state = cells[5].get_text(strip=True)

            # ── col 6 + 7: published date ─────────────────────────────────────
            pub_display  = cells[6].get_text(strip=True)   # "21/04/2026"
            pub_sortable = cells[7].get_text(strip=True)   # "202604210750"
            pub_date = (_parse_display_date(pub_display)
                        or _parse_sortable_date(pub_sortable))

            # ── col 8: closing date ───────────────────────────────────────────
            close_sortable = cells[8].get_text(strip=True)
            close_display  = cells[4].get_text(strip=True)
            deadline = (_parse_sortable_date(close_sortable)
                        or _parse_display_date(close_display))

            # ── col 9: metadata array ─────────────────────────────────────────
            meta       = self._parse_meta(cells[9].get_text(strip=True))
            country_name = meta.get('country', '')
            entity       = meta.get('entity', '')
            sector       = meta.get('sector', '')
            goods_type   = meta.get('goods_type', '')
            project_name = meta.get('project', '')
            project_id   = meta.get('project_id', '')

            country_code = _cc_from_name(country_name)

            # ── derive category + status ──────────────────────────────────────
            category = _goods_to_category(goods_type)
            status   = _notice_to_status(notice_type or state)

            description = (
                f'{title}\n\n'
                f'Project: {project_name} (ID: {project_id})\n'
                f'Country: {country_name}\n'
                f'Entity: {entity}\n'
                f'Sector: {sector}\n'
                f'Type: {goods_type}\n'
                f'Notice: {notice_type}'
            ).strip()

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = notice_id,
                reference_no        = notice_id,
                description         = description,
                entity              = entity,
                category            = category,
                status              = status,
                currency            = 'EUR',
                country_code        = country_code,
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_notice_id(href: str) -> str:
        """Extract displayNoticeId=NNNNN from href."""
        m = re.search(r'displayNoticeId=(\d+)', href)
        return m.group(1) if m else ''

    @staticmethod
    def _strip_country_prefix(title: str) -> str:
        """
        Strip the "Country: " prefix that EBRD adds to every title.
        "Ukraine: Supply and installation of..." → "Supply and installation of..."
        If no colon found, return as-is.
        """
        if ': ' in title:
            return title.split(': ', 1)[1].strip()
        return title.strip()

    @staticmethod
    def _parse_meta(raw: str) -> dict:
        """
        Parse the hidden metadata string:
            [project_name, project_id, country, proc_title,
             notice_id, goods_type, method, entity, sector, notice_type]

        Returns a dict with named keys.
        """
        # Strip surrounding brackets
        raw = raw.strip()
        if raw.startswith('[') and raw.endswith(']'):
            raw = raw[1:-1]

        # Split by ", " but respect HTML entities and quoted commas
        # EBRD uses plain comma+space separation — no quoting
        parts = [p.strip() for p in raw.split(',')]

        # Unescape HTML entities
        def _unescape(s: str) -> str:
            return (s.replace('&amp;', '&')
                     .replace('&quot;', '"')
                     .replace('&lt;', '<')
                     .replace('&gt;', '>'))

        def _get(idx: int) -> str:
            return _unescape(parts[idx]) if idx < len(parts) else ''

        return {
            'project':    _get(0),
            'project_id': _get(1),
            'country':    _get(2),
            'proc_title': _get(3),
            'notice_id':  _get(4),
            'goods_type': _get(5),
            'method':     _get(6),
            'entity':     _get(7),
            'sector':     _get(8),
            'notice_type': _get(9),
        }
