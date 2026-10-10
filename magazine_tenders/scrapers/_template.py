# magazine_tenders/scrapers/_template.py
"""
House-style skeleton for a magazine scraper. NOT registered — copy it to
`<code>.py`, fill in the investigation notes below, and add the matching entry
to `magazine_tenders/sources.py`.

Keep this docstring shape in the copy: every existing scraper in the project
documents its own investigation here, and that record is what makes a broken
site diagnosable a year later.

    URL          : the exact listing page
    Technology   : static HTML / JSON API / HTML fragment via POST
    Request      : GET or POST, params or payload, required headers
    Structure    : the CSS selector (or JSON path) for one item, and the fields
                   each item exposes
    Sample item  : one real item, pasted verbatim
    Date format  : e.g. 15/08/2026, 2026-08-15, ١٥ آب ٢٠٢٦
    Pagination   : none / ?page=N / offset / load-more
    Investigated : YYYY-MM-DD

Rules this file exists to enforce (see the plan, §3 and §6.2):

* A scraper NEVER writes to the database. It returns `list[TenderData]`;
  saving and classification belong to `magazine_tenders.runner`.
* Never query `ScraperSource.objects.get(..., is_active=True)` the way the web
  scrapers do — a magazine's anchor row is deliberately inactive. The runner
  assigns `self.source` before calling `scrape()`.
* `except Exception as exc:` — never a bare `except`.
* Every log line starts with `[MAG:<CODE>]`.
"""
import logging
from datetime import date, datetime
from typing import Optional

from django.conf import settings

from tenders.scrapers.base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

CODE = 'EXAMPLE'

BASE_URL = 'https://example-magazine.com'
LIST_URL = f'{BASE_URL}/tenders'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Referer':         BASE_URL,
}

#: Hard cap on pages walked, so a pagination bug cannot crawl a whole site.
MAX_PAGES = 5


class ExampleMagazineScraper(BaseScraper):
    """
    Scraper for Example Magazine's tender-announcement listing.

    Parses <describe the structure — static card grid, JSON array, table rows>.
    """

    country_code = 'JO'
    source_name  = 'Example Magazine — Tender Announcements'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print(f'  X  [MAG:{CODE}] beautifulsoup4 not installed')
            return []

        # The runner sets this. The anchor row is inactive by design, so the
        # is_active=True lookup the web scrapers do would never find it.
        source = getattr(self, 'source', None)
        if source is None:
            print(f'  X  [MAG:{CODE}] self.source not set — skipping')
            logger.error(f'[MAG:{CODE}] scrape() called without a source')
            return []

        ua = getattr(settings, 'MAGAZINE_USER_AGENT', '') or HEADERS['User-Agent']
        self.session.headers.update({**HEADERS, 'User-Agent': ua})

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : {type(self).__name__}')
        print(f'  MODE    : HTML scraping (<describe>)')
        print(f'  URL     : {LIST_URL}')
        print(f'  FILTER  : AI decides AEC — no keyword gate here')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        today = date.today()
        max_pages = min(MAX_PAGES, getattr(settings, 'MAGAZINE_MAX_PAGES', MAX_PAGES))
        results: list[TenderData] = []

        for page in range(1, max_pages + 1):
            try:
                resp = self.session.get(LIST_URL, params={'page': page},
                                        timeout=self.timeout)
                resp.raise_for_status()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print(f'  X  [MAG:{CODE}] Blocked on page {page}: {exc}')
                else:
                    print(f'  X  [MAG:{CODE}] Error on page {page}: {exc}')
                logger.error(f'[MAG:{CODE}] fetch error (page {page}): {exc}')
                break

            soup  = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.select('div.tender-card')          # ← the item selector
            print(f'       > page {page}: {len(cards)} item(s)')
            if not cards:
                break

            for card in cards:
                try:
                    td = self._parse_card(card, today)
                except Exception as exc:
                    logger.warning(f'[MAG:{CODE}] item parse failed: {exc}')
                    continue
                if td is not None:
                    results.append(td)

        print(f'  TOTAL   : {len(results)} announcement(s)')
        logger.info(f'[MAG:{CODE}] complete — total={len(results)}')
        return results

    # ── one item ─────────────────────────────────────────────────────────────
    def _parse_card(self, card, today: date) -> Optional[TenderData]:
        title = (card.select_one('h3') or card).get_text(' ', strip=True)
        if not title:
            return None

        link = card.select_one('a[href]')
        detail_url = link['href'] if link else LIST_URL
        if detail_url.startswith('/'):
            detail_url = BASE_URL + detail_url

        description = ''
        entity      = ''
        ref_no      = ''
        pub_date    = None
        deadline    = self._parse_date(card.select_one('.deadline'))

        # A missing deadline is left as None rather than guessed: the shared
        # save policy treats "no deadline" as still open, while a wrong past
        # date would make the announcement disappear entirely.
        status = 'OPEN' if deadline is None or deadline >= today else 'CLOSED'

        # Provisional only — the classification service overwrites `category`
        # (and decides is_aec) after the runner saves the row.
        category = self.detect_category(f'{title} {description}')

        return TenderData(
            title               = title,
            title_ar            = '',
            source_url          = detail_url,
            external_id         = '',          # runner hashes one if left blank
            reference_no        = ref_no,
            entity              = entity,
            entity_ar           = '',
            description         = description,
            description_ar      = '',
            category            = category,
            status              = status,
            publication_date    = pub_date,
            submission_deadline = deadline,
            country_code        = self.country_code,
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    _DATE_FORMATS = ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d %B %Y', '%d %b %Y')

    def _parse_date(self, node) -> Optional[date]:
        """
        Parse one date cell. Returns None on anything unrecognised — an
        unparseable date must not lose the whole announcement.
        """
        if node is None:
            return None
        raw = node.get_text(' ', strip=True) if hasattr(node, 'get_text') else str(node)
        raw = raw.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')).strip()
        if not raw:
            return None
        for fmt in self._DATE_FORMATS:
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        logger.debug(f'[MAG:{CODE}] unrecognised date {raw!r}')
        return None
