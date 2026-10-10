# tenders/scrapers/joneps.py
"""
JONEPS scraper — Jordan Online E-Procurement System
https://www.joneps.gov.jo/ep/invt/selectListTendInvitAL.do

Architecture
------------
ASP.NET/Java portal (KOICA-built Korean e-procurement system).
The listing page renders a full HTML table — no AJAX needed.

Pagination:
    POST to same URL with form fields:
        currentPageNo = N
        offSet        = (N-1) * recordCountPerPage
        _csrf         = <token from meta tag>
        searchStartSubmDlineDt = today's date  (dd/mm/yyyy)
        searchEndSubmDlineDt   = today + 90 days
        recordCountPerPage = 20

Table structure (2 rows per tender):
    Row 1 (aliceblue background):
        col[0]: رقم العطاء — contains onclick fn_goDetail params
        col[1]: اسم العطاء — title text + link
        col[2]: الجهة المشترية — entity
        col[3]: نوع العطاء — type
        col[4]: تاريخ النشر — pub date dd/mm/yyyy
        col[5]: آخر موعد — deadline dd/mm/yyyy
    Row 2: approval steps (skip)

Detail URL built from fn_goDetail params:
    tendNo, tendSeq, tendCategCd, tendTypeCd1
    → /ep/invt/GoodsDetails.do?tendNo=...&tendSeq=...
    → /ep/invt/ServiceDetails.do?tendNo=...&tendSeq=...
    → /ep/invt/WorkDetails.do?tendNo=...&tendSeq=...
"""

import logging
import re
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.joneps.gov.jo'
LIST_URL  = f'{BASE_URL}/ep/invt/selectListTendInvitAL.do'
MAX_PAGES = 10
PER_PAGE  = 20

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'ar,en;q=0.9',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
    'Content-Type':    'application/x-www-form-urlencoded',
}

# Detail URL routing based on tendCategCd
DETAIL_PATHS = {
    'EP1311': '/ep/invt/ServiceDetails.do',   # Services / Goods
    'EP1312': '/ep/invt/WorkDetails.do',       # Works
    'EP1313': '/ep/invt/MedicineDetails.do',   # Medicine
}


class JONEPSScraper(BaseScraper):
    """
    Scraper for Jordan JONEPS government procurement portal.
    POST-based pagination with CSRF token extraction.
    """

    country_code = 'JO'
    source_name  = 'JONEPS Jordan'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='JONEPSScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [JONEPS] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='JONEPSScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)
        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : JONEPSScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  PAGES   : {MAX_PAGES} × {PER_PAGE} = up to {MAX_PAGES * PER_PAGE}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        results: list[TenderData] = []
        csrf_token = ''

        # ── Step 1: GET first page to get CSRF token ───────────────────────
        today     = date.today()
        end_date  = today + timedelta(days=90)
        date_from = today.strftime('%d/%m/%Y')
        date_to   = end_date.strftime('%d/%m/%Y')

        try:
            resp = self.get(LIST_URL)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            csrf_token = self._extract_csrf(soup)
            print(f'  CSRF    : {"found ✓" if csrf_token else "not found ✗"}')
        except Exception as exc:
            print(f'✗ Initial GET failed: {exc}')
            return []

        # ── Step 2: Paginate via POST ──────────────────────────────────────
        for page in range(1, MAX_PAGES + 1):
            print(f'       > page {page:>2}/{MAX_PAGES}', end='  ', flush=True)

            if page == 1:
                # First page already loaded
                pass
            else:
                offset = (page - 1) * PER_PAGE
                form_data = {
                    '_csrf':                  csrf_token,
                    'currentPageNo':          str(page),
                    'offSet':                 str(offset),
                    'cache':                  'true',
                    'recordCountPerPage':      str(PER_PAGE),
                    'searchStartSubmDlineDt': date_from,
                    'searchEndSubmDlineDt':   date_to,
                    'searchTendNm':           '',
                    'searchTendNo':           '',
                    'searchTendTypeCd1':      '',
                    'searchIntrlLocalTypeCd': '',
                    'searchTendStatCd':       '',
                    'searchTendStatusCd':     '',
                    'screenType':             '',
                }
                try:
                    resp = self.session.post(
                        LIST_URL, data=form_data,
                        timeout=self.timeout
                    )
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    # Refresh CSRF for next iteration
                    new_csrf = self._extract_csrf(soup)
                    if new_csrf:
                        csrf_token = new_csrf
                except Exception as exc:
                    print(f'✗ ERROR: {exc}')
                    logger.error(f'[JONEPS] page {page}: {exc}')
                    break

            items = self._parse_table(soup)

            if not items:
                print('(empty — stopping)')
                break

            matched = 0
            for t in items:
                combined = f"{t.title} {t.entity or ''} {t.reference_no or ''}"
                if self.detect_relevance(combined):
                    results.append(t)
                    matched += 1

            print(f'→ {len(items)} rows, {matched} relevant')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant tender(s)  ←  JONEPS')
        print(f'  {"═" * 58}\n')
        return results

    # ── HTML table parser ─────────────────────────────────────────────────────
    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse the tenders table. Two rows per tender:
        Row 1 (aliceblue): data — col[0..5]
        Row 2: approval steps (skip)
        """
        items = []
        tbody = soup.select_one('table.ta_style3 tbody')
        if not tbody:
            return items

        rows = tbody.find_all('tr', recursive=False)

        # Process every other row (data rows only — skip step rows)
        i = 0
        while i < len(rows):
            row = rows[i]

            # Skip approval-steps rows (contain colspan=6)
            if row.find('td', attrs={'colspan': '6'}):
                i += 1
                continue

            cells = row.find_all('td')
            if len(cells) < 6:
                i += 1
                continue

            # ── col[0]: tender number + fn_goDetail params ────────────────
            ref_no    = cells[0].get_text(strip=True)
            onclick   = cells[0].find('a', onclick=True)
            detail_params = self._parse_fn_godetail(
                onclick['onclick'] if onclick else ''
            )
            source_url = self._build_detail_url(detail_params)

            # ── col[1]: title ──────────────────────────────────────────────
            title_el = cells[1].find('a')
            title    = title_el.get_text(strip=True) if title_el else cells[1].get_text(strip=True)
            if not title or len(title) < 3:
                i += 1
                continue

            # ── col[2]: entity ─────────────────────────────────────────────
            entity = cells[2].get_text(strip=True)

            # ── col[3]: type ───────────────────────────────────────────────
            tender_type = cells[3].get_text(strip=True)

            # ── col[4]: publication date ───────────────────────────────────
            pub_date = self._parse_date(cells[4].get_text(strip=True))

            # ── col[5]: deadline ───────────────────────────────────────────
            deadline = self._parse_date(cells[5].get_text(strip=True))

            items.append(TenderData(
                title               = title,
                source_url          = source_url,
                reference_no        = ref_no,
                external_id         = detail_params.get('tendNo', ref_no),
                entity              = entity,
                description         = tender_type,
                category            = self.detect_category(f'{title} {tender_type}'),
                status              = 'OPEN',
                currency            = 'JOD',
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))
            i += 1

        return items

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_csrf(soup: BeautifulSoup) -> str:
        """Extract _csrf token from meta tag or hidden input."""
        meta = soup.find('meta', attrs={'name': '_csrf'})
        if meta:
            return meta.get('content', '')
        inp = soup.find('input', attrs={'name': '_csrf'})
        if inp:
            return inp.get('value', '')
        return ''

    @staticmethod
    def _parse_fn_godetail(onclick: str) -> dict:
        """
        Parse: fn_goDetail('2026001243','00','','EP1311','','EP0061','EP0021','EP0011')
        Returns: {tendNo, tendSeq, tendCategCd, tendTypeCd1, tendMthodCd}
        """
        if not onclick:
            return {}
        m = re.search(
            r"fn_goDetail\('([^']+)'\s*,\s*'([^']*)'[^,]*,[^,]*,"
            r"\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,"
            r"\s*'([^']*)'\s*,\s*'([^']*)'",
            onclick
        )
        if not m:
            # Simpler match
            args = re.findall(r"'([^']*)'", onclick)
            if len(args) >= 4:
                return {
                    'tendNo':     args[0],
                    'tendSeq':    args[1],
                    'tendCategCd': args[3],
                    'tendMthodCd': args[6] if len(args) > 6 else '',
                    'tendTypeCd1': args[7] if len(args) > 7 else '',
                }
        else:
            return {
                'tendNo':     m.group(1),
                'tendSeq':    m.group(2),
                'tendCategCd': m.group(4),
                'intrlLocalTypeCd': m.group(5),
                'tendMthodCd': m.group(6),
                'tendTypeCd1': m.group(7),
            }
        return {}

    @staticmethod
    def _build_detail_url(params: dict) -> str:
        """Build the correct detail URL based on tendCategCd and tendMthodCd."""
        if not params:
            return LIST_URL

        tend_no    = params.get('tendNo', '')
        tend_seq   = params.get('tendSeq', '00')
        categ_cd   = params.get('tendCategCd', '')
        method_cd  = params.get('tendMthodCd', '')
        type_cd1   = params.get('tendTypeCd1', '')
        intrl      = params.get('intrlLocalTypeCd', '')

        qs = (f'?tendNo={tend_no}&tendSeq={tend_seq}'
              f'&tendCategCd={categ_cd}&workType=&intrlLocalTypeCd={intrl}'
              f'&tendMthodCd={method_cd}&invtYn=Y')

        if categ_cd == 'EP1312' or method_cd in ('EP0022', 'EP5021'):
            path = '/ep/invt/WorkDetails.do'
        elif categ_cd == 'EP1313':
            path = '/ep/invt/MedicineDetails.do'
        elif categ_cd == 'EP1311':
            if type_cd1 == 'EP0011':
                path = '/ep/invt/GoodsDetails.do'
            else:
                path = '/ep/invt/ServiceDetails.do'
        else:
            path = '/ep/invt/ServiceDetails.do'

        return f'{BASE_URL}{path}{qs}'

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        if not text:
            return None
        val = text.strip()
        for fmt, length in [
            ('%d/%m/%Y', 10),
            ('%Y-%m-%d', 10),
            ('%d-%m-%Y', 10),
        ]:
            try:
                return datetime.strptime(val[:length], fmt).date()
            except ValueError:
                continue
        return None
