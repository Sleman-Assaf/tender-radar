# tenders/scrapers/ncp_saudi.py
"""
Saudi Arabia — National Centre for Privatization (NCP) Investor Portal
URL: https://investors.ncp.gov.sa

What is this?
-------------
The NCP (المركز الوطني للتخصيص والشراكة) publishes PPP / privatisation
pipeline projects in three categories:

  • Live Transactions    (parentStageCode=4, parentStageName="Live Transaction")
  • Upcoming Opportunities (parentStageCode != 4 and not closed)
  • Closed Projects       — parentStageCode derived from name containing "closed"

We SKIP Closed Projects and keep Live + Upcoming only.

API (confirmed from browser Network tab):
    POST https://investors.ncp.gov.sa/api/Project/GetPipelineProjects
    Content-Type: application/json
    Body: {"pageSize": 50, "pageNumber": 1}

Response shape (confirmed from real response):
    {
        "confirm": false,
        "message": null,
        "success": true,
        "value": [
            {
                "id":                    "1d6e92d2-0e9e-45ab-8dce-08dec4d28e2a",
                "name":                  "Sports Clubs Investment and Privatization Project",
                "sectorId":              "6f8c9583-...",
                "sectorName":            "Sports Sector",
                "sectorLogoId":          "...",
                "sectorLogo":            "<base64>",
                "projectSubStageId":     "3e4ecfd0-...",
                "projectSubStageName":   "Request For Qualification (RFQ)",
                "projectSubStageCode":   11,
                "parentStageId":         "7a5e04d5-...",
                "parentStageName":       "Live Transaction",
                "parentStageCode":       4,
                "backgroundUrl":         null
            },
            ...
        ]
    }

Status logic (from parentStageCode / parentStageName):
    parentStageCode == 4  → "Live Transaction"  → keep as OPEN
    parentStageName contains "upcoming/future"  → keep as UPCOMING
    parentStageName contains "closed/award"     → SKIP

Detail URL (guessed pattern — update after confirming from browser):
    https://investors.ncp.gov.sa/projects/<id>
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL   = 'https://investors.ncp.gov.sa'
API_URL    = f'{BASE_URL}/api/Project/GetPipelineProjects'
DETAIL_URL = f'{BASE_URL}/home/project/details'   # confirmed from browser

PAGE_SIZE  = 50    # confirmed from browser payload
MAX_PAGES  = 20    # 20 × 50 = 1 000 projects max

# parentStageName substrings that mean "Closed" → skip these
# (the red tab was labelled "Closed Projects" in the screenshot)
CLOSED_KEYWORDS = (
    'closed', 'awarded', 'completed', 'finished',
    'مغلق', 'ملغى', 'منتهي',
)

HEADERS = {
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Content-Type':    'application/json',
    'Referer':         f'{BASE_URL}/',
    'Origin':           BASE_URL,
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}


class NCPSaudiScraper(BaseScraper):
    """
    Scraper for Saudi National Centre for Privatization (NCP) pipeline projects.

    Fetches Live Transactions (green) + Upcoming Opportunities (orange).
    Skips Closed Projects (red) based on parentStageName.

    Uses POST /api/Project/GetPipelineProjects with JSON body
    {"pageSize": 50, "pageNumber": N}.

    Response envelope: {"success": true, "value": [...]}
    """

    country_code = 'SA'
    source_name  = 'NCP Saudi'
    base_url     = BASE_URL

    # ── Entry point ───────────────────────────────────────────────────────────
    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='NCPSaudiScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print(
                '  X  [NCPSaudi] No active ScraperSource — '
                'run seed_data to create it'
            )
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='NCPSaudiScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : NCPSaudiScraper')
        print(f'  MODE    : POST JSON API')
        print(f'  API URL : {API_URL}')
        print(f'  FILTER  : Live + Upcoming (skip Closed)')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:       list[TenderData] = []
        page           = 1
        total_pages    = None
        skipped_closed = 0

        while True:
            print(f'       > page {page:>4}', end='  ', flush=True)

            body = {'pageSize': PAGE_SIZE, 'pageNumber': page}

            try:
                resp = self.session.post(
                    API_URL, json=body, timeout=self.timeout
                )
                resp.raise_for_status()
                envelope = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked by WAF — stopping)')
                    logger.warning(f'[NCPSaudi] WAF block page {page}: {exc}')
                elif '404' in err:
                    print('X 404 — endpoint not found')
                    logger.error(f'[NCPSaudi] 404 page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[NCPSaudi] page {page} error: {exc}')
                break

            # ── Unwrap envelope ───────────────────────────────────────────
            # Real shape: {"confirm":false,"success":true,"value":[...]}
            if isinstance(envelope, list):
                raw_list    = envelope
                total_count = 0
            elif isinstance(envelope, dict):
                success = envelope.get('success', True)
                if not success:
                    msg = envelope.get('message', '(no message)')
                    print(f'X API returned success=false: {msg}')
                    logger.error(f'[NCPSaudi] API error page {page}: {msg}')
                    break

                raw_list = (
                    envelope.get('value') or
                    envelope.get('data')  or
                    envelope.get('items') or
                    envelope.get('result') or
                    []
                )
                # total may be in envelope or absent (stop on partial page)
                total_count = int(
                    envelope.get('totalCount') or
                    envelope.get('total')      or
                    envelope.get('Total')      or
                    0
                )
            else:
                raw_list    = []
                total_count = 0

            if total_pages is None and total_count:
                total_pages = min(
                    math.ceil(total_count / PAGE_SIZE),
                    MAX_PAGES,
                )
                print(f'(total={total_count}, pages≤{total_pages})', end='  ')

            if not raw_list:
                print('(empty — stopping)')
                break

            # Debug dump on page 1 — shows real field names
            if page == 1:
                sample = raw_list[0]
                print(f'\n  [DEBUG] keys            : {list(sample.keys())}')
                print(f'  [DEBUG] name            : {sample.get("name", "?")}')
                print(f'  [DEBUG] sectorName      : {sample.get("sectorName", "?")}')
                print(f'  [DEBUG] parentStageName : {sample.get("parentStageName", "?")}')
                print(f'  [DEBUG] parentStageCode : {sample.get("parentStageCode", "?")}')
                print(f'  [DEBUG] subStageName    : {sample.get("projectSubStageName", "?")}')
                print()

            matched      = 0
            page_closed  = 0
            for item in raw_list:
                if self._is_closed(item):
                    page_closed    += 1
                    skipped_closed += 1
                    continue
                text = self._item_text(item)
                if self.detect_relevance(text):
                    results.append(self._to_tender_data(item))
                    matched += 1

            print(
                f'-> {len(raw_list)} items, '
                f'{page_closed} closed (skipped), '
                f'{matched} relevant'
            )
            logger.info(
                f'[NCPSaudi] page {page}: '
                f'{len(raw_list)} items, {page_closed} closed, {matched} relevant'
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
        print(f'  TOTAL : {len(results)} relevant NCP project(s)  <-- NCPSaudi')
        print(f'  SKIPPED (Closed): {skipped_closed}')
        print(f'  {"=" * 58}\n')
        logger.info(
            f'[NCPSaudi] complete — total={len(results)}, '
            f'skipped_closed={skipped_closed}'
        )
        return results

    # ── Closed-status filter ──────────────────────────────────────────────────

    @staticmethod
    def _is_closed(item: dict) -> bool:
        """
        Return True if the project is Closed (red tab).

        From the real response:
          parentStageName = "Live Transaction"   → keep
          parentStageName = "Upcoming ..."        → keep
          parentStageName = "Closed ..."          → skip
        """
        stage_name = (
            item.get('parentStageName') or
            item.get('stageName')       or ''
        ).lower().strip()

        if any(kw in stage_name for kw in CLOSED_KEYWORDS):
            return True

        # Fallback: if parentStageCode is known to mean closed.
        # From screenshot: Live=4.  Upcoming and Closed codes unknown yet.
        # After first run check [DEBUG] output for non-Live codes.
        return False

    # ── Text helpers ──────────────────────────────────────────────────────────

    def _item_text(self, item: dict) -> str:
        """Concatenate all searchable text fields for keyword matching."""
        return ' '.join(filter(None, [
            item.get('name',                 '') or '',
            item.get('sectorName',           '') or '',
            item.get('projectSubStageName',  '') or '',
            item.get('parentStageName',      '') or '',
            # fallbacks in case API adds more fields later
            item.get('description',          '') or '',
            item.get('agencyName',           '') or '',
            item.get('title',                '') or '',
        ]))

    def _to_tender_data(self, item: dict) -> TenderData:
        project_id = item.get('id') or item.get('projectId') or ''

        # Detail URL — update once real pattern confirmed from browser
        # Common Angular patterns: /project-detail/<id>  or  /projects/<id>
        source_url = (
            f'{DETAIL_URL}/{project_id}'
            if project_id else BASE_URL
        )

        combined    = self._item_text(item)
        stage_name  = (item.get('parentStageName') or '').lower()
        sub_stage   = item.get('projectSubStageName') or ''

        # Map to TenderRadar status
        if 'upcoming' in stage_name or 'future' in stage_name or 'pipeline' in stage_name:
            status = 'UPCOMING'
        else:
            # "Live Transaction" → OPEN
            status = 'OPEN'

        # Description: sector + sub-stage + parent stage
        desc_parts = [
            item.get('sectorName',          '') or '',
            sub_stage,
            item.get('parentStageName',     '') or '',
        ]

        return TenderData(
            title               = item.get('name') or '',
            title_ar            = item.get('name') or '',    # API returns English only
            source_url          = source_url,
            external_id         = str(project_id),
            reference_no        = (
                item.get('referenceNumber') or
                item.get('refNo')           or
                item.get('projectCode')     or
                str(item.get('projectSubStageCode') or '') or
                ''
            ),
            entity              = item.get('agencyName', '') or '',
            entity_ar           = item.get('agencyNameAr', '') or '',
            description         = ' | '.join(filter(None, desc_parts)),
            category            = self.detect_category(combined),
            status              = status,
            currency            = 'SAR',
            estimated_value     = self._parse_value(
                item.get('estimatedValue') or
                item.get('projectValue')   or
                item.get('value')
            ),
            publication_date    = self._parse_date(
                item.get('publishDate')    or
                item.get('publishedDate')  or
                item.get('createdAt')      or
                item.get('startDate')
            ),
            submission_deadline = self._parse_date(
                item.get('closingDate')    or
                item.get('deadlineDate')   or
                item.get('submissionDate') or
                item.get('endDate')
            ),
            opening_date        = self._parse_date(
                item.get('openingDate') or
                item.get('launchDate')
            ),
        )

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_value(val) -> Optional[float]:
        if val is None:
            return None
        try:
            if isinstance(val, (int, float)):
                return float(val) if val > 0 else None
            if isinstance(val, str):
                cleaned = (val
                           .replace(',', '')
                           .replace(' ', '')
                           .replace('SAR', '')
                           .replace('ريال', '')
                           .strip())
                return float(cleaned) if cleaned else None
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if not val:
            return None
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return None
            # ASP.NET /Date(ms)/ format
            if val.startswith('/Date('):
                try:
                    ms_str = val[6:val.index(')')]
                    ms_str = ms_str.split('+')[0].split('-')[0]
                    return datetime.utcfromtimestamp(int(ms_str) / 1000).date()
                except (ValueError, IndexError):
                    return None
            # Strip milliseconds before parsing
            if '.' in val and 'T' in val:
                val = val[:val.rindex('.')]
            for fmt, length in [
                ('%Y-%m-%dT%H:%M:%S', 19),
                ('%Y-%m-%dT%H:%M',    16),
                ('%Y-%m-%d',          10),
                ('%d/%m/%Y',          10),
                ('%d-%m-%Y',          10),
            ]:
                try:
                    return datetime.strptime(val[:length], fmt).date()
                except ValueError:
                    continue
        return None
