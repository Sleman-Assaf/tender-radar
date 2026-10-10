# tenders/scrapers/africagateway.py
"""
AfricaGateway scraper — https://www.africagateway.info

AJAX endpoint:
    GET https://www.africagateway.info/showtenderlist
        ?field=&order=&page=0&perpage=30
        &notice_type=1,2,3,7,8,10,11,16
        &countrycode=BI&sec_count_name=Burundi

Architecture
─────────────────────────────────────────────────────────────────────────────
AfricaGateway shows the same tender under multiple country pages — but that
does NOT mean the tender is relevant to all those countries.  It's simply
how their aggregator works (they index by region, not strict country).

Correct approach:
1. Fetch all 15 country pages, deduplicate by external_id.
2. Assign each unique tender to its PRIMARY country (first page it appeared on,
   ordered by AFRICA_COUNTRIES list so more active countries come first).
3. Save each tender once under that primary country.
4. The mirror system in services.py then TEXT-MINES the title + description
   to find real country name mentions and creates mirror rows only for those.

This means:
  ✓  "Supply of Medical Equipment to Kenyatta Hospital" → KE only (no mirrors)
  ✓  "UN Regional Water Project Kenya Tanzania Uganda"  → KE primary, TZ+UG mirrors
  ✗  No spurious mirrors because a tender appeared on multiple pages

The source country code is 'ZA' (generic Africa umbrella), which is in
_MIRROR_SOURCES, so the standard mirror pipeline fires automatically.
"""

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL     = 'https://www.africagateway.info'
AJAX_URL     = f'{BASE_URL}/showtenderlist'
NOTICE_TYPES = '1,2,3,7,8,10,11,16'
PER_PAGE     = 30

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':           'text/html, */*; q=0.01',
    'X-Requested-With': 'XMLHttpRequest',
}

# Countries ordered — more active ones first so they become the primary country
AFRICA_COUNTRIES = [
    ('KE', 'KE', 'Kenya'),
    ('ET', 'ET', 'Ethiopia'),
    ('TZ', 'TZ', 'Tanzania'),
    ('UG', 'UG', 'Uganda'),
    ('RW', 'RW', 'Rwanda'),
    ('MZ', 'MZ', 'Mozambique'),
    ('MG', 'MG', 'Madagascar'),
    ('MW', 'MW', 'Malawi'),
    ('ZW', 'ZW', 'Zimbabwe'),
    ('MU', 'MU', 'Mauritius'),
    ('SO', 'SO', 'Somalia'),
    ('BI', 'BI', 'Burundi'),
    ('DJ', 'DJ', 'Djibouti'),
    ('KM', 'KM', 'Comoros'),
    ('ER', 'ER', 'Eritrea'),
]


class AfricaGatewayScraper(BaseScraper):
    """
    Multi-country AfricaGateway scraper.

    Fetches all 15 African countries, deduplicates by external_id,
    then saves each unique tender once under its primary country.

    Mirror copies are created automatically by services.py using TEXT MINING
    of the tender title/description — NOT based on which country pages it
    appeared on. This prevents spurious cross-country duplication.

    Source country 'ZA' is in _MIRROR_SOURCES, so the mirror pipeline fires.
    """

    country_code = 'ZA'   # Africa umbrella — triggers mirror pipeline
    source_name  = 'AfricaGateway'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='AfricaGatewayScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗ [AfricaGateway] No active ScraperSource configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='AfricaGatewayScraper', is_active=True
            ).first()

        self.source = source
        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : AfricaGatewayScraper')
        print(f'  COUNTRIES: {len(AFRICA_COUNTRIES)} × {PER_PAGE} rows')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        # ext_id → TenderData (first country seen = primary)
        tender_map: dict[str, TenderData] = {}
        page_counts: dict[str, int] = {}

        for iso2, countrycode, country_name in AFRICA_COUNTRIES:
            print(f'  › {country_name:<15}', end='  ', flush=True)

            self.session.headers.update({
                **HEADERS,
                'Referer': f'{BASE_URL}/country/Tenders-{country_name}',
            })

            params = {
                'field': '', 'order': '',
                'page': 0, 'perpage': PER_PAGE,
                'notice_type':    NOTICE_TYPES,
                'countrycode':    countrycode,
                'sec_count_name': country_name,
                'count_desc':     '',
            }

            try:
                resp = self.get(AJAX_URL, params=params)
                resp.raise_for_status()
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[AfricaGateway/{country_name}] {exc}')
                continue

            items = self._parse_fragment(
                BeautifulSoup(resp.text, 'html.parser'), iso2, country_name
            )

            new_count = 0
            for item in items:
                if not item.external_id:
                    continue
                if item.external_id not in tender_map:
                    # First time seeing this tender — assign primary country
                    tender_map[item.external_id] = item
                    new_count += 1
                # else: already seen from a higher-priority country, skip

            print(f'{len(items)} rows, {new_count} new unique')

        total_unique = len(tender_map)
        print(f'\n  Unique tenders after dedup: {total_unique}')

        # ── Relevance filter ──────────────────────────────────────────────────
        results: list[TenderData] = []
        for item in tender_map.values():
            combined = f'{item.title} {item.description or ""} {item.entity or ""}'
            if self.detect_relevance(combined):
                item.category = self.detect_category(combined)
                results.append(item)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant / {total_unique} unique  ←  AfricaGateway')
        print(f'  NOTE  : Mirrors created by text-mining country names in title/desc')
        print(f'  {"═" * 58}\n')
        logger.info(f'[AfricaGateway] scrape() complete — total={len(results)}')
        return results

    # ── HTML fragment parser ──────────────────────────────────────────────────

    def _parse_fragment(self, soup: BeautifulSoup,
                        iso2: str, country_name: str) -> list[TenderData]:
        """
        Parse AJAX HTML table fragment for one country.

        Col layout (5-6 cells):
            [0] Country  [1] Published  [2] Title+link  [3] Entity  [4] Deadline
        """
        items        = []
        fallback_url = f'{BASE_URL}/country/Tenders-{country_name}'

        for row in soup.select('tr'):
            cells = row.find_all('td')
            if not cells or len(cells) < 3:
                continue

            cell2       = cells[2] if len(cells) > 2 else cells[-1]
            detail_link = cell2.find('a', href=True)

            if detail_link:
                title      = detail_link.get_text(strip=True)[:300]
                href       = detail_link.get('href', '').strip()
                source_url = urljoin(BASE_URL, href)
                ref_m      = re.search(r'ref=(\d+)', href)
                id_m       = re.search(r'/tenderdetail/(\d+)', href)
                ext_id     = ref_m.group(1) if ref_m else (id_m.group(1) if id_m else '')
            else:
                title      = cell2.get_text(strip=True)[:300]
                source_url = fallback_url
                ext_id     = ''

            if not title or len(title) < 5:
                continue

            pub_date = self._parse_date(cells[1].get_text(strip=True)) if len(cells) > 1 else None
            entity   = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            deadline = self._parse_date(cells[4].get_text(strip=True)) if len(cells) > 4 else None

            items.append(TenderData(
                title               = title,
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ext_id,
                entity              = entity,
                # Include country name in description so text miner can detect it
                description         = (
                    f'{title}\n\n'
                    f'Country: {country_name}\n'
                    f'Source: AfricaGateway'
                ),
                category            = 'OTHER',
                status              = 'OPEN',
                currency            = 'USD',
                country_code        = iso2,   # real primary country
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items

    @staticmethod
    def _parse_date(text: str) -> Optional[datetime.date]:
        if not text:
            return None
        val = re.sub(r'\s+', ' ', text.strip())
        for fmt, length in [
            ('%d-%b-%Y', 11), ('%d/%m/%Y', 10), ('%Y-%m-%d', 10),
            ('%d-%m-%Y', 10), ('%d %b %Y', 11),
            ('%B %d, %Y', None), ('%d %B %Y', None),
        ]:
            try:
                return datetime.strptime(val[:length] if length else val, fmt).date()
            except ValueError:
                continue
        return None
