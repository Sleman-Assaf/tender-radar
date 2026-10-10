# tenders/scrapers/afd.py
"""
AFD (Agence Française de Développement) tenders scraper
Source: https://tenders-afd.dgmarket.com/tenders/brandedNoticeList.do

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from actual HTML)
─────────────────────────────────────────────────────────────────────────────
URL:    GET https://tenders-afd.dgmarket.com/tenders/brandedNoticeList.do
Pagination params: d-446978-p=N  &  selPageNumber=N   (1-indexed)
20 results per page, ~151 total → 3 pages = up to 60 tenders

Table: <table id="notice" class="simple">
Columns:
    Country        → <td class="country">
    Notice Title   → <td><a href="/tender/107466225">Title</a></td>
    Published      → <td class="published">Apr 23, 2026</td>
    Deadline       → <td class="deadline">May 22, 2026</td>

External ID: numeric part of href  e.g. "/tender/107466225" → "107466225"
Detail URL:  https://tenders-afd.dgmarket.com/tender/107466225

Country: plain text name → mapped to ISO2 for mirror extraction
Source country: DE (AFD = France, but source code follows TenderRadar convention)
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://tenders-afd.dgmarket.com'
LIST_URL = f'{BASE_URL}/tenders/brandedNoticeList.do'

# 20 results per page (site default)
PER_PAGE = 20

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8',
    'Referer':         LIST_URL,
}

# Country name (as shown in the listing) → ISO2
COUNTRY_MAP = {
    'international':                   'UN',
    'afghanistan':                     'AF', 'albania':              'AL',
    'algeria':                         'DZ', 'angola':               'AO',
    'argentina':                       'AR', 'armenia':              'AM',
    'bangladesh':                      'BD', 'benin':                'BJ',
    'bolivia':                         'BO', 'bosnia and herzegovina':'BA',
    'botswana':                        'BW', 'brazil':               'BR',
    'burkina faso':                    'BF', 'burundi':              'BI',
    'cabo verde':                      'CV', 'cambodia':             'KH',
    'cameroon':                        'CM', 'central african republic':'CF',
    'chad':                            'TD', 'chile':                'CL',
    'china':                           'CN', 'colombia':             'CO',
    'comoros':                         'KM', 'congo, democratic republic':'CD',
    'congo, republic':                 'CG', "côte d'ivoire":        'CI',
    'djibouti':                        'DJ', 'dominican republic':   'DO',
    'ecuador':                         'EC', 'egypt':                'EG',
    'ethiopia':                        'ET', 'fiji':                 'FJ',
    'gabon':                           'GA', 'gambia':               'GM',
    'georgia':                         'GE', 'ghana':                'GH',
    'guatemala':                       'GT', 'guinea':               'GN',
    'guinea-bissau':                   'GW', 'haiti':                'HT',
    'honduras':                        'HN', 'india':                'IN',
    'indonesia':                       'ID', 'iraq':                 'IQ',
    'jordan':                          'JO', 'kenya':                'KE',
    'kosovo':                          'XK', 'kyrgyzstan':           'KG',
    'laos':                            'LA', 'lebanon':              'LB',
    'lesotho':                         'LS', 'liberia':              'LR',
    'libya':                           'LY', 'madagascar':           'MG',
    'malawi':                          'MW', 'malaysia':             'MY',
    'mali':                            'ML', 'mauritania':           'MR',
    'mauritius':                       'MU', 'mexico':               'MX',
    'moldova':                         'MD', 'mongolia':             'MN',
    'montenegro':                      'ME', 'morocco':              'MA',
    'mozambique':                      'MZ', 'myanmar':              'MM',
    'namibia':                         'NA', 'nepal':                'NP',
    'nicaragua':                       'NI', 'niger':                'NE',
    'nigeria':                         'NG', 'pakistan':             'PK',
    'palestine':                       'PS', 'panama':               'PA',
    'papua new guinea':                'PG', 'paraguay':             'PY',
    'peru':                            'PE', 'philippines':          'PH',
    'rwanda':                          'RW', 'senegal':              'SN',
    'serbia':                          'RS', 'sierra leone':         'SL',
    'somalia':                         'SO', 'south africa':         'ZA',
    'south sudan':                     'SS', 'sri lanka':            'LK',
    'sudan':                           'SD', 'suriname':             'SR',
    'syria':                           'SY', 'tajikistan':           'TJ',
    'tanzania':                        'TZ', 'thailand':             'TH',
    'timor-leste':                     'TL', 'togo':                 'TG',
    'tunisia':                         'TN', 'turkey':               'TR',
    'turkmenistan':                    'TM', 'uganda':               'UG',
    'ukraine':                         'UA', 'uzbekistan':           'UZ',
    'vanuatu':                         'VU', 'venezuela':            'VE',
    'vietnam':                         'VN', 'west bank and gaza':   'PS',
    'yemen':                           'YE', 'zambia':               'ZM',
    'zimbabwe':                        'ZW',
}

DATE_FMTS = ('%b %d, %Y', '%B %d, %Y', '%d %b %Y', '%Y-%m-%d')


def _parse_date(text: str) -> Optional[date]:
    text = (text or '').strip()
    if not text or text in ('-', 'N/A', ''):
        return None
    # Normalise multiple spaces
    text = re.sub(r'\s+', ' ', text)
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _country_to_iso(name: str) -> str:
    """Map country display name to ISO2, default 'DE' (AFD source)."""
    key = name.strip().lower()
    return COUNTRY_MAP.get(key, 'DE')


class AFDScraper(BaseScraper):
    """
    Scraper for AFD (Agence Française de Développement) tenders
    via the dgMarket branded portal at tenders-afd.dgmarket.com.

    Scrapes up to max_pages (default 3) pages of 20 tenders each
    = up to 60 tenders per run.

    TenderingURL in DB:
        url              = https://tenders-afd.dgmarket.com/tenders/brandedNoticeList.do
        pagination_style = QUERY
        page_param       = selPageNumber
        max_pages        = 3
    """

    country_code = 'DE'   # AFD = French development agency (source country)
    source_name  = 'AFD dgMarket'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='AFDScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗ [AFD] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='AFDScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        try:
            from tenders.services import scraping_mode_is_all
            max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10
        except Exception:
            max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : AFDScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  PAGES   : up to {max_pages} × {PER_PAGE} = {max_pages * PER_PAGE} tenders')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:   set[str]          = set()

        for page in range(1, max_pages + 1):
            print(f'       › Page {page:>2}/{max_pages}', end='  ', flush=True)
            logger.info(f'[AFD] Scraping page {page}')

            params = {
                'd-446978-s':   'p',
                'd-446978-p':   str(page),
                'd-446978-n':   '1',
                'selPageNumber': str(page),
            }

            try:
                resp = self.get(LIST_URL, params=params)
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[AFD] GET page {page} failed: {exc}')
                break

            soup  = BeautifulSoup(resp.text, 'html.parser')
            items = self._parse_notice_table(soup)

            if not items:
                print('(empty — stopping)')
                break

            if page == 1:
                print()
                print(f'  [DEBUG] Response length : {len(resp.text):,} chars')
                print(f'  [DEBUG] Rows parsed     : {len(items)}')
                # Total from "1-20 of 151"
                m = re.search(r'of\s+([\d,]+)', resp.text)
                if m:
                    print(f'  [DEBUG] Total on site   : {m.group(1)} tenders')
                if items:
                    s = items[0]
                    print(f'  [DEBUG] Sample title    : {s.title[:65]!r}')
                    print(f'  [DEBUG] Sample ext_id   : {s.external_id!r}')
                    print(f'  [DEBUG] Sample country  : {s.country_code!r}')
                    print(f'  [DEBUG] Sample deadline : {s.submission_deadline!r}')
                print()

            matched = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    combined = f'{item.title} {item.description}'
                    if self.detect_relevance(combined):
                        item.category = self.detect_category(combined)
                        all_results.append(item)
                        matched += 1

            print(f'→ {len(items)} rows, {matched} relevant')

            if len(items) < PER_PAGE:
                logger.info(f'[AFD] page {page}: partial — last page')
                break

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  AFD')
        print(f'  {"═" * 58}\n')
        logger.info(f'[AFD] scrape() complete — total={len(all_results)}')
        return all_results

    # ── Table parser ──────────────────────────────────────────────────────────

    def _parse_notice_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse <table id="notice" class="simple"> rows.

        Columns: Country | Notice Title | Published | Deadline
        """
        table = soup.find('table', id='notice')
        if not table:
            logger.warning('[AFD] notice table not found')
            return []

        items: list[TenderData] = []

        for row in table.select('tbody tr'):
            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            # ── Country ───────────────────────────────────────────────────────
            country_text = cells[0].get_text(strip=True)
            country_iso  = _country_to_iso(country_text)

            # ── Title + external ID ───────────────────────────────────────────
            a_tag = cells[1].find('a')
            if not a_tag:
                continue
            title  = a_tag.get_text(strip=True)
            href   = a_tag.get('href', '')
            # href: "/tender/107466225"  or  "https://...107466225"
            m = re.search(r'/tender/(\d+)', href)
            if not m:
                continue
            ext_id     = m.group(1)
            source_url = (
                href if href.startswith('http')
                else f'{BASE_URL}{href}'
            )

            # ── Dates ─────────────────────────────────────────────────────────
            pub_date = _parse_date(cells[2].get_text(strip=True))
            deadline = _parse_date(cells[3].get_text(strip=True))

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ext_id,
                description         = f'{title}\n\nCountry: {country_text}\nSource: AFD dgMarket',
                entity              = '',
                category            = 'OTHER',      # refined by detect_category()
                status              = 'OPEN',
                currency            = 'EUR',
                country_code        = country_iso,
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items
