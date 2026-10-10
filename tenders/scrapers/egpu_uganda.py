# tenders/scrapers/egpu_uganda.py
# Path: TenderRadar/tenders/scrapers/egpu_uganda.py
"""
Uganda — Electronic Government Procurement (EGPU)
Portal: https://egpuganda.go.ug/bid-notices

Table column mapping (from live page inspection):
  td[0]: Procuring Entity
          └─ a.text-default         → ref_no (first line)
          └─ div.text-muted         → entity name
          └─ a[href]                → detail URL (/index/{id}_egp)
  td[1]: span.badge                 → category/type
  td[2]: span.text-muted            → Subject of Procurement (TITLE)
  td[3]: span.text-success-600      → published date
  td[4]: span.text-success-600      → deadline
  td[5]: a.dropdown-item[0]         → detail link (same as td[0])

Pagination: ?page=N
"""

import logging
import random
import re
import time
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://egpuganda.go.ug'
LISTING_URL = 'https://egpuganda.go.ug/bid-notices'
MAX_PAGES   = 30

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;'
        'q=0.9,image/avif,image/webp,*/*;q=0.8'
    ),
    'Accept-Language':           'en-US,en;q=0.9',
    'Accept-Encoding':           'gzip, deflate, br',
    'Connection':                'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest':            'document',
    'Sec-Fetch-Mode':            'navigate',
    'Sec-Fetch-Site':            'none',
    'Sec-Fetch-User':            '?1',
}


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    val = text.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %B %Y', '%d %b %Y'):
        try:
            return datetime.strptime(val[:len(fmt)+2].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_rows(soup: BeautifulSoup, label: str) -> list[TenderData]:
    results = []

    table = soup.find('table')
    if not table:
        logger.warning(f'[EGPU] {label}: no table found')
        return results

    rows = table.find('tbody').find_all('tr') if table.find('tbody') else table.find_all('tr')[1:]
    logger.info(f'[EGPU] {label}: {len(rows)} rows')

    for row in rows:
        cells = row.find_all('td')
        if len(cells) < 5:
            continue

        # ── td[0]: Entity cell ───────────────────────────────────
        td0        = cells[0]
        link_tag   = td0.find('a', href=True)
        href       = link_tag['href'] if link_tag else ''
        detail_url = urljoin(BASE_URL, href) if href else LISTING_URL

        # ref_no: first text node of the <a> (before the nested div)
        ref_no = ''
        if link_tag:
            # Get direct text children only (exclude nested div text)
            ref_no = ''.join(
                t for t in link_tag.strings
                if t.parent == link_tag or t.parent.name not in ('div', 'span')
            ).strip()
            # fallback: full link text first line
            if not ref_no:
                ref_no = link_tag.get_text(separator='\n', strip=True).split('\n')[0]

        # entity: text inside div.text-muted inside td[0]
        entity_div = td0.find('div', class_=re.compile(r'text-muted'))
        entity     = entity_div.get_text(strip=True) if entity_div else ''

        # external_id from URL: /index/373422190_egp  or  /bid-notices/12345
        id_match = re.search(r'/index/(\d+)_egp|/bid-notices/(\d+)', href)
        ext_id   = (id_match.group(1) or id_match.group(2)) if id_match else href.strip('/')

        # ── td[1]: Category/Type ─────────────────────────────────
        td1      = cells[1]
        cat_span = td1.find('span', class_='badge')
        category = cat_span.get_text(strip=True) if cat_span else td1.get_text(strip=True)

        # ── td[2]: Subject of Procurement (TITLE) ────────────────
        td2       = cells[2]
        subj_span = td2.find('span', class_=re.compile(r'text-muted'))
        title     = subj_span.get_text(strip=True) if subj_span else td2.get_text(strip=True)
        # Strip trailing ellipsis
        if title.endswith('...'):
            title = title[:-3].strip()

        # ── td[3]: Published date ─────────────────────────────────
        td3      = cells[3]
        pub_span = td3.find('span')
        pub_date = _parse_date(pub_span.get_text(strip=True) if pub_span else td3.get_text(strip=True))

        # ── td[4]: Deadline ───────────────────────────────────────
        td4      = cells[4]
        dl_span  = td4.find('span')
        deadline = _parse_date(dl_span.get_text(strip=True) if dl_span else td4.get_text(strip=True))

        if not title:
            continue

        results.append(TenderData(
            title               = title,
            title_ar            = '',
            source_url          = detail_url,
            external_id         = ext_id or detail_url[-80:],
            reference_no        = ref_no[:100],
            entity              = entity,
            entity_ar           = '',
            description         = f'{ref_no} — {title}' if ref_no else title,
            description_ar      = '',
            category            = 'OTHER',
            status              = 'OPEN',
            publication_date    = pub_date,
            submission_deadline = deadline,
            country_code        = 'UG',
        ))

    return results


def _get_max_page(soup: BeautifulSoup) -> int:
    max_p = 1
    for a in soup.find_all('a', href=True):
        m = re.search(r'[?&]page=(\d+)', a['href'])
        if m:
            max_p = max(max_p, int(m.group(1)))
    for a in soup.select('.pagination a, .page-item a'):
        t = a.get_text(strip=True)
        if t.isdigit():
            max_p = max(max_p, int(t))
    return max_p


class EGPUUgandaScraper(BaseScraper):
    """
    Uganda EGPU — Electronic Government Procurement.
    Scrapes Procurement Notices tab at /bid-notices.

    Note: Site uses Cloudflare WAF — must run from your production server.
    """

    source_name  = 'EGPU Uganda'
    country_code = 'UG'
    base_url     = BASE_URL
    MAX_PAGES    = MAX_PAGES

    def scrape(self) -> list[TenderData]:
        session = requests.Session()
        session.headers.clear()

        logger.info(f'[EGPU] Starting — {LISTING_URL}')

        all_results: list[TenderData] = []
        seen_ids:    set[str]         = set()
        max_pages    = 1
        page         = 1

        while page <= min(max_pages, self.MAX_PAGES):
            url = f'{LISTING_URL}?page={page}' if page > 1 else LISTING_URL

            hdrs = dict(_HEADERS)
            if page > 1:
                hdrs['Referer']          = f'{LISTING_URL}?page={page - 1}'
                hdrs['Sec-Fetch-Site']   = 'same-origin'
                hdrs['Sec-Fetch-User']   = None  # not set on pagination clicks

            try:
                r = session.get(url, headers={k: v for k, v in hdrs.items() if v}, timeout=30)
                r.raise_for_status()
                logger.info(f'[EGPU] p{page}: {r.status_code} ({len(r.content)}b)')
            except requests.RequestException as exc:
                logger.error(f'[EGPU] p{page} failed: {exc}')
                break

            soup = BeautifulSoup(r.text, 'html.parser')

            if page == 1:
                max_pages = _get_max_page(soup)
                logger.info(f'[EGPU] Detected {max_pages} pages')

            items = _parse_rows(soup, f'p{page}')
            added = 0
            for item in items:
                if item.external_id not in seen_ids:
                    seen_ids.add(item.external_id)
                    all_results.append(item)
                    added += 1

            logger.info(
                f'[EGPU] p{page}/{max_pages}: {len(items)} parsed, '
                f'{added} new (total={len(all_results)})'
            )

            if not items:
                logger.info('[EGPU] Empty page — stopping')
                break

            page += 1
            if page <= max_pages:
                time.sleep(random.uniform(1.5, 2.5))

        logger.info(f'[EGPU] Done — {len(all_results)} tenders')
        return all_results
