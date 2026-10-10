# tenders/scrapers/afd_dgmarket.py
"""
AFD DGMarket — Agence Française de Développement — Tenders sponsored by AFD
URL: https://tenders-afd.dgmarket.com/tenders/brandedNoticeList.do

What is this?
-------------
This is AFD's (Agence Française de Développement / French Development
Agency) white-label DGMarket instance, listing procurement notices for
projects AFD finances worldwide — construction, infrastructure,
consultancy, equipment supply.

DIFFERENT from the general www2.dgmarket.com instance covered by
DGMarketScraper — this is a separate, older DGMarket white-label site
with much simpler server-rendered HTML (no JS challenge, no Cloudflare
blocking observed). All notices here are specifically AFD-funded
projects, which is a strong relevance signal in itself given AFD's
heavy focus on infrastructure, water, energy, and urban development
in Africa, MENA, and Asia.

Technology: Simple JSP/Struts-based table HTML. GET-based pagination.

Confirmed HTML structure (from live page):
    Table: table#notice.simple > tbody > tr.odd / tr.even
        td.country    → country name (e.g. "Côte d'Ivoire", " International")
        td (2nd, no class) > a[href='/tender/{id}'] → title + link
        td.published  → "MMM D, YYYY" e.g. "Jun 19, 2026"
        td.deadline   → "MMM D, YYYY" or empty (no fixed deadline / results notice)

Detail URL: https://tenders-afd.dgmarket.com/tender/{id}

Pagination (CONFIRMED — simple GET params):
    /tenders/brandedNoticeList.do?d-446978-p=N&d-446978-s=p&d-446978-n=1&selPageNumber=N
    "1-20 of 131" total count shown at top — 20/page, ~7 pages total
    d-446978-s = sort field: p=published date, c=country, d=deadline
    d-446978-o = sort order (2 = descending, confirmed default)

Date format: "MMM D, YYYY" (e.g. "Jun 19, 2026", "Jul 9, 2026")
    Some rows have an empty deadline (results/award notices, or
    open-ended notices) — treated as OPEN with no deadline tracked.

Sample tenders (confirmed, all highly relevant for AEC):
    Cameroon  - Prequalification for Douala International Airport
                passenger terminal renovation works (buildings lot)
    Tanzania  - Design, supply, construction, installation, testing and
                commissioning of buildings for National Grid Control
                Centre (NGCC) — Lot 1 and Lot 2
    DR Congo  - Connectivity of administrative buildings across
                Governorates and 145 territories (pilot phase)
    China     - Wetland restoration project, Lot 8: Landscape engineering
    Lebanon   - Cold room supply/installation with thermal insulation,
                North Lebanon (Bcharré)
    Senegal   - Effluent treatment stations (turnkey), Hann Bay
                depollution project

Total tenders confirmed: 131 (across ~7 pages of 20 each)
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://tenders-afd.dgmarket.com'
LIST_URL  = f'{BASE_URL}/tenders/brandedNoticeList.do'
PAGE_SIZE = 20
MAX_PAGES = 10   # covers the full ~131 notice backlog (7 pages) with headroom

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8',
    'Referer':         LIST_URL,
}

# Country name (from td.country) → ISO-2
_CC_MAP = {
    "côte d'ivoire": 'CI', 'cameroon': 'CM', 'lebanon': 'LB',
    'congo, democratic republic': 'CD', 'congo, republic': 'CG',
    'ethiopia': 'ET', 'mauritania': 'MR', 'rwanda': 'RW',
    'comoros': 'KM', 'morocco': 'MA', 'tunisia': 'TN',
    'laos': 'LA', 'nigeria': 'NG', 'senegal': 'SN',
    'tanzania': 'TZ', 'benin': 'BJ', 'china': 'CN',
    'jordan': 'JO', 'egypt': 'EG', 'algeria': 'DZ', 'libya': 'LY',
    'syria': 'SY', 'iraq': 'IQ', 'palestine': 'PS', 'yemen': 'YE',
    'ghana': 'GH', 'kenya': 'KE', 'madagascar': 'MG', 'mali': 'ML',
    'niger': 'NE', 'burkina faso': 'BF', 'togo': 'TG', 'chad': 'TD',
    'gabon': 'GA', 'guinea': 'GN', 'mozambique': 'MZ',
    'international': 'ZZ', 'africa': 'ZZ', ' international': 'ZZ',
}


class AFDDGMarketScraper(BaseScraper):
    """
    Scraper for AFD's white-label DGMarket tenders portal.

    Simple GET-based pagination (no JS challenge / Cloudflare block
    encountered, unlike www2.dgmarket.com). All notices are AFD-funded
    projects worldwide, with a focus on Africa, MENA, and Asia.
    """

    country_code = 'ZZ'
    source_name  = 'AFD DGMarket — Agence Française de Développement'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [AFD] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='AFDDGMarketScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [AFD] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='AFDDGMarketScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : AFDDGMarketScraper')
        print(f'  MODE    : Simple GET pagination (?d-446978-p=N)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_urls: set[str]        = set()
        page       = 1
        total_count = None

        while page <= MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                if page == 1:
                    resp = self.session.get(LIST_URL, timeout=self.timeout)
                else:
                    params = {
                        'd-446978-s': 'p',
                        'd-446978-p': str(page),
                        'd-446978-n': '1',
                        'selPageNumber': str(page),
                    }
                    resp = self.session.get(LIST_URL, params=params, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = 'utf-8'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[AFD] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[AFD] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Total count: "1-20 of 131"
            if total_count is None:
                m = re.search(r'of\s+(\d+)', soup.get_text())
                total_count = int(m.group(1)) if m else 0
                total_pages = -(-total_count // PAGE_SIZE) if total_count else 1
                print(f'(total={total_count}, pages≤{min(total_pages, MAX_PAGES)})', end='  ')

            rows = soup.select('table#notice tbody tr')
            if not rows:
                print('(no rows — stopping)')
                if page == 1:
                    print(f'  [DEBUG] Snippet: {repr(resp.text[:300])}')
                break

            if page == 1:
                r0 = rows[0]
                country_el = r0.select_one('td.country')
                title_el   = r0.select_one("a[href^='/tender/']")
                pub_el     = r0.select_one('td.published')
                dl_el      = r0.select_one('td.deadline')
                print(f'\n  [DEBUG] {len(rows)} rows on page 1')
                print(f'  [DEBUG] country: {country_el.get_text(strip=True) if country_el else "?"}')
                print(f'  [DEBUG] title:   {title_el.get_text(strip=True)[:60] if title_el else "?"}')
                print(f'  [DEBUG] href:    {title_el.get("href","?") if title_el else "?"}')
                print(f'  [DEBUG] pub:     {pub_el.get_text(strip=True) if pub_el else "?"}')
                print(f'  [DEBUG] deadline:{dl_el.get_text(strip=True) if dl_el else "(empty)"}')
                print()

            matched  = 0
            page_new = 0

            for row in rows:
                td = self._parse_row(row)
                if not td:
                    continue
                if td.source_url in seen_urls:
                    continue
                seen_urls.add(td.source_url)
                page_new += 1
                combined = f'{td.title} {td.description}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(rows)} rows, {page_new} new, {matched} relevant')
            logger.info(f'[AFD] page {page}: {page_new} new, {matched} relevant')

            if len(rows) < PAGE_SIZE:
                print(f'       > Partial page — end of results')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant AFD DGMarket notice(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[AFD] complete — total={len(results)}')
        return results

    # ── Row parser ────────────────────────────────────────────────────────

    def _parse_row(self, row) -> Optional[TenderData]:
        try:
            link_el = row.select_one("a[href^='/tender/']")
            if not link_el:
                return None

            title = ' '.join(link_el.get_text(strip=True).split())
            href  = (link_el.get('href') or '').strip()
            detail_url = f'{BASE_URL}{href}' if href.startswith('/') else href

            if not title:
                return None

            country_el = row.select_one('td.country')
            country_raw = country_el.get_text(strip=True) if country_el else ''
            country_cc  = self._parse_country(country_raw)

            pub_el = row.select_one('td.published')
            dl_el  = row.select_one('td.deadline')
            pub_str = pub_el.get_text(strip=True) if pub_el else ''
            dl_str  = dl_el.get_text(strip=True) if dl_el else ''

            pub_date = self._parse_afd_date(pub_str)
            deadline = self._parse_afd_date(dl_str)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            notice_id = href.rstrip('/').split('/')[-1] if href else ''

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = notice_id,
                reference_no        = notice_id,
                entity              = f'AFD-funded project ({country_raw.strip() or "Multiple"})',
                entity_ar           = title,
                description         = f'AFD-sponsored tender | {country_raw.strip()}',
                category            = self.detect_category(title),
                status              = status,
                currency            = 'EUR',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
                country_code        = country_cc,
            )

        except Exception as exc:
            logger.warning(f'[AFD] row parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_afd_date(val: str) -> Optional[date]:
        """Parse AFD date format: 'MMM D, YYYY' e.g. 'Jun 19, 2026'."""
        val = val.strip()
        if not val:
            return None
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def _parse_country(cls, val: str) -> str:
        """Parse country name to ISO-2 code."""
        clean = val.strip().lower()
        return _CC_MAP.get(clean, 'ZZ')
