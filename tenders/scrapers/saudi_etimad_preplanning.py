# tenders/scrapers/saudi_etimad_preplanning.py
"""
Saudi Arabia — Etimad Pre-Planning (مشاريع المنافسات المستقبلية)
HTML page: https://tenders.etimad.sa/Preplanning/PrePlaningForVisitor

API endpoint (confirmed from Vue.js source embedded in HTML page):
    GET /PrePlanning/PreplaningPagingForVisitorAsync
        ?pageSize=6&PageNumber=<n>&AgencyCode=<code>

Detail URL (confirmed from JS detailsUrl() method):
    /PrePlanning/Details?id=<id>

What is Pre-Planning?
---------------------
Government agencies publish future project plans (مشاريع المنافسات المستقبلية)
before tendering, allowing suppliers to prepare. Items include:
  - projectName      — project title (Arabic)
  - agencyName       — publishing agency
  - yearQuarterName  — expected fiscal quarter
  - year             — fiscal year
  - insideKSAString  — location (inside/outside KSA)
  - projectNature    — project nature
  - projectDescription — free-text description
  - statusName        — status
  - durationInDays / durationInMonths / durationInYears

Response shape (pagination component returns raw list):
    [
        {
            "id":                  "abc123",
            "projectName":         "مشروع تطوير البنية التحتية",
            "agencyName":          "وزارة الإسكان",
            "agencyCode":          "001",
            "yearQuarterName":     "الربع الثاني",
            "year":                2025,
            "insideKSAString":     "داخل المملكة",
            "projectNature":       "مبنى",
            "projectDescription":  "...",
            "statusName":          "نشط",
            "durationInDays":      null,
            "durationInMonths":    12,
            "durationInYears":     null,
            "projectTypesList":    [{"text": "إنشاء"}, ...]
        },
        ...
    ]

Search parameters (from JS search() method):
    AgencyCode  — agency filter
    pageSize    — page size (default 6)
    PageNumber  — page number
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants (confirmed from Vue.js data block in HTML source) ───────────────
BASE_URL   = 'https://tenders.etimad.sa'
CONTROLLER = f'{BASE_URL}/PrePlanning'

# Confirmed from resource_url in Vue data:
#   resource_url: '/PrePlanning/PreplaningPagingForVisitorAsync?pageSize=' + 6
API_URL    = f'{CONTROLLER}/PreplaningPagingForVisitorAsync'

# Confirmed from detailsUrl() Vue method:
#   return '/PrePlanning/Details?id=' + params
DETAIL_URL = f'{CONTROLLER}/Details'

PAGE_SIZE  = 10   # use 10 instead of HTML default 6 for efficiency
MAX_PAGES  = 50   # up to 500 projects per run

HEADERS = {
    'Accept':           'application/json, text/plain, */*',
    'Accept-Language':  'ar,en;q=0.9',
    'Referer':          f'{CONTROLLER}/PrePlaningForVisitor',
    'Origin':           BASE_URL,
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}


class SaudiEtimadPrePlanningScraper(BaseScraper):
    """
    Scraper for Etimad Future Procurement Plans (مشاريع المنافسات المستقبلية).

    API endpoint confirmed from Vue.js source embedded in:
        https://tenders.etimad.sa/Preplanning/PrePlaningForVisitor

    Shares country SA with all other Etimad scrapers.
    """

    country_code = 'SA'
    source_name  = 'Saudi Etimad'
    base_url     = BASE_URL

    # ── Entry point ───────────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='SaudiEtimadPrePlanningScraper',
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
                    '  X  [EtimadPrePlanning] No active ScraperSource — '
                    'add SaudiEtimadPrePlanningScraper in Admin or run seed_data'
                )
                return []
            except ScraperSource.MultipleObjectsReturned:
                source = ScraperSource.objects.filter(
                    scraper_class='SaudiEtimadScraper', is_active=True
                ).first()
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='SaudiEtimadPrePlanningScraper',
                is_active=True,
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name} — Pre-Planning')
        print(f'  SCRAPER : SaudiEtimadPrePlanningScraper')
        print(f'  MODE    : JSON API  (PreplaningPagingForVisitorAsync)')
        print(f'  API URL : {API_URL}')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:    list[TenderData] = []
        page        = 1
        total_pages = None

        while True:
            print(f'       > page {page:>4}', end='  ', flush=True)

            # Parameters confirmed from JS search() method and resource_url
            params = {
                'pageSize':   PAGE_SIZE,
                'PageNumber': page,
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
                        f'[EtimadPrePlanning] WAF block page {page}: {exc}'
                    )
                elif '404' in err:
                    print('X 404 — endpoint not found; check API_URL constant')
                    logger.error(
                        f'[EtimadPrePlanning] 404 on page {page}: {exc}'
                    )
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(
                        f'[EtimadPrePlanning] page {page} error: {exc}'
                    )
                break

            # ── Handle varied response shapes ─────────────────────────────
            # The pagination component may deliver:
            #   A) plain list of items
            #   B) dict with 'data' key and 'totalCount'
            if isinstance(body, list):
                raw_list    = body
                total_count = 0   # unknown — stop when empty/partial
            else:
                raw_list    = body.get('data') or []
                total_count = int(body.get('totalCount') or body.get('Total') or 0)

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
                f'[EtimadPrePlanning] page {page}: '
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
        print(f'  TOTAL : {len(results)} relevant pre-planning project(s)'
              f'  <-- EtimadPrePlanning')
        print(f'  {"=" * 58}\n')
        logger.info(
            f'[EtimadPrePlanning] scrape() complete — total={len(results)}'
        )
        return results

    # ── Text helpers ──────────────────────────────────────────────────────────

    def _item_text(self, item: dict) -> str:
        """Concatenate all searchable fields for keyword matching."""
        # project types list → flatten to text
        project_types = ' '.join(
            t.get('text', '') for t in (item.get('projectTypesList') or [])
        )
        return ' '.join(filter(None, [
            item.get('projectName',        ''),
            item.get('agencyName',         ''),
            item.get('projectDescription', ''),
            item.get('projectNature',      ''),
            item.get('insideKSAString',    ''),
            item.get('yearQuarterName',    ''),
            item.get('statusName',         ''),
            project_types,
            # fallback aliases
            item.get('name',               ''),
            item.get('description',        ''),
        ]))

    def _to_tender_data(self, item: dict) -> TenderData:
        project_id = (
            item.get('id') or
            item.get('prePlanningId') or
            item.get('projectId') or ''
        )

        # Detail URL confirmed from JS detailsUrl() method:
        #   return '/PrePlanning/Details?id=' + params
        source_url = (
            f'{DETAIL_URL}?id={project_id}'
            if project_id else BASE_URL
        )

        combined = self._item_text(item)

        # Build duration description
        duration_parts = []
        if item.get('durationInYears'):
            duration_parts.append(f"{item['durationInYears']} سنة")
        if item.get('durationInMonths'):
            duration_parts.append(f"{item['durationInMonths']} شهر")
        if item.get('durationInDays'):
            duration_parts.append(f"{item['durationInDays']} يوم")
        duration_str = ' - '.join(duration_parts) if duration_parts else ''

        # Build description from available fields
        desc_parts = [
            item.get('projectNature',      ''),
            item.get('projectDescription', ''),
            item.get('insideKSAString',    ''),
        ]
        if item.get('yearQuarterName'):
            desc_parts.append(item['yearQuarterName'])
        if item.get('year'):
            desc_parts.append(str(item['year']))
        if duration_str:
            desc_parts.append(duration_str)

        # flatten projectTypesList
        type_texts = [
            t.get('text', '') for t in (item.get('projectTypesList') or [])
        ]
        if type_texts:
            desc_parts.append(' / '.join(filter(None, type_texts)))

        return TenderData(
            title               = item.get('projectName') or item.get('name') or '',
            title_ar            = item.get('projectName') or '',
            source_url          = source_url,
            external_id         = str(project_id),
            reference_no        = item.get('referenceNumber', '') or '',
            entity              = item.get('agencyName', '') or '',
            entity_ar           = item.get('agencyName', '') or '',
            description         = ' | '.join(filter(None, desc_parts)),
            category            = self.detect_category(combined),
            status              = self._map_status(item.get('statusName', '')),
            currency            = 'SAR',
            estimated_value     = None,
            publication_date    = self._parse_date(
                item.get('publishedDate') or
                item.get('createdAt')     or
                item.get('publishDate')
            ),
            submission_deadline = self._parse_date(
                item.get('lastDateForJoinRequests') or
                item.get('endDate')
            ),
            opening_date        = None,
        )

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _map_status(status_str: str) -> str:
        s = (status_str or '').strip()
        if s in ('نشط', 'مفتوح', 'Active', 'Open', 'منشور'):
            return 'OPEN'
        if s in ('منتهي', 'منتهية', 'مغلق', 'Closed', 'Expired', 'منتهية الصلاحية'):
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
