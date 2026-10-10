# tenders/scrapers/saudi_etimad_supplier_templates.py
"""
Saudi Arabia — Etimad Supplier Templates (إعلانات قوائم الموردين)
HTML page: /AnnouncementSuppliersTemplate/AllSupplierAnnouncementSupplierTemplates

API endpoint (confirmed from JS source AllSupplierAnnouncementSupplierTemplates.js):
    GET /AnnouncementSuppliersTemplate/GetAllAnnouncementSupplierTemplatesForSupplier
        ?PageSize=10&Sort=PublishedDate&SortDirection=DESC&AnnouncementTabId=1

Detail URL (confirmed from JS detailsUrl() function):
    /AnnouncementSuppliersTemplate/AnnouncementSuppliersTemplateDetailsForSuppliers
        ?announcmentIdString=<announcementIdString>

The Vue app also uses a pagination component with resource_url set to:
    /AnnouncementSuppliersTemplate/GetAllAnnouncementSupplierTemplatesForSupplier

Query parameters supported (from JS search() method):
    AnnouncementName, ReferenceNumber, AreasId, ActivityId, SubActivityId,
    AgencyCode, AnnouncementPublishedDateDaysId, AnnouncementApplyingStatusId,
    StatusId, TypeId, AnnouncementTabId, SortDirection, Sort, PageSize, PageNumber

Tab IDs (AnnouncementTabId):
    1 = all announcements  (Sort=PublishedDate)
    3 = already joined     (Sort=CreatedAt)
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants (confirmed from JS source) ─────────────────────────────────────
BASE_URL   = 'https://tenders.etimad.sa'
CONTROLLER = f'{BASE_URL}/AnnouncementSuppliersTemplate'

# Confirmed from resource_url in Vue data and document.cookie assignment in created()
API_URL    = f'{CONTROLLER}/GetAllAnnouncementSupplierTemplatesForSupplier'

# Confirmed from detailsUrl() method in Vue
DETAIL_URL = f'{CONTROLLER}/AnnouncementSuppliersTemplateDetailsForSuppliers'

PAGE_SIZE  = 10
MAX_PAGES  = 30   # 30 × 10 = 300 announcements per run

HEADERS = {
    'Accept':           'application/json, text/plain, */*',
    'Accept-Language':  'ar,en;q=0.9',
    'Referer':          f'{CONTROLLER}/AllSupplierAnnouncementSupplierTemplates',
    'Origin':           BASE_URL,
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}


class SaudiEtimadSupplierTemplatesScraper(BaseScraper):
    """
    Scraper for Etimad Supplier List Announcements (قوائم الموردين).

    API endpoint confirmed from the Vue.js source file:
        AllSupplierAnnouncementSupplierTemplates.js

    Shares country SA with SaudiEtimadScraper and SaudiEtimadQualScraper.
    """

    country_code = 'SA'
    source_name  = 'Saudi Etimad'
    base_url     = BASE_URL

    # ── Entry point ───────────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='SaudiEtimadSupplierTemplatesScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            # Fallback: reuse the main Etimad ScraperSource
            try:
                source = ScraperSource.objects.get(
                    scraper_class='SaudiEtimadScraper', is_active=True
                )
            except ScraperSource.DoesNotExist:
                print(
                    '  X  [EtimadSupplierTemplates] No active ScraperSource — '
                    'add SaudiEtimadSupplierTemplatesScraper in Admin or run seed_data'
                )
                return []
            except ScraperSource.MultipleObjectsReturned:
                source = ScraperSource.objects.filter(
                    scraper_class='SaudiEtimadScraper', is_active=True
                ).first()
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SaudiEtimadSupplierTemplatesScraper',
                is_active=True,
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name} — Supplier Templates')
        print(f'  SCRAPER : SaudiEtimadSupplierTemplatesScraper')
        print(f'  MODE    : JSON API  (GetAllAnnouncementSupplierTemplatesForSupplier)')
        print(f'  API URL : {API_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:    list[TenderData] = []
        page        = 1
        total_pages = None

        while True:
            print(f'       > page {page:>4}', end='  ', flush=True)

            # Parameters match the JS search() method exactly
            params = {
                'PageNumber':       page,
                'PageSize':         PAGE_SIZE,
                'Sort':             'PublishedDate',
                'SortDirection':    'DESC',
                'AnnouncementTabId': 1,   # 1 = all announcements
            }

            try:
                resp = self.get(API_URL, params=params)
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked by WAF — stopping)')
                    logger.warning(
                        f'[EtimadSupplierTemplates] WAF block page {page}: {exc}'
                    )
                elif '404' in err:
                    print('X 404 — endpoint not found; check API_URL constant')
                    logger.error(
                        f'[EtimadSupplierTemplates] 404 on page {page}: {exc}'
                    )
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(
                        f'[EtimadSupplierTemplates] page {page} error: {exc}'
                    )
                break

            # ── Handle varied response shapes ─────────────────────────────
            # The pagination component passes raw data; the endpoint may
            # return a dict with 'data'/'totalCount' OR a plain list.
            if isinstance(body, list):
                raw_list    = body
                total_count = len(body)   # no totalCount in this shape
            else:
                raw_list    = body.get('data') or []
                total_count = int(body.get('totalCount') or 0)

            if total_pages is None and total_count:
                total_pages = min(
                    math.ceil(total_count / PAGE_SIZE),
                    MAX_PAGES,
                )
                print(f'(total={total_count}, pages≤{total_pages})', end='  ')

            if not raw_list:
                print('(empty — stopping)')
                break

            # Debug dump on page 1
            if page == 1:
                sample = raw_list[0]
                txt0   = self._item_text(sample)
                hits   = [kw for kw in self._relevance_keywords
                          if kw in txt0.lower()]
                print(f'\n  [DEBUG] keys   : {list(sample.keys())}')
                print(f'  [DEBUG] text   : {txt0[:160]}')
                print(f'  [DEBUG] kw hits: {hits}\n')

            matched = 0
            for item in raw_list:
                text = self._item_text(item)
                if self.detect_relevance(text):
                    results.append(self._to_tender_data(item))
                    matched += 1

            print(f'-> {len(raw_list)} items, {matched} relevant')
            logger.info(
                f'[EtimadSupplierTemplates] page {page}: '
                f'{len(raw_list)} items, {matched} relevant'
            )

            if total_pages and page >= total_pages:
                print(f'       > Reached last page ({page})')
                break
            if page >= MAX_PAGES:
                print(f'       > Hit MAX_PAGES cap ({MAX_PAGES})')
                break
            if len(raw_list) < PAGE_SIZE:
                print(f'       > Partial page ({len(raw_list)}) — end of data')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant supplier template(s)'
              f'  <-- EtimadSupplierTemplates')
        print(f'  {"=" * 58}\n')
        logger.info(
            f'[EtimadSupplierTemplates] scrape() complete — total={len(results)}'
        )
        return results

    # ── Text helpers ──────────────────────────────────────────────────────────

    def _item_text(self, item: dict) -> str:
        """Concatenate all searchable fields for keyword matching."""
        return ' '.join(filter(None, [
            item.get('announcementName',                             ''),
            item.get('agencyName',                                   ''),
            item.get('branchName',                                   ''),
            item.get('tenderTypeString',                             ''),
            item.get('statusString',                                 ''),
            item.get('announcementTemplateSuppliersListTypeString',   ''),
            item.get('mainActivity',                                 ''),
            # extra fallback aliases
            item.get('tenderName',                                   ''),
            item.get('tenderActivityName',                           ''),
            item.get('activityName',                                 ''),
            item.get('name',                                         ''),
        ]))

    def _to_tender_data(self, item: dict) -> TenderData:
        ann_id = (
            item.get('announcementIdString') or
            item.get('tenderIdString')       or
            item.get('id')                   or ''
        )

        # Detail URL confirmed from JS detailsUrl() + addJoinRequestToAnnouncementTemplate()
        source_url = (
            f'{DETAIL_URL}?announcmentIdString={ann_id}'
            if ann_id else BASE_URL
        )

        combined = self._item_text(item)

        return TenderData(
            title               = (
                item.get('announcementName') or
                item.get('tenderName')       or
                item.get('name')             or ''
            ),
            title_ar            = (
                item.get('announcementName') or
                item.get('tenderName')       or ''
            ),
            source_url          = source_url,
            external_id         = str(ann_id),
            reference_no        = item.get('referenceNumber', '') or '',
            entity              = item.get('agencyName', '') or '',
            entity_ar           = item.get('agencyName', '') or '',
            description         = ' | '.join(filter(None, [
                item.get('tenderTypeString',                             ''),
                item.get('mainActivity',                                 ''),
                item.get('announcementTemplateSuppliersListTypeString',   ''),
            ])),
            category            = self.detect_category(combined),
            status              = self._map_status(item.get('statusString', '')),
            currency            = 'SAR',
            estimated_value     = None,
            publication_date    = self._parse_date(
                item.get('publishedDateString') or
                item.get('submitionDate')        or
                item.get('publishDate')
            ),
            submission_deadline = self._parse_date(
                item.get('lastDateForJoinRequests') or
                item.get('lastOfferPresentationDate')
            ),
            opening_date        = self._parse_date(
                item.get('offersOpeningDate')
            ),
        )

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _map_status(status_str: str) -> str:
        s = (status_str or '').strip()
        if s in ('نشط', 'مفتوح', 'Active', 'Open'):
            return 'OPEN'
        if s in ('منتهي', 'منتهية', 'مغلق', 'Closed', 'Expired'):
            return 'CLOSED'
        if s in ('ملغى', 'ملغاة', 'Cancelled'):
            return 'CANCEL'
        return 'OPEN'

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if not val:
            return None
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return None
            if val.startswith('/Date('):
                try:
                    ms_str = val[6:val.index(')')]
                    ms_str = ms_str.split('+')[0].split('-')[0]
                    return datetime.utcfromtimestamp(int(ms_str) / 1000).date()
                except (ValueError, IndexError):
                    return None
            if '.' in val:
                val = val[:val.index('.')]
            for fmt, length in [
                ('%Y-%m-%dT%H:%M:%S', 19),
                ('%Y-%m-%dT%H:%M',    16),
                ('%Y-%m-%d',          10),
                ('%d/%m/%Y',          10),
            ]:
                try:
                    return datetime.strptime(val[:length], fmt).date()
                except ValueError:
                    continue
        return None
