# tenders/scrapers/gulf_portals.py
"""
Gulf Countries — Oman, Qatar, Kuwait
"""

import logging
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

OMAN_BASE    = 'https://etendering.tenderboard.gov.om'
OMAN_DASH    = f'{OMAN_BASE}/product/publicDash'
OMAN_REPLICA = f'{OMAN_BASE}/product/publicDashReplica'
OMAN_DETAIL  = f'{OMAN_BASE}/product/nitParameterView'

OMAN_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,ar;q=0.9',
    'Referer':         OMAN_REPLICA,
}

# Full confirmed URL from browser — switchLangFlag=1 appears twice = English
# Pagination: append &pageNo=N (page 1 = no extra param)
OMAN_EN_URL = (
    f'{OMAN_DASH}?viewFlag=NewTenders&switchLangFlag=1&switchLangFlag=1'
)
OMAN_SECTIONS = [
    (OMAN_EN_URL, 'NewTenders', 'New Tenders (English)', 'LTR'),
]


def _oman_parse_date(text: str) -> Optional[datetime.date]:
    """
    Parse dates from Oman portal.
    Confirmed format: "27-04-2026 04:10"  →  DD-MM-YYYY HH:MM
    """
    if not text:
        return None
    text = text.strip()
    # DD-MM-YYYY HH:MM  or  DD-MM-YYYY
    m = re.match(r'^(\d{2})-(\d{2})-(\d{4})', text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
        except ValueError:
            pass
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d %b %Y'):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


class OmanTenderBoardScraper(BaseScraper):
    """
    Oman Tender Board — eTendering portal.
    URL: https://etendering.tenderboard.gov.om/product/publicDash?viewFlag=InProcessTenders

    ─────────────────────────────────────────────────────────────────────
    Confirmed structure (from full HTML document):

    Table columns (RTL, Arabic):
      td[0]: رقم التسلسل  — row number (ignored)
      td[1]: رقم المناقصة — tender reference number (span text)
      td[2]: إسم المناقصة — truncated title (full title in hidden input)
      td[3]: الجهة/الوحدة الحكومية — entity/ministry
      td[4]: [الدرجة]فئة المشتريات — procurement category + grade
      td[5]: نوع المناقصه[نوع الشركة] — tender type [company type]
      td[6]: تاريخ الفتح الفعلي — actual opening date "27-04-2026 04:10"
      td[7]: رسوم المناقصة — tender fees
      td[8]: الضمان البنكي(%/value) — bank guarantee
      td[9]: إجراء — action buttons

    Full title: hidden input <input id="tendDescN" value="FULL ARABIC TITLE">
      where N = row sequence (1, 2, 3, ...)

    Tender ID (internal): extracted from onclick="getNit('86672')"
    Detail URL: /product/nitParameterView?mode=public&tenderNo=86672&PublicUrl=1

    Pagination: POST to /product/publicDash?viewFlag=InProcessTenders
      POST data: viewFlag=InProcessTenders, pageNo=N, CTRL_STRDIRECTION=RTL,
                 randomno=fixedrandomno, PublicUrl=1, securityFlag=1

    50 rows per page, up to 372+ pages (18,600+ open tenders).
    ─────────────────────────────────────────────────────────────────────
    """

    country_code = 'OM'
    source_name  = 'Oman Tender Board'
    base_url     = OMAN_BASE

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class=self.__class__.__name__, is_active=True
            )
        except ScraperSource.DoesNotExist:
            print(f'  ✗ [Oman] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class=self.__class__.__name__, is_active=True
            ).first()

        self.source = source
        url_obj     = source.tendering_urls.filter(is_active=True).first()
        max_pages   = url_obj.max_pages if url_obj and url_obj.max_pages else 10

        self._reload_keywords_from_db()
        self.session.headers.update(OMAN_HEADERS)

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : OmanTenderBoardScraper')
        print(f'  URL     : {OMAN_DASH}?viewFlag=NewTenders')
        print(f'  PAGES   : up to {max_pages} × 50 = {max_pages * 50} tenders')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()

        for endpoint, view_flag, label, direction in OMAN_SECTIONS:
            print(f'\n  ── {label}')
            for page in range(1, max_pages + 1):
                print(f'       › Page {page:>3}/{max_pages}', end='  ', flush=True)

                html = self._fetch_page(endpoint, view_flag, page, direction)
                if not html:
                    print('✗ Unreachable')
                    if page == 1:
                        break
                    break

                soup  = BeautifulSoup(html, 'html.parser')
                items = self._parse_rows(soup, view_flag, direction)

                if not items:
                    # Debug: show what we got
                    all_tr = soup.find_all('tr')
                    all_td_counts = [len(tr.find_all('td')) for tr in all_tr]
                    title = soup.find('title')
                    lang_dir = soup.find('html')
                    print(f'(no rows — title={title.text.strip()[:40] if title else "?"}'
                          f' dir={lang_dir.get("dir","?") if lang_dir else "?"}'
                          f' trs={len(all_tr)} td_counts={all_td_counts[:10]})')
                    logger.debug(f'[Oman] page {page} HTML[:1000]:\n{soup.get_text()[:500]}')
                    break

                matched = 0
                for item in items:
                    if item.external_id not in seen_ids:
                        seen_ids.add(item.external_id)
                        combined = f'{item.title} {item.entity} {item.description}'
                        if self.detect_relevance(combined):
                            item.category = self.detect_category(combined)
                            all_results.append(item)
                            matched += 1

                print(f'→ {len(items)} rows, {matched} relevant')

                # Check max pages from pagination widget
                max_p = self._get_max_pages(soup)
                if max_p and page >= min(max_pages, max_p):
                    break
                if len(items) < 50:
                    break

                time.sleep(1.5)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  Oman Tender Board')
        print(f'  {"═" * 58}\n')
        return all_results

    # ── HTTP ──────────────────────────────────────────────────────────────

    def _fetch_page(self, endpoint: str, view_flag: str, page: int, direction: str = 'LTR') -> Optional[str]:
        """
        GET the Oman eTendering portal page.

        The confirmed working URL format is:
          publicDash?viewFlag=NewTenders&switchLangFlag=1&switchLangFlag=1

        Pagination: append &pageNo=N for pages 2+
        Page 1 uses the base URL as-is (no pageNo param).
        """
        url = endpoint if page == 1 else f'{endpoint}&pageNo={page}'

        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update(OMAN_HEADERS)
            r = s.get(url, timeout=25)
            if r.status_code == 200 and len(r.text) > 500:
                self.session.cookies.update(s.cookies)
                return r.text
            logger.warning(f'[Oman] GET {url} → {r.status_code}')
        except Exception as exc:
            logger.warning(f'[Oman] cloudscraper: {exc}')

        # Fallback: requests session
        try:
            self.session.headers.update(OMAN_HEADERS)
            r = self.session.get(url, timeout=25)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
        except Exception as exc:
            logger.warning(f'[Oman] fallback: {exc}')
        return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_detail_url(tender_id: str, randomno: str = 'fixedrandomno', direction: str = 'LTR') -> str:
        """
        Build the NIT detail popup URL with correct hashval.

        Verified formula:
          hashval = sha256(mode + tenderNo + PublicUrl + CTRL_STRDIRECTION + randomno)
          e.g. sha256('public88047' + '1' + 'LTR' + 'fixedrandomno')
               = 28556554c002d09112a71de0f340d169...  ✓ (matches live URL)
        """
        import hashlib
        mode    = 'public'
        pub_url = '1'
        raw     = mode + tender_id + pub_url + direction + randomno
        hval    = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        return (
            f'{OMAN_DETAIL}'
            f'?mode={mode}&tenderNo={tender_id}&PublicUrl={pub_url}'
            f'&CTRL_STRDIRECTION={direction}'
            f'&encparam=mode,tenderNo,PublicUrl,CTRL_STRDIRECTION,randomno'
            f'&hashval={hval}'
        )

    # ── Parser ────────────────────────────────────────────────────────────

    def _parse_rows(self, soup: BeautifulSoup, view_flag: str, direction: str = 'LTR') -> list[TenderData]:
        """
        Parse tbody rows from Oman eTendering portal tables.

        English (publicDashReplica / LTR) column layout — confirmed from screenshot:
          td[0]: S.No.
          td[1]: Tender No
          td[2]: Tender Title (truncated; full title in hidden input #tendDescN)
          td[3]: Entity
          td[4]: Category [Grade]
          td[5]: Tender Type [Vendor Type]
          td[6]: Date — "Sales EndDate:31-05-2026\nBid Closing Date:05-06-2026"
          td[7]: Tender Fee
          td[8]: Tender Bond (%/value)
          td[9]: Action — getNit('XXXXX') → detail URL

        Arabic (publicDash / RTL) column layout — confirmed from HTML doc:
          Same columns, td[6] = "27-04-2026 04:10" (opening datetime only)

        Full title: <input type="hidden" id="tendDescN" value="FULL TITLE">
        """
        items   = []
        # randomno for public users = "fixedrandomno" (verified against live URLs)
        rn_el = soup.find("input", {"name": "randomno", "type": "text"}) or soup.find("input", {"name": "randomno"})
        page_randomno = (rn_el.get("value") or "fixedrandomno") if rn_el else "fixedrandomno"
        is_en   = (direction == 'LTR')

        # ── Build row_seq → full_title map from hidden inputs ─────────────
        title_map: dict[str, str] = {}
        for inp in soup.find_all('input', type='hidden'):
            inp_id = inp.get('id', '')
            m = re.match(r'^tendDesc(\d+)$', inp_id)
            if m:
                title_map[m.group(1)] = inp.get('value', '').strip()

        # ── Collect data rows (rows with numeric sequence in td[0]) ────────
        data_rows = []
        for tr in soup.find_all('tr'):
            cells = tr.find_all('td')
            if len(cells) >= 5:   # NewTenders may have fewer visible columns
                seq_text = cells[0].get_text(strip=True).rstrip('.').strip()
                if seq_text.isdigit():
                    data_rows.append((seq_text, cells))

        for seq, cells in data_rows:
            # Normalize seq: "01" → "1" to match title_map keys
            seq_norm = str(int(seq)) if seq.isdigit() else seq
            # ── Reference number ─────────────────────────────────────────
            ref_no = cells[1].get_text(' ', strip=True).strip()

            # ── Full title from hidden input #tendDescN ──────────────────
            # The td[2] title attr and text are both TRUNCATED with ".....".
            # The hidden input value and td[2] onmouseover Tip() both contain
            # the full untruncated title.
            title = title_map.get(seq_norm, title_map.get(seq, '')).strip()
            if not title:
                # Fallback: extract from onmouseover Tip('...')
                omo = cells[2].get('onmouseover', '')
                m_tip = re.search(r"Tip\('([^']+)'\)", omo)
                if m_tip:
                    title = m_tip.group(1).strip()
            if not title:
                # Last fallback: strip truncation dots from cell text
                title = re.sub(r'\.{3,}$', '', cells[2].get_text(strip=True)).strip()
            if not title or len(title) < 3:
                continue

            description = ''

            # ── Entity ───────────────────────────────────────────────────
            entity = cells[3].get_text(strip=True)

            # ── Category ─────────────────────────────────────────────────
            category_text = cells[4].get_text(strip=True)

            # ── Tender type ───────────────────────────────────────────────
            tender_type = cells[5].get_text(' ', strip=True).strip()

            # ── Date parsing ──────────────────────────────────────────────
            # English (LTR):  "Sales EndDate:31-05-2026 Bid Closing Date:05-06-2026"
            # Arabic (RTL):   "27-04-2026 04:10"
            date_text    = cells[6].get_text(' ', strip=True)
            pub_date     = None
            deadline     = None

            if is_en:
                # Extract Sales EndDate (= document sale end, used as pub_date)
                m_sales = re.search(r'Sales\s*EndDate[:\s]+([\d\-]+)', date_text, re.I)
                if m_sales:
                    pub_date = _oman_parse_date(m_sales.group(1))
                # Extract Bid Closing Date (= submission deadline)
                m_bid = re.search(r'Bid\s*Closing\s*Date[:\s]+([\d\-]+)', date_text, re.I)
                if m_bid:
                    deadline = _oman_parse_date(m_bid.group(1))
            else:
                # Arabic: opening date only
                pub_date = _oman_parse_date(date_text)

            # ── Tender ID from onclick="getNit('86672')" ──────────────────
            tender_id  = ''
            action_cell = cells[9] if len(cells) > 9 else cells[-1]
            m = re.search(r"getNit\('(\d+)'\)", str(action_cell))
            if m:
                tender_id = m.group(1)

            # ── Detail URL (popup with hashval) ─────────────────────────────
            if tender_id:
                source_url = self._build_detail_url(tender_id, page_randomno, direction)
                ext_id     = f'OM-{tender_id}'
            else:
                import hashlib
                source_url = f'{OMAN_DASH}?viewFlag={view_flag}'
                ext_id     = f'OM-{hashlib.md5(ref_no.encode()).hexdigest()[:12]}'

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ref_no,
                description         = description or (
                    f'Tender No: {ref_no}\n'
                    f'Entity: {entity}\n'
                    f'Category: {category_text}\n'
                    f'Type: {tender_type}'
                ),
                entity              = entity,
                category            = 'OTHER',
                status              = 'OPEN',
                currency            = 'OMR',
                country_code        = 'OM',
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items

    @staticmethod
    def _get_max_pages(soup: BeautifulSoup) -> Optional[int]:
        """Extract max page count from pagination: <input name='hidMax' value='372'>"""
        inp = soup.find('input', {'name': 'hidMax'})
        if inp:
            try:
                return int(inp.get('value', 0))
            except ValueError:
                pass
        return None

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        """Kept for base-class compatibility — delegates to _parse_rows."""
        return self._parse_rows(soup, 'InProcessTenders', 'RTL')


class QatarTendersScraper(BaseScraper):
    country_code = 'QA'
    source_name  = 'Qatar Government Tenders'
    base_url     = 'https://www.tender.gov.qa'

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        items = []
        cards = soup.select(
            '.tender-card, .procurement-item, article, '
            '.tender-row, table tr'
        )
        for card in cards:
            if card.name == 'tr' and card.find('th'):
                continue
            link  = card.find('a', href=True)
            title_el = card.find(['h2', 'h3', 'h4', 'td'])
            title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:200]
            if not title:
                continue
            items.append(TenderData(
                title      = title,
                source_url = urljoin(self.base_url, link['href']) if link else tendering_url.url,
                external_id= (link['href'].split('/')[-1] if link else ''),
                status     = 'OPEN',
                currency   = 'QAR',
            ))
        return items


class KuwaitCAPTScraper(BaseScraper):
    country_code = 'KW'
    source_name  = 'Kuwait CAPT'
    base_url     = 'https://www.capt.gov.kw'

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        items = []
        rows = soup.select('table tr, .tender-row, .list-item')
        for row in rows:
            cells = row.find_all('td')
            if not cells:
                continue
            link = row.find('a', href=True)
            title = cells[0].get_text(strip=True)
            if not title:
                continue
            items.append(TenderData(
                title               = title,
                source_url          = urljoin(self.base_url, link['href']) if link else tendering_url.url,
                external_id         = cells[1].get_text(strip=True) if len(cells) > 1 else '',
                reference_no        = cells[1].get_text(strip=True) if len(cells) > 1 else '',
                entity              = cells[2].get_text(strip=True) if len(cells) > 2 else '',
                status              = 'OPEN',
                currency            = 'KWD',
                submission_deadline = self._parse_date(
                    cells[3].get_text(strip=True) if len(cells) > 3 else ''
                ),
            ))
        return items

    def _parse_date(self, text: str) -> Optional[datetime.date]:
        if not text:
            return None
        val = text.strip()
        if not val:
            return None
        if '.' in val and 'T' not in val:
            val = val[:val.index('.')]
        for fmt, length in [
            ('%Y-%m-%dT%H:%M:%S', 19),
            ('%Y-%m-%dT%H:%M',    16),
            ('%Y-%m-%d',          10),
            ('%d/%m/%Y',          10),
            ('%d-%m-%Y',          10),
            ('%d %b %Y',          11),
        ]:
            try:
                return datetime.strptime(val[:length], fmt).date()
            except ValueError:
                continue
        return None
