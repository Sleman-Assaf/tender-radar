# tenders/scrapers/wasac_rwanda.py
"""
WASAC Group Rwanda Tender Scraper
URL: https://www.wasac.rw/en/public-information/tenders

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from full HTML document):

  TYPO3 CMS site — single-page listing, ALL tenders in one HTML response.
  No pagination — the full list is rendered server-side.

  Desktop table (hidden on mobile, used by scraper):
    table.min-w-full.bg-white.border
      thead > tr > th: Title | Deadline | Summary | (empty = Download)
      tbody > tr per tender:
        td[0]: <a href="/en/public-information/tenders/tenderdetails/<slug>?cHash=...">
                  TENDER TITLE
               </a>
        td[1]: "27/04/2026 15:00\nPM"  ← Deadline (D/M/YYYY HH:MM + AM/PM)
        td[2]: Summary text (first ~200 chars of description)
        td[3]: <a href="...">Download</a>  ← same detail URL

  Detail URL: https://www.wasac.rw/en/public-information/tenders/tenderdetails/<slug>?cHash=<hash>

  External ID: extracted from slug, e.g.:
    /en/public-information/tenders/tenderdetails/request-for-expressions-of-interest-advertisement-non-revenue-water-expert
    → WASAC-request-for-expressions-of-interest-advertisement-non-revenue-water-expert

  Date format: "27/04/2026 15:00\nPM" → D/M/YYYY → 2026-04-27
─────────────────────────────────────────────────────────────────────────────
"""

import hashlib
import logging
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.wasac.rw'
ENTITY   = 'WASAC Group Rwanda'
PAGE_URL = '/en/public-information/tenders'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         'https://www.wasac.rw/',
}


def _parse_date(text: str) -> Optional['datetime.date']:
    """
    Parse WASAC deadline strings.

    Confirmed format: "27/04/2026 15:00\nPM"
    Also handles:     "06/04/2026 15:00 PM", "14/01/2026 03:00 AM"
    """
    if not text:
        return None
    # Normalize whitespace and take first part (date + time, strip AM/PM)
    text = ' '.join(text.split()).strip()
    # Remove trailing AM/PM if present after time
    text = re.sub(r'\s*(AM|PM)\s*$', '', text, flags=re.I).strip()
    # Extract D/M/YYYY from start
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        day, mo, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(yr, mo, day).date()
        except ValueError:
            pass
    return None


def _make_ext_id(href: str, title: str) -> str:
    """Stable external ID from URL slug."""
    # Extract slug: /en/public-information/tenders/tenderdetails/<slug>
    m = re.search(r'/tenderdetails/([^?&/]+)', href)
    if m:
        return f'WASAC-{m.group(1)[:80]}'
    return f'WASAC-{hashlib.md5(title.encode()).hexdigest()[:12]}'


class WASACRwandaScraper(BaseScraper):
    """
    WASAC Group Rwanda tender scraper.

    TYPO3 CMS — all tenders rendered in a single HTML page.
    Parses the desktop table (hidden md:block) with columns:
      Title | Deadline | Summary | Download

    No pagination needed — full list on one page.
    """

    source_name  = 'WASAC Group Rwanda'
    base_url     = BASE_URL
    country_code = 'RW'

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class=self.__class__.__name__, is_active=True
            )
        except ScraperSource.DoesNotExist:
            print(f'  ✗ [WASAC Rwanda] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class=self.__class__.__name__, is_active=True
            ).first()

        self.source = source
        self._reload_keywords_from_db()

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : WASACRwandaScraper')
        print(f'  URL     : {BASE_URL}{PAGE_URL}')
        print(f'  CMS     : TYPO3 — single-page listing')
        print(f'{"─" * 62}')

        url  = BASE_URL + PAGE_URL
        resp = self._wasac_get(url)

        if not resp:
            print(f'  ✗ Cannot reach {url}')
            return []

        print(f'  ✓ {resp.status_code} | {len(resp.text):,} bytes')
        soup  = BeautifulSoup(resp.text, 'html.parser')
        items = self._parse_table(soup)

        all_results = []
        seen_ids    = set()

        for item in items:
            if item.external_id not in seen_ids:
                seen_ids.add(item.external_id)
                combined = f'{item.title} {item.description}'
                if self.detect_relevance(combined):
                    item.category = self.detect_category(combined)
                    all_results.append(item)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  WASAC Rwanda')
        print(f'  {"═" * 58}\n')
        return all_results

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _wasac_get(self, url: str):
        """GET with cloudscraper fallback."""
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update(HEADERS)
            r = s.get(url, timeout=25)
            if r.status_code == 200 and len(r.text) > 500:
                return r
            logger.debug(f'[WASAC] cloudscraper → {r.status_code}')
        except Exception as exc:
            logger.debug(f'[WASAC] cloudscraper: {exc}')

        try:
            self.session.headers.update(HEADERS)
            r = self.session.get(url, timeout=25)
            if r.status_code == 200 and len(r.text) > 500:
                return r
            logger.warning(f'[WASAC] GET {url} → {r.status_code}')
        except Exception as exc:
            logger.warning(f'[WASAC] requests: {exc}')
        return None

    # ── Parser ────────────────────────────────────────────────────────────────

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse the desktop tender table.

        Confirmed columns (from live HTML):
          0: Title  →  <a href="/en/public-information/tenders/tenderdetails/<slug>?cHash=...">
          1: Deadline → "27/04/2026 15:00\nPM"
          2: Summary → plain text description
          3: Download → <a href="..."> (same URL as title, ignored)
        """
        items = []

        # Target: table with thead containing "Title" and "Deadline"
        target_table = None
        for table in soup.find_all('table'):
            headers = [th.get_text(strip=True).lower()
                       for th in table.find_all('th')]
            if 'title' in headers and 'deadline' in headers:
                target_table = table
                break

        if not target_table:
            # Fallback: first table with tbody rows containing links
            for table in soup.find_all('table'):
                if table.find('tbody') and table.find('a', href=True):
                    target_table = table
                    break

        if not target_table:
            logger.warning('[WASAC] No tender table found in HTML')
            return []

        tbody = target_table.find('tbody') or target_table
        rows  = tbody.find_all('tr')

        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 2:
                continue

            # ── Column 0: Title + detail URL ─────────────────────────────────
            title_cell = cols[0]
            a_tag      = title_cell.find('a', href=True)
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            href       = a_tag.get('href', '')
            source_url = urljoin(BASE_URL, href) if href.startswith('/') else href

            # ── Column 1: Deadline ────────────────────────────────────────────
            deadline = None
            if len(cols) > 1:
                deadline = _parse_date(cols[1].get_text(' ', strip=True))

            # ── Column 2: Summary/description ────────────────────────────────
            description = ''
            if len(cols) > 2:
                description = cols[2].get_text(strip=True)[:500]

            # ── External ID ───────────────────────────────────────────────────
            ext_id = _make_ext_id(href, title)

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = '',
                description         = (
                    f'{description}\n\n'
                    f'Entity: {ENTITY}\n'
                    f'Source: WASAC Rwanda Tenders Portal'
                ) if description else f'{title}\n\nEntity: {ENTITY}',
                entity              = ENTITY,
                category            = 'OTHER',
                status              = 'OPEN',
                currency            = 'RWF',
                country_code        = 'RW',
                publication_date    = None,
                submission_deadline = deadline,
            ))

        return items
