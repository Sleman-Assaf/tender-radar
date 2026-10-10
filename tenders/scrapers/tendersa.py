# tenders/scrapers/tendersa.py
"""
TenderSA scraper — Saudi Arabia tenders portal (www.tendersa.com).

Identical platform to TenderJO / IQTenders (same ASP.NET backend, same JSON API).
Only differences:
  - BASE_URL  → https://www.tendersa.com
  - source_name / scraper_class
  - Referer header updated

All pagination, relevance, and parsing logic inherited from TenderJOScraper.
"""

from .tenderjo import TenderJOScraper, CONSULTANCY_CATEGORIES


class TenderSAScraper(TenderJOScraper):
    """
    Saudi Arabia tenders scraper — reuses TenderJO logic completely.
    """

    source_name = 'TenderSA'

    BASE_URL = 'https://www.tendersa.com'

    HEADERS = {
        **TenderJOScraper.HEADERS,
        'Referer': (
            'https://www.tendersa.com/TendersSearch.aspx'
            '?CategoryID=35&country=sa&lang=ar'
        ),
    }

    def scrape(self):
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='TenderSAScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  X  [TenderSA] ScraperSource not configured — add via Admin')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='TenderSAScraper', is_active=True
            ).first()

        self.session.headers.update(self.HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : TenderSAScraper')
        print(f'  MODE    : JSON API  ({len(CONSULTANCY_CATEGORIES)} sub-categories)')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # The inherited fetcher is _fetch_category(); _scrape_category() never
        # existed, so every run died with AttributeError before fetching anything.
        #
        # country='sa' is not optional: the parameter defaults to 'jo', so calling
        # it without one would have quietly filled the Saudi source with Jordanian
        # tenders — a wrong answer is worse than the crash it replaces.
        #
        # filter_relevant=False because these are the AEC category IDs themselves;
        # the category *is* the filter, which is the contract _fetch_category
        # documents for an AEC-specific sweep.
        all_results = []
        for cat_id in CONSULTANCY_CATEGORIES:
            for item in self._fetch_category(cat_id=cat_id, country='sa',
                                             filter_relevant=False):
                item.country_code = 'SA'      # inherited parser defaults to JO
                all_results.append(item)

        # Deduplicate by external_id
        seen: set[str] = set()
        unique = []
        for item in all_results:
            key = item.external_id or item.title
            if key not in seen:
                seen.add(key)
                unique.append(item)

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(unique)} relevant tender(s)  <-  TenderSA')
        print(f'  {"=" * 58}\n')

        return unique
