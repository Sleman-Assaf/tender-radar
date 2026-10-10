# tenders/scrapers/mola_tenders.py
"""
MOLA — Jordan Ministry of Local Administration — Tenders
URL: https://www.mola.gov.jo/Ar/Modules/Tenders

What is this?
-------------
MOLA (وزارة الإدارة المحلية) is Jordan's Ministry of Local Administration,
overseeing municipal infrastructure, solid waste management, local governance
projects, and donor-funded programs (often EU/AFD/World Bank co-financed).

Highly relevant for AEC consultancies — covers engineering supervision for
transfer stations, solid waste management facilities, municipal infrastructure,
and training/consultancy services tied to municipal development projects.

Technology: ASP.NET WebForms (UpdatePanel/AJAX), Arabic RTL.
Note: Page includes a TSPD bot-protection script
(window["loaderConfig"] = "/TSPD/?type=21") — likely F5/Imperva. If blocked,
may require browser-realistic headers or the corporate proxy.

Confirmed HTML structure (from live page, doc 11):
    List: a[href^='/Ar/TendersDetails/'] (each tender wraps in an <a>)
        div.media.news-block:
            h5.card-title > span[id$='lblTitle'] → title
            p.date > span[id$='lblTender_PublishDate'] → DD/MM/YYYY (publish)
                     span[id$='lblTenderDash'] → "-"
                     span[id$='lblTender_ExpiredhDate'] → DD/MM/YYYY (deadline, often empty)
            p.card-text > span[id$='lblBrif'] → description/brief

Detail URL: https://www.mola.gov.jo/Ar/TendersDetails/{slug}
    slug = Arabic title with spaces replaced by underscores (URL-encoded)

Pagination: ASP.NET DataPager via __doPostBack (POST with VIEWSTATE)
    Page label: "صفحة N من M" (Page N of M)
    Numeric page links:
        javascript:__doPostBack('ctl00$ctl00$MainContent$ContentDetails$TendersDataPage$ctl01$ctlNN','')
    where ctl01 = page button index (ctl01=page2, ctl02=page3, etc.)
    4 pages confirmed in snapshot (10 tenders/page = ~40 total)

Category filter (not used by default — scrapes all):
    select#ddlTendersCategory
        value=3: محلي ودولي (local + international)
        value=4: محلي (local only)

Date format: DD/MM/YYYY (Jordanian/Arabic locale)
Deadline empty → tender has no fixed close date (treated as OPEN)
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.mola.gov.jo'
LIST_URL  = f'{BASE_URL}/Ar/Modules/Tenders'
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


class MOLATendersScraper(BaseScraper):
    """
    Scraper for MOLA (Jordan Ministry of Local Administration) tenders.

    Page 1: GET. Pages 2+: POST with ASP.NET VIEWSTATE + DataPager button.
    Arabic content — title_ar is the primary field; title mirrors it
    (no separate English version available on this portal).
    """

    country_code = 'JO'
    source_name  = 'MOLA — Jordan Ministry of Local Administration'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [MOLA] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='MOLATendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [MOLA] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='MOLATendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : MOLATendersScraper')
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
                    post_data = self._build_post(viewstate, vsg, ev, page_btn, page)
                    resp = self.session.post(
                        LIST_URL, data=post_data, timeout=self.timeout
                    )
                resp.raise_for_status()
                resp.encoding = 'utf-8'
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — possible TSPD bot protection)')
                    logger.warning(f'[MOLA] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[MOLA] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Refresh ASP.NET state for next POST
            vs   = soup.find('input', {'id': '__VIEWSTATE'})
            vsg_el = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            ev_el  = soup.find('input', {'id': '__EVENTVALIDATION'})
            if vs:     viewstate = vs.get('value', '')
            if vsg_el: vsg       = vsg_el.get('value', '')
            if ev_el:  ev        = ev_el.get('value', '')

            # Find next page postback target
            page_btn = self._find_next_page_target(soup, page + 1)

            # Parse tender items
            items = soup.select("a[href^='/Ar/TendersDetails/']")
            if not items:
                print('(no items — stopping)')
                if page == 1:
                    print(f'  [DEBUG] Possible bot block. Snippet: {repr(resp.text[:300])}')
                break

            if page == 1:
                t0 = items[0]
                title_el = t0.select_one("span[id$='lblTitle']")
                pub_el   = t0.select_one("span[id$='lblTender_PublishDate']")
                exp_el   = t0.select_one("span[id$='lblTender_ExpiredhDate']")
                print(f'\n  [DEBUG] {len(items)} items on page 1')
                print(f'  [DEBUG] title:  {title_el.get_text(strip=True)[:50] if title_el else "?"}')
                print(f'  [DEBUG] pub:    {pub_el.get_text(strip=True) if pub_el else "?"}')
                print(f'  [DEBUG] exp:    {exp_el.get_text(strip=True) if exp_el else "(empty)"}')
                print()

            matched  = 0
            page_new = 0

            for a in items:
                td = self._parse_item(a)
                if not td:
                    continue
                if td.source_url in seen_urls:
                    continue
                seen_urls.add(td.source_url)
                page_new += 1
                combined = f'{td.title_ar} {td.description}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(items)} items, {page_new} new, {matched} relevant')
            logger.info(f'[MOLA] page {page}: {page_new} new, {matched} relevant')

            if not page_btn:
                print(f'       > No next page — done')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant MOLA tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[MOLA] complete — total={len(results)}')
        return results

    # ── POST builder ──────────────────────────────────────────────────────

    @staticmethod
    def _build_post(viewstate: str, vsg: str, ev: str, target: str, page: int) -> dict:
        """Build ASP.NET POST payload for DataPager page navigation."""
        return {
            '__EVENTTARGET':         target,
            '__EVENTARGUMENT':       '',
            '__LASTFOCUS':           '',
            '__VIEWSTATE':           viewstate,
            '__VIEWSTATEGENERATOR':  vsg,
            '__EVENTVALIDATION':     ev,
            'ctl00$ctl00$txtSearch': '',
            'ctl00$ctl00$txtSearch1': '',
            'ctl00$ctl00$MainContent$ContentDetails$txtFilterName_Tender': '',
            'ctl00$ctl00$MainContent$ContentDetails$txtFilterFrom_Tender': '',
            'ctl00$ctl00$MainContent$ContentDetails$txtFilterTo_Tenders':  '',
            'ctl00$ctl00$MainContent$ContentDetails$ddlTendersCategory':   '0',
            'ctl00$ctl00$MainContent$hf1': '0',
        }

    # ── Item parser ───────────────────────────────────────────────────────

    def _parse_item(self, a) -> Optional[TenderData]:
        try:
            href = (a.get('href') or '').strip()
            if not href:
                return None
            detail_url = f'{BASE_URL}{href}' if href.startswith('/') else href

            title_el = a.select_one("span[id$='lblTitle']")
            title = title_el.get_text(strip=True) if title_el else ''
            if not title:
                return None

            pub_el = a.select_one("span[id$='lblTender_PublishDate']")
            exp_el = a.select_one("span[id$='lblTender_ExpiredhDate']")
            brief_el = a.select_one("span[id$='lblBrif']")

            pub_str = pub_el.get_text(strip=True) if pub_el else ''
            exp_str = exp_el.get_text(strip=True) if exp_el else ''
            brief   = brief_el.get_text(strip=True) if brief_el else ''

            pub_date = self._parse_mola_date(pub_str)
            deadline = self._parse_mola_date(exp_str)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                # No expiry date listed → treat as currently open
                status        = 'OPEN'
                save_deadline = deadline

            # Extract a reference id from the slug (last path segment)
            ref = href.rstrip('/').split('/')[-1][:50]

            return TenderData(
                title               = title,   # Arabic is primary content here
                title_ar            = title,
                source_url          = detail_url,
                external_id         = ref,
                reference_no        = ref,
                entity              = 'Ministry of Local Administration (MOLA)',
                entity_ar           = 'وزارة الإدارة المحلية',
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
            logger.warning(f'[MOLA] item parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_mola_date(val: str) -> Optional[date]:
        """Parse DD/MM/YYYY date format used by MOLA."""
        val = val.strip()
        if not val:
            return None
        try:
            return datetime.strptime(val, '%d/%m/%Y').date()
        except ValueError:
            return None

    @staticmethod
    def _find_next_page_target(soup, next_page: int) -> str:
        """
        Find the __EVENTTARGET value for the given next_page number
        from the DataPager numeric links.
        Confirmed pattern:
            javascript:__doPostBack('ctl00$ctl00$MainContent$ContentDetails$TendersDataPage$ctl01$ctlNN','')
        The link text equals the page number.
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
