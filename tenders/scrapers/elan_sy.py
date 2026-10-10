# tenders/scrapers/elan_sy.py

import logging
import re
import time
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('elan_sy')

BASE_URL = 'http://elan.gov.sy'
LIST_URL = f'{BASE_URL}/2017/site/arabic/index.php'

HEADERS = {
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ar,en;q=0.9',
    'Referer':         f'{BASE_URL}/2017/site/arabic/index.php?node=343',
}


def _parse_date(text: str) -> Optional[datetime.date]:
    if not text:
        return None
    text = text.strip()
    # skip zero dates like 0000-00-00
    if text.startswith('0000'):
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


class ElanSYScraper(BaseScraper):
    """
    Scraper for elan.gov.sy — Syrian Official Tenders Portal.

    HTML structure (confirmed from live page):
      Each tender = one <table> with bgcolor="#282461" header row containing:
        td[1] = العدد label
        td[2] = issue number  e.g. "15813/2026"
        td[3] = title (inside <div id='BTxxxxxx'>)
        td[4] = رقم المناقصة label
        td[5] = tender ref number

      Next <tr bgcolor="#FFFFD4"> = metadata row with nested table:
        الجهة | المدينة | التصنيف | النوع

      Next <tr bgcolor="#ffffff"> = detail body with:
        "تاريخ البدء: YYYY-MM-DD" and "تاريخ الانتهاء: YYYY-MM-DD"
        full description text in <div id='Bxxxxxx'>

    Pagination:
      Links in the format:
        index.php?node=343&Last=87361&CurrentPage=N&First=...
      Last= is extracted from page 1 links and reused.
    """

    country_code = 'SY'
    source_name  = 'Elan Syria'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(scraper_class='ElanSYScraper', is_active=True)
        except ScraperSource.DoesNotExist:
            print('  ✗ [ElanSY] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(scraper_class='ElanSYScraper', is_active=True).first()

        self.source = source
        self.session.headers.update(HEADERS)

        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : ElanSYScraper')
        print(f'  URL     : {LIST_URL}?node=343')
        print(f'  PAGES   : up to {max_pages}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()
        last_id      = 0

        for page_idx in range(max_pages):
            print(f'       › Page {page_idx + 1:>3}/{max_pages}', end='  ', flush=True)

            params = {
                'node':        343,
                'Last':        last_id,
                'CurrentPage': page_idx,
                'First':       page_idx * 10,
                'RID':         -1,
                'Vld':         '',
                'Cat':         '',
                'City':        '',
                'Loc1':        '',
                'Key1':        '',
                'SDate':       '',
                'EDate':       '',
                'Year':        '',
                'or':          '',
                'Country':     '',
                'Num':         '',
                'Dep':         -1,
            }

            try:
                resp = self.get(LIST_URL, params=params)
                resp.encoding = 'windows-1256'  # site uses windows-1256
                html = resp.text
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[ElanSY] page {page_idx + 1}: {exc}')
                break

            soup  = BeautifulSoup(html, 'html.parser')
            items = self._parse_page(soup)

            if not items:
                print('(empty — stopping)')
                break

            # Extract Last= from pagination links on first page
            if last_id == 0:
                last_id = self._extract_last_id(soup)
                if last_id:
                    print(f'[Last={last_id}] ', end='', flush=True)

            matched = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    combined = f'{item.title} {item.description} {item.entity}'
                    if self.detect_relevance(combined):
                        item.category = self.detect_category(combined)
                        all_results.append(item)
                        matched += 1

            print(f'→ {len(items)} rows, {matched} relevant')

            if len(items) < 8:
                break

            time.sleep(self.delay)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  Elan Syria')
        print(f'  {"═" * 58}\n')
        logger.info(f'[ElanSY] scrape() complete — total={len(all_results)}')
        return all_results

    def _parse_page(self, soup: BeautifulSoup) -> list[TenderData]:
        items = []
        # Each tender is a standalone <table> with a dark header row (#282461)
        for table in soup.find_all('table', recursive=True):
            header_row = table.find('tr', attrs={'bgcolor': '#282461'})
            if not header_row:
                continue
            item = self._parse_tender_table(table, header_row)
            if item:
                items.append(item)
        return items

    def _parse_tender_table(self, table, header_row) -> Optional[TenderData]:
        tds = header_row.find_all('td')
        if len(tds) < 5:
            return None

        # td[2] = issue number (العدد value)
        issue_no = tds[1].get_text(strip=True)  # e.g. "15813/2026"

        # td[3] = title (inside div BT...)
        title_td  = tds[2]
        title_div = title_td.find('div', id=re.compile(r'^BT'))
        title     = title_div.get_text(strip=True) if title_div else title_td.get_text(strip=True)

        # td[5] = رقم المناقصة (ref number)
        ref_no = tds[4].get_text(strip=True) if len(tds) > 4 else ''

        if not title or not ref_no:
            return None

        # Metadata row (yellow #FFFFD4)
        entity = city = category_raw = tender_type = ''
        meta_row = table.find('tr', attrs={'bgcolor': '#FFFFD4'})
        if meta_row:
            meta_tds = meta_row.find_all('td')
            meta_text = ' '.join(td.get_text(strip=True) for td in meta_tds)
            # extract field values from label-value pairs
            inner_table = meta_row.find('table')
            if inner_table:
                inner_tds = inner_table.find_all('td')
                for i, td in enumerate(inner_tds):
                    t = td.get_text(strip=True)
                    if 'الجهة' in t and i + 1 < len(inner_tds):
                        entity = inner_tds[i + 1].get_text(strip=True)
                    elif 'المدينة' in t and i + 1 < len(inner_tds):
                        city = inner_tds[i + 1].get_text(strip=True)
                    elif 'التصنيف' in t and i + 1 < len(inner_tds):
                        category_raw = inner_tds[i + 1].get_text(strip=True)
                    elif 'النوع' in t:
                        strong = td.find('strong')
                        if strong:
                            tender_type = strong.get_text(strip=True)

        # Detail row (white #ffffff) — contains dates and description
        pub_date = deadline = None
        description = ''
        detail_row = table.find('tr', attrs={'bgcolor': '#ffffff'})
        if detail_row:
            detail_text = detail_row.get_text(' ', strip=True)
            # dates
            pub_m = re.search(r'تاريخ البدء[:\s]*(\d{4}-\d{2}-\d{2})', detail_text)
            end_m = re.search(r'تاريخ الانتهاء[:\s]*(\d{4}-\d{2}-\d{2})', detail_text)
            if pub_m:
                pub_date = _parse_date(pub_m.group(1))
            if end_m:
                deadline = _parse_date(end_m.group(1))

            # description from div Bxxxxxx
            desc_div = detail_row.find('div', id=re.compile(r'^B\d'))
            if desc_div:
                description = re.sub(r'\s+', ' ', desc_div.get_text(' ', strip=True))[:2000]
            else:
                description = re.sub(r'\s+', ' ', detail_text)[:2000]

        # Build source URL linking directly to this tender's issue
        source_url = (
            f'{LIST_URL}?node=343&RID={ref_no}'
            if ref_no.isdigit()
            else f'{LIST_URL}?node=343'
        )

        ext_id = f'elan-sy-{ref_no}'

        return TenderData(
            title               = title[:300],
            title_ar            = title[:300],
            description         = description,
            description_ar      = description,
            entity              = entity or 'جهة سورية',
            entity_ar           = entity or 'جهة سورية',
            reference_no        = f'{issue_no} / {ref_no}' if issue_no else ref_no,
            external_id         = ext_id,
            source_url          = source_url,
            category            = 'OTHER',
            status              = 'OPEN',
            country_code        = 'SY',
            publication_date    = pub_date,
            submission_deadline = deadline,
        )

    @staticmethod
    def _extract_last_id(soup: BeautifulSoup) -> int:
        for a in soup.find_all('a', href=True):
            m = re.search(r'Last=(\d+)', a['href'])
            if m:
                val = int(m.group(1))
                if val > 0:
                    return val
        return 0

    def parse_page(self, soup, tendering_url):
        return []
