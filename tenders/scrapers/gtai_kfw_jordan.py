# tenders/scrapers/gtai_kfw_jordan.py
"""
GTAI (Germany Trade & Invest) — KfW-Financed Tenders in Jordan
URL: https://www.gtai.de

What is this?
-------------
GTAI publishes international tender notices for projects financed by German
development banks (KfW, GIZ, etc.). This scraper targets a pre-filtered
search URL that shows:

  Filters baked into the state token:
    • Rubric    = Tenders       (99662)
    • Country   = Jordan        (5058)
    • Financer  = KfW Entwicklungsbank (4486)

  → ~43 results across ~5 pages (10 results/page, 0-indexed)

The URL uses a CoreMedia CMS "formState" token that encodes all active
filters. The token is stable — pagination just adds ?page=N.

Search URL pattern:
    https://www.gtai.de/en/meta/search/66080!search;<TOKEN>?page=N

Detail URL pattern (relative path in each result):
    /en/trade/jordan/tenders/<slug>
    → https://www.gtai.de/en/trade/jordan/tenders/<slug>

HTML structure (each <li class="result-item">):
    .overline__text.date       → publication date e.g. "Nov 12, 2025"
    .overline__text [1]        → notice type   e.g. "Tender Notice"
    .overline__text [2]        → country       e.g. "Jordan"
    .overline__text [3]        → sector        e.g. "Vocational Education"
    .content a[href]           → detail URL path
    .content h3                → title
    .content p:nth-of-type(1)  → phase  e.g. "Tender Award", "Cancellation Notice KfW"
    .content p:nth-of-type(2)  → "Financing: KfW Entwicklungsbank"
    .content p.excerpt         → description excerpt
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL = 'https://www.gtai.de'

# State token encodes: Tenders + Jordan + KfW Entwicklungsbank filter
# If the token ever expires/changes, update it from the browser URL
SEARCH_TOKEN = (
    'eNqNksFOwzAMht_F51YaaKu2PgAvADfEwU3dkSl1OjuBddPenXQTHAoVuUXW_9n-f-cC'
    'HRoKCvUFmqiWSbVEIZwKSo5MoBbqVwiCLcFbAXQyLhXSy_ABakAe4VqAxEasmVO7XVU9'
    '_kFxdC5BxkcOMs6pzWqzXYY06bzoDFqUO_zMlepAxqLLlZPx7PtfppfdRg2-z168s4xsS'
    'ObprNfbajmd4R2Vsjfy_ZDu95TO0GKwnrOtfxCHl3HInqQklrKta2wOSZSdlJcew__d73'
    '91Cgn39GzPafuHVQHHe6M01kuaCWVLHUYXSiggxXLzSNx-qwR5Tz9IwIm5LXL9AljFGBY'
)
SEARCH_PATH  = f'/en/meta/search/66080!search;{SEARCH_TOKEN}'
SEARCH_URL   = f'{BASE_URL}{SEARCH_PATH}'

RESULTS_PER_PAGE = 10
MAX_PAGES        = 20    # safety cap

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': BASE_URL,
}

# Notice phases that mean the tender is still relevant (not just an award notice)
# We collect ALL phases including awards since they still show AEC project info
SKIP_PHASES: set[str] = set()   # empty = keep everything


class GTAIKfwJordanScraper(BaseScraper):
    """
    Scraper for GTAI KfW-financed tender notices in Jordan.

    Parses the GTAI CoreMedia CMS search result pages (HTML).
    Pagination via ?page=N (0-indexed).
    """

    country_code = 'JO'
    source_name  = 'GTAI KfW Jordan'
    base_url     = BASE_URL

    # ── Entry point ───────────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [GTAIKfwJordan] beautifulsoup4 not installed — run: pip install beautifulsoup4')
            return []

        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='GTAIKfwJordanScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [GTAIKfwJordan] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='GTAIKfwJordanScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : GTAIKfwJordanScraper')
        print(f'  MODE    : HTML scraping (CoreMedia CMS)')
        print(f'  FILTER  : Tenders + Jordan + KfW Entwicklungsbank')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:     list[TenderData] = []
        page         = 0
        total_pages  = None

        while True:
            url = f'{SEARCH_URL}?page={page}'
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF / IP restriction)')
                    logger.warning(f'[GTAIKfwJordan] WAF block page {page}: {exc}')
                elif '404' in err:
                    print('X 404 — URL not found')
                    logger.error(f'[GTAIKfwJordan] 404 page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[GTAIKfwJordan] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.select('li.result-item')

            # Extract total count on first page
            if total_pages is None:
                total_el = soup.select_one('.counter b')
                if total_el:
                    try:
                        total = int(total_el.get_text(strip=True).replace(',', ''))
                        total_pages = min(math.ceil(total / RESULTS_PER_PAGE), MAX_PAGES)
                        print(f'(total={total}, pages≤{total_pages})', end='  ')
                    except ValueError:
                        pass

            if not items:
                print('(empty — stopping)')
                break

            # Debug on page 0
            if page == 0 and items:
                sample  = items[0]
                ot      = [t.get_text(strip=True) for t in sample.select('.overline__text')]
                h3_el   = sample.select_one('h3')
                print(f'\n  [DEBUG] overline texts : {ot}')
                print(f'  [DEBUG] title          : {h3_el.get_text(strip=True)[:80] if h3_el else "?"}')
                print()

            matched = 0
            for item in items:
                td = self._parse_item(item)
                if td:
                    if self.detect_relevance(
                        f'{td.title} {td.description} {td.entity}'
                    ):
                        results.append(td)
                        matched += 1

            print(f'-> {len(items)} items, {matched} relevant')
            logger.info(f'[GTAIKfwJordan] page {page}: {len(items)} items, {matched} relevant')

            if total_pages is not None and page >= total_pages - 1:
                print(f'       > Reached last page ({page})')
                break
            if page >= MAX_PAGES - 1:
                print(f'       > Hit MAX_PAGES cap')
                break
            if len(items) < RESULTS_PER_PAGE:
                print(f'       > Partial page ({len(items)}) — end of data')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant GTAI KfW Jordan tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[GTAIKfwJordan] complete — total={len(results)}')
        return results

    # ── HTML item parser ──────────────────────────────────────────────────────

    def _parse_item(self, item) -> Optional[TenderData]:
        """Parse one <li class="result-item"> into a TenderData."""
        try:
            # ── Overline fields ───────────────────────────────────────────
            overlines = item.select('.overline__text')
            pub_date_str = ''
            notice_type  = ''
            country_str  = ''
            sector_str   = ''

            for i, el in enumerate(overlines):
                cls = el.get('class', [])
                txt = el.get_text(strip=True)
                if 'date' in cls:
                    pub_date_str = txt
                elif i == 1:
                    notice_type = txt        # "Tender Notice"
                elif i == 2:
                    country_str = txt        # "Jordan"
                elif i == 3:
                    sector_str  = txt        # "Vocational Education"

            # ── Content block ─────────────────────────────────────────────
            content = item.select_one('.content')
            if not content:
                return None

            # Detail URL
            link_el = content.select_one('a[href]')
            href    = link_el['href'] if link_el else ''
            detail_url = (
                f'{BASE_URL}{href}' if href.startswith('/') else href
            ) if href else BASE_URL

            # Extract numeric ID from slug (last segment after last -)
            # e.g. "consulting-services-for-gender...-1892082" → "1892082"
            external_id = ''
            if href:
                slug = href.rstrip('/').split('/')[-1]
                parts = slug.rsplit('-', 1)
                if len(parts) == 2 and parts[1].isdigit():
                    external_id = parts[1]

            # Title
            h3_el = content.select_one('h3')
            title = h3_el.get_text(strip=True) if h3_el else ''
            if not title:
                return None

            # Paragraphs: phase, financing, excerpt
            ps        = content.select('p')
            phase     = ''
            financing = ''
            excerpt   = ''
            for p in ps:
                cls = p.get('class', [])
                txt = p.get_text(strip=True)
                if 'excerpt' in cls:
                    excerpt = txt
                elif not phase:
                    phase = txt          # first p = phase
                elif not financing:
                    financing = txt      # second p = "Financing: ..."

            # ── Status mapping ────────────────────────────────────────────
            phase_lower = phase.lower()
            if 'cancellation' in phase_lower:
                status = 'CANCELLED'
            elif 'award' in phase_lower:
                status = 'AWARDED'
            elif 'extension' in phase_lower:
                status = 'OPEN'
            elif 'result' in phase_lower or 'pq-result' in phase_lower:
                status = 'AWARDED'
            else:
                status = 'OPEN'

            # ── Description ───────────────────────────────────────────────
            desc_parts = [
                phase,
                financing,
                f'Sector: {sector_str}' if sector_str else '',
                excerpt,
            ]
            description = ' | '.join(filter(None, desc_parts))

            combined = f'{title} {sector_str} {excerpt}'

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = external_id,
                reference_no        = external_id,
                entity              = 'KfW Entwicklungsbank',
                entity_ar           = 'بنك التنمية الألماني KfW',
                description         = description,
                category            = self.detect_category(combined),
                status              = status,
                currency            = 'EUR',
                estimated_value     = None,
                publication_date    = self._parse_gtai_date(pub_date_str),
                submission_deadline = None,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[GTAIKfwJordan] parse error: {exc}')
            return None

    # ── Date parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_gtai_date(val: str) -> Optional[date]:
        """Parse GTAI date format: 'Nov 12, 2025' or 'Sep 29, 2025'."""
        if not val:
            return None
        val = val.strip()
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None
