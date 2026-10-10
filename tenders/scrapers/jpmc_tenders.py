# tenders/scrapers/jpmc_tenders.py
"""
JPMC — Jordan Phosphate Mines Company — Currently Offered Tenders
URL: https://www.jpmc.com.jo/en/tender

What is this?
-------------
JPMC (شركة مناجم الفوسفات الأردنية) is a major Jordanian public limited
company (est. 1949) engaged in phosphate mining, fertilizer production,
and chemical processing. It operates large industrial complexes,
mines (Al-Hassa, Al-Abyad, Al-Shidiya), and an export port in Aqaba.

Highly relevant for AEC consultancies — tenders cover mine engineering
services, EPC/EPCM contracts for major industrial projects (e.g. JTFC
300,000 MTPY P₂O₅ fertilizer plant), civil/structural works, maintenance,
warehouse construction, and housing rehabilitation at mine sites.

Technology: Yii2 PHP framework — clean, modern HTML. NO ASP.NET ViewState
needed. Simple GET-based pagination.

Confirmed HTML structure (from live page):
    Card: div.ads-card
        span (1st child)  → "Starting Date: DD-MM-YYYY"
        span (2nd child)  → "End Date: DD-MM-YYYY"
        div.ads-card-content:
            h3                → title (often Arabic with HTML entity encoding)
            a.hover-growing-links[href] → "Read More" → detail URL

Detail URL: https://www.jpmc.com.jo{href}
    e.g. /en/tender/tender-no-jtfcitb001-jordan-turkiye-fertilizer-company...

Pagination (CONFIRMED — simple GET, no POST/ViewState required):
    GET /en/tender?page=N   (1-based)
    div.pagination-container li.page-item a[href*="page="]
    5 pages confirmed in snapshot, ~9 tenders/page

Date format: DD-MM-YYYY (Jordanian format)
    Note: "Starting Date" here = publication date, "End Date" = deadline

Sample tenders (confirmed):
    T2026/22 M  - Insurance for electrical cranes against fire risks
                  (Al-Hassa, Al-Shidiya mines) — 21-06-2026 to 28-06-2026
    JTFC_ITB_001 - Jordan Türkiye Fertilizer Company: JTFC 300,000 MTPY
                   P₂O₅ Project — 18-05-2026 to 11-06-2026 (major EPC tender)
    T2026/17 W  - Engineering services for central warehouse hangar
                  construction at Al-Abyad mine — 12-05-2026 to 18-05-2026
    T2026/15 SH - Workers housing rehabilitation B4-1 & B4-3,
                  Al-Shidiya mine — 31-05-2026 to 07-06-2026

Note: Titles are mostly in Arabic (HTML-entity encoded numeric refs like
&#1575; for ا) even on the /en/ URL — this site doesn't fully translate
tender titles. Both title and title_ar store the decoded text.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.jpmc.com.jo'
LIST_URL  = f'{BASE_URL}/en/tender'
MAX_PAGES = 15

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Referer':         LIST_URL,
}


class JPMCTendersScraper(BaseScraper):
    """
    Scraper for JPMC (Jordan Phosphate Mines Company) tenders.

    Simple GET-based pagination — no ASP.NET ViewState complexity.
    Yii2 framework with clean HTML structure.
    """

    country_code = 'JO'
    source_name  = 'JPMC — Jordan Phosphate Mines Company'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [JPMC] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='JPMCTendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [JPMC] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='JPMCTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : JPMCTendersScraper')
        print(f'  MODE    : Simple GET pagination (?page=N)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_urls: set[str]        = set()
        page       = 1
        total_pages = None

        while page <= MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                params = {'page': page} if page > 1 else {}
                resp = self.session.get(LIST_URL, params=params, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = 'utf-8'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[JPMC] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[JPMC] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.select('div.ads-card')

            if not cards:
                print('(no items — stopping)')
                if page == 1:
                    print(f'  [DEBUG] Snippet: {repr(resp.text[:300])}')
                break

            # Determine total pages from pagination on page 1
            if total_pages is None:
                page_links = soup.select('li.page-item a.page-link[href*="page="]')
                nums = []
                for a in page_links:
                    txt = a.get_text(strip=True)
                    if txt.isdigit():
                        nums.append(int(txt))
                total_pages = max(nums) if nums else 1
                print(f'(total_pages={total_pages})', end='  ')

            if page == 1:
                c0 = cards[0]
                spans = c0.find_all('span', recursive=False)
                title_el = c0.select_one('div.ads-card-content h3')
                link_el  = c0.select_one('div.ads-card-content a.hover-growing-links')
                print(f'\n  [DEBUG] {len(cards)} items on page 1')
                print(f'  [DEBUG] start: {spans[0].get_text(strip=True) if len(spans)>0 else "?"}')
                print(f'  [DEBUG] end:   {spans[1].get_text(strip=True) if len(spans)>1 else "?"}')
                print(f'  [DEBUG] title: {title_el.get_text(strip=True)[:60] if title_el else "?"}')
                print(f'  [DEBUG] href:  {link_el.get("href","?") if link_el else "?"}')
                print()

            matched  = 0
            page_new = 0

            for card in cards:
                td = self._parse_card(card)
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

            print(f'-> {len(cards)} items, {page_new} new, {matched} relevant')
            logger.info(f'[JPMC] page {page}: {page_new} new, {matched} relevant')

            if page >= total_pages:
                print(f'       > Reached last page ({total_pages})')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant JPMC tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[JPMC] complete — total={len(results)}')
        return results

    # ── Card parser ───────────────────────────────────────────────────────

    def _parse_card(self, card) -> Optional[TenderData]:
        try:
            # Title — h3 inside ads-card-content
            title_el = card.select_one('div.ads-card-content h3')
            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                return None

            # Detail link
            link_el = card.select_one('div.ads-card-content a.hover-growing-links[href]')
            href = (link_el.get('href') or '').strip() if link_el else ''
            detail_url = (
                f'{BASE_URL}{href}' if href.startswith('/')
                else href or LIST_URL
            )

            # Dates from the two top-level spans (not inside ads-card-content)
            spans = card.find_all('span', recursive=False)
            start_str = ''
            end_str   = ''
            for sp in spans:
                txt = sp.get_text(strip=True)
                if txt.lower().startswith('starting date'):
                    start_str = txt.split(':', 1)[-1].strip()
                elif txt.lower().startswith('end date'):
                    end_str = txt.split(':', 1)[-1].strip()

            pub_date = self._parse_jpmc_date(start_str)
            deadline = self._parse_jpmc_date(end_str)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            # Extract reference number from title if present, e.g. "(T2026/22 M)"
            ref_match = re.search(r'\(?([T]\d{4}/\d+\s*[A-Z]*)\)?', title)
            ref = ref_match.group(1).strip() if ref_match else (
                href.rstrip('/').split('/')[-1][:50] if href else ''
            )

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = ref,
                reference_no        = ref,
                entity              = 'Jordan Phosphate Mines Company (JPMC)',
                entity_ar           = 'شركة مناجم الفوسفات الأردنية',
                description         = title[:300],
                category            = self.detect_category(title),
                status              = status,
                currency            = 'JOD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[JPMC] card parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_jpmc_date(val: str) -> Optional[date]:
        """Parse JPMC date format: DD-MM-YYYY."""
        val = val.strip()
        if not val:
            return None
        try:
            return datetime.strptime(val, '%d-%m-%Y').date()
        except ValueError:
            return None
