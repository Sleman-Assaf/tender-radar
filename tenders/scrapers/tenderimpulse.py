# tenders/scrapers/tenderimpulse.py
"""
TenderImpulse — Middle East & Africa tenders scraper

Source:
  Middle East: https://tenderimpulse.com/middle-east-tenders  (region=13)
  Africa:      https://tenderimpulse.com/africa-tenders       (region=10)

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from live HTML analysis)
─────────────────────────────────────────────────────────────────────────────
Tenders are loaded via AJAX POST to:
  https://tenderimpulse.com/filter_search_ajax_new.php

POST params:
  region[]         = 13 (Middle East) or 10 (Africa)
  page_no          = 1, 2, 3, ...
  records_per_page = 100
  options          = (empty)

Optional filters (we don't use them — take all):
  keyword, days_filter, cpv[], country[], sector[], organization[]
  from_publish_date, to_publish_date, from_deadline_date, to_deadline_date

Count endpoint (separate):
  https://tenderimpulse.com/filter_search_ajax_new_count
  (same POST params + fetch_type=count)

AJAX response: raw HTML fragment injected into #tender-list div.

Each tender card in the response (confirmed from similar sites):
  div.tender-card or div.listing-item containing:
    - Tender title (h3 or .tender-title)
    - Country
    - Deadline date
    - Organization
    - Tender number / reference
    - Source URL / detail link

Country mapping: tenders have a country field → ISO2 via pycountry/manual map.

Region IDs:
  10 = African
  13 = Middle East

TenderingURL in DB:
  Middle East: url=https://tenderimpulse.com/middle-east-tenders
  Africa:      url=https://tenderimpulse.com/africa-tenders
  pagination_style = SINGLE  (we handle pagination via AJAX)
  max_pages = 10
  query_params = {"region": "13"}  (or "10")
"""

import logging
import re
import time
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL       = 'https://tenderimpulse.com'
AJAX_URL       = f'{BASE_URL}/filter_search_ajax_new.php'
COUNT_URL      = f'{BASE_URL}/filter_search_ajax_new_count'
RECORDS_PAGE   = 100    # max per request

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':             'text/html, */*; q=0.01',
    'Accept-Language':    'en-US,en;q=0.9',
    'X-Requested-With':   'XMLHttpRequest',
    'Content-Type':       'application/x-www-form-urlencoded; charset=UTF-8',
    'Origin':             BASE_URL,
}

# Country name → ISO2  (common ones seen in TenderImpulse Middle East / Africa)
COUNTRY_MAP = {
    # Middle East
    'Saudi Arabia': 'SA', 'Saudi Arab': 'SA',
    'UAE': 'AE', 'United Arab Emirates': 'AE',
    'Kuwait': 'KW', 'Qatar': 'QA', 'Oman': 'OM',
    'Bahrain': 'BH', 'Jordan': 'JO', 'Iraq': 'IQ',
    'Lebanon': 'LB', 'Yemen': 'YE', 'Israel': 'IL',
    'Palestine': 'PS', 'Palestinian Territory': 'PS',
    'Syria': 'SY', 'Iran': 'IR', 'Egypt': 'EG',
    'Libya': 'LY', 'Turkey': 'TR',
    # Africa
    'Morocco': 'MA', 'Tunisia': 'TN', 'Algeria': 'DZ',
    'Sudan': 'SD', 'Ethiopia': 'ET', 'Kenya': 'KE',
    'Nigeria': 'NG', 'Ghana': 'GH', 'South Africa': 'ZA',
    'Tanzania': 'TZ', 'Uganda': 'UG', 'Rwanda': 'RW',
    'Senegal': 'SN', 'Mozambique': 'MZ', 'Zambia': 'ZM',
    'Zimbabwe': 'ZW', 'Malawi': 'MW', 'Somalia': 'SO',
    'Angola': 'AO', 'Cameroon': 'CM', 'Ivory Coast': 'CI',
    "Cote d,Ivoire": 'CI', 'Mali': 'ML', 'Burkina Faso': 'BF',
    'Niger': 'NE', 'Chad': 'TD', 'Gabon': 'GA',
    'Congo': 'CG', 'Liberia': 'LR', 'Sierra Leone': 'SL',
    'Gambia': 'GM', 'Togo': 'TG', 'Benin': 'BJ',
    'Guinea': 'GN', 'GUINEA-BISSAU': 'GW',
    'Equatorial Guinea': 'GQ', 'Madagascar': 'MG',
    'Seychelles': 'SC', 'Mauritius': 'MU',
    'Botswana': 'BW', 'Namibia': 'NA', 'Lesotho': 'LS',
    'Swaziland': 'SZ', 'South Sudan': 'SS',
    'Eritrea': 'ER', 'Djibouti': 'DJ', 'DJIBOUTI': 'DJ',
    'Libya': 'LY', 'Comoros': 'KM',
}

DATE_FMTS = (
    '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y',
    '%d %b %Y', '%d %B %Y', '%B %d, %Y',
)


def _parse_date(text: str) -> Optional[datetime.date]:
    if not text:
        return None
    text = text.strip()
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Try extracting YYYY-MM-DD
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y-%m-%d').date()
        except ValueError:
            pass
    return None


def _country_to_iso2(name: str) -> str:
    if not name:
        return ''
    name = name.strip()
    if name in COUNTRY_MAP:
        return COUNTRY_MAP[name]
    # Partial match
    for k, v in COUNTRY_MAP.items():
        if k.lower() in name.lower() or name.lower() in k.lower():
            return v
    return ''


class TenderImpulseScraper(BaseScraper):
    """
    Base scraper for TenderImpulse region pages.
    Subclassed per region (Middle East / Africa).

    Uses the AJAX endpoint:
        POST https://tenderimpulse.com/filter_search_ajax_new.php
        region[] = <region_id>
        page_no  = N
        records_per_page = 100
    """

    region_id:   int = 0        # set in subclass
    region_name: str = ''       # human label
    page_url:    str = ''       # canonical page URL (for Referer)
    default_country: str = ''   # ISO2 fallback

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class=self.__class__.__name__, is_active=True
            )
        except ScraperSource.DoesNotExist:
            print(f'  ✗ [{self.region_name}] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class=self.__class__.__name__, is_active=True
            ).first()

        self.source = source
        self.session.headers.update({
            **HEADERS,
            'Referer': self.page_url,
        })

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : {self.__class__.__name__}')
        print(f'  REGION  : {self.region_name} (id={self.region_id})')
        print(f'  PAGES   : up to {max_pages} × {RECORDS_PAGE} = {max_pages * RECORDS_PAGE} tenders')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        # Warm up session — visit page first to get cookies
        try:
            self.session.get(self.page_url, timeout=20)
            time.sleep(1)
        except Exception:
            pass

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()

        for page in range(1, max_pages + 1):
            print(f'       › Page {page:>3}/{max_pages}', end='  ', flush=True)

            post_data = {
                'region[]':        self.region_id,
                'page_no':         page,
                'records_per_page': RECORDS_PAGE,
                'options':         '',
            }

            try:
                resp = self.session.post(AJAX_URL, data=post_data, timeout=30)
                resp.raise_for_status()
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[{self.region_name}] page {page}: {exc}')
                break

            html  = resp.text
            items = self._parse_response(html)

            if not items:
                print('(empty — stopping)')
                break

            # Debug on first page
            if page == 1:
                print(f'→ {len(items)} rows')
                if items:
                    s = items[0]
                    print(f'  [DEBUG] title  : {s.title[:70]!r}')
                    print(f'  [DEBUG] country: {s.country_code!r}')
                    print(f'  [DEBUG] deadline: {s.submission_deadline!r}')
            else:
                matched = sum(
                    1 for it in items if it.external_id not in seen_ids
                    and self.detect_relevance(f'{it.title} {it.entity}')
                )
                print(f'→ {len(items)} rows, {matched} relevant')

            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    combined = f'{item.title} {item.entity} {item.description}'
                    if self.detect_relevance(combined):
                        item.category = self.detect_category(combined)
                        all_results.append(item)

            if len(items) < RECORDS_PAGE:
                print(f'       (last page — {len(items)} < {RECORDS_PAGE})')
                break

            time.sleep(1.5)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  {self.region_name}')
        print(f'  {"═" * 58}\n')
        return all_results

    def _parse_response(self, html: str) -> list[TenderData]:
        """
        Parse AJAX HTML fragment.

        Confirmed structure:
            div.row.list-box
              div.col-md-11
                h2 > a[href]          → title + detail URL
                div.row
                  p.list-sector  "Deadline : DD Mon YYYY"
                  p.list-sector  "Country : <name>"
              div.col-md-1
                a[href]               → same detail URL
        """
        soup  = BeautifulSoup(html, 'html.parser')
        cards = soup.find_all('div', class_='list-box')

        if not cards:
            logger.warning(f'[{self.region_name}] No div.list-box cards found — raw:\n{html[:500]}')
            return []

        items = []
        for card in cards:
            item = self._parse_card(card)
            if item:
                items.append(item)
        return items

    def _parse_card(self, card) -> Optional[TenderData]:
        """
        Parse one div.row.list-box card.

        Structure (confirmed from live AJAX response):
          h2 > a[href=/government-tenders/<country>/<slug>-<ID>]  → title + URL
          p.list-sector "Deadline : 30 Apr 2026"
          p.list-sector "Country : Oman"
        """
        import hashlib

        # ── Title + Detail URL ─────────────────────────────────────────────────
        h2 = card.find('h2')
        if not h2:
            return None
        a_tag  = h2.find('a', href=True)
        title  = h2.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        href = a_tag['href'] if a_tag else ''
        source_url = (BASE_URL + href) if href.startswith('/') else (href or self.page_url)

        # ── External ID from URL slug (last numeric suffix) ────────────────────
        # e.g. /government-tenders/oman/...-12608434  →  TI-12608434
        m = re.search(r'-(\d{5,})/?$', href)
        ext_id = f'TI-{m.group(1)}' if m else                  f'TI-{self.region_id}-{hashlib.md5(title.encode()).hexdigest()[:12]}'

        # ── Deadline + Country from p.list-sector ──────────────────────────────
        deadline     = None
        country_code = self.default_country
        country_name = ''

        for p in card.find_all('p', class_='list-sector'):
            txt = p.get_text(strip=True)   # e.g. "Deadline : 30 Apr 2026"
            if txt.startswith('Deadline'):
                # Extract "30 Apr 2026" — format: DD Mon YYYY
                date_part = re.sub(r'^Deadline\s*:\s*', '', txt).strip()
                deadline  = _parse_date(date_part)
            elif txt.startswith('Country'):
                country_name = re.sub(r'^Country\s*:\s*', '', txt).strip()
                country_code = _country_to_iso2(country_name) or self.default_country

        return TenderData(
            title               = title[:300],
            source_url          = source_url,
            external_id         = ext_id,
            reference_no        = '',
            description         = (
                f'{title}\n\nCountry: {country_name}\nSource: TenderImpulse'
            ),
            entity              = '',
            category            = 'OTHER',
            status              = 'OPEN',
            currency            = '',
            country_code        = country_code,
            publication_date    = None,
            submission_deadline = deadline,
        )


# ── Concrete scrapers ─────────────────────────────────────────────────────────

class TenderImpulseMiddleEastScraper(TenderImpulseScraper):
    """TenderImpulse — Middle East tenders (region=13)."""
    region_id      = 13
    region_name    = 'TenderImpulse Middle East'
    page_url       = 'https://tenderimpulse.com/middle-east-tenders'
    default_country = 'AE'   # fallback: UAE (most common ME country)
    country_code   = 'AE'
    source_name    = 'TenderImpulse Middle East'
    base_url       = BASE_URL


class TenderImpulseAfricaScraper(TenderImpulseScraper):
    """TenderImpulse — Africa tenders (region=10)."""
    region_id      = 10
    region_name    = 'TenderImpulse Africa'
    page_url       = 'https://tenderimpulse.com/africa-tenders'
    default_country = 'ZA'   # fallback: South Africa
    country_code   = 'ZA'
    source_name    = 'TenderImpulse Africa'
    base_url       = BASE_URL
