# tenders/scrapers/pdtra_tenders.py
"""
PDTRA — Petra Development and Tourism Region Authority — Tenders
URL: https://pdtra.gov.jo/AR/Modules/Tenders

What is this?
-------------
PDTRA (سلطة إقليم البترا التنموي السياحي) is the Jordanian authority
responsible for developing the Petra archaeological region, covering
tourism infrastructure, transport systems, real estate development,
and heritage site management around Petra.

Relevant for AEC consultancies — tenders cover transport system supply/
implementation, tourism infrastructure, urban planning (land organization),
and construction projects in the Petra Development and Tourism Region.

Technology: ASP.NET WebForms, newer "Echo Technology" template variant
(different from MOLA/MEMR's older template — no userway accessibility
widget conflicts, different CSS classes).

IMPORTANT — confirmed differences from MOLA/MEMR scrapers:
    1. DATE FORMAT IS YYYY/MM/DD (not DD/MM/YYYY like MOLA/MEMR!)
       Confirmed: "2026/05/12" - "2026/07/08"
    2. Title span has NO id attribute — must use h5.tendersTitle > span
    3. The whole card is NOT a clickable <a> wrapper (unlike MOLA/MEMR).
       Instead, a "اقرأ المزيد" (Read more) link is nested inside via
       div.readmore2 > a[href]
    4. Card container class: div.d-flex.flex-row.border-bottom.bdr-olive

Confirmed HTML structure (from live page):
    Card: div.d-flex.flex-row.border-bottom.bdr-olive
        div.media-body:
            p.date (class contains "date"):
                span[id$='lblTender_PublishDate'] → YYYY/MM/DD (publish)
                span[id$='lblTenderDash'] → "-"
                span[id$='lblTender_ExpiredhDate'] → YYYY/MM/DD (deadline)
            h5.card-title.tendersTitle > span → title (often English)
            p.card-text > span → Arabic description/brief
            div.readmore2 > a[href='/Ar/TendersDetails/{slug}'] → detail link

Detail URL: https://pdtra.gov.jo/Ar/TendersDetails/{slug}
    slug = English title with spaces/punctuation replaced by underscores

Sample tender (confirmed):
    "Supply, Operation, Implementation, and Maintenance of the Electric
     transport System in the Petra Development and Tourism Region"
    Arabic: توريد وتشغيل وتنفيذ وصيانة نظام النقل الكهربائي...
    Publish: 2026/05/12, Deadline: 2026/07/08

Pagination: Same ASP.NET DataPager pattern as MOLA/MEMR, but pager is
hidden (style="display:none") when only 1 page exists, with text
"صفحة N من M" shown in a <p> tag instead of <li>.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://pdtra.gov.jo'
LIST_URL  = f'{BASE_URL}/AR/Modules/Tenders'
MAX_PAGES = 10

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'ar,en-US;q=0.9,en;q=0.8',
    'Origin':          BASE_URL,
    'Referer':         LIST_URL,
}


class PDTRATendersScraper(BaseScraper):
    """
    Scraper for PDTRA (Petra Development and Tourism Region Authority) tenders.

    Page 1: GET. Pages 2+: POST with ASP.NET VIEWSTATE + DataPager button.

    CRITICAL: dates are YYYY/MM/DD format (different from MOLA/MEMR's
    DD/MM/YYYY) — do not reuse their date parser.
    """

    country_code = 'JO'
    source_name  = 'PDTRA — Petra Development and Tourism Region Authority'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [PDTRA] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='PDTRATendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [PDTRA] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='PDTRATendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : PDTRATendersScraper')
        print(f'  MODE    : HTML (ASP.NET DataPager POST pagination)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_urls: set[str]        = set()
        page       = 1
        viewstate  = ''
        vsg        = ''
        ev         = ''
        page_btn   = ''

        while page <= MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                if page == 1:
                    resp = self.session.get(LIST_URL, timeout=self.timeout)
                else:
                    post_data = self._build_post(viewstate, vsg, ev, page_btn)
                    resp = self.session.post(
                        LIST_URL, data=post_data, timeout=self.timeout
                    )
                resp.raise_for_status()
                resp.encoding = 'utf-8'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[PDTRA] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[PDTRA] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Refresh ASP.NET state for next POST
            vs     = soup.find('input', {'id': '__VIEWSTATE'})
            vsg_el = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            ev_el  = soup.find('input', {'id': '__EVENTVALIDATION'})
            if vs:     viewstate = vs.get('value', '')
            if vsg_el: vsg       = vsg_el.get('value', '')
            if ev_el:  ev        = ev_el.get('value', '')

            # Find next page postback target
            page_btn = self._find_next_page_target(soup, page + 1)

            # Parse tender cards — distinctive container class
            cards = soup.select('div.d-flex.flex-row.border-bottom.bdr-olive')
            if not cards:
                print('(no items — stopping)')
                if page == 1:
                    print(f'  [DEBUG] Snippet: {repr(resp.text[:300])}')
                break

            if page == 1:
                c0 = cards[0]
                title_el = c0.select_one('h5.tendersTitle span, h5.card-title span')
                pub_el   = c0.select_one("span[id$='lblTender_PublishDate']")
                exp_el   = c0.select_one("span[id$='lblTender_ExpiredhDate']")
                link_el  = c0.select_one('div.readmore2 a[href]')
                print(f'\n  [DEBUG] {len(cards)} items on page 1')
                print(f'  [DEBUG] title: {title_el.get_text(strip=True)[:60] if title_el else "?"}')
                print(f'  [DEBUG] pub:   {pub_el.get_text(strip=True) if pub_el else "?"}')
                print(f'  [DEBUG] exp:   {exp_el.get_text(strip=True) if exp_el else "(empty)"}')
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
            logger.info(f'[PDTRA] page {page}: {page_new} new, {matched} relevant')

            if not page_btn:
                print(f'       > No next page — done')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant PDTRA tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[PDTRA] complete — total={len(results)}')
        return results

    # ── POST builder ──────────────────────────────────────────────────────

    @staticmethod
    def _build_post(viewstate: str, vsg: str, ev: str, target: str) -> dict:
        """Build ASP.NET POST payload for DataPager page navigation."""
        return {
            '__EVENTTARGET':         target,
            '__EVENTARGUMENT':       '',
            '__LASTFOCUS':           '',
            '__VIEWSTATE':           viewstate,
            '__VIEWSTATEGENERATOR':  vsg,
            '__EVENTVALIDATION':     ev,
            'ctl00$ctl00$txtSearch':  '',
            'ctl00$ctl00$txtSearch1': '',
            'ctl00$ctl00$MainContent$hf1': '0',
        }

    # ── Card parser ───────────────────────────────────────────────────────

    def _parse_card(self, card) -> Optional[TenderData]:
        try:
            # Title — h5.tendersTitle span (NO id attribute on this template)
            title_el = card.select_one('h5.tendersTitle span, h5.card-title span')
            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                return None

            # Detail link — nested inside readmore2 div
            link_el = card.select_one('div.readmore2 a[href]')
            href = (link_el.get('href') or '').strip() if link_el else ''
            detail_url = (
                f'{BASE_URL}{href}' if href.startswith('/')
                else href or LIST_URL
            )

            # Dates — YYYY/MM/DD format (confirmed different from MOLA/MEMR)
            pub_el = card.select_one("span[id$='lblTender_PublishDate']")
            exp_el = card.select_one("span[id$='lblTender_ExpiredhDate']")
            pub_str = pub_el.get_text(strip=True) if pub_el else ''
            exp_str = exp_el.get_text(strip=True) if exp_el else ''

            pub_date = self._parse_pdtra_date(pub_str)
            deadline = self._parse_pdtra_date(exp_str)

            # Arabic description/brief
            brief_el = card.select_one('p.card-text span')
            brief = brief_el.get_text(strip=True) if brief_el else ''

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            ref = href.rstrip('/').split('/')[-1][:60] if href else ''

            return TenderData(
                title               = title,
                title_ar            = brief or title,
                source_url          = detail_url,
                external_id         = ref,
                reference_no        = ref,
                entity              = 'Petra Development and Tourism Region Authority (PDTRA)',
                entity_ar           = 'سلطة إقليم البترا التنموي السياحي',
                description         = brief[:300] if brief else title[:300],
                category            = self.detect_category(f'{title} {brief}'),
                status              = status,
                currency            = 'JOD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[PDTRA] card parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_pdtra_date(val: str) -> Optional[date]:
        """
        Parse PDTRA date format: YYYY/MM/DD
        CONFIRMED DIFFERENT from MOLA/MEMR's DD/MM/YYYY format!
        Example: "2026/05/12" → May 12, 2026
        """
        val = val.strip()
        if not val:
            return None
        try:
            return datetime.strptime(val, '%Y/%m/%d').date()
        except ValueError:
            # Fallback to DD/MM/YYYY in case format varies by record
            try:
                return datetime.strptime(val, '%d/%m/%Y').date()
            except ValueError:
                return None

    @staticmethod
    def _find_next_page_target(soup, next_page: int) -> str:
        """
        Find the __EVENTTARGET value for the given next_page number
        from the DataPager numeric links. Same __doPostBack pattern
        as MOLA/MEMR scrapers.
        """
        pager = soup.find('span', id=re.compile(r'TendersDataPage', re.IGNORECASE))
        if not pager:
            return ''
        for a in pager.find_all('a', class_='NumericClass'):
            if a.get_text(strip=True) == str(next_page):
                m = re.search(r"__doPostBack\('([^']+)'", a.get('href', ''))
                if m:
                    return m.group(1)
        return ''
