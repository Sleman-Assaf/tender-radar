# tenders/scrapers/ec_funding_tenders.py
"""
EU Funding & Tenders Portal — Open Calls for Tenders (CFTs)
URL: https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/calls-for-tenders?isExactMatch=true&status=31094502&order=DESC&pageNumber=1&pageSize=50&sortBy=startDate

What is this?
-------------
The EC Funding & Tenders Portal is the European Commission's single entry point
for procurement and grant opportunities. This scraper targets the "Calls for
Tenders" section filtered to Open status only (status=31094502).

Technology: Angular SPA backed by EC Search API (SEDIA)

API (confirmed from browser Network tab):
    POST https://api.tech.ec.europa.eu/search-api/prod/rest/search
    URL params: apiKey=SEDIA, text=***, pageSize=50, pageNumber=N
    Method: POST
    Auth: none (public API, apiKey is a fixed public key)

Response shape (confirmed from browser):
    {
        "totalResults": 485,
        "pageNumber": 1,
        "pageSize": 50,
        "results": [
            {
                "reference":  "77bbac1e-37e7-4f07-b76e-97b5640416cc-CN",
                "url":        "https://ec.europa.eu/...tender-details/<uuid>-CN",
                "title":      null,        ← always null; use summary/content instead
                "summary":    "EEA/ENS/TC/26/001 - Topic Centre on Freshwater Environment",
                "content":    "EEA/ENS/TC/26/001 - Topic Centre on Freshwater Environment",
                "metadata": {
                    "title":          ["EEA/ENS/TC/26/001 - Topic Centre on Freshwater Environment"],
                    "callIdentifier": ["EEA/2026/OP/0031"],
                    "startDate":      ["2026-06-15T00:00:00.000+0000"],
                    "deadlineDate":   ["2026-08-31T14:00:59.000+0000"],
                    "status":         ["31094502"],
                    "cftId":          ["77bbac1e-37e7-4f07-b76e-97b5640416cc-CN"],
                    "contractType":   ["31095498"],
                    "procedureType":  ["47396220"],
                }
            }
        ]
    }

Status codes:
    31094502 = Open (the only status we fetch)

Post body structure confirmed empirically as the URL params carry the filters.
The Angular portal sends the POST with status filter embedded in URL params
and the body either empty or containing sort config.

NOTE: If POST body format needs to be updated, check the [TODO] comments below.
      After first run, check [DEBUG] output for field names in real responses.
"""

import logging
import math
from datetime import datetime, date, timezone
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL     = 'https://ec.europa.eu/info/funding-tenders/opportunities/portal'
API_URL      = 'https://api.tech.ec.europa.eu/search-api/prod/rest/search'
DETAIL_BASE  = f'{BASE_URL}/screen/opportunities/tender-details'

# EC status code for "Open" tenders (confirmed from response metadata.status[])
STATUS_OPEN  = '31094502'

PAGE_SIZE    = 50     # matches original URL pageSize=50
MAX_PAGES    = 10     # 10 × 50 = 500 results max

# Fixed public API key (seen in browser URL: apiKey=SEDIA)
API_KEY      = 'SEDIA'

HEADERS = {
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Content-Type':    'application/json',
    'Origin':          'https://ec.europa.eu',
    'Referer':         f'{BASE_URL}/screen/opportunities/calls-for-tenders',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
}


class ECFundingTendersScraper(BaseScraper):
    """
    Scraper for EU Funding & Tenders Portal — Open Calls for Tenders.

    Uses the EC Search API (SEDIA) via POST with URL parameters.
    Confirmed from browser: POST /search-api/prod/rest/search?apiKey=SEDIA&text=***&pageSize=50&pageNumber=N

    [TODO] If the first run returns 0 results, check the POST body format:
           The Angular app may send a JSON body with filters instead of URL params.
           Run with --source ECFundingTendersScraper and check [DEBUG] output.
    """

    country_code = 'EU'
    source_name  = 'EU Funding & Tenders Portal'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource

        try:
            source = ScraperSource.objects.get(
                scraper_class='ECFundingTendersScraper',
                is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [ECFundingTenders] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='ECFundingTendersScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : ECFundingTendersScraper')
        print(f'  MODE    : POST JSON API (EC SEDIA Search)')
        print(f'  API URL : {API_URL}')
        print(f'  FILTER  : Open tenders only (status={STATUS_OPEN})')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:     list[TenderData] = []
        page         = 1
        total_pages  = None

        while True:
            print(f'       > page {page:>3}', end='  ', flush=True)

            # URL params (confirmed from browser)
            params = {
                'apiKey':     API_KEY,
                'text':       '***',
                'pageSize':   PAGE_SIZE,
                'pageNumber': page,
            }

            # [TODO] Verify POST body format after first run.
            # Option A: Filters in URL params (isExactMatch, status, sortBy)
            # Option B: Filters in JSON body
            # Trying Option A first (matches original portal URL params):
            params.update({
                'isExactMatch': 'true',
                'status':       STATUS_OPEN,
                'order':        'DESC',
                'sortBy':       'startDate',
            })

            # POST body — try empty first; if 0 results, add filter body
            body = {}

            try:
                resp = self.session.post(
                    API_URL, params=params, json=body, timeout=self.timeout
                )
                resp.raise_for_status()
                envelope = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[ECFundingTenders] WAF block page {page}: {exc}')
                elif '404' in err:
                    print('X 404')
                    logger.error(f'[ECFundingTenders] 404: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[ECFundingTenders] page {page}: {exc}')
                break

            # ── Unwrap response ───────────────────────────────────────────
            raw_list    = envelope.get('results', [])
            total       = envelope.get('totalResults', 0)

            if total_pages is None and total:
                total_pages = min(math.ceil(total / PAGE_SIZE), MAX_PAGES)
                print(f'(total={total}, pages≤{total_pages})', end='  ')

            if not raw_list:
                # If empty, may need filters in body — log for debugging
                print('(empty — stopping)')
                print(f'\n  [DEBUG] totalResults={total}')
                print(f'  [DEBUG] envelope keys: {list(envelope.keys())}')
                print(f'  [DEBUG] If totalResults>0 but results empty,')
                print(f'  [DEBUG] the POST body needs the filter. Check Network tab.')
                break

            # Debug on page 1
            if page == 1:
                s = raw_list[0]
                m = s.get('metadata', {})
                print(f'\n  [DEBUG] reference   : {s.get("reference","?")}')
                print(f'  [DEBUG] summary     : {s.get("summary","?")[:70]}')
                print(f'  [DEBUG] title[]     : {(m.get("title") or ["?"])[0][:60]}')
                print(f'  [DEBUG] identifier  : {(m.get("callIdentifier") or ["?"])[0]}')
                print(f'  [DEBUG] startDate   : {(m.get("startDate") or ["?"])[0]}')
                print(f'  [DEBUG] deadlineDate: {(m.get("deadlineDate") or ["?"])[0]}')
                print(f'  [DEBUG] status      : {(m.get("status") or ["?"])[0]}')
                print()

            matched = 0
            for item in raw_list:
                td = self._parse_item(item)
                if td:
                    combined = f'{td.title} {td.description}'
                    if self.detect_relevance(combined):
                        results.append(td)
                        matched += 1

            print(f'-> {len(raw_list)} items, {matched} relevant')
            logger.info(f'[ECFundingTenders] page {page}: {len(raw_list)} items, {matched} relevant')

            if total_pages and page >= total_pages:
                print(f'       > Reached last page ({page})')
                break
            if page >= MAX_PAGES:
                print(f'       > Hit MAX_PAGES cap')
                break
            if len(raw_list) < PAGE_SIZE:
                print(f'       > Partial page ({len(raw_list)}) — end of data')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant EU tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[ECFundingTenders] complete — total={len(results)}')
        return results

    # ── Item parser ───────────────────────────────────────────────────────────

    def _parse_item(self, item: dict) -> Optional[TenderData]:
        try:
            meta = item.get('metadata', {})

            def m1(key: str) -> str:
                """Get first value from metadata array field."""
                vals = meta.get(key)
                return vals[0] if vals else ''

            # Title — use metadata.title[0] (confirmed from sample)
            # .title at root level is always null
            title = m1('title') or item.get('summary') or item.get('content') or ''
            if not title:
                return None

            # Reference / call identifier
            call_id    = m1('callIdentifier')   # e.g. "EEA/2026/OP/0031"
            cft_id     = m1('cftId')            # UUID-CN
            reference  = item.get('reference', cft_id)  # "77bbac1e-...-CN"

            # Detail URL (already full URL in response)
            source_url = (
                item.get('url') or
                (f'{DETAIL_BASE}/{cft_id}' if cft_id else BASE_URL)
            )

            # Dates
            start_str    = m1('startDate')
            deadline_str = m1('deadlineDate')
            pub_date  = self._parse_ec_date(start_str)
            deadline  = self._parse_ec_date(deadline_str)

            # Status — always Open (we filter for 31094502)
            status = 'OPEN'

            # Description
            desc_parts = [
                f'Call ID: {call_id}' if call_id else '',
                f'Deadline: {deadline_str[:10]}' if deadline_str else '',
            ]
            description = ' | '.join(filter(None, desc_parts))

            combined = f'{title} {call_id}'

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = source_url,
                external_id         = cft_id or reference,
                reference_no        = call_id,
                entity              = 'European Commission',
                entity_ar           = 'المفوضية الأوروبية',
                description         = description,
                category            = self.detect_category(combined),
                status              = status,
                currency            = 'EUR',
                estimated_value     = None,
                publication_date    = pub_date,
                submission_deadline = deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[ECFundingTenders] parse error: {exc}')
            return None

    # ── Date parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ec_date(val: str) -> Optional[date]:
        """Parse EC API date: '2026-06-15T00:00:00.000+0000'."""
        if not val:
            return None
        val = val.strip()
        # Strip milliseconds and timezone for parsing
        # "2026-06-15T00:00:00.000+0000" → "2026-06-15"
        try:
            return datetime.fromisoformat(
                val.replace('+0000', '+00:00').replace('.000+', '+')
            ).date()
        except ValueError:
            pass
        # Fallback: just take the date part
        try:
            return datetime.strptime(val[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
