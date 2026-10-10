# tenders/scrapers/isdb.py
"""
IsDB — Islamic Development Bank Project Procurement Tenders scraper
Source: https://www.isdb.org/project-procurement/tenders

─────────────────────────────────────────────────────────────────────────────
How the IsDB tender listing works
─────────────────────────────────────────────────────────────────────────────
This is a Drupal 8 site serving paginated HTML.

Listing URL:
    GET https://www.isdb.org/project-procurement/tenders?page=<N>
    where N is 0-based.  The default filter is status=active so we only
    need to scrape the active tenders page (pagination: ?page=0, ?page=1 …)

    Filter for active only:
    GET https://www.isdb.org/project-procurement/tenders?status=active&page=<N>

Each page shows ~20 tenders.  The pager element at the bottom shows total
pages so we know when to stop.

Row structure (each tender = <article> element):

    <article data-nid="3696" lang="en"
             class="display-teaser teaser type-tender"
             about="/project-procurement/tenders/2026/spn/provision-engineering-...">

        <div class="field-title mt-4">
            <h2><a href="/project-procurement/tenders/2026/spn/..." hreflang="en">
                Provision of Engineering Infrastructure at Paiko-Kore Grazing Reserve
            </a></h2>
        </div>

        <div class="tender-badges">
            <!-- Status -->
            <div class="field--name-field-tender-status ...">
                <div class="taxonomy-term ... slug-active">Active</div>
            </div>
            <!-- Type -->
            <div class="field--name-field-tender-type ...">
                <div class="taxonomy-term ... slug-spn">Specific Procurement Notice</div>
            </div>
            <!-- Country -->
            <div class="field--name-field-world-country ...">Nigeria</div>
            <!-- Deadline -->
            <div class="field--name-field-close-date ...">
                <time datetime="00Z">21 April 2026</time>
            </div>
        </div>
    </article>

External ID: data-nid attribute on <article>
Detail URL:  BASE_URL + href of <a> inside h2, OR about attribute on article
             e.g. https://www.isdb.org/project-procurement/tenders/2026/spn/…

Tender types → category mapping:
    spn (Specific Procurement Notice)     → CIVIL/INFRA based on title keywords
    spn-cw (SPN Civil Works)              → CIVIL
    gpn (General Procurement Notice)      → ADV
    eoi (Expression of Interest)          → ADV
    pqn (Pre-Qualification)               → CIVIL
    contract-award                        → OTHER

Pagination:
    We read ?page=0, ?page=1 … up to max_pages (TenderingURL setting).
    We detect empty pages (no <article class="type-tender">) and stop early.

Country mapping:
    The article provides the country name as plain text inside
    .field--name-field-world-country.  We map common names to ISO2 codes.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://www.isdb.org'
TENDERS_URL = f'{BASE_URL}/project-procurement/tenders'

# ── Country name → ISO2 code ──────────────────────────────────────────────────
COUNTRY_NAME_TO_CC: dict[str, str] = {
    'afghanistan':       'AF',
    'albania':           'AL',
    'algeria':           'DZ',
    'azerbaijan':        'AZ',
    'bahrain':           'BH',
    'bangladesh':        'BD',
    'benin':             'BJ',
    'bosnia':            'UN',
    'bosnia & herzegovina': 'UN',
    'brunei':            'BN',
    'burkina faso':      'BF',
    'cameroon':          'CM',
    'central african republic': 'UN',
    'chad':              'TD',
    'comoros':           'KM',
    "cote d'ivoire":     'CI',
    'cote divoire':      'CI',
    'djibouti':          'DJ',
    'egypt':             'EG',
    'gabon':             'GA',
    'gambia':            'GM',
    'guinea':            'GN',
    'guinea bissau':     'UN',
    'guyana':            'GY',
    'indonesia':         'ID',
    'iran':              'UN',
    'iraq':              'IQ',
    'jordan':            'JO',
    'kazakhstan':        'KZ',
    'kenya':             'KE',
    'kyrgyz':            'UN',
    'kyrgyz republic':   'UN',
    'kuwait':            'KW',
    'lebanon':           'UN',
    'libya':             'UN',
    'malaysia':          'UN',
    'maldives':          'UN',
    'mali':              'ML',
    'mauritania':        'MR',
    'morocco':           'MA',
    'mozambique':        'MZ',
    'niger':             'NE',
    'nigeria':           'NG',
    'oman':              'OM',
    'pakistan':          'PK',
    'palestine':         'UN',
    'qatar':             'QA',
    'saudi arabia':      'SA',
    'senegal':           'SN',
    'sierra leone':      'UN',
    'somalia':           'UN',
    'sudan':             'UN',
    'suriname':          'UN',
    'syria':             'UN',
    'tajikistan':        'TJ',
    'togo':              'TG',
    'tunisia':           'TN',
    'turkey':            'UN',
    'turkmenistan':      'UN',
    'türkiye':           'UN',
    'uganda':            'UG',
    'united arab emirates': 'AE',
    'uae':               'AE',
    'united kingdom':    'UN',
    'uzbekistan':        'UN',
    'yemen':             'UN',
}

# ── Tender type slug → TenderRadar category hint ──────────────────────────────
TYPE_CATEGORY: dict[str, str] = {
    'spn':            'CIVIL',   # Specific Procurement Notice
    'spn-cw':         'CIVIL',   # SPN Civil Works
    'pqn':            'CIVIL',   # Pre-Qualification Notice
    'gpn':            'ADV',     # General Procurement Notice
    'eoi':            'ADV',     # Expression of Interest
    'contract-award': 'OTHER',
    'oth':            'OTHER',
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         TENDERS_URL,
}

# Date formats used by IsDB ("21 April 2026", "29 October 2025", "1 November 2025")
DATE_FORMATS = (
    '%d %B %Y',   # "21 April 2026"
    '%d %b %Y',   # "21 Apr 2026"
    '%B %d, %Y',  # "April 21, 2026"
    '%Y-%m-%d',
)


def _effective_max_pages(url_obj, default: int = 10) -> int:
    """Return max_pages from DB setting (scraping mode does NOT affect page count)."""
    return url_obj.max_pages if (url_obj and url_obj.max_pages) else default


class IsDBScraper(BaseScraper):
    """
    Scraper for IsDB (Islamic Development Bank) Project Procurement tenders.

    Fetches paginated listing from:
        https://www.isdb.org/project-procurement/tenders?status=active&page=<N>

    TenderingURL in DB should be:
        url              = https://www.isdb.org/project-procurement/tenders
        pagination_style = QUERY
        page_param       = page
        max_pages        = 10     (≈ 200 tenders; adjust as needed)
        extra_params     = {"status": "active"}
    """

    country_code = 'SA'   # IsDB headquartered in Jeddah; default country SA
    source_name  = 'IsDB'
    base_url     = BASE_URL

    # ── Main entry point ──────────────────────────────────────────────────────

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='IsDBScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [IsDB] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='IsDBScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = _effective_max_pages(url_obj, default=10)
        extra     = url_obj.extra_params if url_obj else {}

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : IsDBScraper')
        print(f'  URL     : {TENDERS_URL}')
        print(f'  PAGES   : up to {max_pages}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []

        for page_idx in range(max_pages):
            params = {'page': page_idx, 'status': 'active'}
            params.update(extra)

            print(f'       › page {page_idx + 1:>2}/{max_pages}  '
                  f'(?page={page_idx})', end='  ', flush=True)
            logger.info(f'[IsDB] page {page_idx + 1}/{max_pages} (idx={page_idx})')

            try:
                resp = self.get(TENDERS_URL, params=params)
            except Exception as exc:
                print(f'✗ ERROR: {exc}')
                logger.error(f'[IsDB] GET page {page_idx} failed: {exc}', exc_info=True)
                break

            soup  = BeautifulSoup(resp.text, 'html.parser')
            items = self._parse_articles(soup)

            if page_idx == 0:
                print()
                print(f'  [DEBUG] Response length : {len(resp.text):,} chars')
                print(f'  [DEBUG] Tenders found   : {len(items)}')
                if items:
                    s = items[0]
                    print(f'  [DEBUG] Sample title    : {s.title[:80]!r}')
                    print(f'  [DEBUG] Sample ext_id   : {s.external_id!r}')
                    print(f'  [DEBUG] Sample country  : {s.country_code!r}')
                    print(f'  [DEBUG] Sample deadline : {s.submission_deadline!r}')
                print()

            if not items:
                print('(empty — stopping)')
                logger.info(f'[IsDB] page {page_idx + 1}: no tenders, stopping')
                break

            # ── Relevance + category ──────────────────────────────────────────
            page_text = soup.get_text(separator=' ', strip=True)
            matched   = 0

            if self.detect_relevance(page_text):
                # Page-level hit: accept all, refine category per item
                for item in items:
                    combined = self._item_text(item)
                    if not item.category or item.category == 'OTHER':
                        item.category = self.detect_category(combined)
                    all_results.append(item)
                matched = len(items)
                print(f'→ {len(items)} tenders, {matched} relevant (page hit)')
                logger.info(f'[IsDB] page {page_idx + 1}: page relevant — all {matched} taken')
            else:
                for item in items:
                    combined = self._item_text(item)
                    if self.detect_relevance(combined):
                        if not item.category or item.category == 'OTHER':
                            item.category = self.detect_category(combined)
                        all_results.append(item)
                        matched += 1
                print(f'→ {len(items)} tenders, {matched} relevant')
                logger.info(f'[IsDB] page {page_idx + 1}: {matched}/{len(items)} relevant')

            # Stop early if this is the last page (pager detection)
            if self._is_last_page(soup, page_idx):
                logger.info(f'[IsDB] reached last page at index {page_idx}')
                break

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant tender(s)  ←  IsDB')
        print(f'  {"═" * 58}\n')
        logger.info(f'[IsDB] scrape() complete — total={len(all_results)}')

        return all_results

    # ── HTML parser ───────────────────────────────────────────────────────────

    def _parse_articles(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse all <article class="type-tender"> elements on one listing page.
        """
        articles = soup.select('article.type-tender')
        items: list[TenderData] = []

        for art in articles:
            # ── External ID from data-nid ─────────────────────────────────────
            nid = art.get('data-nid', '').strip()
            if not nid:
                continue

            # ── Title & detail URL ────────────────────────────────────────────
            h2_a = art.select_one('div.field-title h2 a')
            if not h2_a:
                continue
            title = h2_a.get_text(strip=True)
            if not title:
                continue

            href = h2_a.get('href', '').strip()
            if href.startswith('http'):
                source_url = href
            elif href:
                source_url = f'{BASE_URL}{href}'
            else:
                # Fall back to about attribute on article
                about = art.get('about', '')
                source_url = f'{BASE_URL}{about}' if about else ''

            # ── Status ───────────────────────────────────────────────────────
            status_div = art.select_one(
                '.field--name-field-tender-status .taxonomy-term'
            )
            status_slug = ''
            if status_div:
                for cls in (status_div.get('class') or []):
                    if cls.startswith('slug-'):
                        status_slug = cls[len('slug-'):]
                        break
            status = 'OPEN' if status_slug in ('active', 'actif') else 'CLOSED'

            # ── Tender type ───────────────────────────────────────────────────
            type_div = art.select_one(
                '.field--name-field-tender-type .taxonomy-term'
            )
            type_slug = ''
            type_name = ''
            if type_div:
                type_name = type_div.get_text(strip=True)
                for cls in (type_div.get('class') or []):
                    if cls.startswith('slug-'):
                        type_slug = cls[len('slug-'):]
                        break

            category = TYPE_CATEGORY.get(type_slug, 'OTHER')

            # ── Country ───────────────────────────────────────────────────────
            country_div = art.select_one('.field--name-field-world-country')
            country_name = country_div.get_text(strip=True) if country_div else ''
            country_code = self._country_name_to_cc(country_name)

            # ── Deadline ─────────────────────────────────────────────────────
            time_el = art.select_one('.field--name-field-close-date time')
            deadline_raw = time_el.get_text(strip=True) if time_el else ''
            deadline = self._parse_date(deadline_raw)

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = nid,
                reference_no        = nid,    # IsDB uses nid; no separate ref
                description         = (
                    f'{title}\n\nType: {type_name}\n'
                    f'Country: {country_name}'
                ).strip(),
                entity              = 'Islamic Development Bank (IsDB)',
                entity_ar           = 'البنك الإسلامي للتنمية',
                category            = category,
                status              = status,
                currency            = 'USD',
                country_code        = country_code,
                submission_deadline = deadline,
                publication_date    = None,   # not shown on listing page
            ))

        return items

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _country_name_to_cc(name: str) -> str:
        """Map IsDB country name text → ISO2 code used by TenderRadar."""
        return COUNTRY_NAME_TO_CC.get(name.strip().lower(), 'UN')

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        """
        Parse date strings like "21 April 2026", "1 November 2025".
        IsDB renders dates without leading zeros.
        """
        if not text:
            return None
        text = text.strip()
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _is_last_page(soup: BeautifulSoup, current_page: int) -> bool:
        """
        Check if current page is the last page by reading the Drupal pager.
        If .pager__item--next is absent or disabled, we are on the last page.
        """
        next_li = soup.select_one('li.pager__item--next')
        if next_li is None:
            return True
        # Also detect when no articles are on the page (handled by caller)
        return False

    @staticmethod
    def _item_text(item: TenderData) -> str:
        """Build combined text for relevance/category keyword matching."""
        return ' '.join(filter(None, [
            item.title, item.description, item.country_code,
        ]))
