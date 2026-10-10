# tenders/scrapers/gam_tenders.py
"""
GAM — Greater Amman Municipality (أمانة عمّان الكبرى) — Tenders
URL: https://www.gamtenders.gov.jo/adlocaltenders.aspx

What is this?
-------------
The Greater Amman Municipality (GAM) publishes local tenders (مناقصات محلية)
on its dedicated tenders portal at gamtenders.gov.jo. These cover infrastructure,
supply, services, and construction works across Amman city.

Technology: ASP.NET WebForms with UpdatePanel. The tender table is
server-rendered in the page HTML — no JavaScript rendering needed.
Pagination requires POSTing back with __VIEWSTATE + __EVENTTARGET.

HTML structure (confirmed from live page):
    Table: #GVtenders (table.txtwhite)
    Headers: رقم المناقصة | المناقصة | كفالة الدخول | ثمن نسخة الدخول | التفاصيل
    tbody tr:
        td[0]: reference number (e.g. "1002/2/2026", "49/1/2026", "27/1/2026")
        td[1]: Arabic title
        td[2]: bond/security (e.g. "3%", "42000", "2000")
        td[3]: copy price in JOD (e.g. "250", "125", "0")
        td[4]: "التفاصيل..." link (doPostBack — not a real URL)

    Pagination row: tr.pagination-ys
        <a href="javascript:__doPostBack('GVtenders','Page$2')">2</a>

Sidebar widget: .widget .post-content .post-info
    h5.widget-post-title a → title (matches table row by position)
    span.post-date → "آخر موعد لبيع النسخ: 22/6/2026"
                      (Last date to buy copies = document purchase deadline)

Pagination: ASP.NET ViewState postback
    __EVENTTARGET = GVtenders
    __EVENTARGUMENT = Page$N (N = page number, 1-indexed)
    Must carry __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION

Reference number format: "رقم/نوع/سنة"
    e.g. "1002/2/2026" → tender 1002, type 2, year 2026
    e.g. "49/1/2026"   → tender 49, type 1 (works), year 2026

Bond/security interpretation:
    "3%"   → percentage of contract value
    "42000" → fixed JOD amount → used as estimated_value proxy

Detail URL: No direct URL available (ASP.NET postback only).
    We use the list page URL as source_url since there's no stable detail URL.

Note: Arabic-only interface. All titles are in Arabic.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.gamtenders.gov.jo'
LIST_URL = f'{BASE_URL}/adlocaltenders.aspx'
MAX_PAGES = 10   # Safety cap; typically 2-3 pages

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'ar,en;q=0.9,ar-JO;q=0.8',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
}


class GAMTendersScraper(BaseScraper):
    """
    Scraper for GAM (Greater Amman Municipality) local tenders.

    Parses the server-rendered HTML table #GVtenders.
    Pagination uses ASP.NET ViewState POST.
    Deadline dates are extracted from the sidebar widget (.post-date).
    """

    country_code = 'JO'
    source_name  = 'GAM — Greater Amman Municipality'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [GAMTenders] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='GAMTendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [GAMTenders] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='GAMTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : GAMTendersScraper')
        print(f'  MODE    : HTML scraping (ASP.NET ViewState pagination)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_refs: set[str]        = set()
        page       = 1
        viewstate  = ''
        vsg        = ''
        evval      = ''

        while page <= MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                if page == 1:
                    resp = self.session.get(LIST_URL, timeout=self.timeout, verify=False)
                else:
                    # ASP.NET ViewState pagination POST
                    post_data = {
                        '__LASTFOCUS':        '',
                        '__EVENTTARGET':      'GVtenders',
                        '__EVENTARGUMENT':    f'Page${page}',
                        '__VIEWSTATE':        viewstate,
                        '__VIEWSTATEGENERATOR': vsg,
                        '__EVENTVALIDATION':  evval,
                        'txtname':            '',
                        'switch':             'rdbbyname',
                    }
                    resp = self.session.post(
                        LIST_URL, data=post_data, timeout=self.timeout, verify=False
                    )
                resp.raise_for_status()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[GAMTenders] WAF block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[GAMTenders] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Extract ViewState for next page POST
            vs_el  = soup.find('input', {'id': '__VIEWSTATE'})
            vsg_el = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            ev_el  = soup.find('input', {'id': '__EVENTVALIDATION'})
            if vs_el:  viewstate = vs_el.get('value', '')
            if vsg_el: vsg       = vsg_el.get('value', '')
            if ev_el:  evval     = ev_el.get('value', '')

            # Extract sidebar dates (indexed to match table rows)
            sidebar_dates = self._extract_sidebar_dates(soup)

            # Parse main table
            table = soup.find('table', {'id': 'GVtenders'})
            if not table:
                print('(table #GVtenders not found — stopping)')
                break

            rows = table.find_all('tr')
            data_rows = [
                r for r in rows
                if r.find('td') and
                   'pagination-ys' not in r.get('class', []) and
                   r.find('th') is None
            ]

            if not data_rows:
                print('(no data rows — stopping)')
                break

            # Debug on page 1
            if page == 1 and data_rows:
                cells = data_rows[0].find_all('td')
                print(f'\n  [DEBUG] ref:   {cells[0].get_text(strip=True)[:25] if cells else "?"}')
                print(f'  [DEBUG] title: {cells[1].get_text(strip=True)[:60] if len(cells)>1 else "?"}')
                print(f'  [DEBUG] bond:  {cells[2].get_text(strip=True) if len(cells)>2 else "?"}')
                print(f'  [DEBUG] price: {cells[3].get_text(strip=True) if len(cells)>3 else "?"}')
                if sidebar_dates:
                    print(f'  [DEBUG] deadline[0]: {sidebar_dates[0]}')
                print()

            matched  = 0
            page_new = 0
            for idx, row in enumerate(data_rows):
                cells = row.find_all('td')
                if len(cells) < 4:
                    continue

                ref_no = ' '.join(cells[0].get_text(strip=True).split())
                title  = ' '.join(cells[1].get_text(strip=True).split())
                bond   = cells[2].get_text(strip=True)
                # price  = cells[3].get_text(strip=True)  # copy price — not needed

                if not title:
                    continue

                ref_key = ref_no.lower()
                if ref_key in seen_refs:
                    continue
                seen_refs.add(ref_key)
                page_new += 1

                # Deadline from sidebar (indexed by position on this page)
                deadline = sidebar_dates[idx] if idx < len(sidebar_dates) else None

                # Estimated value from bond if it's a numeric JOD amount
                est_value = self._parse_bond_value(bond)

                today  = date.today()
                if deadline and deadline < today:
                    status        = 'CLOSED'
                    save_deadline = None
                else:
                    status        = 'OPEN'
                    save_deadline = deadline

                # Source URL — no stable detail URL available
                source_url = LIST_URL

                # Publication date — derive from ref year if possible
                pub_date = self._pub_date_from_ref(ref_no)

                combined = f'{title} {ref_no}'

                if self.detect_relevance(combined):
                    results.append(TenderData(
                        title               = title,
                        title_ar            = title,
                        source_url          = source_url,
                        external_id         = ref_no,
                        reference_no        = ref_no,
                        entity              = 'أمانة عمّان الكبرى (Greater Amman Municipality)',
                        entity_ar           = 'أمانة عمّان الكبرى',
                        description         = f'كفالة الدخول: {bond}',
                        category            = self.detect_category(combined),
                        status              = status,
                        currency            = 'JOD',
                        estimated_value     = est_value,
                        publication_date    = pub_date,
                        submission_deadline = save_deadline,
                        opening_date        = None,
                    ))
                    matched += 1

            print(f'-> {len(data_rows)} rows, {page_new} new, {matched} relevant')
            logger.info(
                f'[GAMTenders] page {page}: '
                f'{len(data_rows)} rows, {page_new} new, {matched} relevant'
            )

            # Check if next page exists
            max_page = self._get_max_page(soup)
            if page >= max_page:
                print(f'       > Last page ({page}/{max_page})')
                break
            if page_new == 0:
                print(f'       > No new rows — stopping')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant GAM tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[GAMTenders] complete — total={len(results)}')
        return results

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_sidebar_dates(soup) -> list[Optional[date]]:
        """
        Extract deadline dates from sidebar .post-date spans.
        Format: "آخر موعد لبيع النسخ:  22/6/2026"
        Returns list indexed to match table rows on this page.
        """
        dates = []
        for span in soup.select('.post-content .post-date'):
            text = span.get_text(strip=True)
            m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
            if m:
                try:
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    dates.append(d)
                except ValueError:
                    dates.append(None)
            else:
                dates.append(None)
        return dates

    @staticmethod
    def _get_max_page(soup) -> int:
        """Find the maximum page number from pagination row."""
        pg_row = soup.find('tr', class_='pagination-ys')
        if not pg_row:
            return 1
        pages = []
        # Current page (span) + other pages (a tags)
        for span in pg_row.find_all('span'):
            try:
                pages.append(int(span.get_text(strip=True)))
            except ValueError:
                pass
        for a in pg_row.find_all('a'):
            try:
                pages.append(int(a.get_text(strip=True)))
            except ValueError:
                pass
        return max(pages) if pages else 1

    @staticmethod
    def _parse_bond_value(bond: str) -> Optional[float]:
        """
        Extract numeric estimated value from bond field.
        "3%" → None (percentage, not a fixed amount)
        "42000" → 42000.0  (JOD fixed bond → proxy for contract value)
        "2000" → 2000.0
        """
        bond = bond.strip()
        if not bond or '%' in bond or bond == '0':
            return None
        try:
            val = float(re.sub(r'[,\s]', '', bond))
            return val if val > 0 else None
        except ValueError:
            return None

    @staticmethod
    def _pub_date_from_ref(ref_no: str) -> Optional[date]:
        """
        Try to derive a publication year from reference like "1002/2/2026".
        Returns January 1st of the year as approximate pub date.
        """
        m = re.search(r'/(\d{4})$', ref_no.strip())
        if m:
            try:
                return date(int(m.group(1)), 1, 1)
            except ValueError:
                pass
        return None
