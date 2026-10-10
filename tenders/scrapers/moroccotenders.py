# tenders/scrapers/moroccotenders.py
"""
MoroccoTenders scraper — https://www.moroccotenders.com/tenders.php/N

Pagination:  path-based  /tenders.php/1  /tenders.php/2  …
Listing page HTML structure:
    <div class="tender-card">
        <a href="/tender/<slug>.php">
            <p class="tender-card-heading">Title text</p>
        </a>
        <div class="row tender-card-content">
            <p><i class="fa fa-file"></i> MAT Ref No.:&nbsp;&nbsp;139432323</p>
            <p><i class="fa fa-clock"></i> Deadline:&nbsp;&nbsp;23 Apr 2026</p>
            <a class="Viewbutton pull-right" href="/tender/<slug>.php">View Details</a>
        </div>
    </div>

Each page shows 10 tenders.  Last page number is read from pagination
on first page so we stop exactly at the real last page.
"""

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.moroccotenders.com'
LIST_BASE = f'{BASE_URL}/tenders.php'
MAX_PAGES = 10          # safety cap — overridden by TenderingURL.max_pages

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}


class MoroccoTendersScraper(BaseScraper):
    """
    Scraper for MoroccoTenders.com — public/government tenders in Morocco.

    Uses the standard BaseScraper.scrape() → scrape_url() → parse_page() flow.
    TenderingURL in DB should be:
        url              = https://www.moroccotenders.com/tenders.php
        pagination_style = PATH   (builds /tenders.php/1, /tenders.php/2 …)
        max_pages        = 20  (or however many you want)
    """

    country_code = 'MA'
    source_name  = 'MoroccoTenders'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='MoroccoTendersScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [MoroccoTenders] ScraperSource not configured — add via seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='MoroccoTendersScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)
        return super().scrape()

    def scrape_url(self, tendering_url) -> list[TenderData]:
        """
        Override to build MoroccoTenders path URLs correctly:
            /tenders.php/1  /tenders.php/2  …  (no trailing slash)
        """
        results:  list[TenderData] = []
        max_pages = tendering_url.max_pages or MAX_PAGES
        base      = tendering_url.url.rstrip('/')  # https://…/tenders.php

        for page in range(1, max_pages + 1):
            page_url = f'{base}/{page}'
            print(f'       › Page {page:>3}: {page_url}', end='  ', flush=True)
            logger.info(f'[MoroccoTenders] Scraping page {page}: {page_url}')

            try:
                s     = self.soup(page_url)
                items = self.parse_page(s, tendering_url)

                if not items:
                    print('(no items — stopping)')
                    logger.info(f'[MoroccoTenders] Page {page}: no items, stopping')
                    break

                page_text = s.get_text(separator=' ', strip=True)

                if self.detect_relevance(page_text):
                    for item in items:
                        if not item.category or item.category == 'OTHER':
                            item.category = self.detect_category(
                                self._build_match_text(item, page_text)
                            )
                    results.extend(items)
                    print(f'→ {len(items)} items, all relevant (page hit)')
                    logger.info(f'[MoroccoTenders] Page {page}: {len(items)} taken')
                else:
                    print(f'→ {len(items)} items, 0 relevant (page not matched)')
                    logger.info(f'[MoroccoTenders] Page {page}: not relevant — skipped')

            except Exception as exc:
                print(f'✗ ERROR: {exc}')
                logger.error(f'[MoroccoTenders] Page {page} error: {exc}', exc_info=True)
                break

        return results

    # ── parse_page: called by BaseScraper.scrape_url() for each page ──────────

    def parse_page(self, soup: BeautifulSoup, tendering_url) -> list[TenderData]:
        """
        Parse one listing page of MoroccoTenders.

        Each tender card:
            <div class="tender-card">
                <a href="/tender/<slug>.php">
                    <p class="tender-card-heading">Title…</p>
                </a>
                <div class="row tender-card-content">
                    <p>… MAT Ref No.: 139432323</p>
                    <p>… Deadline:   23 Apr 2026</p>
                    <a class="Viewbutton …" href="/tender/<slug>.php">View Details</a>
                </div>
            </div>
        """
        cards = soup.select('div.tender-card')

        if not cards:
            # Debug hint on empty page
            page_preview = soup.get_text(separator=' ', strip=True)[:300]
            print(f'\n  [DEBUG] No .tender-card found. Page preview: {page_preview!r}\n')
            return []

        # ── Debug first page ──────────────────────────────────────────────────
        first_call = getattr(self, '_morocco_first_page_done', False)
        if not first_call:
            self._morocco_first_page_done = True
            print(f'\n  [DEBUG] Found {len(cards)} tender-card(s) on first page')
            if cards:
                sample = cards[0]
                title_el = sample.select_one('.tender-card-heading')
                link_el  = sample.select_one('a[href]')
                print(f'  [DEBUG] sample title : {title_el.get_text(strip=True)[:80] if title_el else "–"}')
                print(f'  [DEBUG] sample href  : {link_el["href"][:80] if link_el else "–"}')
            print()

        items: list[TenderData] = []

        for card in cards:
            # ── title ─────────────────────────────────────────────────────────
            title_el = card.select_one('.tender-card-heading')
            title    = title_el.get_text(strip=True) if title_el else ''
            if not title or len(title) < 5:
                continue

            # ── source URL from the detail link ───────────────────────────────
            detail_link = card.select_one('a.Viewbutton, a[href*="/tender/"]')
            if not detail_link:
                detail_link = card.find('a', href=re.compile(r'/tender/'))
            href       = detail_link.get('href', '') if detail_link else ''
            source_url = urljoin(BASE_URL, href) if href else BASE_URL

            # ── content row: ref no + deadline ────────────────────────────────
            content = card.select_one('.tender-card-content')
            ref_no  = ''
            deadline_raw = ''

            if content:
                for p in content.find_all('p'):
                    text = p.get_text(separator=' ', strip=True)
                    # "MAT Ref No.: 139432323"
                    ref_match = re.search(r'Ref\s+No\.\s*[:\s]\s*(\S+)', text, re.I)
                    if ref_match:
                        ref_no = ref_match.group(1).strip()
                    # "Deadline: 23 Apr 2026"
                    dl_match = re.search(r'Deadline\s*[:\s]\s*(.+)', text, re.I)
                    if dl_match:
                        deadline_raw = dl_match.group(1).strip()

            # ── external_id: use ref_no or slug from URL ───────────────────────
            ext_id = ref_no
            if not ext_id and href:
                slug_match = re.search(r'/tender/([^/]+)\.php', href)
                ext_id = slug_match.group(1)[-40:] if slug_match else href[-40:]

            items.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ref_no,
                description         = title,   # title is the full visible description
                category            = self.detect_category(title),
                status              = 'OPEN',
                currency            = 'MAD',   # Moroccan Dirham
                publication_date    = None,     # not shown on listing page
                submission_deadline = self._parse_date(deadline_raw),
                country_code        = 'MA',
            ))

        return items

    # ── Date parser ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(text: str) -> Optional[datetime.date]:
        """Parse dates like '23 Apr 2026', '04 May 2026'."""
        if not text:
            return None
        val = text.strip()
        for fmt, length in [
            ('%d %b %Y', 11),    # "23 Apr 2026"
            ('%d/%m/%Y', 10),
            ('%d-%m-%Y', 10),
            ('%Y-%m-%d', 10),
            ('%d %B %Y', None),  # "23 April 2026"
            ('%B %d, %Y', None),
        ]:
            try:
                sliced = val[:length] if length else val
                return datetime.strptime(sliced, fmt).date()
            except ValueError:
                continue
        return None
