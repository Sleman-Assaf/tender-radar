# tenders/scrapers/eib_procurement.py
"""
EIB — European Investment Bank — All Procurement
URL: https://www.eib.org/en/about/procurement/all/index.htm

What is this?
-------------
The European Investment Bank (EIB) publishes procurement notices covering:
- Calls for tenders        (EIB's own institutional procurement)
- TA operations            (Technical Assistance for EIB-financed projects)
- Project procurement      (procurement by EIB borrowers in non-EU countries)
- ESIF / RRF / Other mandates

Highly relevant for AEC consultancies — many notices involve infrastructure
advisory, engineering supervision, road/water/rail construction for projects
in MENA, Africa, Eastern Europe, and Central Asia.

API (confirmed from browser Network tab):
    GET https://www.eib.org/provider-eib/app/list/medias/procurements
    No authentication required (public API, CORS: *)

Parameters (confirmed):
    search=                         → keyword (empty = all)
    sortColumn=configuration.contentStart
    sortDir=desc                    → newest first
    pageNumber=0                    → 0-based page index
    itemPerPage=25                  → page size (max tested: 25)
    pageable=true
    language=EN
    defaultLanguage=EN
    yearTo= / yearFrom=             → year filter
    orYearTo=true / orYearFrom=true
    procurementStatus=All           → All / onGoing / closed
    or_g_procurementInformations_type=true

Response JSON (confirmed):
    {
        "data": [
            {
                "id": "6a316fb062560a13e17eefc4",
                "title": "Supervision of Construction / Rehabilitation Works ...",
                "url": "20160404-12-tender-award-2026-06-16",  ← slug
                "subType": "procurements-call-projects-non-eu",
                "startDate": 1781625600000,  ← epoch milliseconds
                "endDate": 1782943200000,    ← epoch milliseconds (null if no deadline)
                "additionalInformation": [
                    "On going",          ← [0] status
                    "Project procurement",  ← [1] type label
                    "20160404-12",       ← [2] reference number
                    "16/06/2026",        ← [3] publication date DD/MM/YYYY
                    "02/07/2026"         ← [4] deadline DD/MM/YYYY (or " " if none)
                ],
                "primaryTags": [          ← country tags (may be null)
                    {"label": "Ukraine", "subType": "countries", "value": "ua"},
                    {"label": "Eastern Neighbourhood", "subType": "regions", ...}
                ],
                "description": "<p>...</p>",
                "type": "media",
                "urlType": "data"
            }
        ],
        "totalItems": 1126,
        "itemPerPage": 25,
        "pageNumber": 0,
        "pageable": true,
        "resultItems": 25,
        "message": "Success",
        "valid": true
    }

Detail URL: https://www.eib.org/en/about/procurement/all/{url}
e.g. https://www.eib.org/en/about/procurement/all/20160404-12-tender-award-2026-06-16

Total records: ~1,126 with procurementStatus=All
               ~few hundred with procurementStatus=onGoing (recommended)

Filter strategy: procurementStatus=onGoing to get only active tenders,
then detect_relevance() for AEC/infrastructure matching.
"""

import logging
from datetime import datetime, date, timezone
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://www.eib.org'
LIST_URL  = f'{BASE_URL}/en/about/procurement/all/index.htm'
API_URL   = f'{BASE_URL}/provider-eib/app/list/medias/procurements'

PAGE_SIZE = 25
MAX_PAGES = 20    # 20 × 25 = 500 tenders

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
    'sec-fetch-dest':  'empty',
    'sec-fetch-mode':  'cors',
    'sec-fetch-site':  'same-origin',
    'X-Requested-With': 'XMLHttpRequest',
}


class EIBProcurementScraper(BaseScraper):
    """
    Scraper for EIB (European Investment Bank) procurement notices.

    Uses the public JSON API at /provider-eib/app/list/medias/procurements.
    Paginates via pageNumber=0,1,2,...
    Country detected from primaryTags[].value or title keywords.
    """

    country_code = 'ZZ'   # multi-country / EU + global
    source_name  = 'EIB — European Investment Bank Procurement'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='EIBProcurementScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [EIB] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='EIBProcurementScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : EIBProcurementScraper')
        print(f'  MODE    : GET JSON API (provider-eib/app/list/medias/procurements)')
        print(f'  API URL : {API_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_ids:  set[str]        = set()
        page       = 0
        total      = None

        while page < MAX_PAGES:
            print(f'       > page {page:>3}', end='  ', flush=True)

            params = {
                'search':                           '',
                'sortColumn':                       'configuration.contentStart',
                'sortDir':                          'desc',
                'pageNumber':                       str(page),
                'itemPerPage':                      str(PAGE_SIZE),
                'pageable':                         'true',
                'language':                         'EN',
                'defaultLanguage':                  'EN',
                'yearTo':                           '',
                'orYearTo':                         'true',
                'yearFrom':                         '',
                'orYearFrom':                       'true',
                'procurementStatus':                'onGoing',
                'or_g_procurementInformations_type':'true',
            }

            try:
                resp = self.session.get(API_URL, params=params, timeout=self.timeout)
                resp.raise_for_status()
                envelope = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[EIB] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[EIB] page {page}: {exc}')
                break

            items = envelope.get('data', [])

            if total is None:
                total      = envelope.get('totalItems', 0)
                per_page   = envelope.get('itemPerPage', PAGE_SIZE)
                total_pages = -(-total // per_page)  # ceiling division
                print(f'(totalItems={total}, pages≈{total_pages})', end='  ')

                if items:
                    i0 = items[0]
                    add = i0.get('additionalInformation', [])
                    print(f'\n  [DEBUG] item[0].title:   {i0.get("title","?")}')
                    print(f'  [DEBUG] item[0].url:     {i0.get("url","?")}')
                    print(f'  [DEBUG] item[0].subType: {i0.get("subType","?")}')
                    print(f'  [DEBUG] additionalInfo:  {add}')
                    print(f'  [DEBUG] startDate:       {i0.get("startDate")}')
                    print(f'  [DEBUG] endDate:         {i0.get("endDate")}')
                    print(f'  [DEBUG] primaryTags:     {i0.get("primaryTags")}')
                    print()

            if not items:
                print('(empty — stopping)')
                break

            matched  = 0
            page_new = 0

            for item in items:
                td = self._parse_item(item)
                if not td:
                    continue
                item_id = item.get('id') or td.source_url
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                page_new += 1
                combined = f'{td.title} {td.description}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(items)} items, {page_new} new, {matched} relevant')
            logger.info(f'[EIB] page {page}: {page_new} new, {matched} relevant')

            page += 1
            if len(items) < PAGE_SIZE:
                print(f'       > Partial page ({len(items)}) — end of data')
                break
            if total and page * PAGE_SIZE >= total:
                print(f'       > All {total} records fetched')
                break

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant EIB procurement(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[EIB] complete — total={len(results)}')
        return results

    # ── Item parser ───────────────────────────────────────────────────────

    def _parse_item(self, item: dict) -> Optional[TenderData]:
        try:
            title = (item.get('title') or '').strip()
            if not title:
                return None

            slug       = item.get('url') or ''
            sub_type   = item.get('subType') or ''
            # URL path from subType: "procurements-call-projects-non-eu" → "call-projects-non-eu"
            path_seg   = sub_type.replace('procurements-', '') if sub_type else 'all'
            detail_url = (
                f'{BASE_URL}/en/about/procurement/{path_seg}/{slug}'
                if slug else LIST_URL
            )

            # additionalInformation: [status, type, ref, pub_date, deadline]
            add = item.get('additionalInformation') or []
            status_raw = (add[0] if len(add) > 0 else '').strip().lower()
            type_label = (add[1] if len(add) > 1 else '').strip()
            ref_no     = (add[2] if len(add) > 2 else '').strip()
            pub_str    = (add[3] if len(add) > 3 else '').strip()
            dl_str     = (add[4] if len(add) > 4 else '').strip()

            # Dates — prefer epoch ms fields, fall back to additionalInfo strings
            pub_date = (
                self._epoch_ms_to_date(item.get('startDate'))
                or self._parse_dmy(pub_str)
            )
            deadline = (
                self._epoch_ms_to_date(item.get('endDate'))
                or self._parse_dmy(dl_str)
            )

            today = date.today()
            is_open = 'on going' in status_raw or status_raw == 'ongoing'
            if deadline and deadline < today and not is_open:
                status        = 'CLOSED'
                save_deadline = None
            else:
                status        = 'OPEN' if is_open else 'CLOSED'
                save_deadline = deadline

            # Country from primaryTags
            detected_cc = self._country_from_tags(
                item.get('primaryTags') or [],
                title
            )

            # subType for description
            sub_type = item.get('subType', '').replace('procurements-call-', '').replace('-', ' ').title()
            description = f'{type_label} | {sub_type}' if sub_type else type_label

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = item.get('id') or ref_no or slug,
                reference_no        = ref_no or slug,
                entity              = 'European Investment Bank (EIB)',
                entity_ar           = 'البنك الأوروبي للاستثمار (EIB)',
                description         = description,
                category            = self.detect_category(f'{title} {description}'),
                status              = status,
                currency            = 'EUR',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = save_deadline,
                opening_date        = None,
                country_code        = detected_cc,
            )

        except Exception as exc:
            logger.warning(f'[EIB] parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _epoch_ms_to_date(val) -> Optional[date]:
        """Convert epoch milliseconds to date."""
        if not val:
            return None
        try:
            return datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc).date()
        except (ValueError, OSError):
            return None

    @staticmethod
    def _parse_dmy(val: str) -> Optional[date]:
        """Parse DD/MM/YYYY date string."""
        if not val or val.strip() in ('', ' '):
            return None
        try:
            return datetime.strptime(val.strip(), '%d/%m/%Y').date()
        except ValueError:
            return None

    # ISO alpha-2 map from EIB primaryTags value field
    _TAG_CC_MAP = {
        'ao': 'AO', 'dz': 'DZ', 'eg': 'EG', 'et': 'ET', 'gh': 'GH',
        'ke': 'KE', 'ly': 'LY', 'ma': 'MA', 'mz': 'MZ', 'ng': 'NG',
        'rw': 'RW', 'sn': 'SN', 'sd': 'SD', 'tz': 'TZ', 'tn': 'TN',
        'ug': 'UG', 'za': 'ZA', 'zm': 'ZM', 'zw': 'ZW', 'cm': 'CM',
        'cd': 'CD', 'ci': 'CI', 'ml': 'ML', 'ne': 'NE', 'bf': 'BF',
        'jo': 'JO', 'eg': 'EG', 'iq': 'IQ', 'lb': 'LB', 'ps': 'PS',
        'sa': 'SA', 'tn': 'TN', 'ye': 'YE', 'sy': 'SY', 'om': 'OM',
        'ua': 'UA', 'md': 'MD', 'ge': 'GE', 'am': 'AM', 'az': 'AZ',
        'kz': 'KZ', 'uz': 'UZ', 'tj': 'TJ', 'kg': 'KG', 'mn': 'MN',
        'in': 'IN', 'id': 'ID', 'vn': 'VN', 'pk': 'PK', 'bd': 'BD',
        'al': 'AL', 'ba': 'BA', 'me': 'ME', 'mk': 'MK', 'rs': 'RS',
        'xk': 'XK', 'br': 'BR', 'co': 'CO', 'pe': 'PE', 'ec': 'EC',
        'ht': 'HT', 'gt': 'GT', 'hn': 'HN',
    }

    # Title keyword → country fallback
    _TITLE_CC_MAP = {
        'jordan': 'JO', 'egypt': 'EG', 'morocco': 'MA', 'tunisia': 'TN',
        'algeria': 'DZ', 'libya': 'LY', 'lebanon': 'LB', 'iraq': 'IQ',
        'ghana': 'GH', 'kenya': 'KE', 'ethiopia': 'ET', 'nigeria': 'NG',
        'tanzania': 'TZ', 'uganda': 'UG', 'rwanda': 'RW', 'senegal': 'SN',
        'mozambique': 'MZ', 'zambia': 'ZM', 'south africa': 'ZA',
        'angola': 'AO', 'cameroon': 'CM', 'ivory coast': 'CI',
        "cote d'ivoire": 'CI', 'mali': 'ML', 'niger': 'NE',
        'ukraine': 'UA', 'moldova': 'MD', 'georgia': 'GE',
        'armenia': 'AM', 'azerbaijan': 'AZ', 'uzbekistan': 'UZ',
        'india': 'IN', 'indonesia': 'ID', 'vietnam': 'VN',
        'albania': 'AL', 'bosnia': 'BA', 'montenegro': 'ME',
        'serbia': 'RS', 'kosovo': 'XK', 'north macedonia': 'MK',
    }

    @classmethod
    def _country_from_tags(cls, tags: list, title: str) -> str:
        """Detect country ISO code from primaryTags or title."""
        # 1. Check primaryTags with subType='countries'
        for tag in tags:
            if isinstance(tag, dict) and tag.get('subType') == 'countries':
                cc = cls._TAG_CC_MAP.get(tag.get('value', '').lower())
                if cc:
                    return cc
        # 2. Fallback: title keyword scan
        t = title.lower()
        for kw, cc in sorted(cls._TITLE_CC_MAP.items(), key=lambda x: -len(x[0])):
            if kw in t:
                return cc
        return 'ZZ'
