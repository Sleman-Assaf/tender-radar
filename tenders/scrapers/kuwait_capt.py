# tenders/scrapers/kuwait_capt.py

import logging
import re
import time
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from django.conf import settings

from .base import BaseScraper, TenderData

logger = logging.getLogger('kuwait_capt')

BASE_URL = 'https://capt.gov.kw'
LIST_URL = f'{BASE_URL}/ar/tenders/opening-tenders/'
PROXY    = getattr(settings, 'SCRAPER_PROXY', 'http://172.10.0.90:3128')

HEADERS = {
    'User-Agent':                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language':           'ar,en-US;q=0.9,en;q=0.8',
    'Accept-Encoding':           'gzip, deflate, br',
    'Connection':                'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest':            'document',
    'Sec-Fetch-Mode':            'navigate',
    'Sec-Fetch-Site':            'none',
    'Sec-Fetch-User':            '?1',
    'Cache-Control':             'max-age=0',
    'Referer':                   BASE_URL + '/',
}

AR_MONTHS = {
    'يناير': 'January',    'فبراير': 'February',   'مارس': 'March',
    'إبريل': 'April',      'أبريل':  'April',       'مايو': 'May',
    'يونيو': 'June',       'يوليو':  'July',        'أغسطس': 'August',
    'سبتمبر': 'September', 'أكتوبر': 'October',     'نوفمبر': 'November',
    'ديسمبر': 'December',
}
DATE_FMTS = ('%d %B, %Y', '%B %d, %Y', '%d %B %Y', '%Y-%m-%d')


def _parse_ar_date(text: str) -> Optional[datetime.date]:
    if not text:
        return None
    text = text.strip()
    for ar, en in AR_MONTHS.items():
        text = text.replace(ar, en)
    text = text.replace('،', ',').strip()
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _extract_detail_field(detail_div, label: str) -> str:
    for ul in detail_div.select('ul'):
        items = ul.find_all('li')
        if len(items) >= 2 and items[0].get_text(strip=True) == label:
            return items[1].get_text(strip=True)
    return ''


class KuwaitCAPTScraper(BaseScraper):

    country_code = 'KW'
    source_name  = 'Kuwait CAPT'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(scraper_class='KuwaitCAPTScraper', is_active=True)
        except ScraperSource.DoesNotExist:
            print('  ✗ [CAPT] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(scraper_class='KuwaitCAPTScraper', is_active=True).first()

        self.source = source
        url_obj   = source.tendering_urls.filter(is_active=True).first()
        max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : KuwaitCAPTScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  PROXY   : {PROXY}')
        print(f'  PAGES   : up to {max_pages}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        all_results: list[TenderData] = []
        seen_ids:   set[str]          = set()

        for page in range(1, max_pages + 1):
            print(f'       › Page {page:>3}/{max_pages}', end='  ', flush=True)
            params = {} if page == 1 else {'page': page}

            try:
                html = self._fetch(LIST_URL, params)
            except Exception as exc:
                print(f'✗ {exc}')
                logger.error(f'[CAPT] page {page}: {exc}')
                break

            soup  = BeautifulSoup(html, 'html.parser')
            items = self._parse_page(soup)

            if not items:
                print('(empty — stopping)')
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

            last_page = self._get_last_page(soup)
            if last_page and page >= last_page:
                break
            if len(items) < 5:
                break

            time.sleep(self.delay)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(all_results)} relevant / {len(seen_ids)} scraped  ←  Kuwait CAPT')
        print(f'  {"═" * 58}\n')
        logger.info(f'[CAPT] scrape() complete — total={len(all_results)}')
        return all_results

    # ── HTTP — proxy-first, then curl_cffi, then cloudscraper ───────────────

    def _fetch(self, url: str, params: dict) -> str:
        """
        Try fetching in order:
          1. requests via corporate proxy (172.10.0.90:3128) — works per browser test
          2. curl_cffi with proxy
          3. cloudscraper with proxy
        """
        proxies = {'http': PROXY, 'https': PROXY}

        # ── 1. Plain requests through proxy ──────────────────────────────────
        try:
            import requests as req
            s = req.Session()
            s.headers.update(HEADERS)
            s.proxies  = proxies
            s.trust_env = False
            r = s.get(url, params=params, timeout=30)
            r.raise_for_status()
            logger.info(f'[CAPT] proxy requests OK — status {r.status_code}')
            return r.text
        except Exception as exc:
            logger.warning(f'[CAPT] proxy requests failed: {exc}')

        # ── 2. curl_cffi through proxy ────────────────────────────────────────
        try:
            from curl_cffi.requests import Session as CurlSession
            s = CurlSession(impersonate='chrome124')
            s.headers.update(HEADERS)
            r = s.get(url, params=params, timeout=30,
                      proxies={'http': PROXY, 'https': PROXY})
            r.raise_for_status()
            logger.info(f'[CAPT] curl_cffi+proxy OK — status {r.status_code}')
            return r.text
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f'[CAPT] curl_cffi+proxy failed: {exc}')

        # ── 3. cloudscraper through proxy ─────────────────────────────────────
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            s.headers.update(HEADERS)
            s.proxies   = proxies
            s.trust_env = False
            r = s.get(url, params=params, timeout=30)
            r.raise_for_status()
            logger.info(f'[CAPT] cloudscraper+proxy OK — status {r.status_code}')
            return r.text
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f'[CAPT] cloudscraper+proxy failed: {exc}')

        raise RuntimeError(f'[CAPT] All fetch strategies failed for {url}')

    # ── Page parser ──────────────────────────────────────────────────────────

    def _parse_page(self, soup) -> list[TenderData]:
        items = []
        tender_list = soup.find('div', class_='tenderslist') or soup
        boxes = tender_list.find_all('div', class_='content-box')

        i = 0
        while i < len(boxes):
            box = boxes[i]
            if 'tender-info' in box.get('class', []):
                i += 1
                continue
            detail = None
            if i + 1 < len(boxes):
                nxt = boxes[i + 1]
                if 'tender-info' in nxt.get('class', []):
                    detail = nxt
            item = self._parse_pair(box, detail)
            if item:
                items.append(item)
            i += 1
        return items

    def _parse_pair(self, summary, detail) -> Optional[TenderData]:
        tenderno_p = summary.find('p', class_='tenderno')
        if not tenderno_p:
            return None

        span = tenderno_p.find('span')
        pub_date_text = span.get_text(strip=True) if span else ''
        if span:
            span.extract()
        tender_no = tenderno_p.get_text(strip=True)
        if not tender_no:
            return None

        pub_date = _parse_ar_date(pub_date_text)

        wrapper = summary.find('div', class_='tender-detail')
        if not wrapper:
            return None

        orgname_p = wrapper.find('p', class_='orgname')
        entity    = orgname_p.get_text(strip=True) if orgname_p else ''

        title = ''
        if orgname_p:
            next_p = orgname_p.find_next_sibling('p')
            if next_p:
                title = next_p.get_text(strip=True)
        if not title:
            for p in wrapper.find_all('p'):
                if 'orgname' not in p.get('class', []):
                    title = p.get_text(strip=True)
                    break
        if not title:
            return None

        deadline = tender_type = price_text = ''
        if detail:
            deadline_text = _extract_detail_field(detail, 'اخر موعد للعطاء')
            deadline      = _parse_ar_date(deadline_text)
            tender_type   = _extract_detail_field(detail, 'النوع')
            price_text    = _extract_detail_field(detail, 'السعر')
            if not pub_date:
                pub_date = _parse_ar_date(_extract_detail_field(detail, 'تاريخ الطلب'))

        ext_id = re.sub(r'\s+', '', tender_no)[:100]

        return TenderData(
            title               = title[:300],
            source_url          = LIST_URL,
            external_id         = ext_id,
            reference_no        = tender_no,
            description         = (
                f'{title}\nالرقم: {tender_no}\nالجهة: {entity}\n'
                f'النوع: {tender_type}\nالسعر: {price_text}\n'
                f'المصدر: الجهاز المركزي للمناقصات العامة - الكويت'
            ),
            entity              = entity,
            category            = 'OTHER',
            status              = 'OPEN',
            currency            = 'KWD',
            country_code        = 'KW',
            publication_date    = pub_date,
            submission_deadline = deadline if isinstance(deadline, type(pub_date)) else None,
        )

    @staticmethod
    def _get_last_page(soup) -> Optional[int]:
        last_link = soup.find('li', class_='last')
        if last_link:
            a = last_link.find('a')
            if a:
                m = re.search(r'page=(\d+)', a.get('href', ''))
                if m:
                    return int(m.group(1))
        return None

    def parse_page(self, soup, tendering_url):
        return []
