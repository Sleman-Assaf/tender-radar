# tenders/scrapers/mof_uae.py
# Path: TenderRadar/tenders/scrapers/mof_uae.py
"""
UAE Ministry of Finance — Current Business Opportunities
https://mof.gov.ae/en/public-finance/government-procurement/current-business-opportunities/

The page is a WordPress site. Tenders are published as WP posts in a specific
category. Each post body is a paragraph in Arabic announcing a tender with:
  - BPM number (e.g. BPM005427)
  - Subject/title (short description)
  - Closing date (e.g. 05/06/2026 or الجمعة الموافق 05/06/2026)
  - Issuing entity (Ministry of Finance, Climate Change Ministry, etc.)

We use the WP REST API to fetch posts, then parse the text.
Fallback: Playwright scrape of the rendered page.
"""

import logging
import re
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

PAGE_URL = 'https://mof.gov.ae/en/public-finance/government-procurement/current-business-opportunities/'
WP_API   = 'https://mof.gov.ae/wp-json/wp/v2/posts'

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'application/json, text/html, */*',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Referer':         'https://mof.gov.ae/',
}

# BPM reference number pattern
_BPM_RE = re.compile(r'BPM\d{6,}', re.IGNORECASE)

# Date patterns found in post text
# Arabic: تغلق / تقفل / يغلق ... 05/06/2026  or   05 / 06 / 2026
# English: closes on 05/06/2026 | closing date: 2026-06-05
_DATE_PATTERNS = [
    re.compile(r'(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})'),   # DD/MM/YYYY
    re.compile(r'(\d{4})-(\d{2})-(\d{2})'),                     # YYYY-MM-DD
    re.compile(r'(\d{1,2})-(\d{2})-(\d{4})'),                   # DD-MM-YYYY
]


class MOFUAEScraper(BaseScraper):
    """
    Scraper for UAE Ministry of Finance current business opportunities.
    Uses WordPress REST API first; falls back to Playwright.
    """

    source_name  = 'UAE Ministry of Finance — Current Business Opportunities'
    country_code = 'AE'
    base_url     = 'https://mof.gov.ae'

    def scrape(self) -> list[TenderData]:
        posts = self._fetch_posts_via_api()
        if not posts:
            posts = self._fetch_posts_via_playwright()
        if not posts:
            return []

        results = []
        for post in posts:
            tender = self._parse_post(post)
            if tender:
                results.append(tender)

        logger.info(f'[MOF-UAE] Done — {len(results)} tenders')
        return results

    # ── Data fetching ─────────────────────────────────────────────────────

    def _fetch_posts_via_api(self) -> list[dict]:
        """Fetch WordPress posts from REST API — up to 10 pages."""
        all_posts = []
        base_params = {
            'per_page': 20,
            'orderby':  'date',
            'order':    'desc',
            'status':   'publish',
            '_fields':  'id,date,title,content,link,excerpt',
        }
        for page_num in range(1, 11):
            params = {**base_params, 'page': page_num}
            try:
                r = requests.get(WP_API, params=params, headers=_HEADERS, timeout=20)
                if r.status_code == 400:
                    logger.info(f'[MOF-UAE] WP API page {page_num}: no more pages')
                    break
                if r.status_code != 200:
                    logger.info(f'[MOF-UAE] WP API page {page_num}: status {r.status_code} — stopping')
                    break
                data = r.json()
                if not isinstance(data, list) or not data:
                    logger.info(f'[MOF-UAE] WP API page {page_num}: empty — stopping')
                    break
                all_posts.extend(data)
                logger.info(f'[MOF-UAE] WP API page {page_num}: {len(data)} posts (total: {len(all_posts)})')
                if len(data) < base_params['per_page']:
                    break
            except Exception as exc:
                logger.info(f'[MOF-UAE] WP API page {page_num} error: {exc}')
                break

        if all_posts:
            logger.info(f'[MOF-UAE] WP API total: {len(all_posts)} posts')
            return all_posts

        logger.info('[MOF-UAE] WP API returned nothing — trying Playwright')
        return []

    def _fetch_posts_via_playwright(self) -> list[dict]:
        """Scrape rendered page with Playwright — paginate through up to 10 pages."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error('[MOF-UAE] Playwright not installed and API unavailable.')
            return []

        try:
            all_posts = []
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
                page = browser.new_page(user_agent=_HEADERS['User-Agent'])
                page.goto(PAGE_URL, wait_until='domcontentloaded', timeout=30_000)

                for page_num in range(1, 11):
                    try:
                        page.wait_for_selector('table.w-full tbody tr, .mof-dpp-list__item', timeout=8_000)
                    except Exception:
                        pass

                    html = page.content()
                    soup = BeautifulSoup(html, 'html.parser')

                    # Parse table rows: td cells contain rfq, entity, title, open date, close date
                    page_posts = []
                    for row in soup.select('table.w-full tbody tr'):
                        cells = row.find_all('td')
                        if len(cells) >= 5:
                            rfq    = cells[0].get_text(strip=True)
                            entity = cells[1].get_text(strip=True)
                            title  = cells[2].get_text(strip=True)
                            open_d = cells[3].get_text(strip=True)
                            close_d = cells[4].get_text(strip=True)
                            link_el = cells[5].find('a') if len(cells) > 5 else None
                            link = link_el['href'] if link_el else PAGE_URL
                            body = f'{title} | {entity} | Open: {open_d} | Close: {close_d}'
                            page_posts.append({
                                'id':      rfq,
                                'date':    open_d,
                                'title':   {'rendered': title},
                                'content': {'rendered': body},
                                'link':    link,
                                '_entity': entity,
                                '_close':  close_d,
                                '_rfq':    rfq,
                            })

                    # Fallback: BPM mentions in paragraphs
                    if not page_posts:
                        for el in soup.find_all(['p', 'li']):
                            text = el.get_text(' ', strip=True)
                            if _BPM_RE.search(text) and len(text) > 20:
                                page_posts.append({
                                    'id':      None,
                                    'date':    '',
                                    'title':   {'rendered': ''},
                                    'content': {'rendered': str(el)},
                                    'link':    PAGE_URL,
                                })

                    all_posts.extend(page_posts)
                    logger.info(f'[MOF-UAE] Playwright page {page_num}: {len(page_posts)} rows (total: {len(all_posts)})')

                    # Try to click "Next" page button
                    try:
                        next_btn = page.query_selector('a[data-page="{}"]'.format(page_num + 1))
                        if not next_btn:
                            logger.info(f'[MOF-UAE] Playwright: no more pages after {page_num}')
                            break
                        next_btn.click()
                        page.wait_for_timeout(1500)
                    except Exception:
                        break

                browser.close()

            logger.info(f'[MOF-UAE] Playwright total: {len(all_posts)} rows')
            return all_posts
        except Exception as exc:
            logger.error(f'[MOF-UAE] Playwright error: {exc}')
            return []

    # ── Parsing ───────────────────────────────────────────────────────────

    def _parse_post(self, post: dict) -> Optional[TenderData]:
        # ── Structured row from Playwright table ──────────────────────────
        if post.get('_rfq'):
            rfq    = post['_rfq'].strip()
            title  = (post.get('title', {}).get('rendered', '') or '').strip()
            entity = post.get('_entity', 'UAE Federal Government').strip()
            source_url = post.get('link', PAGE_URL) or PAGE_URL
            deadline   = self._extract_date(post.get('_close', ''))
            pub_date   = self._extract_date(post.get('date', ''))
            if not title or len(title) < 4:
                title = f'UAE MoF Tender {rfq}'
            return TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = rfq,
                reference_no        = rfq,
                entity              = entity[:200],
                entity_ar           = '',
                description         = '',
                category            = self._map_category(title),
                status              = 'OPEN',
                currency            = 'AED',
                publication_date    = pub_date,
                submission_deadline = deadline,
                country_code        = 'AE',
            )

        # ── WP post / BPM text-based parsing ─────────────────────────────
        raw_html = post.get('content', {}).get('rendered', '') or \
                   post.get('excerpt', {}).get('rendered', '')
        soup = BeautifulSoup(raw_html, 'html.parser')
        text = soup.get_text(' ', strip=True)

        bpm_match = _BPM_RE.search(text)
        if not bpm_match:
            return None
        bpm_ref = bpm_match.group(0).upper()

        wp_title = post.get('title', {}).get('rendered', '').strip()
        title = BeautifulSoup(wp_title, 'html.parser').get_text(strip=True) if wp_title else ''

        if not title or title.lower() in ('current business opportunities', ''):
            after_bpm = text[bpm_match.end():].strip()
            clean = re.sub(r'^[\s\u0600-\u06FF\s،,.:؟?!\-]+', '', after_bpm)
            title = re.split(r'[،؟\n.!]', clean[:200])[0].strip()

        if not title or len(title) < 5:
            title = f'UAE MoF Tender {bpm_ref}'

        entity = self._extract_entity(text)
        deadline = self._extract_date(text)

        pub_date = None
        raw_date = post.get('date', '')
        if raw_date:
            try:
                pub_date = datetime.fromisoformat(raw_date[:10]).date()
            except ValueError:
                pass

        source_url = post.get('link', PAGE_URL) or PAGE_URL

        return TenderData(
            title               = title[:300],
            source_url          = source_url,
            external_id         = bpm_ref,
            reference_no        = bpm_ref,
            entity              = entity,
            entity_ar           = '',
            description         = text[:500],
            category            = self._map_category(title),
            status              = 'OPEN',
            currency            = 'AED',
            publication_date    = pub_date,
            submission_deadline = deadline,
            country_code        = 'AE',
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_date(text: str) -> Optional[date]:
        """Extract the last/most relevant date from post text."""
        candidates = []
        for pat in _DATE_PATTERNS:
            for m in pat.finditer(text):
                groups = m.groups()
                try:
                    if len(groups[0]) == 4:
                        # YYYY-MM-DD
                        d = date(int(groups[0]), int(groups[1]), int(groups[2]))
                    else:
                        # DD/MM/YYYY or DD-MM-YYYY
                        d = date(int(groups[2]), int(groups[1]), int(groups[0]))
                    if 2024 <= d.year <= 2030:
                        candidates.append(d)
                except (ValueError, IndexError):
                    continue
        # Prefer the latest date (likely closing date, not publication)
        return max(candidates) if candidates else None

    @staticmethod
    def _extract_entity(text: str) -> str:
        """Try to identify which ministry published the tender."""
        patterns = [
            (r'وزارة المالية',           'UAE Ministry of Finance'),
            (r'Ministry of Finance',     'UAE Ministry of Finance'),
            (r'وزارة التغير المناخي',   'UAE Ministry of Climate Change & Environment'),
            (r'Climate Change',          'UAE Ministry of Climate Change & Environment'),
            (r'وزارة الطاقة',            'UAE Ministry of Energy & Infrastructure'),
            (r'Ministry of Energy',      'UAE Ministry of Energy & Infrastructure'),
            (r'وزارة الاقتصاد',          'UAE Ministry of Economy'),
            (r'Ministry of Economy',     'UAE Ministry of Economy'),
            (r'وزارة الصحة',             'UAE Ministry of Health & Prevention'),
            (r'Ministry of Health',      'UAE Ministry of Health & Prevention'),
            (r'وزارة الداخلية',          'UAE Ministry of Interior'),
            (r'Ministry of Interior',    'UAE Ministry of Interior'),
            (r'وزارة التعليم',           'UAE Ministry of Education'),
            (r'Ministry of Education',   'UAE Ministry of Education'),
        ]
        for pattern, label in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return label
        return 'UAE Federal Government'

    @staticmethod
    def _map_category(title: str) -> str:
        t = title.lower()
        if any(w in t for w in ('consult', 'advisory', 'study', 'review', 'assessment', 'تقييم')):
            return 'CONSULTANCY'
        if any(w in t for w in ('supply', 'purchase', 'توريد', 'شراء', 'equipment', 'hardware')):
            return 'GOODS'
        if any(w in t for w in ('install', 'construct', 'works', 'أعمال', 'إنشاء', 'تركيب')):
            return 'WORKS'
        return 'SERVICES'
