# tenders/scrapers/biddetail.py
"""
BidDetail — Region-based tender scraper

Sources:
  Western Asia: https://www.biddetail.com/region/western-asia-tenders
  Eastern Africa: https://www.biddetail.com/region/eastern-africa-tenders

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from live HTML)
─────────────────────────────────────────────────────────────────────────────
Page 1: Full HTML at GET https://www.biddetail.com/region/<slug>
  10 tenders in div#tenderList > div.tender_row

Pagination: POST https://www.biddetail.com/RegionWiseTenderLazyLoad
  Form fields (from hidden form #formRegionWiseFilterParams):
    p         = page index (2, 3, ...)
    k         = region slug (e.g. 'western-asia', 'eastern-africa')
    LiveCount = total tender count (from hidden input #hdnLiveCount)
  Returns: raw HTML fragment (same div.tender_row blocks)

Each div.tender_row:
  h2 > a[title, href]              → entity (title attr), detail URL (href)
  a.m-notice-text[title, href]     → description (title attr)
    p.workDesc                     → description text
  li.price                         → tender value (e.g. "19,781,316,792")
  li.dd                            → deadline ("13 Jul 2026")
  li.state                         → region/location
  li.idno                          → BDR reference number (e.g. "111733116")
  Detail URL: /latest-tenders/<BDR-ID>$<UUID>

Country: extracted from detail URL slug or entity name
Date format: "DD Mon YYYY" (e.g. "13 Jul 2026")
"""

import logging
import re
import time
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL      = 'https://www.biddetail.com'
LAZY_URL      = f'{BASE_URL}/RegionWiseTenderLazyLoad'
RECORDS_PAGE  = 10   # BidDetail shows 10 per page

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

DATE_FMTS = ('%d %b %Y', '%d %B %Y', '%B %d, %Y', '%d-%m-%Y', '%Y-%m-%d')


def _parse_date(text: str) -> Optional[datetime.date]:
    if not text:
        return None
    text = text.strip()
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# Country slug → ISO2 (extracted from BidDetail detail URLs like /latest-tenders/...)
# Country name → ISO2 code for resolving from entity/title text
_COUNTRY_MAP = {
    'jordan': 'JO', 'iraq': 'IQ', 'syria': 'SY', 'lebanon': 'LB',
    'palestine': 'PS', 'saudi': 'SA', 'yemen': 'YE', 'oman': 'OM',
    'kuwait': 'KW', 'bahrain': 'BH', 'qatar': 'QA', 'uae': 'AE',
    'emirates': 'AE', 'turkey': 'TR', 'egypt': 'EG', 'iran': 'IR',
    'kenya': 'KE', 'tanzania': 'TZ', 'uganda': 'UG', 'ethiopia': 'ET',
    'rwanda': 'RW', 'somalia': 'SO', 'eritrea': 'ER', 'djibouti': 'DJ',
    'south sudan': 'SS', 'sudan': 'SD', 'burundi': 'BI', 'comoros': 'KM',
    'ghana': 'GH', 'nigeria': 'NG', 'senegal': 'SN', 'mali': 'ML',
    'morocco': 'MA', 'algeria': 'DZ', 'tunisia': 'TN', 'libya': 'LY',
    'mozambique': 'MZ', 'zambia': 'ZM', 'zimbabwe': 'ZW', 'angola': 'AO',
    'india': 'IN', 'pakistan': 'PK', 'bangladesh': 'BD', 'nepal': 'NP',
    'afghanistan': 'AF', 'myanmar': 'MM', 'cambodia': 'KH',
    'indonesia': 'ID', 'philippines': 'PH', 'vietnam': 'VN',
}


def _resolve_country_from_text(text: str) -> str:
    """Extract ISO2 country code from entity/title text. Returns 'ZZ' if unknown."""
    if not text:
        return 'ZZ'
    t = text.lower()
    # Check multi-word first
    for name, code in sorted(_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if name in t:
            return code
    return 'ZZ'


# The region pages don't include individual country — we derive from entity or region
REGION_DEFAULT_COUNTRY = {
    'western-asia':   '',   # multi-country — leave blank, region=ME
    'eastern-africa': '',   # multi-country — leave blank, region=AF
}


class BidDetailScraper(BaseScraper):
    """
    Base scraper for BidDetail region pages.

    Page 1 = GET the region URL (tenders are in HTML).
    Pages 2+ = POST to /RegionWiseTenderLazyLoad with p, k, LiveCount.
    """

    region_slug: str = ''       # e.g. 'western-asia'
    region_name: str = ''       # human label
    page_url:    str = ''       # canonical URL
    default_country: str = ''   # ISO2 fallback

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class=self.__class__.__name__, is_active=True
            )
        except ScraperSource.DoesNotExist:
            print(f'  ✗ [{self.region_name}] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class=self.__class__.__name__, is_active=True
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
        print(f'  SCRAPER : {self.__class__.__name__}')
        print(f'  REGION  : {self.region_name} ({self.region_slug})')
        print(f'  PAGES   : up to {max_pages} × {RECORDS_PAGE} = {max_pages * RECORDS_PAGE} tenders')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()
        live_count   = 0

        # ── Page 1: GET the region page HTML ─────────────────────────────────
        print(f'       › Page   1/{max_pages}', end='  ', flush=True)
        try:
            resp = self._bd_get(self.page_url)
        except Exception as exc:
            print(f'✗ {exc}')
            logger.error(f'[{self.region_name}] page 1: {exc}')
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Extract LiveCount from hidden input
        lc_el = soup.find('input', {'id': 'hdnLiveCount'})
        if lc_el:
            try:
                live_count = int(lc_el.get('value', 0))
            except ValueError:
                pass
        total_pages = max(1, -(-live_count // RECORDS_PAGE))  # ceiling div
        pages_to_scrape = min(max_pages, total_pages)

        items = self._parse_rows(soup)
        print(f'→ {len(items)} rows  (total={live_count}, pages={total_pages})')

        if items and len(items) > 0:
            s = items[0]
            print(f'  [DEBUG] entity  : {s.entity[:60]!r}')
            print(f'  [DEBUG] title   : {s.title[:60]!r}')
            print(f'  [DEBUG] deadline: {s.submission_deadline!r}')
            print(f'  [DEBUG] bdr_no  : {s.reference_no!r}')

        for item in items:
            if item.external_id not in seen_ids:
                seen_ids.add(item.external_id)
                combined = f'{item.title} {item.entity}'
                if self.detect_relevance(combined):
                    item.category = self.detect_category(combined)
                    all_results.append(item)

        # ── Pages 2+: POST lazy load ──────────────────────────────────────────
        for page in range(2, pages_to_scrape + 1):
            print(f'       › Page {page:>3}/{pages_to_scrape}', end='  ', flush=True)

            post_data = {
                'p':         page,
                'k':         self.region_slug,
                'LiveCount': live_count,
            }

            try:
                resp = self._bd_post(LAZY_URL, data=post_data)
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[{self.region_name}] page {page}: {exc}')
                break

            html  = resp.text
            if not html or len(html) < 50:
                print('(empty — stopping)')
                break

            soup  = BeautifulSoup(html, 'html.parser')
            items = self._parse_rows(soup)

            if not items:
                print('(no rows — stopping)')
                break

            matched = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    combined = f'{item.title} {item.entity}'
                    if self.detect_relevance(combined):
                        item.category = self.detect_category(combined)
                        all_results.append(item)
                        matched += 1

            print(f'→ {len(items)} rows, {matched} relevant')
            time.sleep(1.5)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  {self.region_name}')
        print(f'  {"═" * 58}\n')
        return all_results

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _bd_get(self, url: str):
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update(HEADERS)
            r = s.get(url, timeout=25)
            r.raise_for_status()
            self.session.cookies.update(s.cookies)
            return r
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f'[{self.region_name}] cloudscraper GET: {exc}')
        return self.get(url)

    def _bd_post(self, url: str, data: dict):
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update({
                **HEADERS,
                'Referer':          self.page_url,
                'X-Requested-With': 'XMLHttpRequest',
                'Content-Type':     'application/x-www-form-urlencoded',
            })
            s.cookies.update(self.session.cookies)
            r = s.post(url, data=data, timeout=25)
            r.raise_for_status()
            self.session.cookies.update(s.cookies)
            return r
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f'[{self.region_name}] cloudscraper POST: {exc}')
        return self.session.post(url, data=data, timeout=25,
                                 headers={'X-Requested-With': 'XMLHttpRequest',
                                          'Referer': self.page_url})

    # ── Page parser ───────────────────────────────────────────────────────────

    def _parse_rows(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse div.tender_row blocks.

        Confirmed structure:
          div.tender_row > div.row
            div.col-sm-9
              h2 > a[title=entity, href=detail_url]
              a.m-notice-text[title=tender_title]
                p.workDesc → tender title
            div.col-sm-3.desc > ul
              li.price  → tender value
              li.dd     → "13 Jul 2026"
              li.state  → "Western Asia"
              li.idno   → "111733116"
        """
        rows  = soup.find_all('div', class_='tender_row')
        items = []
        for row in rows:
            item = self._parse_row(row)
            if item:
                items.append(item)
        return items

    def _parse_row(self, row) -> Optional[TenderData]:
        """Parse one div.tender_row into TenderData."""
        # ── Entity + Detail URL ───────────────────────────────────────────────
        h2 = row.find('h2')
        if not h2:
            return None
        a_h2 = h2.find('a', href=True)
        if not a_h2:
            return None

        entity = (a_h2.get('title') or a_h2.get_text(strip=True)).strip()
        # Strip trailing region label (e.g. ", Western Asia")
        entity = re.sub(r',\s*(Western Asia|Eastern Africa|Africa|Asia)\s*$', '',
                        entity, flags=re.IGNORECASE).strip()

        href       = a_h2['href']
        source_url = (BASE_URL + href) if href.startswith('/') else href

        # ── Tender title ──────────────────────────────────────────────────────
        a_notice = row.find('a', class_='m-notice-text')
        if a_notice:
            title = (a_notice.get('title') or
                     a_notice.find('p', class_='workDesc') and
                     a_notice.find('p', class_='workDesc').get_text(strip=True) or
                     '').strip()
        else:
            title = ''

        if not title:
            p_desc = row.find('p', class_='workDesc')
            title  = p_desc.get_text(strip=True) if p_desc else ''

        if not title or len(title) < 5:
            return None

        # ── BDR reference number ──────────────────────────────────────────────
        # From li.idno text or from href: /latest-tenders/111733116$uuid
        bdr_no = ''
        li_idno = row.find('li', class_='idno')
        if li_idno:
            bdr_no = re.sub(r'[^\d]', '', li_idno.get_text(strip=True))
        if not bdr_no:
            m = re.search(r'/(\d+)\$', href)
            if m:
                bdr_no = m.group(1)

        ext_id = f'BD-{bdr_no}' if bdr_no else f'BD-{hash(title) & 0xFFFFFF:06x}'

        # ── Deadline ──────────────────────────────────────────────────────────
        deadline = None
        li_dd = row.find('li', class_='dd')
        if li_dd:
            dd_text = li_dd.get_text(strip=True)
            # Remove icon text "Closing Date" if any
            dd_text = re.sub(r'^[^0-9]*', '', dd_text).strip()
            deadline = _parse_date(dd_text)

        # ── Tender value ──────────────────────────────────────────────────────
        value_text = ''
        li_price = row.find('li', class_='price')
        if li_price:
            value_text = re.sub(r'[^\d,.]', '', li_price.get_text(strip=True)).strip()

        return TenderData(
            title               = title[:300],
            source_url          = source_url,
            external_id         = ext_id,
            reference_no        = bdr_no,
            description         = (
                f'{title}\n\nEntity: {entity}\n'
                f'BDR No: {bdr_no}\nValue: {value_text}\n'
                f'Source: BidDetail — {self.region_name}'
            ),
            entity              = entity,
            category            = 'OTHER',
            status              = 'OPEN',
            currency            = 'USD',
            country_code        = _resolve_country_from_text(entity + ' ' + title) if not self.default_country else self.default_country,
            publication_date    = None,
            submission_deadline = deadline,
        )


# ── Concrete scrapers ─────────────────────────────────────────────────────────

class BidDetailWesternAsiaScraper(BidDetailScraper):
    """BidDetail — Western Asia tenders."""
    region_slug     = 'western-asia'
    region_name     = 'BidDetail Western Asia'
    page_url        = 'https://www.biddetail.com/region/western-asia-tenders'
    default_country = ''    # multi-country — resolved per tender from entity text
    country_code    = 'ZZ'  # international
    source_name     = 'BidDetail Western Asia'
    base_url        = BASE_URL


class BidDetailEasternAfricaScraper(BidDetailScraper):
    """BidDetail — Eastern Africa tenders."""
    region_slug     = 'eastern-africa'
    region_name     = 'BidDetail Eastern Africa'
    page_url        = 'https://www.biddetail.com/region/eastern-africa-tenders'
    default_country = ''    # multi-country — resolved per tender from entity text
    country_code    = 'ZZ'  # international
    source_name     = 'BidDetail Eastern Africa'
    base_url        = BASE_URL
