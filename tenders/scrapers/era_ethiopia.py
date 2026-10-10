# tenders/scrapers/era_ethiopia.py
"""
Ethiopian Roads Administration (ERA) Tender Scraper
URL: https://www.era.gov.et/en_US/web/guest/cuntry-profile

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from live screenshot + HTML doc):

  Liferay Asset Publisher portlet with a standard HTML table:
    table.table.table-bordered.table-hover.table-striped
      thead > tr > th: Title | Author | Categories | Publish Date | Expiration Date | View Count
      tbody > tr per tender:
        td[0]: <a href="/en_US/web/guest/cuntry-profile/-/asset_publisher/SE1JjNbYnO8Z/content/<slug>?...">
                  Tender Title
               </a>
        td[1]: "Ethiopian Roads Administration gov"
        td[2]: "Bid: ICB NCB" or "Bid: ICB"
        td[3]: "4/27/26"  ← Publish Date (M/D/YY format, 2-digit year)
        td[4]: ""          ← Expiration Date (usually empty)
        td[5]: "23"        ← View Count

  Detail page URL (real tender URL):
    https://www.era.gov.et/en_US/web/guest/cuntry-profile/-/asset_publisher/SE1JjNbYnO8Z/content/<slug>
    (strip ?inheritRedirect=false&redirect=... from href)

  Pagination (Liferay):
    Page 1 = base URL
    Page N = ?p_p_id=101_INSTANCE_SE1JjNbYnO8Z&...&_101_INSTANCE_SE1JjNbYnO8Z_cur=N
    Items per page = 20 (shown in HTML: 20 items per page)

  Also scrapes:
    /en_US/web/guest/geographical-location-emblem  → National competitive bid
    /en_US/web/guest/climate                       → Notice

─────────────────────────────────────────────────────────────────────────────
ACCESS:
  era.gov.et blocks non-Ethiopian IPs (SSL cert issues + firewall).
  Scraper uses SSL bypass + cloudscraper.
  Works when deployed on Ethiopian/local server.
─────────────────────────────────────────────────────────────────────────────
"""

import hashlib
import logging
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.era.gov.et'
ENTITY   = 'Ethiopian Roads Administration (ERA)'

# Portlet instance ID (from HTML: 101_INSTANCE_SE1JjNbYnO8Z)
PORTLET_ID = '101_INSTANCE_SE1JjNbYnO8Z'

# All ERA tender sections to scrape
TENDER_SECTIONS = [
    {
        'path':  '/en_US/web/guest/cuntry-profile',
        'label': 'International Competitive Bid (ICB)',
    },
    {
        'path':  '/en_US/web/guest/geographical-location-emblem',
        'label': 'National Competitive Bid (NCB)',
    },
    {
        'path':  '/en_US/web/guest/climate',
        'label': 'Notices',
    },
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

ITEMS_PER_PAGE = 20   # ERA Liferay default


def _parse_date(text: str) -> Optional['datetime.date']:
    """
    Parse ERA date strings.

    Formats seen:
      '4/27/26'    → M/D/YY  (2-digit year, assume 2000s)
      '12/24/25'   → M/D/YY
      '4/27/2026'  → M/D/YYYY
    """
    if not text:
        return None
    text = text.strip()
    if not text or text in ('-', '—', 'N/A'):
        return None

    # M/D/YY or M/D/YYYY
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})$', text)
    if m:
        mo, day, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        try:
            return datetime(yr, mo, day).date()
        except ValueError:
            pass

    # D/M/YYYY
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d %b %Y', '%d %B %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _clean_detail_url(href: str) -> str:
    """
    Strip Liferay redirect params from the tender detail URL.

    Input:  /en_US/web/guest/cuntry-profile/-/asset_publisher/SE1JjNbYnO8Z/content/slug
              ?inheritRedirect=false&redirect=<long encoded string>
    Output: https://www.era.gov.et/en_US/web/guest/cuntry-profile/-/asset_publisher/SE1JjNbYnO8Z/content/slug
    """
    if not href:
        return href
    # Build absolute URL
    if href.startswith('/'):
        full = BASE_URL + href
    elif href.startswith('http'):
        full = href
    else:
        return href

    # Remove redirect & inheritRedirect params
    parsed   = urlparse(full)
    qs       = parse_qs(parsed.query, keep_blank_values=True)
    keep_qs  = {k: v for k, v in qs.items()
                if k not in ('redirect', 'inheritRedirect')}
    new_q    = urlencode(keep_qs, doseq=True)
    clean    = urlunparse(parsed._replace(query=new_q))
    return clean


def _make_ext_id(href: str, title: str) -> str:
    """Stable external ID from URL slug or title hash."""
    # Try to extract the slug from the asset publisher URL
    # e.g. /content/notice-of-amendment-for-sub-contracting-works
    m = re.search(r'/content/([^?&]+)', href)
    if m:
        slug = m.group(1).strip('/')
        return f'ERA-{slug[:80]}'
    return f'ERA-{hashlib.md5(title.encode()).hexdigest()[:12]}'


def _era_get(url: str, session: requests.Session) -> Optional[requests.Response]:
    """GET with SSL bypass + cloudscraper fallback."""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # 1. Try cloudscraper
    try:
        import cloudscraper
        s = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )
        s.headers.update(HEADERS)
        r = s.get(url, timeout=25, verify=False)
        if r.status_code == 200 and len(r.text) > 500:
            session.cookies.update(s.cookies)
            return r
        logger.debug(f'[ERA] cloudscraper → {r.status_code}')
    except Exception as exc:
        logger.debug(f'[ERA] cloudscraper: {exc}')

    # 2. Standard requests with SSL bypass
    try:
        r = session.get(url, timeout=25, verify=False, headers=HEADERS)
        if r.status_code == 200 and len(r.text) > 500:
            return r
        logger.warning(f'[ERA] GET {url} → {r.status_code}')
    except Exception as exc:
        logger.warning(f'[ERA] requests error: {exc}')

    return None


class ERAEthiopiaScraper(BaseScraper):
    """
    Ethiopian Roads Administration (ERA) tender scraper.

    Parses the Liferay Asset Publisher table which lists tenders with columns:
      Title | Author | Categories | Publish Date | Expiration Date | View Count

    The tender detail URL is extracted from the title <a href> and cleaned
    (Liferay redirect params stripped).

    Publish Date = publication_date (M/D/YY format)
    Expiration Date = submission_deadline (usually empty on the list page)
    """

    source_name  = 'Ethiopian Roads Administration (ERA)'
    base_url     = BASE_URL
    country_code = 'ET'

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class=self.__class__.__name__, is_active=True
            )
        except ScraperSource.DoesNotExist:
            print(f'  ✗ [ERA Ethiopia] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class=self.__class__.__name__, is_active=True
            ).first()

        self.source  = source
        url_obj      = source.tendering_urls.filter(is_active=True).first()
        max_pages    = (url_obj.max_pages if url_obj and url_obj.max_pages else 10)

        rel_src, cat_src = self._reload_keywords_from_db()

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : ERAEthiopiaScraper')
        print(f'  SECTIONS: ICB · NCB · Notices')
        print(f'  PAGES   : up to {max_pages} per section')
        print(f'  NOTE    : SSL bypass enabled (era.gov.et self-signed cert)')
        print(f'{"─" * 62}')

        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()

        for section in TENDER_SECTIONS:
            base_path = section['path']
            label     = section['label']
            base_url  = BASE_URL + base_path

            print(f'\n  ── {label}')

            for page in range(1, max_pages + 1):
                # Liferay pagination URL
                if page == 1:
                    url = base_url
                else:
                    url = (
                        f'{base_url}'
                        f'?p_p_id={PORTLET_ID}'
                        f'&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view'
                        f'&p_p_col_id=column-1&p_p_col_count=1'
                        f'&_{PORTLET_ID}_delta={ITEMS_PER_PAGE}'
                        f'&_{PORTLET_ID}_keywords='
                        f'&_{PORTLET_ID}_advancedSearch=false'
                        f'&_{PORTLET_ID}_andOperator=true'
                        f'&p_r_p_564233524_resetCur=false'
                        f'&_{PORTLET_ID}_cur={page}'
                    )

                print(f'       › Page {page:>3}/{max_pages}', end='  ', flush=True)
                resp = _era_get(url, self.session)

                if not resp:
                    print('✗ Unreachable (IP blocked or site down)')
                    if page == 1:
                        break   # Skip this section entirely
                    break

                soup  = BeautifulSoup(resp.text, 'html.parser')
                items = self._parse_table(soup)

                if not items:
                    print('(no rows)')
                    break

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

                # Check if there are more pages
                total = self._total_items(soup)
                if total and (page * ITEMS_PER_PAGE) >= total:
                    break   # Got all pages

                if len(items) < ITEMS_PER_PAGE:
                    break   # Last page (partial)

                time.sleep(1.5)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  ERA Ethiopia')
        if not all_results and not seen_ids:
            print(f'  ⚠ No data — site likely blocked this server IP.')
            print(f'    Works when deployed on an Ethiopian IP or with VPN.')
        print(f'  {"═" * 58}\n')
        return all_results

    # ── Parser ────────────────────────────────────────────────────────────────

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse Liferay Asset Publisher table.

        Expected columns (from confirmed HTML):
          0: Title  (linked <a href=...>)
          1: Author  ("Ethiopian Roads Administration gov")
          2: Categories  ("Bid: ICB NCB" or "Bid: ICB")
          3: Publish Date  ("4/27/26")
          4: Expiration Date  ("" or date)
          5: View Count  ("23")
        """
        items = []

        table = soup.find('table', class_='table')
        if not table:
            # Fallback: any table with thead
            table = soup.find('table')

        if not table:
            return []

        tbody = table.find('tbody') or table
        rows  = tbody.find_all('tr')

        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 2:
                continue

            # ── Column 0: Title + URL ─────────────────────────────────────────
            title_cell = cols[0]
            a_tag      = title_cell.find('a', href=True)
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            # Clean the detail URL — strip Liferay redirect params
            raw_href   = a_tag.get('href', '')
            source_url = _clean_detail_url(raw_href)

            # ── Column 2: Categories (bid type) ───────────────────────────────
            bid_type = ''
            if len(cols) > 2:
                bid_type = cols[2].get_text(' ', strip=True)
                # e.g. "Bid: ICB NCB" → "ICB NCB"
                bid_type = re.sub(r'^Bid:\s*', '', bid_type).strip()

            # ── Column 3: Publish Date ────────────────────────────────────────
            pub_date = None
            if len(cols) > 3:
                pub_date = _parse_date(cols[3].get_text(strip=True))

            # ── Column 4: Expiration Date (deadline) ──────────────────────────
            deadline = None
            if len(cols) > 4:
                deadline = _parse_date(cols[4].get_text(strip=True))

            # ── External ID ───────────────────────────────────────────────────
            ext_id = _make_ext_id(raw_href, title)

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = bid_type,
                description         = (
                    f'{title}\n\n'
                    f'Bid Type: {bid_type}\n'
                    f'Entity: {ENTITY}\n'
                    f'Source: ERA Ethiopia Tenders Portal'
                ),
                entity              = ENTITY,
                category            = 'OTHER',
                status              = 'OPEN',
                currency            = 'ETB',
                country_code        = 'ET',
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items

    @staticmethod
    def _total_items(soup: BeautifulSoup) -> Optional[int]:
        """
        Extract total result count from Liferay pagination text.
        e.g. "Showing 1 - 20 of 39 results."
        """
        text = soup.get_text(' ')
        m = re.search(r'Showing\s+\d+\s*[-–]\s*\d+\s+of\s+(\d+)\s+results', text, re.I)
        if m:
            return int(m.group(1))
        return None
