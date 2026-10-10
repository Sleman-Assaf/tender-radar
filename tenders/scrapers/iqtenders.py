# tenders/scrapers/iqtenders.py
"""
IQTenders scraper — Iraq tenders portal (www.iqtenders.com).

Identical platform to TenderJO (same ASP.NET backend, same JSON API):
    GET /api/HomePage/GetValue
        ?id=1 &catidvalue=0 &IDFrom=0 &IDTo=15
        &startDate=YYYY-MM-DD &endDate=YYYY-MM-DD
        &Country_Code=iq &ComeFrom=2

Strategy:
    - catidvalue=0  → ALL categories (not just consultancy)
    - startDate = endDate = today → only tenders published TODAY
    - Paginate until no more results
    - Run once/day via scheduled task for freshness

This avoids scraping 97k+ historical tenders and keeps the feed current.
"""

import time
import logging
from datetime import date

import requests

from .base    import BaseScraper, TenderData
from .tenderjo import TenderJOScraper, PAGE_SIZE

logger = logging.getLogger('tenders')


class IQTendersScraper(TenderJOScraper):
    """
    Iraq tenders scraper — today's tenders across ALL categories.

    Reuses TenderJO JSON API and _parse_item() logic.
    Overrides scrape() to:
      1. Use BASE_URL = iqtenders.com
      2. Fetch catidvalue=0 (all categories)
      3. Filter by startDate=endDate=today
    """

    source_name = 'IQTenders'
    BASE_URL    = 'https://www.iqtenders.com'

    HEADERS = {
        **TenderJOScraper.HEADERS,
        'Referer': 'https://www.iqtenders.com/TendersSearch.aspx?country=iq&lang=ar',
    }

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='IQTendersScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [IQTenders] ScraperSource not configured — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='IQTendersScraper', is_active=True
            ).first()

        self.source = source
        self.session.headers.update(self.HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        today     = date.today().strftime('%Y-%m-%d')
        url_obj   = source.tendering_urls.filter(is_active=True).first()

        # Scraping mode: ALL = no date restriction + unlimited pages
        try:
            from tenders.services import scraping_mode_is_all
            _all_mode = scraping_mode_is_all()
        except Exception:
            _all_mode = False

        max_pages = url_obj.max_pages if (url_obj and url_obj.max_pages) else 10
        if _all_mode:
            start_date = ''      # no date filter in ALL mode
            end_date   = ''
        else:
            start_date = today
            end_date   = today

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : IQTendersScraper')
        print(f'  DATE    : {start_date or "ALL"} – {end_date or "ALL"}')
        print(f'  CAT     : 0 = ALL categories')
        print(f'  MAX PAG : {max_pages}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        results = self._scrape_today(start_date, end_date, max_pages)

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant tender(s)  ←  IQTenders')
        print(f'  {"═" * 58}\n')
        logger.info(f'[IQTenders] scrape() complete — total={len(results)}')
        return results

    def _scrape_today(self, start_date: str, end_date: str, max_pages: int) -> list[TenderData]:
        """Fetch tenders (catidvalue=0) paginated, optionally filtered by date."""
        results:  list[TenderData] = []
        seen_ids: set[str]         = set()
        id_from   = 0
        id_to     = PAGE_SIZE
        page      = 1

        url = f'{self.BASE_URL}/api/HomePage/GetValue'

        while page <= max_pages:
            params = {
                'id':             1,
                'Country_Code':   'iq',
                'catidvalue':     0,        # ALL categories
                'buyerid':        '',
                'cityid':         '',
                'tendertypeid':   '',
                'tenderstatusid': '',
                'sortbyid':       1,        # sort by date desc
                'tendernameid':   '',
                'IDFrom':         id_from,
                'IDTo':           id_to,
                'User':           0,
                'startDate':      start_date,
                'endDate':        end_date,
                'ComeFrom':       2,
            }

            print(f'       › Page {page:>3}: IDFrom={id_from} IDTo={id_to}',
                  end='  ', flush=True)

            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                items_raw = resp.json()
            except requests.RequestException as exc:
                print(f'✗ {exc}')
                logger.error(f'[IQTenders] page={page}: {exc}')
                break
            except Exception as exc:
                print(f'✗ parse error: {exc}')
                break

            if not items_raw:
                print('(no more data)')
                break

            matched = 0
            for raw in items_raw:
                ext_id = str(raw.get('TenderID') or raw.get('ID') or '')
                if ext_id in seen_ids:
                    continue
                seen_ids.add(ext_id)

                td = self._parse_item(raw)
                td.country_code = 'IQ'
                combined = f'{td.title} {td.description} {td.entity}'
                if self.detect_relevance(combined):
                    td.category = self.detect_category(combined)
                    results.append(td)
                    matched += 1

            total = int(items_raw[0].get('TenderCount', 0) or 0) if items_raw else 0
            print(f'→ {len(items_raw)} rows | {matched} relevant | total={total}')

            if id_to >= total or len(items_raw) < PAGE_SIZE:
                break

            id_from  = id_to + 1
            id_to   += PAGE_SIZE
            page    += 1
            time.sleep(self.delay)

        return results
