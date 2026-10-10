# tenders/scrapers/adb.py
# Path: TenderRadar/tenders/scrapers/adb.py
"""
Asian Development Bank (ADB) -- Projects & Tenders
URL: https://www.adb.org/projects

Architecture:
  The page uses SearchStax Studio React (v4.1.9) -- pure client-side rendering.
  No data exists in the initial HTML. JavaScript renders results into #site-search.
  Pagination uses URL params:
    ?searchstax[query]=*&searchstax[page]=N&searchstax[order]=ds_date_board+desc

Strategy: Playwright with network interception.
  1. Open the page with Playwright (Chromium, headless).
  2. Intercept the XHR/fetch call that the React app makes to Solr/SearchStax proxy.
  3. Capture the endpoint URL + response JSON for direct requests on subsequent pages.
  4. Parse project cards from rendered DOM.

  If Playwright is unavailable, falls back to requests + BeautifulSoup
  which will get 0 results but won't crash the scheduler.

Per-country: ADB covers 50+ Asian/Pacific DMCs (Developing Member Countries).
  Facet param: searchstax[facets][sm_country][]=<Country Name>
  But user asked for all countries, first 10 pages.

Page limit: MAX_PAGES = 10 per user instruction.

Fields from ADB project cards:
  - Project number (ADB project ID)
  - Title
  - Country
  - Sector
  - Approval date (ds_date_board)
  - Status
  - Detail URL: /projects/<project-id>
"""

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.adb.org'
PROJECTS_URL = 'https://www.adb.org/projects'
MAX_PAGES = 10

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}

_KNOWN_ENDPOINTS = [
    # SearchStax Studio typically proxies through a Drupal route
    # These are attempted in order; first to return valid JSON wins.
    'https://www.adb.org/api/search-studio/projects',
    'https://www.adb.org/api/adb-search/projects',
    'https://www.adb.org/adb-search/select',
    'https://www.adb.org/adb_search/api',
]


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    text = text.strip()
    for fmt in ('%d %b %Y', '%B %d, %Y', '%Y-%m-%d', '%d %B %Y', '%b %Y', '%Y'):
        try:
            return datetime.strptime(text[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _page_url(page: int) -> str:
    return (
        f'{PROJECTS_URL}'
        f'?searchstax[query]=*'
        f'&searchstax[page]={page}'
        f'&searchstax[order]=ds_date_board+desc'
    )


class ADBScraper(BaseScraper):
    """
    Scraper for Asian Development Bank (ADB) projects.
    Uses Playwright to render SearchStax Studio React pages and intercept the
    backend search API, then uses the captured endpoint for subsequent pages.
    Falls back to DOM extraction if direct API access fails.
    """

    source_name  = 'Asian Development Bank (ADB) -- Projects & Tenders'
    country_code = 'MULTI'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
            return self._scrape_playwright()
        except ImportError:
            logger.error('[ADB] Playwright not installed. Install: pip install playwright && playwright install chromium')
            return self._scrape_requests_fallback()
        except Exception as exc:
            logger.error(f'[ADB] Playwright scrape failed: {exc}', exc_info=True)
            return self._scrape_requests_fallback()

    # ------------------------------------------------------------------
    # Playwright path
    # ------------------------------------------------------------------

    def _scrape_playwright(self) -> list[TenderData]:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        all_results: list[TenderData] = []
        seen_ids: set[str] = set()
        captured_api: dict = {}   # will store {url, headers, payload} of first detected API call

        logger.info('[ADB] Starting Playwright scrape')

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
            context = browser.new_context(
                viewport={'width': 1280, 'height': 900},
                user_agent=_HEADERS['User-Agent'],
                locale='en-US',
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            )
            page = context.new_page()

            def _capture_route(route, request):
                """Intercept API calls to capture endpoint URL and payload."""
                url = request.url
                # SearchStax Studio makes POST/GET to search endpoints
                if ('solr' in url or 'searchstax' in url or 'select' in url.lower()
                        or '/api/' in url) and 'adb.org' in url:
                    if not captured_api:
                        logger.info(f'[ADB] Captured API call: {request.method} {url}')
                        body = request.post_data or ''
                        captured_api['url']     = url
                        captured_api['method']  = request.method
                        captured_api['headers'] = dict(request.headers)
                        captured_api['body']    = body
                route.continue_()

            page.route('**/*', _capture_route)

            for page_num in range(1, MAX_PAGES + 1):
                url = _page_url(page_num)
                logger.info(f'[ADB] Loading page {page_num}: {url}')

                try:
                    page.goto(url, wait_until='networkidle', timeout=45_000)
                except PWTimeout:
                    logger.warning(f'[ADB] Timeout on page {page_num}, trying to extract anyway')

                # Wait for results container
                try:
                    page.wait_for_selector(
                        '.searchstax-site-search .search-result, '
                        '.searchstax-site-search article, '
                        '.project-search-result, '
                        '[data-project-id], '
                        '.project-list-item, '
                        '.result-item',
                        timeout=20_000,
                    )
                except PWTimeout:
                    logger.warning(f'[ADB] No results selector found on page {page_num}')

                html = page.content()
                tenders = self._parse_html(html, page_num)
                logger.info(f'[ADB] Page {page_num}: {len(tenders)} projects extracted')

                new = 0
                for t in tenders:
                    if t.external_id not in seen_ids:
                        seen_ids.add(t.external_id)
                        all_results.append(t)
                        new += 1

                if new == 0 and page_num > 1:
                    logger.info(f'[ADB] No new results on page {page_num}, stopping')
                    break

                time.sleep(1.5)

            browser.close()

        # If we captured the API endpoint, try to use it directly for remaining pages
        # (but since we scraped all 10 pages via Playwright above, this is informational)
        if captured_api:
            logger.info(f'[ADB] Captured API endpoint: {captured_api.get("url")}')

        logger.info(f'[ADB] Done -- {len(all_results)} unique projects')
        return all_results

    # ------------------------------------------------------------------
    # HTML parser (shared between Playwright and fallback)
    # ------------------------------------------------------------------

    def _parse_html(self, html: str, page_num: int) -> list[TenderData]:
        soup = BeautifulSoup(html, 'html.parser')
        results = []

        # Try multiple CSS selector patterns that ADB might use
        selectors = [
            # SearchStax Studio renders results in these containers
            '.searchstax-result',
            '.search-result',
            'article.project',
            '.project-item',
            '.project-list-item',
            '.result-item',
            # Table rows
            'table.projects-table tr',
            # Any article or li in the search container
            '#site-search article',
            '#site-search li',
        ]

        items = []
        for sel in selectors:
            items = soup.select(sel)
            if items:
                logger.debug(f'[ADB] Selector {sel!r} found {len(items)} items')
                break

        if not items:
            # Fallback: any div with a link to /projects/<id>
            items = soup.find_all('div', attrs={'data-project-id': True})
            if not items:
                # Last resort: find all links to /projects/<number>
                links = soup.find_all('a', href=re.compile(r'/projects/\d+'))
                for link in links:
                    t = self._parse_link(link)
                    if t:
                        results.append(t)
                return results

        for item in items:
            t = self._parse_item(item)
            if t:
                results.append(t)

        return results

    def _parse_item(self, item) -> Optional[TenderData]:
        # Extract project link
        a = item.find('a', href=re.compile(r'/projects/\d+'))
        if not a:
            return None

        href    = a['href']
        url     = BASE_URL + href if href.startswith('/') else href
        proj_id = re.search(r'/projects/(\d+)', href)
        ext_id  = proj_id.group(1) if proj_id else href.rstrip('/').split('/')[-1]

        title = a.get_text(strip=True)
        if not title:
            h = item.find(['h2', 'h3', 'h4', 'h5', 'strong'])
            title = h.get_text(strip=True) if h else ''
        if not title or len(title) < 5:
            return None

        text = item.get_text(separator=' ', strip=True)

        # Extract country
        country_el = item.select_one(
            '.country, .location, [class*="country"], [data-country]'
        )
        country = country_el.get_text(strip=True) if country_el else ''
        if not country:
            # Try to match "Country: X" pattern in text
            m = re.search(r'Country[:\s]+([A-Za-z ,]+?)(?:Sector|Status|$)', text, re.I)
            country = m.group(1).strip() if m else ''

        # Extract sector
        sector_el = item.select_one('.sector, [class*="sector"]')
        sector = sector_el.get_text(strip=True) if sector_el else ''

        # Extract status
        status_el = item.select_one('.status, [class*="status"]')
        status_text = status_el.get_text(strip=True).upper() if status_el else ''
        if not status_text:
            m = re.search(r'\b(Active|Approved|Closed|Completed|Proposed|Pipeline)\b', text, re.I)
            status_text = m.group(1).upper() if m else ''

        # Map status to our enum values
        status_map = {
            'ACTIVE': 'OPEN', 'APPROVED': 'OPEN', 'PROPOSED': 'OPEN', 'PIPELINE': 'OPEN',
            'CLOSED': 'CLOSED', 'COMPLETED': 'CLOSED',
        }
        status = status_map.get(status_text, 'OPEN')

        # Extract dates
        date_el = item.select_one('.date, .approval-date, [class*="date"]')
        date_text = date_el.get_text(strip=True) if date_el else ''
        if not date_text:
            m = re.search(r'\b(\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{4})\b', text)
            date_text = m.group(1) if m else ''

        # Project number / reference
        ref_el = item.select_one('.project-number, .project-no, [class*="number"]')
        ref = ref_el.get_text(strip=True) if ref_el else ''
        if not ref:
            m = re.search(r'\b(\d{4,6}-\d{3,6})\b', text)
            ref = m.group(1) if m else ext_id

        # Category from sector
        category = 'OTHER'
        sec_low = sector.lower()
        if any(w in sec_low for w in ('transport', 'road', 'rail', 'port', 'aviation')):
            category = 'WORKS'
        elif any(w in sec_low for w in ('water', 'sanit', 'urban', 'energy', 'power')):
            category = 'WORKS'
        elif any(w in sec_low for w in ('consult', 'capacity', 'technical')):
            category = 'CONSULTANCY'
        elif any(w in sec_low for w in ('goods', 'supply', 'equipment', 'material')):
            category = 'GOODS'

        return TenderData(
            title            = title,
            source_url       = url,
            external_id      = ext_id,
            reference_no     = ref[:100],
            entity           = 'Asian Development Bank',
            entity_ar        = 'بنك التنمية الآسيوي',
            description      = f'ADB Project | Country: {country} | Sector: {sector}'.strip(),
            category         = category,
            status           = status,
            publication_date = _parse_date(date_text),
            country_code     = self._map_country(country),
        )

    def _parse_link(self, link) -> Optional[TenderData]:
        href    = link['href']
        url     = BASE_URL + href if href.startswith('/') else href
        proj_id = re.search(r'/projects/(\d+)', href)
        ext_id  = proj_id.group(1) if proj_id else ''
        title   = link.get_text(strip=True)
        if not title or not ext_id:
            return None
        return TenderData(
            title        = title,
            source_url   = url,
            external_id  = ext_id,
            reference_no = ext_id,
            entity       = 'Asian Development Bank',
            entity_ar    = 'بنك التنمية الآسيوي',
            category     = 'OTHER',
            status       = 'OPEN',
            country_code = 'MULTI',
        )

    # ------------------------------------------------------------------
    # Country code mapping
    # ------------------------------------------------------------------

    _COUNTRY_MAP = {
        'afghanistan': 'AF', 'armenia': 'AM', 'azerbaijan': 'AZ',
        'bangladesh': 'BD', 'bhutan': 'BT', 'cambodia': 'KH',
        'cook islands': 'CK', 'fiji': 'FJ', 'georgia': 'GE',
        'india': 'IN', 'indonesia': 'ID', 'kazakhstan': 'KZ',
        'kiribati': 'KI', 'kyrgyz': 'KG', 'lao': 'LA',
        'maldives': 'MV', 'marshall islands': 'MH', 'micronesia': 'FM',
        'mongolia': 'MN', 'myanmar': 'MM', 'nauru': 'NR',
        'nepal': 'NP', 'niue': 'NU', 'pakistan': 'PK',
        'palau': 'PW', 'papua new guinea': 'PG', 'philippines': 'PH',
        'samoa': 'WS', 'solomon islands': 'SB', 'sri lanka': 'LK',
        'tajikistan': 'TJ', 'thailand': 'TH', 'timor': 'TL',
        'tonga': 'TO', 'turkmenistan': 'TM', 'tuvalu': 'TV',
        'uzbekistan': 'UZ', 'vanuatu': 'VU', 'viet nam': 'VN',
        'vietnam': 'VN', 'turkey': 'TR', 'turkiye': 'TR',
        "people's republic of china": 'CN', 'china': 'CN',
        'regional': 'MULTI',
    }

    def _map_country(self, country: str) -> str:
        if not country:
            return 'MULTI'
        c = country.lower().strip()
        for key, code in self._COUNTRY_MAP.items():
            if key in c:
                return code
        return 'MULTI'

    # ------------------------------------------------------------------
    # Requests fallback (returns empty, logs warning)
    # ------------------------------------------------------------------

    def _scrape_requests_fallback(self) -> list[TenderData]:
        """
        Fallback when Playwright is unavailable.
        ADB projects page is pure JS-rendered -- this will return 0 results.
        The scraper logs a clear message so the user knows to install Playwright.
        """
        logger.error(
            '[ADB] Playwright required for ADB scraper. '
            'Install: pip install playwright && playwright install chromium --with-deps'
        )
        session = requests.Session()
        session.headers.update(_HEADERS)

        for ep in _KNOWN_ENDPOINTS:
            try:
                r = session.get(
                    ep,
                    params={'q': '*', 'rows': 20, 'start': 0, 'sort': 'ds_date_board desc', 'wt': 'json'},
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    if 'response' in data:
                        logger.info(f'[ADB] Found working endpoint: {ep}')
                        return self._parse_solr_response(data)
            except Exception as exc:
                logger.debug(f'[ADB] Endpoint {ep} failed: {exc}')
                continue

        return []

    def _parse_solr_response(self, data: dict) -> list[TenderData]:
        """Parse a Solr JSON response from SearchStax."""
        docs = data.get('response', {}).get('docs', [])
        results = []
        for doc in docs:
            title = doc.get('ss_title') or doc.get('title') or doc.get('ss_name', '')
            if not title:
                continue

            proj_id = doc.get('ss_project_number') or doc.get('id', '')
            url_path = doc.get('ss_url') or doc.get('url') or f'/projects/{proj_id}'
            url = BASE_URL + url_path if url_path.startswith('/') else url_path

            country = doc.get('sm_country', [''])[0] if isinstance(doc.get('sm_country'), list) else doc.get('sm_country', '')
            sector  = doc.get('sm_sector', [''])[0]  if isinstance(doc.get('sm_sector'),  list) else doc.get('sm_sector',  '')

            date_str = doc.get('ds_date_board') or doc.get('ds_date', '')
            pub_date = _parse_date(str(date_str)[:10]) if date_str else None

            status_raw = (doc.get('ss_status') or '').upper()
            status = 'OPEN' if status_raw in ('ACTIVE', 'APPROVED', 'PROPOSED') else 'CLOSED'

            results.append(TenderData(
                title            = str(title),
                source_url       = url,
                external_id      = str(proj_id or title[:80]),
                reference_no     = str(proj_id)[:100],
                entity           = 'Asian Development Bank',
                entity_ar        = 'بنك التنمية الآسيوي',
                description      = f'ADB Project | Country: {country} | Sector: {sector}',
                category         = 'OTHER',
                status           = status,
                publication_date = pub_date,
                country_code     = self._map_country(str(country)),
            ))
        return results
