# tenders/scrapers/sytenders.py
"""
SyTenders scraper — Syria tenders portal (www.sytenders.com).

Identical ASP.NET/JSON platform to TenderJO.
Inherits all pagination, parsing, and relevance logic from TenderJOScraper.
Differences: BASE_URL, country code = sy, Referer header.
"""

from .tenderjo import TenderJOScraper, MAX_PAGES_PASS1


class SyTendersScraper(TenderJOScraper):

    source_name = 'SyTenders'
    BASE_URL    = 'https://www.sytenders.com'

    HEADERS = {
        **TenderJOScraper.HEADERS,
        'Referer': 'https://www.sytenders.com/Default.aspx?lang=ar&country=sy',
    }

    def scrape(self):
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='SyTendersScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [SyTenders] ScraperSource not configured — add via Admin')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SyTendersScraper', is_active=True
            ).first()

        self.session.headers.update(self.HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 64}')
        print(f'  SOURCE  : {source.name}')
        print(f'  URL     : {self.BASE_URL}/Default.aspx?lang=ar&country=sy')
        print(f'  MODE    : JSON API — all categories, max {MAX_PAGES_PASS1} pages')
        print(f'  Keywords: {rel_count} relevance [{rel_src}] | {cat_count} category [{cat_src}]')
        print(f'{"─" * 64}')

        seen_ids: set[str] = set()
        all_results = []

        print(f'\n  ▶  All categories (relevance-filtered, max {MAX_PAGES_PASS1} pages)')
        for item in self._fetch_category(
            cat_id=0,
            country='sy',
            filter_relevant=True,
            max_pages=MAX_PAGES_PASS1,
        ):
            # Override country code for Syria
            item.country_code = 'SY'
            key = item.external_id or item.title
            if key not in seen_ids:
                seen_ids.add(key)
                all_results.append(item)

        print(f'\n  {"═" * 60}')
        print(f'  TOTAL : {len(all_results)} tender(s)  ←  SyTenders')
        print(f'  {"═" * 60}\n')

        return all_results
