# tenders/scrapers/tenderuae.py
"""
TenderUAE scraper — https://www.tenderuae.com
UAE tenders portal — same ASP.NET platform family as TenderJO, but with a
slightly different GetValue contract (verified against a live capture):

    GET https://www.tenderuae.com/api/HomePage/GetValue
        ?id=1&Country_Code=ae&catidvalue=0&buyerid=&cityid=&tendertypeid=
        &tenderstatusid=1&sortbyid=0&tendernameid=&IDFrom=1&IDTo=30
        &User=0&startDate=&endDate=&ComeFrom=1

Differences vs TenderJO that this module handles:
  - Country_Code=ae            (not jo)
  - tenderstatusid=1           (active/new tenders — empty returns nothing)
  - sortbyid=0, ComeFrom=1     (site uses these values on the homepage feed)
  - IDFrom/IDTo are 1-indexed  (first page is IDFrom=1, IDTo=30)
  - Page size 30
  - English fields available   (tnd_name_en, tpb_company_name_en)
  - City in are_name           (Abu Dhabi / Dubai / Ajman / Federal Entities…)

Keyword/relevance/category helpers are inherited from TenderJOScraper.
"""

import time
import logging

import requests

from .base import TenderData
from .tenderjo import TenderJOScraper, CONSULTANCY_CATEGORIES

logger = logging.getLogger(__name__)

PAGE_SIZE         = 30   # matches the site's own IDFrom=1&IDTo=30 requests
MAX_PAGES_ALL     = 20   # all-categories relevance pass  (20 × 30 = 600)
MAX_PAGES_PER_CAT = 30   # per-AEC-category sweep


class TenderUAEScraper(TenderJOScraper):
    """
    UAE tenders via TenderUAE — inherits keyword logic from TenderJO,
    but uses TenderUAE's own request contract and field mapping.
    """

    source_name = 'TenderUAE'

    BASE_URL     = 'https://www.tenderuae.com'
    API_ENDPOINT = '/api/HomePage/GetValue'

    HEADERS = {
        **TenderJOScraper.HEADERS,
        'Referer': 'https://www.tenderuae.com/Default.aspx?lang=en&country=ae',
    }

    # ── Entry point ─────────────────────────────────────────────────────────

    def scrape(self):
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='TenderUAEScraper', is_active=True
            )
        except ScraperSource.DoesNotExist:
            print('  ✗  [TenderUAE] ScraperSource not configured')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='TenderUAEScraper', is_active=True
            ).first()

        self.session.headers.update(self.HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"─" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : TenderUAEScraper')
        print(f'  MODE    : JSON API — all categories + AEC sweep')
        print(f'  URL     : {self.BASE_URL}{self.API_ENDPOINT}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"─" * 62}')

        seen: set[str] = set()
        results: list[TenderData] = []

        def _add(items):
            for item in items:
                key = item.external_id or item.title
                if key not in seen:
                    seen.add(key)
                    results.append(item)

        # Pass 1 — all categories (catidvalue=0), per-tender relevance filter
        print(f'\n  ▶  All categories (relevance-filtered, max {MAX_PAGES_ALL} pages)')
        _add(self._fetch_uae_category(
            cat_id=0, filter_relevant=True, max_pages=MAX_PAGES_ALL
        ))

        # Pass 2 — AEC / consultancy categories, keep everything
        print(f'\n  ▶  AEC categories sweep ({len(CONSULTANCY_CATEGORIES)} categories)')
        for cat_id in CONSULTANCY_CATEGORIES:
            _add(self._fetch_uae_category(
                cat_id=cat_id, filter_relevant=False, max_pages=MAX_PAGES_PER_CAT
            ))

        print(f'\n  {"═" * 58}')
        print(f'  TOTAL : {len(results)} relevant tender(s)  ←  TenderUAE')
        print(f'  {"═" * 58}\n')
        logger.info(f'[TenderUAE] scrape() done — {len(results)} tenders')
        return results

    # ── Paginated fetcher (TenderUAE request contract) ──────────────────────

    def _fetch_uae_category(
        self,
        cat_id: int,
        filter_relevant: bool = True,
        max_pages: int = MAX_PAGES_PER_CAT,
    ) -> list[TenderData]:
        results: list[TenderData] = []
        id_from = 1                      # 1-indexed on TenderUAE
        id_to   = PAGE_SIZE
        page    = 1

        cat_label = f'cat={cat_id}' if cat_id else 'all-categories'

        while page <= max_pages:
            params = {
                'id':             1,
                'Country_Code':   'ae',
                'catidvalue':     cat_id,
                'buyerid':        '',
                'cityid':         '',
                'tendertypeid':   '',
                'tenderstatusid': 1,     # active/new — required, empty → no rows
                'sortbyid':       0,
                'tendernameid':   '',
                'IDFrom':         id_from,
                'IDTo':           id_to,
                'User':           0,
                'startDate':      '',
                'endDate':        '',
                'ComeFrom':       1,     # homepage feed value from live capture
            }

            print(
                f'     [{cat_label}] page {page:>3} '
                f'IDFrom={id_from} IDTo={id_to}',
                end='  ', flush=True
            )

            try:
                resp = self.session.get(
                    f'{self.BASE_URL}{self.API_ENDPOINT}',
                    params=params,
                    timeout=self.timeout,
                )
                resp.raise_for_status()

                try:
                    items_raw = resp.json()
                except ValueError:
                    print('✗ invalid JSON')
                    logger.error(f'[TenderUAE] {cat_label} page {page}: invalid JSON')
                    break

                if not items_raw:
                    print('(end)')
                    break

                total_count = 0
                try:
                    total_count = int(items_raw[0].get('TenderCount', 0) or 0)
                except (IndexError, TypeError, ValueError):
                    pass

                taken = 0
                for raw in items_raw:
                    tender = self._parse_uae_item(raw)
                    if filter_relevant:
                        combined = self._item_text(raw)
                        if not self.detect_relevance(combined):
                            continue
                    results.append(tender)
                    taken += 1

                print(f'→ {len(items_raw)} fetched, {taken} kept (total={total_count})')
                logger.info(
                    f'[TenderUAE] {cat_label} page {page}: '
                    f'fetched={len(items_raw)} kept={taken} total={total_count}'
                )

                if total_count and id_to >= total_count:
                    break
                if len(items_raw) < PAGE_SIZE:
                    break

                id_from += PAGE_SIZE
                id_to   += PAGE_SIZE
                page    += 1
                time.sleep(self.delay)

            except requests.RequestException as exc:
                print(f'✗ {exc}')
                logger.error(f'[TenderUAE] {cat_label} page {page} request error: {exc}')
                break
            except Exception as exc:
                print(f'✗ parse error: {exc}')
                logger.error(
                    f'[TenderUAE] {cat_label} page {page} error: {exc}',
                    exc_info=True,
                )
                break

        return results

    # ── Item parser (TenderUAE field mapping) ───────────────────────────────

    def _parse_uae_item(self, t: dict) -> TenderData:
        tdc_id     = str(t.get('tdc_id', '') or '')
        title_en   = (t.get('tnd_name_en', '') or '').strip()
        title_ar   = (t.get('tnd_name', '') or '').strip()
        entity_en  = (t.get('tpb_company_name_en', '') or '').strip()
        entity_ar  = (t.get('tpb_company_name', '') or '').strip()
        cat_name   = (t.get('cat_Name', '') or '').strip()
        cat_parent = (t.get('cat_ParentName', '') or '').strip()
        city       = (t.get('are_name', '') or '').strip()
        ad_type    = (t.get('tender_ad_type', '') or '').strip()

        pub_date = self._parse_date(t.get('tnd_publish_date'))       # dd/mm/yyyy
        end_date = self._parse_date(t.get('tnd_buy_tender_date'))    # dd/mm/yyyy

        combined = self._item_text(t)
        category = self.detect_category(combined) or 'ADV'
        status   = self._map_status(t.get('tnd_sts', ''))

        desc_bits = [b for b in (cat_parent, cat_name, city, ad_type) if b]
        description = ' · '.join(desc_bits)

        source_url = (
            f'{self.BASE_URL}/TenderDetails.aspx?tdc_id={tdc_id}&lang=en'
            if tdc_id else self.BASE_URL
        )

        return TenderData(
            title               = title_en or title_ar or cat_name or 'Untitled',
            title_ar            = title_ar or title_en or cat_name or '',
            description         = description,
            description_ar      = description,
            entity              = entity_en or entity_ar or city or 'TenderUAE',
            entity_ar           = entity_ar or entity_en or city or '',
            reference_no        = tdc_id,
            external_id         = f'tenderuae-{tdc_id}' if tdc_id else '',
            source_url          = source_url,
            category            = category,
            status              = status,
            publication_date    = pub_date,
            submission_deadline = end_date,
            country_code        = 'AE',
        )
