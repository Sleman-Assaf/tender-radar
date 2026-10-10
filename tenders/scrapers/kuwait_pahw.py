# tenders/scrapers/kuwait_pahw.py
"""
Kuwait PAHW — Public Authority for Housing Welfare
(المؤسسة العامة للرعاية السكنية)

Source: https://www.pahw.gov.kw/Tenders_arabic

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from live HTML)
─────────────────────────────────────────────────────────────────────────────
URL:  https://www.pahw.gov.kw/Tenders_arabic
Pagination: ASP.NET __doPostBack  (ViewState + EventTarget + EventArgument)
            ctl00$MainContent$rptPaging$ctl{N}$lbPaging
            ~12 tenders per page

Each tender block:
    h2                      → "المناقصة رقم: م.ع.ر.س/م/1527- 2026/ 2027"
    p (after h2)            → title/subject
    "حالة المناقصة" label   → status text
    "نوع المناقصة" label    → type/category

Detail table (collapsed, always in DOM):
    Row "الإعلان":
        col "تاريخ الإعلان: DD/MM/YYYY   تاريخ الإقفال: DD/MM/YYYY"
    Attachments link: /Downloads/Tenders/AR/...pdf

Dates: DD/MM/YYYY

Active statuses (include):  جاري الإعلان, تم الإعلان, تم تمديد الاقفال
Skip statuses:              دراسة العطاءات (evaluating), تم الترسية (awarded),
                            توقيع العقد (signed), تم الإلغاء (cancelled)

Country:  KW
Currency: KWD
Entity:   المؤسسة العامة للرعاية السكنية (PAHW)
"""

import logging
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.pahw.gov.kw'
LIST_URL = f'{BASE_URL}/Tenders_arabic'
ENTITY   = 'المؤسسة العامة للرعاية السكنية'

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

# Skip tenders that are past the active stage
SKIP_STATUSES = {
    'دراسة العطاءات',
    'تم الترسية',
    'توقيع العقد',
    'تم الإلغاء',
    'منتهي',
}


def _parse_date(text: str) -> Optional[datetime.date]:
    """Parse DD/MM/YYYY → date."""
    if not text:
        return None
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
        except ValueError:
            pass
    return None


def _extract_aspnet_fields(soup: BeautifulSoup) -> dict:
    """Extract ASP.NET hidden fields needed for __doPostBack."""
    fields = {}
    for name in ('__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION',
                 '__EVENTTARGET', '__EVENTARGUMENT'):
        el = soup.find('input', {'name': name})
        if el:
            fields[name] = el.get('value', '')
        else:
            fields[name] = ''
    return fields


class KuwaitPAHWScraper(BaseScraper):
    """
    Scraper for Kuwait PAHW — Public Authority for Housing Welfare.

    Handles ASP.NET __doPostBack pagination.

    TenderingURL in DB:
        url              = https://www.pahw.gov.kw/Tenders_arabic
        pagination_style = SINGLE   (we handle pagination ourselves)
        max_pages        = 10
    """

    country_code = 'KW'
    source_name  = 'Kuwait PAHW'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='KuwaitPAHWScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗ [PAHW] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='KuwaitPAHWScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : KuwaitPAHWScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  PAGES   : up to {max_pages}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()

        # ── Page 1: simple GET ────────────────────────────────────────────────
        print(f'       › Page   1/{max_pages}', end='  ', flush=True)
        try:
            resp = self._pahw_get(LIST_URL)
        except Exception as exc:
            print(f'✗ {exc}')
            logger.error(f'[PAHW] page 1: {exc}')
            return []

        soup       = BeautifulSoup(resp.text, 'html.parser')
        asp_fields = _extract_aspnet_fields(soup)
        items      = self._parse_page(soup)

        # Debug sample
        print(f'→ {len(items)} rows')
        if items:
            s = items[0]
            print(f'  [DEBUG] Sample ref  : {s.reference_no!r}')
            print(f'  [DEBUG] Sample title: {s.title[:60]!r}')
            print(f'  [DEBUG] Sample dates: pub={s.publication_date}  deadline={s.submission_deadline}')

        for item in items:
            if item.external_id not in seen_ids:
                seen_ids.add(item.external_id)
                combined = f'{item.title} {item.entity}'
                item.category = self.detect_category(combined)
                all_results.append(item)

        # ── Pages 2+: ASP.NET __doPostBack ────────────────────────────────────
        last_page = self._get_last_page(soup)
        if last_page:
            print(f'  [DEBUG] Total pages : {last_page}')
        pages_to_scrape = min(max_pages, last_page) if last_page else max_pages

        for page in range(2, pages_to_scrape + 1):
            print(f'       › Page {page:>3}/{pages_to_scrape}', end='  ', flush=True)

            # Build __doPostBack event target for page N
            # Pattern: ctl00$MainContent$rptPaging$ctl{NN}$lbPaging
            # ctl index = page - 1  (page 2 = ctl01, page 3 = ctl02, etc.)
            ctl_idx    = f'ctl{(page - 1):02d}'
            event_tgt  = f'ctl00$MainContent$rptPaging${ctl_idx}$lbPaging'

            post_data  = {
                '__EVENTTARGET':        event_tgt,
                '__EVENTARGUMENT':      '',
                '__VIEWSTATE':          asp_fields.get('__VIEWSTATE', ''),
                '__VIEWSTATEGENERATOR': asp_fields.get('__VIEWSTATEGENERATOR', ''),
                '__EVENTVALIDATION':    asp_fields.get('__EVENTVALIDATION', ''),
            }

            try:
                resp = self._pahw_post(LIST_URL, data=post_data)
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[PAHW] page {page}: {exc}')
                break

            soup       = BeautifulSoup(resp.text, 'html.parser')
            asp_fields = _extract_aspnet_fields(soup)   # update for next page
            items      = self._parse_page(soup)

            if not items:
                print('(empty — stopping)')
                break

            matched = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    combined = f'{item.title} {item.entity}'
                    item.category = self.detect_category(combined)
                    all_results.append(item)
                    matched += 1

            print(f'→ {len(items)} rows, {matched} saved')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  Kuwait PAHW')
        print(f'  {"═" * 58}\n')
        logger.info(f'[PAHW] scrape() complete — total={len(all_results)}')
        return all_results

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _pahw_get(self, url: str):
        """GET with cloudscraper fallback."""
        import time
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update(HEADERS)
            r = s.get(url, timeout=25)
            r.raise_for_status()
            self._cloudscraper_cookies = s.cookies
            return r
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f'[PAHW] cloudscraper GET failed: {exc}')

        time.sleep(1.5)
        return self.get(url)

    def _pahw_post(self, url: str, data: dict):
        """POST with ASP.NET form fields; reuses cloudscraper session cookies."""
        import time
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update(HEADERS)
            # Reuse cookies from previous GET to maintain ASP.NET session
            if hasattr(self, '_cloudscraper_cookies'):
                s.cookies.update(self._cloudscraper_cookies)
            r = s.post(url, data=data, timeout=25)
            r.raise_for_status()
            self._cloudscraper_cookies = s.cookies
            return r
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f'[PAHW] cloudscraper POST failed: {exc}')

        time.sleep(1.5)
        return self.session.post(url, data=data, timeout=25)

    # ── Page parser ───────────────────────────────────────────────────────────

    def _parse_page(self, soup: BeautifulSoup) -> list[TenderData]:
        """Parse all tender blocks from a page."""
        items = []

        # Each tender block is an h2 with "رقم:" in text
        for h2 in soup.find_all('h2'):
            h2_text = h2.get_text(strip=True)
            if 'رقم' not in h2_text:
                continue
            item = self._parse_tender_block(h2)
            if item:
                items.append(item)

        return items

    def _parse_tender_block(self, h2) -> Optional[TenderData]:
        """Parse one tender starting from its h2 heading."""

        # ── Reference number ──────────────────────────────────────────────────
        # "المناقصة رقم: م.ع.ر.س/م/1527- 2026/ 2027"
        h2_text   = h2.get_text(strip=True)
        ref_match = re.search(r'رقم[:\s]*(.+)', h2_text)
        ref_no    = ref_match.group(1).strip() if ref_match else h2_text.strip()
        ref_no    = re.sub(r'\s+', ' ', ref_no).strip()

        # ── Title (next <p> sibling) ───────────────────────────────────────────
        title = ''
        nxt = h2.find_next_sibling()
        while nxt:
            if nxt.name == 'p':
                txt = nxt.get_text(strip=True)
                if txt and 'حالة' not in txt and 'نوع' not in txt:
                    title = txt
                    break
            elif nxt.name == 'h2':
                break
            nxt = nxt.find_next_sibling()

        if not title:
            return None

        # ── Status and type ───────────────────────────────────────────────────
        # These come as label+value div pairs after the title
        status_text = ''
        type_text   = ''

        # Search within the parent container (div.tender-block or parent div)
        parent = h2.parent
        if parent:
            full_text = parent.get_text('\n', strip=True)
            # Extract حالة المناقصة value
            m_status = re.search(r'حالة[^‌\n]*[\n\r\t]+([^\n\r\t]+)', full_text)
            if m_status:
                status_text = m_status.group(1).strip()
            m_type = re.search(r'نوع[^‌\n]*[\n\r\t]+([^\n\r\t]+)', full_text)
            if m_type:
                type_text = m_type.group(1).strip()

        # Skip non-active tenders
        for skip in SKIP_STATUSES:
            if skip in status_text:
                return None

        # ── Dates from detail table ───────────────────────────────────────────
        pub_date  = None
        deadline  = None
        attach_url = ''

        # Find the detail section (table or div after المزيد link)
        # The detail block follows the h2 in DOM — look in parent
        if parent:
            # Find الإعلان row: "تاريخ الإعلان: DD/MM/YYYY  تاريخ الإقفال: DD/MM/YYYY"
            full_html = str(parent)
            soup_p    = BeautifulSoup(full_html, 'html.parser')
            for td in soup_p.find_all(['td', 'div', 'p']):
                txt = td.get_text(' ', strip=True)
                if 'تاريخ الإعلان' in txt and 'تاريخ الإقفال' in txt:
                    # Extract both dates
                    m_pub = re.search(r'تاريخ الإعلان[:\s]*(\d{1,2}/\d{1,2}/\d{4})', txt)
                    m_dl  = re.search(r'تاريخ الإقفال[:\s]*(\d{1,2}/\d{1,2}/\d{4})', txt)
                    if m_pub:
                        pub_date = _parse_date(m_pub.group(1))
                    if m_dl:
                        deadline = _parse_date(m_dl.group(1))
                    break

            # Attachment PDF link
            for a in soup_p.find_all('a', href=True):
                href = a['href']
                if '/Downloads/Tenders/' in href and href.endswith('.pdf'):
                    attach_url = BASE_URL + href if href.startswith('/') else href
                    break

        # ── External ID ───────────────────────────────────────────────────────
        ext_id = re.sub(r'[\s/\\]+', '-', ref_no)[:100]

        # ── Source URL ────────────────────────────────────────────────────────
        source_url = attach_url or LIST_URL

        return TenderData(
            title               = title[:300],
            source_url          = source_url,
            external_id         = ext_id,
            reference_no        = ref_no,
            description         = (
                f'{title}\n\n'
                f'الرقم: {ref_no}\n'
                f'الجهة: {ENTITY}\n'
                f'النوع: {type_text}\n'
                f'الحالة: {status_text}\n'
                f'المصدر: المؤسسة العامة للرعاية السكنية - الكويت'
            ),
            entity              = ENTITY,
            category            = 'OTHER',
            status              = 'OPEN',
            currency            = 'KWD',
            country_code        = 'KW',
            publication_date    = pub_date,
            submission_deadline = deadline,
            force_relevant      = True,
        )

    @staticmethod
    def _get_last_page(soup: BeautifulSoup) -> Optional[int]:
        """Extract last page number from ASP.NET paging links."""
        # Look for paging table at bottom — find highest page number
        max_page = 1
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if '__doPostBack' in href or 'lbPaging' in href:
                txt = a.get_text(strip=True)
                if txt.isdigit():
                    max_page = max(max_page, int(txt))

        # Also check onclick attributes
        for a in soup.find_all('a'):
            onclick = a.get('onclick', '')
            if 'lbPaging' in onclick:
                txt = a.get_text(strip=True)
                if txt.isdigit():
                    max_page = max(max_page, int(txt))

        return max_page if max_page > 1 else None
