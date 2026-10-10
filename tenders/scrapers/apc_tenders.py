# tenders/scrapers/apc_tenders.py
"""
APC — Arab Potash Company — Active Tenders
URL: https://www.arabpotash.com/En/Modules/Tenders

What is this?
-------------
APC (شركة البوتاس العربية) is one of Jordan's largest industrial companies,
mining potash from the Dead Sea and operating major chemical/mineral
processing plants (Ghor Al-Safi, Numira). Listed on Amman Stock Exchange.

EXTREMELY relevant for AEC consultancies — tenders include EPC contracts
for bridges, warehouses, plant construction (CCP3), gas pipeline
re-alignment, consultancy/supervision services, and civil/structural works.

Technology: Same ASP.NET "Echo Technology" platform family as MOLA/MEMR,
but a RICHER card template with bid bond values, copy prices, and vendor
qualification requirements — useful extra fields for filtering.

Confirmed HTML structure (from live page):
    Card: a[href^='/En/TendersDetails?PID='] (whole card wrapped in <a>)
        h5.card-title > span[id$='lblTitle'] → title (often English, IFB No. format)
        span "Purchase Date:" + span[id$='lblTender_PublishDate'] → DD/MM/YYYY
        span "Closing date:" + span[id$='lblTender_ExpiredhDate'] → DD/MM/YYYY
        span "Copy Price:" + span[id$='Label9'] → "JOD 1,160.00"
        span "Bid Bond Value:" + span[id$='Label10'] → "JOD 195,000.00"
        span "Vendor Qualifications:" + span[id$='Label11'] → Arabic eligibility text
        span "Remarks:" + span[id$='Label12'] → optional notes
        p.card-text > span[id$='lblBrif'] → brief (often same as title)

Detail URL: https://www.arabpotash.com/En/TendersDetails?PID={id}

Pagination: ASP.NET DataPager via __doPostBack (same pattern as MOLA/MEMR)
    "Page N of M" — CONFIRMED 47 pages in this snapshot (huge backlog)
    Numeric links: ctl00$ctl00$MainContent$ContentDetails$TendersDataPage$ctl01$ctlNN
    "..." ellipsis link present after page 6 for jumping further

Date format: DD/MM/YYYY

Sample tenders (confirmed, all highly relevant):
    IFB No. 202601839/AN - Southern Expansion Projects - Package 6 - Bridge No. 2
        Bid Bond: JOD 195,000 | Vendor Quals: 1st-class local bridge contractors
    IFB No. 202601732/GS - Construction of Potash Storage Warehouses (Red/White)
        for new CCP3 Plant | Bid Bond: JOD 605,000
    IFB No. 202601651/MM - Consultancy Services for CCP3 Warehouses Supervision
        Vendor Quals: Grade A local building consultants
    IFB No. 202601214/GS - Southern Expansion - Package 4: Gas Pipeline Re-alignment
        Bid Bond: JOD 330,000 | Vendor Quals: International EPC Contractors
    IFB No. 202601218/MM - Slab on Grade for all APC Plants - Phase 3
    IFB No. 202600786/GS - اتفاقية خدمات هندسية للإشراف على مشاريع الطاقة المتجددة
        (Engineering services for renewable energy project supervision)

With 47 pages of history, MAX_PAGES is capped to scan only the most
recent pages — increase if a full backfill is ever needed.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.arabpotash.com'
LIST_URL  = f'{BASE_URL}/En/Modules/Tenders'
MAX_PAGES = 10   # 47 pages exist total; scan latest 10 (~100 tenders) per run

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Origin':          BASE_URL,
    'Referer':         LIST_URL,
}


class APCTendersScraper(BaseScraper):
    """
    Scraper for APC (Arab Potash Company) tenders.

    Page 1: GET. Pages 2+: POST with ASP.NET VIEWSTATE + DataPager button.
    Richer card data than MOLA/MEMR: captures bid bond value, copy price,
    and vendor qualification requirements as extra description context.
    """

    country_code = 'JO'
    source_name  = 'APC — Arab Potash Company'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [APC] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='APCTendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [APC] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='APCTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : APCTendersScraper')
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
        total_pages = None

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
                    logger.warning(f'[APC] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[APC] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Refresh ASP.NET state for next POST
            vs     = soup.find('input', {'id': '__VIEWSTATE'})
            vsg_el = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            ev_el  = soup.find('input', {'id': '__EVENTVALIDATION'})
            if vs:     viewstate = vs.get('value', '')
            if vsg_el: vsg       = vsg_el.get('value', '')
            if ev_el:  ev        = ev_el.get('value', '')

            if total_pages is None:
                m = re.search(r'Page\s+\d+\s+of\s+(\d+)', soup.get_text())
                total_pages = int(m.group(1)) if m else 1
                print(f'(total_pages={total_pages}, scanning≤{min(total_pages, MAX_PAGES)})', end='  ')

            # Find next page postback target
            page_btn = self._find_next_page_target(soup, page + 1)

            # Parse tender cards
            cards = soup.select("a[href^='/En/TendersDetails?PID=']")
            if not cards:
                print('(no items — stopping)')
                if page == 1:
                    print(f'  [DEBUG] Snippet: {repr(resp.text[:300])}')
                break

            if page == 1:
                c0 = cards[0]
                title_el = c0.select_one("span[id$='lblTitle']")
                pub_el   = c0.select_one("span[id$='lblTender_PublishDate']")
                exp_el   = c0.select_one("span[id$='lblTender_ExpiredhDate']")
                bond_el  = c0.select_one("span[id$='Label10']")
                print(f'\n  [DEBUG] {len(cards)} items on page 1')
                print(f'  [DEBUG] title: {title_el.get_text(strip=True)[:60] if title_el else "?"}')
                print(f'  [DEBUG] pub:   {pub_el.get_text(strip=True) if pub_el else "?"}')
                print(f'  [DEBUG] exp:   {exp_el.get_text(strip=True) if exp_el else "?"}')
                print(f'  [DEBUG] bond:  {bond_el.get_text(strip=True) if bond_el else "?"}')
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
            logger.info(f'[APC] page {page}: {page_new} new, {matched} relevant')

            if not page_btn:
                print(f'       > No next page button — done')
                break
            if page >= min(total_pages, MAX_PAGES):
                print(f'       > Reached scan limit')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant APC tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[APC] complete — total={len(results)}')
        return results

    # ── POST builder ──────────────────────────────────────────────────────

    @staticmethod
    def _build_post(viewstate: str, vsg: str, ev: str, target: str) -> dict:
        """Build ASP.NET POST payload for DataPager page navigation."""
        return {
            '__EVENTTARGET':         target,
            '__EVENTARGUMENT':       '',
            '__VIEWSTATE':           viewstate,
            '__VIEWSTATEGENERATOR':  vsg,
            '__EVENTVALIDATION':     ev,
            'ctl00$ctl00$txtSearch': '',
            'ctl00$ctl00$MainContent$ContentDetails$txtFilterName_Tender': '',
            'ctl00$ctl00$MainContent$ContentDetails$txtFilterFrom_Tender': '',
            'ctl00$ctl00$MainContent$ContentDetails$txtFilterTo_Tenders':  '',
        }

    # ── Card parser ───────────────────────────────────────────────────────

    def _parse_card(self, card) -> Optional[TenderData]:
        try:
            href = (card.get('href') or '').strip()
            if not href:
                return None
            detail_url = f'{BASE_URL}{href}' if href.startswith('/') else href

            title_el = card.select_one("span[id$='lblTitle']")
            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                return None

            pub_el   = card.select_one("span[id$='lblTender_PublishDate']")
            exp_el   = card.select_one("span[id$='lblTender_ExpiredhDate']")
            price_el = card.select_one("span[id$='Label9']")   # Copy Price
            bond_el  = card.select_one("span[id$='Label10']")  # Bid Bond Value
            qual_el  = card.select_one("span[id$='Label11']")  # Vendor Qualifications
            brief_el = card.select_one("span[id$='lblBrif']")

            pub_str  = pub_el.get_text(strip=True) if pub_el else ''
            exp_str  = exp_el.get_text(strip=True) if exp_el else ''
            price    = price_el.get_text(strip=True) if price_el else ''
            bond     = bond_el.get_text(strip=True) if bond_el else ''
            qual     = qual_el.get_text(strip=True) if qual_el else ''
            brief    = brief_el.get_text(strip=True) if brief_el else ''

            pub_date = self._parse_apc_date(pub_str)
            deadline = self._parse_apc_date(exp_str)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            est_value = self._parse_bond_value(bond)

            # Reference from "IFB No: 202601347/EB" style prefix in title
            ref_match = re.search(r'(?:IFB|RFP|RFQ)\.?\s*No\.?:?\s*([\d/A-Z]+)', title, re.IGNORECASE)
            ref = ref_match.group(1).strip() if ref_match else (
                re.search(r'PID=(\d+)', href).group(1) if 'PID=' in href else ''
            )

            description_parts = [brief or title]
            if qual:
                description_parts.append(f'Vendor Qualifications: {qual}')
            description = ' | '.join(description_parts)

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = ref,
                reference_no        = ref,
                entity              = 'Arab Potash Company (APC)',
                entity_ar           = 'شركة البوتاس العربية',
                description         = description[:500],
                category            = self.detect_category(f'{title} {qual}'),
                status              = status,
                currency            = 'JOD',
                estimated_value     = est_value,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[APC] card parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_apc_date(val: str) -> Optional[date]:
        """Parse DD/MM/YYYY date format used by APC."""
        val = val.strip()
        if not val:
            return None
        try:
            return datetime.strptime(val, '%d/%m/%Y').date()
        except ValueError:
            return None

    @staticmethod
    def _parse_bond_value(val: str) -> Optional[float]:
        """Parse 'JOD 195,000.00' → 195000.0. Used as estimated_value proxy."""
        m = re.search(r'[\d,]+(?:\.\d+)?', val)
        if m:
            try:
                v = float(m.group().replace(',', ''))
                return v if v > 0 else None
            except ValueError:
                pass
        return None

    @staticmethod
    def _find_next_page_target(soup, next_page: int) -> str:
        """
        Find the __EVENTTARGET for the given next_page number.
        Same pattern as MOLA/MEMR. APC has up to 47 pages with a "..." link
        for jumping forward — we only follow numbered links within MAX_PAGES.
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
