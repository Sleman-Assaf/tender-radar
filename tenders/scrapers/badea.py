# tenders/scrapers/badea.py
"""
BADEA — Arab Bank for Economic Development in Africa
Procurement Notices: https://www.badea.org/procurement-notices-2/

What is this?
-------------
BADEA (البنك العربي للتنمية الاقتصادية في أفريقيا) finances infrastructure and
development projects across Sub-Saharan Africa. It publishes procurement notices
for consulting, construction, and supply contracts financed by its loans.

Technology stack:
    WordPress 6.9 + Elementor Pro + JetEngine Loop Grid + JetSmartFilters
    Custom post type: "files"
    Taxonomy: "categories", term_id=55 (procurement-notices-en)

From the JetSmartFilters JS config embedded in the page:
    found_posts: 29, max_num_pages: 5 (6 items/page approx)

Pagination (static WordPress pagination — confirmed from anchor element):
    Page 1: https://www.badea.org/procurement-notices-2/
    Page 2: https://www.badea.org/procurement-notices-2/2/
    Page N: https://www.badea.org/procurement-notices-2/N/

HTML structure (each <div class="e-loop-item">):
    h1.elementor-heading-title              → title
    .elementor-element-8f2543d span         → start date value (YYYY-MM-DD, optional)
    .elementor-element-d66cf2f span         → end date / deadline (YYYY-MM-DD)
    a.elementor-button[download][href]      → PDF download URL (source_url)

The detail page is the PDF itself (no separate HTML detail page).
We use the PDF URL as the source_url and the post URL (if found) as backup.

Country coverage: Multi-country Africa (Zambia, Ivory Coast, Guinea-Bissau,
                  Mozambique, Mauritius, etc.)
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL      = 'https://www.badea.org'
PAGE1_URL     = f'{BASE_URL}/procurement-notices-2/'
PAGE_N_URL    = f'{BASE_URL}/procurement-notices-2/{{n}}/'
MAX_PAGES     = 20   # safety cap

# Elementor element IDs for date fields (confirmed from page HTML)
# These are stable as long as the Elementor template isn't rebuilt
START_DATE_EL = 'elementor-element-8f2543d'   # "Start Date" value
END_DATE_EL   = 'elementor-element-d66cf2f'   # "End Date" / deadline value

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Referer': BASE_URL,
}


class BADEAScraper(BaseScraper):
    """
    Scraper for BADEA (Arab Bank for Economic Development in Africa)
    Procurement Notices.

    Parses WordPress + JetEngine loop grid HTML pages.
    Pagination via /procurement-notices-2/N/ (1-indexed, page 1 is root URL).
    """

    country_code = 'MULTI'   # covers multiple African countries
    source_name  = 'BADEA'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [BADEA] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='BADEAScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [BADEA] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='BADEAScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : BADEAScraper')
        print(f'  MODE    : HTML scraping (WP + JetEngine)')
        print(f'  BASE    : {PAGE1_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:    list[TenderData] = []
        page        = 1
        total_pages = None

        while True:
            url = PAGE1_URL if page == 1 else PAGE_N_URL.format(n=page)
            print(f'       > page {page:>3}  {url}', end='  ', flush=True)

            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[BADEA] WAF block page {page}: {exc}')
                elif '404' in err:
                    print('(404 — end of pages)')
                    logger.info(f'[BADEA] 404 page {page} — stopping')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[BADEA] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.select('div.e-loop-item')

            # Extract max pages from anchor on first page
            if total_pages is None:
                anchor = soup.select_one('.e-load-more-anchor[data-max-page]')
                if anchor:
                    try:
                        total_pages = min(
                            int(anchor['data-max-page']),
                            MAX_PAGES,
                        )
                        print(f'(max_pages={total_pages})', end='  ')
                    except (ValueError, KeyError):
                        pass

            if not items:
                print('(empty — stopping)')
                break

            # Debug on page 1
            if page == 1 and items:
                item0 = items[0]
                h1 = item0.select_one('h1.elementor-heading-title')
                dl = item0.select_one('a.elementor-button[download]')
                print(f'\n  [DEBUG] title     : {h1.get_text(strip=True)[:70] if h1 else "?"}')
                print(f'  [DEBUG] pdf_url   : {dl["href"][:80] if dl else "none"}')
                # Show all heading spans to verify date element IDs
                for cls in [START_DATE_EL, END_DATE_EL]:
                    el = item0.select_one(f'.{cls} span')
                    print(f'  [DEBUG] {cls[:25]}: {el.get_text(strip=True) if el else "(not in item0 — check item1)"}')
                print()

            matched = 0
            for item in items:
                td = self._parse_item(item)
                if td:
                    combined = f'{td.title} {td.description}'
                    if self.detect_relevance(combined):
                        results.append(td)
                        matched += 1

            print(f'-> {len(items)} items, {matched} relevant')
            logger.info(f'[BADEA] page {page}: {len(items)} items, {matched} relevant')

            if total_pages and page >= total_pages:
                print(f'       > Reached last page ({page})')
                break
            if page >= MAX_PAGES:
                print(f'       > Hit MAX_PAGES cap')
                break
            if len(items) < 3:
                # Very thin page = end of data
                print(f'       > Thin page ({len(items)}) — end of data')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant BADEA tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[BADEA] complete — total={len(results)}')
        return results

    # ── HTML item parser ──────────────────────────────────────────────────────

    def _parse_item(self, item) -> Optional[TenderData]:
        try:
            # ── Title ─────────────────────────────────────────────────────
            h1 = item.select_one('h1.elementor-heading-title')
            title = h1.get_text(strip=True) if h1 else ''
            if not title:
                return None

            # ── Reference number from title (e.g. CWA/C2022/122) ─────────
            ref_match = re.search(
                r'\b([A-Z]{2,6}/[A-Z]\d{4}/\d{2,6})\b', title
            )
            reference_no = ref_match.group(1) if ref_match else ''

            # ── Dates (Elementor element ID based) ───────────────────────
            start_el = item.select_one(f'.{START_DATE_EL} span.elementor-heading-title')
            end_el   = item.select_one(f'.{END_DATE_EL}   span.elementor-heading-title')

            start_date_str = start_el.get_text(strip=True) if start_el else ''
            end_date_str   = end_el.get_text(strip=True)   if end_el   else ''

            pub_date  = self._parse_iso_date(start_date_str)
            deadline  = self._parse_iso_date(end_date_str)

            # ── PDF / source URL ─────────────────────────────────────────
            dl_btn = item.select_one('a.elementor-button[download]')
            if dl_btn:
                pdf_href = dl_btn.get('href', '')
                source_url = pdf_href if pdf_href else PAGE1_URL
            else:
                source_url = PAGE1_URL

            # External ID from WP post class (e-loop-item-NNNNN)
            classes    = item.get('class', [])
            ext_id     = ''
            for cls in classes:
                m = re.match(r'e-loop-item-(\d+)', cls)
                if m:
                    ext_id = m.group(1)
                    break

            # ── Status ────────────────────────────────────────────────────
            today = date.today()
            if deadline and deadline < today:
                status = 'CLOSED'
            else:
                status = 'OPEN'

            # ── Description ───────────────────────────────────────────────
            desc_parts = [f'Deadline: {end_date_str}' if end_date_str else '']
            if start_date_str:
                desc_parts.insert(0, f'Start: {start_date_str}')
            description = ' | '.join(filter(None, desc_parts))

            return TenderData(
                title               = title,
                title_ar            = title,   # English only on site
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = reference_no,
                entity              = 'BADEA — Arab Bank for Economic Development in Africa',
                entity_ar           = 'البنك العربي للتنمية الاقتصادية في أفريقيا',
                description         = description,
                category            = self.detect_category(title),
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[BADEA] parse error: {exc}')
            return None

    # ── Date parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_iso_date(val: str) -> Optional[date]:
        """Parse YYYY-MM-DD date strings used by the site."""
        if not val:
            return None
        val = val.strip()
        # Primary format seen in page: 2026-05-13, 2026-06-26
        try:
            return datetime.strptime(val[:10], '%Y-%m-%d').date()
        except ValueError:
            pass
        # Fallback: DD/MM/YYYY
        try:
            return datetime.strptime(val, '%d/%m/%Y').date()
        except ValueError:
            pass
        return None
