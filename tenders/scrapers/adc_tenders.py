# tenders/scrapers/adc_tenders.py
"""
ADC — Aqaba Development Corporation — Tenders Announcements
URL: https://www.adc.jo/nwsgroup.aspx?group_key=tan

What is this?
-------------
The Aqaba Development Corporation (ADC) was established in 2004 to develop
the Aqaba Special Economic Zone (ASEZ). It publishes procurement tenders
covering infrastructure, construction, port facilities, real estate development,
and services in the Aqaba SEZ — including Aqaba port, border crossings,
marine facilities, and industrial estates.

Relevant for AEC consultancies — ADC tenders include engineering supervision,
border crossing infrastructure, marina/port development, civil construction,
and project management services in Aqaba.

Technology: ASP.NET WebForms with Telerik RadDatePicker and ASP.NET DataPager.
Static server-rendered HTML. Page 1 = GET. Pages 2+ = POST with __VIEWSTATE.

HTML structure (confirmed from live page):
    Form:   POST ./nwsgroup.aspx?group_key=tan
    List:   div.tender-desc > ul > li  (10 tenders per page)

    Each li:
        h3 > a[href="News.aspx?id=N"]  → title (Arabic or English)
        p                               → short description snippet
        table.table.table-bordered tbody tr:
            td[0]: <date> M/d/yyyy h:mm:ss tt </date>  ← Publish Date
            td[1]: <date> M/d/yyyy h:mm:ss tt </date>  ← End Date
            td[2]: <a href="News.aspx?id=N" class="btn btn-primary">Read More</a>

Pagination (DataPager confirmed from HTML):
    span#ctl00_ContentPlaceHolder1_DataPager1
    Page 1: span.pageCurrent → current (no button, GET)
    Page 2: input[name="ctl00$ContentPlaceHolder1$DataPager1$ctl01$ctl01" value="2"]
    Page 3: input[name="ctl00$ContentPlaceHolder1$DataPager1$ctl01$ctl02" value="3"]
    ...
    Next>>: javascript:__doPostBack('ctl00$ContentPlaceHolder1$DataPager1$ctl02$ctl00','')
    Strategy: POST with button name + value to trigger the page

Detail URL: https://www.adc.jo/News.aspx?id=N

Date format: M/d/yyyy h:mm:ss tt  (ASP.NET US locale)
             e.g. "10/29/2024 12:00:00 AM"

Sample tenders (confirmed from live page):
    id=856 - Tender No(35/A/2024) — Management, Operation & Maintenance Services
    id=855 - اعلان نتائج عطاء رقم (26/أ/2024) الخاص بالاشراف على تنفيذ معبر الدرة الحدودي
    id=852 - اعلان نتائج عطاء رقم (22/أ/2024) — تطوير معبر الدرة الحدودي
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.adc.jo'
LIST_URL  = f'{BASE_URL}/nwsgroup.aspx?group_key=tan'
MAX_PAGES = 10

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Cache-Control':   'no-cache',
    'Origin':          BASE_URL,
    'Referer':         LIST_URL,
}


class ADCTendersScraper(BaseScraper):
    """
    Scraper for ADC (Aqaba Development Corporation) tenders.

    Page 1: GET request.
    Pages 2+: POST with ASP.NET VIEWSTATE + DataPager button name/value.
    All 10 items per page parsed from div.tender-desc ul li.
    """

    country_code = 'JO'
    source_name  = 'ADC — Aqaba Development Corporation'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print('  X  [ADC] beautifulsoup4 not installed')
            return []

        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='ADCTendersScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [ADC] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='ADCTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : ADCTendersScraper')
        print(f'  MODE    : HTML (ASP.NET DataPager POST pagination)')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:    list[TenderData] = []
        seen_ids:   set[str]        = set()
        page        = 1

        # ASP.NET state carried between pages
        viewstate   = ''
        vsg         = ''
        page_btn    = ''   # name of button for NEXT page

        while page <= MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            try:
                if page == 1:
                    resp = self.session.get(LIST_URL, timeout=self.timeout)
                else:
                    post_data = self._build_post(viewstate, vsg, page_btn, page)
                    resp = self.session.post(
                        LIST_URL, data=post_data, timeout=self.timeout
                    )
                resp.raise_for_status()
                resp.encoding = 'utf-8'

            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[ADC] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[ADC] page {page}: {exc}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Refresh VIEWSTATE for the next POST
            vs     = soup.find('input', {'id': '__VIEWSTATE'})
            vsg_el = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            if vs:     viewstate = vs.get('value', '')
            if vsg_el: vsg       = vsg_el.get('value', '')

            # Find button for NEXT page in DataPager
            page_btn = self._find_page_btn(soup, page + 1)

            # Parse tender items
            items = soup.select('div.tender-desc ul li')
            if not items:
                print('(no items — stopping)')
                break

            # Debug on page 1
            if page == 1:
                a0 = items[0].select_one('h3 a') if items else None
                dt = items[0].select('table td date') if items else []
                print(f'\n  [DEBUG] {len(items)} items on page 1')
                if a0:
                    print(f'  [DEBUG] title[0]: {a0.get_text(strip=True)[:60]}')
                    print(f'  [DEBUG] href[0]:  {a0.get("href","")}')
                if dt:
                    print(f'  [DEBUG] dates[0]: {[d.get_text(strip=True) for d in dt]}')
                print()

            matched  = 0
            page_new = 0

            for li in items:
                td = self._parse_item(li)
                if not td:
                    continue
                if td.external_id and td.external_id in seen_ids:
                    continue
                if td.external_id:
                    seen_ids.add(td.external_id)
                page_new += 1
                combined = f'{td.title} {td.title_ar}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(items)} items, {page_new} new, {matched} relevant')
            logger.info(f'[ADC] page {page}: {page_new} new, {matched} relevant')

            if not page_btn:
                print(f'       > No next page button — done')
                break
            if page_new == 0:
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant ADC tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[ADC] complete — total={len(results)}')
        return results

    # ── POST data builder ─────────────────────────────────────────────────

    @staticmethod
    def _build_post(viewstate: str, vsg: str, btn_name: str, page: int) -> dict:
        """
        Build the complete POST payload for ASP.NET DataPager page navigation.
        All fields confirmed from live page HTML.
        """
        data = {
            # ASP.NET core
            'ctl00_RadScriptManager1_TSM':       '',
            '__EVENTTARGET':                     '',
            '__EVENTARGUMENT':                   '',
            '__VIEWSTATE':                       viewstate,
            '__VIEWSTATEGENERATOR':              vsg,
            '__VIEWSTATEENCRYPTED':              '',

            # Search panel (empty = no filter)
            'ctl00$ContentPlaceHolder1$txtSearch':                           '',
            'ctl00$ContentPlaceHolder1$txtNewsDate':                         '',
            'ctl00$ContentPlaceHolder1$txtNewsDate$dateInput':               '',
            'ctl00_ContentPlaceHolder1_txtNewsDate_dateInput_ClientState':   '',
            'ctl00_ContentPlaceHolder1_txtNewsDate_ClientState':             '',
            'ctl00_ContentPlaceHolder1_txtNewsDate_calendar_SD':             '[]',
            'ctl00_ContentPlaceHolder1_txtNewsDate_calendar_AD':
                '[[1980,1,1],[2099,12,30],[2026,6,17]]',

            'ctl00$ContentPlaceHolder1$txtNewsEndDate':                      '',
            'ctl00$ContentPlaceHolder1$txtNewsEndDate$dateInput':            '',
            'ctl00_ContentPlaceHolder1_txtNewsEndDate_dateInput_ClientState': '',
            'ctl00_ContentPlaceHolder1_txtNewsEndDate_ClientState':          '',
            'ctl00_ContentPlaceHolder1_txtNewsEndDate_calendar_SD':         '[]',
            'ctl00_ContentPlaceHolder1_txtNewsEndDate_calendar_AD':
                '[[1980,1,1],[2099,12,30],[2026,6,17]]',

            # Subscribe widget (empty)
            'ctl00$ContentPlaceHolder1$txtEmailFrom': '',
        }
        # The DataPager page button triggers the POST
        if btn_name:
            data[btn_name] = str(page)
        return data

    # ── Item parser ───────────────────────────────────────────────────────

    def _parse_item(self, li) -> Optional[TenderData]:
        try:
            h3 = li.find('h3')
            if not h3:
                return None
            a = h3.find('a')
            if not a:
                return None

            title = ' '.join(a.get_text(strip=True).split())
            href  = (a.get('href') or '').strip()

            # Extract ID: "News.aspx?id=856"
            m = re.search(r'[?&]id=(\d+)', href, re.IGNORECASE)
            item_id = m.group(1) if m else ''
            detail_url = (
                f'{BASE_URL}/{href.lstrip("/")}' if href else LIST_URL
            )

            # Dates from <date> tags inside table cells
            date_tags = li.select('table td date')
            pub_str = date_tags[0].get_text(strip=True) if len(date_tags) > 0 else ''
            end_str = date_tags[1].get_text(strip=True) if len(date_tags) > 1 else ''

            pub_date = self._parse_adc_date(pub_str)
            deadline = self._parse_adc_date(end_str)

            today = date.today()
            if deadline and deadline < today:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN'
                save_deadline = deadline

            # Description snippet from <p>
            p = li.find('p')
            desc = ' '.join(p.get_text(strip=True).split()) if p else ''
            desc = desc[:200] if desc else 'ADC Tender'

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = item_id,
                reference_no        = item_id,
                entity              = 'Aqaba Development Corporation (ADC)',
                entity_ar           = 'شركة تطوير العقبة',
                description         = desc,
                category            = self.detect_category(f'{title} {desc}'),
                status              = status,
                currency            = 'JOD',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[ADC] item parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_adc_date(val: str) -> Optional[date]:
        """
        Parse ADC date format: M/d/yyyy h:mm:ss tt
        Example: '10/29/2024 12:00:00 AM'
        """
        val = val.strip()
        if not val:
            return None
        for fmt in ('%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _find_page_btn(soup, next_page: int) -> str:
        """
        Find the DataPager submit button for the given page number.
        Buttons confirmed from HTML:
            name="ctl00$ContentPlaceHolder1$DataPager1$ctl01$ctl01" value="2"
            name="ctl00$ContentPlaceHolder1$DataPager1$ctl01$ctl02" value="3"
        Returns button name, or '' if not found (end of pages).
        """
        pager = soup.find(
            'span', id=re.compile(r'DataPager1', re.IGNORECASE)
        )
        if not pager:
            return ''
        for btn in pager.find_all('input', {'type': 'submit'}):
            if btn.get('value', '').strip() == str(next_page):
                return btn.get('name', '')
        return ''
