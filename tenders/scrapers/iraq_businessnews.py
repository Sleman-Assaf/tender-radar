# tenders/scrapers/iraq_businessnews.py
"""
Iraq Business News — Tenders scraper
Source: https://www.iraq-businessnews.com/tenders/

─────────────────────────────────────────────────────────────────────────────
Confirmed structure (from actual HTML)
─────────────────────────────────────────────────────────────────────────────
Single page — all tenders in one HTML table, no pagination.

Table: <table cellspacing="0" cellpadding="0" style="font-size:12px;width:595px">
Header row: Date Added | Description | Company/Ministry | Deadline

Data rows:
    td[0] Date Added  → "25th April 2026"
    td[1] Description → <a href="https://www.iraq-businessnews.com/Tenders/solar-power-energy/">
                            Solar Power Energy
                        </a>
    td[2] Entity      → "United Nations Children's Fund (UNICEF)"
    td[3] Deadline    → "17th May 2026" or "TBC"

External ID: last URL slug — e.g. "solar-power-energy"
Detail URL:  full href from link
Date format: "25th April 2026" — strip ordinal suffix (st/nd/rd/th)
Country:     IQ (Iraq) — all tenders are Iraq-based
"""

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.iraq-businessnews.com'
LIST_URL = f'{BASE_URL}/tenders/'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         BASE_URL,
}

DATE_FMTS = ('%d %B %Y', '%d %b %Y', '%Y-%m-%d', '%d/%m/%Y')


def _parse_date(text: str) -> Optional[datetime.date]:
    """Parse dates like '25th April 2026' — strips ordinal suffix first."""
    text = (text or '').strip()
    if not text or text.upper() in ('TBC', 'N/A', '-', ''):
        return None
    # Strip ordinal suffixes: 1st → 1, 2nd → 2, 3rd → 3, 4th → 4, etc.
    text = re.sub(r'(\d+)(st|nd|rd|th)\b', r'\1', text, flags=re.I)
    # Remove trailing punctuation
    text = text.rstrip('.')
    text = re.sub(r'\s+', ' ', text).strip()
    for fmt in DATE_FMTS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class IraqBusinessNewsScraper(BaseScraper):
    """
    Scraper for Iraq Business News tenders page.

    Single-page scraper — all tenders are listed in one HTML table
    with no pagination. Typically ~80-100 active tenders.

    TenderingURL in DB:
        url              = https://www.iraq-businessnews.com/tenders/
        pagination_style = SINGLE
        max_pages        = 1
    """

    country_code = 'IQ'
    source_name  = 'Iraq Business News'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='IraqBusinessNewsScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗ [IraqBN] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='IraqBusinessNewsScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : IraqBusinessNewsScraper')
        print(f'  URL     : {LIST_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        print('  › Loading tenders page …', end=' ', flush=True)
        try:
            resp = self.get(LIST_URL)
        except Exception as exc:
            print(f'✗ {exc}')
            logger.error(f'[IraqBN] GET failed: {exc}')
            return []

        soup  = BeautifulSoup(resp.text, 'html.parser')
        items = self._parse_table(soup)

        print(f'{len(items)} rows found')
        print(f'  [DEBUG] Response length : {len(resp.text):,} chars')
        if items:
            s = items[0]
            print(f'  [DEBUG] Sample title    : {s.title[:60]!r}')
            print(f'  [DEBUG] Sample ext_id   : {s.external_id!r}')
            print(f'  [DEBUG] Sample entity   : {s.entity[:50]!r}')
            print(f'  [DEBUG] Sample pub_date : {s.publication_date!r}')
            print(f'  [DEBUG] Sample deadline : {s.submission_deadline!r}')
        print()

        results: list[TenderData] = []
        for item in items:
            combined = f'{item.title} {item.entity} {item.description}'
            if self.detect_relevance(combined):
                item.category = self.detect_category(combined)
                results.append(item)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant / {len(items)} total  ←  Iraq BN')
        print(f'  {"═" * 58}\n')
        logger.info(f'[IraqBN] scrape() complete — total={len(results)}')
        return results

    # ── Table parser ──────────────────────────────────────────────────────────

    def _parse_table(self, soup: BeautifulSoup) -> list[TenderData]:
        """
        Parse the single tenders table.

        Identifies the correct table by its inline style width:595px,
        then parses each data row (skips the header row).

        Columns: Date Added | Description (link) | Company/Ministry | Deadline
        """
        # Find the tenders table — identified by its width style
        table = soup.find(
            'table',
            style=lambda s: s and '595px' in s
        )
        if not table:
            # Fallback: find table containing "Date Added" header
            for t in soup.find_all('table'):
                if 'Date Added' in t.get_text():
                    table = t
                    break

        if not table:
            logger.warning('[IraqBN] tenders table not found')
            return []

        items: list[TenderData] = []

        for row in table.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 4:
                continue   # skip header row (uses <th>) and short rows

            # ── Date Added ────────────────────────────────────────────────────
            pub_date = _parse_date(cells[0].get_text(strip=True))

            # ── Title + link ──────────────────────────────────────────────────
            link = cells[1].find('a')
            if not link:
                continue
            title      = link.get_text(strip=True)[:300]
            href       = link.get('href', '').strip()
            source_url = href if href.startswith('http') else urljoin(BASE_URL, href)

            # External ID: last non-empty slug segment
            slug_parts = [p for p in href.rstrip('/').split('/') if p]
            ext_id     = slug_parts[-1] if slug_parts else ''
            if not ext_id:
                import hashlib
                ext_id = hashlib.md5(title.encode()).hexdigest()[:12]

            # ── Entity ────────────────────────────────────────────────────────
            entity = cells[2].get_text(strip=True)

            # ── Deadline ──────────────────────────────────────────────────────
            deadline = _parse_date(cells[3].get_text(strip=True))

            items.append(TenderData(
                title               = title,
                source_url          = source_url,
                external_id         = ext_id,
                reference_no        = ext_id,
                description         = f'{title}\n\nEntity: {entity}\nSource: Iraq Business News',
                entity              = entity,
                category            = 'OTHER',     # refined by detect_category()
                status              = 'OPEN',
                currency            = 'IQD',
                country_code        = 'IQ',
                publication_date    = pub_date,
                submission_deadline = deadline,
            ))

        return items
