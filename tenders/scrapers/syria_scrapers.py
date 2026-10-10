# tenders/scrapers/syria_scrapers.py
"""
Syria scraper — your-tender.com (Arabic interface)

URL    : https://your-tender.com/ar/tenders?page=1 → page=5
Method : HTML — card layout  div.card-grid-2.hover-up

Card structure (from live HTML):
  <div class="card-grid-2 hover-up">
    <div class="card-grid-2-image-left">
      <div class="right-info">
        <a class="name-job" href="...">اسم الجهة</a>   ← entity
      </div>
    </div>
    <div class="card-block-info">
      <h6><a href="/ar/tenders/SLUG">عنوان المناقصة</a></h6>
      <div class="mt-5">
        <span class="card-briefcase">التأهيل والبناء</span>  ← category
        <span class="card-time">Sat, Mar 28, 2026</span>    ← publish date
      </div>
      <p class="font-sm color-text-paragraph mt-15">وصف...</p>
    </div>
  </div>

Note: NO submission deadline shown on listing page — only publication date.
      The scraper stores it as publication_date; deadline left NULL so the
      future-filter in views.py keeps these tenders visible.
"""

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')


class SyriaTenderScraper(BaseScraper):

    country_code = 'SY'
    source_name  = 'SyriaTender'
    base_url     = 'https://your-tender.com'

    _LIST_URL  = 'https://your-tender.com/ar/tenders'
    _MAX_PAGES = 10

    _HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'ar,en;q=0.9',
        'Referer':         'https://your-tender.com/ar',
    }

    # ── Entry point ───────────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='SyriaTenderScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [SyriaTender] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SyriaTenderScraper', is_active=True
            ).first()

        self.session.headers.update(self._HEADERS)
        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : SyriaTenderScraper')
        print(f'  URL     : {self._LIST_URL}?page=1 → page={self._MAX_PAGES}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        results: list[TenderData] = []

        for page in range(1, self._MAX_PAGES + 1):
            print(f'       > page {page}', end='  ', flush=True)

            try:
                resp = self.get(self._LIST_URL, params={'page': page})
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
            except Exception as exc:
                print(f'✗ ERROR: {exc}')
                logger.error(f'[SyriaTender] page {page}: {exc}')
                break

            items = self._parse_page(soup)

            if not items:
                print('(empty — stopping)')
                break

            matched = 0
            for t in items:
                combined = f"{t.title} {t.entity or ''} {t.description or ''}"
                if self.detect_relevance(combined):
                    results.append(t)
                    matched += 1

            print(f'→ {len(items)} cards, {matched} relevant')

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant tender(s)  ←  SyriaTender')
        print(f'  {"═" * 58}\n')
        return results

    # ── Page parser — based on ACTUAL HTML structure ──────────────────────────
    def _parse_page(self, soup: BeautifulSoup) -> list[TenderData]:
        items = []

        # Each tender is in: <div class="card-grid-2 hover-up">
        cards = soup.select('div.card-grid-2.hover-up')

        for card in cards:
            # ── Title & source URL ─────────────────────────────────────────
            # <div class="card-block-info"><h6><a href="/ar/tenders/SLUG">TITLE</a>
            title_a = card.select_one('div.card-block-info h6 a')
            if not title_a:
                continue
            title      = title_a.get_text(strip=True)
            source_url = urljoin(self.base_url, title_a.get('href', ''))

            if not title or len(title) < 5:
                continue

            # ── Entity / publisher ─────────────────────────────────────────
            # <div class="right-info"><a class="name-job" href="...">الجهة</a>
            entity_a = card.select_one('div.right-info a.name-job')
            entity   = entity_a.get_text(strip=True) if entity_a else ''

            # ── Category ───────────────────────────────────────────────────
            # <span class="card-briefcase">التأهيل والبناء</span>
            cat_el   = card.select_one('span.card-briefcase')
            category_text = cat_el.get_text(strip=True) if cat_el else ''

            # ── Publication date ───────────────────────────────────────────
            # <span class="card-time">Sat, Mar 28, 2026</span>
            date_el  = card.select_one('span.card-time')
            pub_date = self._parse_date(date_el.get_text(strip=True) if date_el else '')

            # ── Description snippet ────────────────────────────────────────
            # <p class="font-sm color-text-paragraph mt-15">...</p>
            desc_el  = card.select_one('p.font-sm.color-text-paragraph')
            desc     = desc_el.get_text(strip=True)[:300] if desc_el else ''

            # ── Slug as external_id ────────────────────────────────────────
            slug = title_a.get('href', '').rstrip('/').split('/')[-1]

            items.append(TenderData(
                title               = title,
                source_url          = source_url,
                external_id         = slug,
                entity              = entity,
                description         = desc,
                status              = 'OPEN',
                currency            = 'SYP',
                category            = self.detect_category(
                                          f'{title} {category_text}'
                                      ),
                publication_date    = pub_date,
                # No deadline on listing — leave NULL so future-filter keeps it
                submission_deadline = None,
            ))

        return items

    # ── Date parser ───────────────────────────────────────────────────────────
    @staticmethod
    def _parse_date(text: str) -> Optional[datetime.date]:
        """
        Handles the format used by your-tender.com:
            'Sat, Mar 28, 2026'   ← card-time format
            'Sun, Mar 29, 2026'
        Also handles standard ISO formats as fallback.
        """
        if not text:
            return None
        val = text.strip()
        if not val:
            return None

        for fmt, length in [
            ('%a, %b %d, %Y', None),   # "Sat, Mar 28, 2026"
            ('%A, %B %d, %Y', None),   # "Saturday, March 28, 2026"
            ('%d %b %Y',      11),     # "28 Mar 2026"
            ('%Y-%m-%d',      10),
            ('%d/%m/%Y',      10),
            ('%d-%m-%Y',      10),
        ]:
            try:
                sliced = val[:length] if length else val
                return datetime.strptime(sliced, fmt).date()
            except ValueError:
                continue
        return None
