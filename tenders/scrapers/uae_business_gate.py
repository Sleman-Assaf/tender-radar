# tenders/scrapers/uae_business_gate.py
# Path: TenderRadar/tenders/scrapers/uae_business_gate.py
"""
UAE Business Gate -- UAE Tenders & Business Gate
https://uaebusinessgate.ae/tenders/

WordPress + JetEngine listing. All data available in listing pages — no detail visits.

Per item (.jet-listing-grid__item):
  - Post ID     data-post-id attribute
  - Source URL  .jet-engine-listing-overlay-wrap[data-url]
  - Ref No      dyn_fields[0]  (e.g. 112411726)
  - Status      dyn_fields[1]  (Active / Closed)
  - Date        dyn_fields[2]  (Apr.30.2026)
  - Category    list_texts[0]  (Information Technology and Communication)
  - Title       list_texts[1]  (Corrigendum - Quotation Are Invited For...)

Pagination: /tenders/page/N/  --  total pages from data-pages on grid.
Uses Playwright (egress proxy blocks direct requests to this domain).
"""

import logging
import re
import time
from datetime import date, datetime
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL    = 'https://uaebusinessgate.ae'
LISTING_URL = 'https://uaebusinessgate.ae/tenders/'
MAX_PAGES   = 15
PAGE_DELAY  = 1.5


def _page_url(page: int) -> str:
    return LISTING_URL if page == 1 else f'{LISTING_URL}page/{page}/'


def _parse_date(text: str) -> Optional[date]:
    if not text:
        return None
    # Apr.30.2026 → Apr 30 2026
    text = re.sub(r'\.', ' ', text.strip()).strip()
    for fmt in ('%b %d %Y', '%B %d %Y', '%d %m %Y',
                '%d/%m/%Y', '%Y-%m-%d', '%Y %m %d'):
        try:
            return datetime.strptime(text[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _map_category(label: str) -> str:
    l = label.lower()
    if any(w in l for w in ('consult', 'advisory', 'audit', 'survey',
                             'review', 'design', 'study', 'inspection')):
        return 'CONSULTANCY'
    if any(w in l for w in ('construction', 'civil', 'infrastructure', 'road',
                             'building', 'works', 'maintenance', 'rehabilitation',
                             'sewage', 'plumbing', 'electrical', 'electromechanical',
                             'hvac', 'irrigation', 'aluminum', 'steel', 'drilling',
                             'lighting', 'carpet', 'glass', 'painting',
                             'waterproofing', 'demolition', 'solar', 'fire fighting',
                             'elevator')):
        return 'WORKS'
    if any(w in l for w in ('supply', 'goods', 'equipment', 'material',
                             'furniture', 'medical', 'pharmaceutical', 'food',
                             'water product', 'chemical', 'rubber', 'plastic',
                             'metal', 'wood', 'paper', 'stationery', 'vehicle',
                             'machinery', 'spare', 'laboratory', 'device',
                             'oil', 'gas', 'packaging', 'agricultural', 'clothes',
                             'textile', 'gifts', 'tanks', 'cable')):
        return 'GOODS'
    return 'SERVICES'


def _parse_item(item) -> Optional[TenderData]:
    post_id = item.get('data-post-id', '')
    if not post_id:
        return None

    overlay    = item.select_one('.jet-engine-listing-overlay-wrap')
    source_url = overlay.get('data-url', '') if overlay else ''
    if not source_url:
        btn        = item.select_one('a.elementor-button')
        source_url = btn.get('href', '') if btn else ''

    dyn = [el.get_text(strip=True)
           for el in item.select('.jet-listing-dynamic-field__content')]

    ref_no     = dyn[0] if len(dyn) > 0 else ''
    status_raw = dyn[1] if len(dyn) > 1 else ''
    date_raw   = dyn[2] if len(dyn) > 2 else ''

    status = ('CLOSED' if any(w in status_raw.lower()
                              for w in ('closed', 'expired', 'inactive'))
              else 'OPEN')

    submission_deadline = _parse_date(date_raw)
    if submission_deadline and submission_deadline < date.today():
        status = 'CLOSED'

    list_texts = [el.get_text(strip=True)
                  for el in item.select('.elementor-icon-list-text')
                  if el.get_text(strip=True)]

    category_label = list_texts[0] if len(list_texts) > 0 else ''
    title          = list_texts[1] if len(list_texts) > 1 else ''

    if not title:
        return None

    return TenderData(
        title               = title,
        source_url          = source_url or LISTING_URL,
        external_id         = post_id,
        reference_no        = ref_no[:100],
        entity              = 'UAE Business Gate',
        entity_ar           = 'بوابة الأعمال الإماراتية',
        description         = (f'Category: {category_label} | Status: {status_raw}'
                               if category_label else title),
        category            = _map_category(category_label),
        status              = status,
        submission_deadline = submission_deadline,
        country_code        = 'AE',
    )


class UAEBusinessGateScraper(BaseScraper):
    """
    Scraper for UAE Business Gate tenders listing.
    Parses all info from the listing pages -- no detail page visits needed.
    Requires Playwright (domain blocked by egress proxy for plain requests).
    """

    source_name  = 'UAE Business Gate -- UAE Tenders & Business Gate'
    country_code = 'AE'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error('[UAEBusinessGate] Playwright not installed. '
                         'Run: pip install playwright && playwright install chromium')
            return []

        results:  list[TenderData] = []
        seen_ids: set[str]         = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx     = browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
                locale='en-US',
            )
            page = ctx.new_page()

            try:
                max_pages = MAX_PAGES

                for page_num in range(1, max_pages + 1):
                    url = _page_url(page_num)
                    logger.info(f'[UAEBusinessGate] Page {page_num}/{max_pages}: {url}')

                    try:
                        resp = page.goto(url, wait_until='domcontentloaded', timeout=30_000)
                        if resp and resp.status == 404:
                            logger.info(f'[UAEBusinessGate] 404 on page {page_num} — done')
                            break
                    except Exception as exc:
                        logger.error(f'[UAEBusinessGate] Load error p{page_num}: {exc}')
                        break

                    try:
                        page.wait_for_selector('.jet-listing-grid__item', timeout=10_000)
                    except Exception:
                        logger.warning(f'[UAEBusinessGate] No grid items on page {page_num}')
                        break

                    # Page 1: read total pages from data-pages attribute
                    if page_num == 1:
                        grid = page.query_selector('.jet-listing-grid__items')
                        if grid:
                            try:
                                total     = int(grid.get_attribute('data-pages') or 1)
                                max_pages = min(total, MAX_PAGES)
                                logger.info(
                                    f'[UAEBusinessGate] Site reports {total} pages, '
                                    f'capped to {max_pages}'
                                )
                            except (ValueError, TypeError):
                                pass

                    from bs4 import BeautifulSoup
                    soup  = BeautifulSoup(page.content(), 'html.parser')
                    items = soup.select('.jet-listing-grid__item')
                    logger.info(f'[UAEBusinessGate] Page {page_num}: {len(items)} items')

                    if not items:
                        break

                    new_count = 0
                    for item in items:
                        pid = item.get('data-post-id', '')
                        if pid in seen_ids:
                            continue
                        seen_ids.add(pid)
                        t = _parse_item(item)
                        if t:
                            results.append(t)
                            new_count += 1

                    logger.info(f'[UAEBusinessGate] Page {page_num}: {new_count} new tenders')

                    if page_num < max_pages:
                        time.sleep(PAGE_DELAY)

            finally:
                browser.close()

        logger.info(f'[UAEBusinessGate] Done — {len(results)} tenders total')
        return results
