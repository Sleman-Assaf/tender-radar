# tenders/scrapers/tenderwell.py
"""
TenderWell — Global Tender Search (Jordan filter)
URL: https://tenderwell.com/tender/search

What is this?
-------------
TenderWell is a global tender aggregation platform covering 37M+ tenders
from 190+ countries. This scraper targets Jordan tenders only by filtering
on placeOfOrigin = "JO".

API (confirmed from browser Network tab):
    POST https://tenderwell.com/api/frontend/tenders/search
    Content-Type: application/json
    Auth: None required for public/non-anonymized fields

Payload:
    {
        "page": 1,
        "pageSize": 200,
        "sortBy": ["STATUS", "CREATE_DATE"],
        "sortOrder": ["DESC", "DESC"],
        "placeOfOrigin": ["JO"],   ← Jordan filter (to verify on server)
        "status": ["OPEN"]         ← open tenders only (to verify on server)
    }

Response shape (confirmed from browser):
    {
        "totalResults": 37853359,
        "page": 1,
        "perPage": 10,
        "items": [
            {
                "id":             {"data": 41086526,   "anonymized": false},
                "slug":           {"data": "ssp-605-q-fire-safety-drawing", "anonymized": false},
                "title":          {"data": "SSP/605/Q/Fire Safety Drawing", "anonymized": false},
                "deadlineDate":   {"data": "2026-07-06T09:30:00Z", "anonymized": false},
                "status":         {"data": {"name": "OPEN"}, "anonymized": false},
                "placeOfOrigin":  {"data": {"codeAlpha2": "IN", "name": "India"}, "anonymized": false},
                "publicationDate":{"data": "**", "anonymized": true},   ← requires subscription
                "buyers":         {"data": [...], "anonymized": true},  ← requires subscription
                "tenderTypes":    [{"name": "INVITATION_TO_TENDER", "prettyName": "Invitation to Tender (ITT)"}],
                "procedureTypes": [{"name": "OPEN_PROCEDURE", "prettyName": "Open Procedure"}],
                "language":       {"data": "eng", "anonymized": false},
                "sectors":        {"data": [], "anonymized": false},
            }
        ]
    }

Detail URL: https://tenderwell.com/tender/<slug>
e.g.       https://tenderwell.com/tender/ssp-605-q-fire-safety-drawing

Fields available without subscription:
    id, slug, title, deadlineDate, status, placeOfOrigin,
    tenderTypes, procedureTypes, language, sectors

Fields requiring subscription (anonymized with ***):
    publicationDate, createdDate, buyers, description, budget

[TODO] Verify on Jordan server:
    1. Whether "placeOfOrigin": ["JO"] correctly filters to Jordan
    2. Whether "status": ["OPEN"] correctly filters to open tenders
    3. Check [DEBUG] output for totalResults with JO filter
       (should be ~hundreds not 37M)
"""

import logging
import math
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL  = 'https://tenderwell.com'
API_URL   = f'{BASE_URL}/api/frontend/tenders/search'
LIST_URL  = f'{BASE_URL}/tender/search'

PAGE_SIZE = 10    # One page only
MAX_PAGES = 1     # One page only

LOGIN_URL = f'{BASE_URL}/api/frontend/auth/login'

# Credentials — never in the source. Set them in the environment, or in
# production.env alongside the database settings:
#   TENDERWELL_EMAIL    = your TenderWell account
#   TENDERWELL_PASSWORD = its password
import os
TENDERWELL_EMAIL    = os.environ.get('TENDERWELL_EMAIL', '')
TENDERWELL_PASSWORD = os.environ.get('TENDERWELL_PASSWORD', '')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Content-Type':    'application/json',
    'Referer':         LIST_URL,
    'Origin':          BASE_URL,
    'sec-fetch-dest':  'empty',
    'sec-fetch-mode':  'cors',
    'sec-fetch-site':  'same-origin',
}


class TenderWellScraper(BaseScraper):
    """
    Scraper for TenderWell Jordan tenders.

    Uses POST JSON API at /api/frontend/tenders/search with
    placeOfOrigin=["JO"] filter.

    Non-subscription fields only: title, slug, deadlineDate, status,
    placeOfOrigin, tenderTypes, procedureTypes.

    [TODO] On first run, check [DEBUG] output to verify:
      - totalResults is ~hundreds (not 37M) → Jordan filter working
      - items[0].placeOfOrigin = JO → country filter confirmed
      - If totalResults = 37M → filter param name may differ; check API
    """

    country_code = 'ZZ'   # Multi-country (Africa + MENA)
    source_name  = 'TenderWell — MENA & Africa'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='TenderWellScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [TenderWell] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='TenderWellScraper', is_active=True
            ).first()

        self.session.headers.update(HEADERS)

        # Authenticate and get JWT token
        token = self._login()
        if token:
            # Set both Bearer header (standard) and auth cookie (as browser does)
            self.session.headers['Authorization'] = f'Bearer {token}'
            import json as _json, urllib.parse as _up
            auth_cookie_val = _up.quote(_json.dumps({
                'clientFingerprint': os.environ.get('TENDERWELL_CLIENT_FINGERPRINT', ''),
                'token': token,
            }))
            self.session.cookies.set('auth', auth_cookie_val, domain='tenderwell.com')
            print(f'  AUTH    : JWT token obtained ({len(token)} chars)')
        else:
            print('  AUTH    : No token — trying without auth (may get 403)')

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : TenderWellScraper')
        print(f'  MODE    : POST JSON API')
        print(f'  API URL : {API_URL}')
        print(f'  FILTER  : placeOfOrigin=JO, status=OPEN')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        results:   list[TenderData] = []
        seen_ids:  set[str]        = set()
        page       = 1
        total      = None
        max_pages  = MAX_PAGES

        while page <= max_pages:
            print(f'       > page {page:>3}', end='  ', flush=True)

            payload = {
                'page':          page,
                'pageSize':      PAGE_SIZE,
                'sortBy':        ['STATUS', 'CREATE_DATE'],
                'sortOrder':     ['DESC', 'DESC'],
                'placeOfOrigin': [
                    # Africa
                    'DZ','AO','BJ','BW','BF','BI','CM','CV','CF','TD','KM','CG','CD',
                    'DJ','EG','GQ','ER','SZ','ET','TF','GA','GM','GH','GN','GW','CI',
                    'KE','LS','LR','LY','MG','MW','ML','MR','MU','YT','MA','MZ','NA',
                    'NE','NG','RE','RW','SH','ST','SN','SC','SL','SO','ZA','SS','SD',
                    'TZ','TG','TN','UG','EH','ZM','ZW',
                    # MENA
                    'AZ','BH','IQ','JO','KZ','KW','LB','OM','PS','QA','SA','SY','AE','UZ','YE',
                ],
                'status':        ['OPEN'],
            }

            try:
                resp = self.session.post(
                    API_URL, json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                envelope = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked — WAF/IP restriction)')
                    logger.warning(f'[TenderWell] WAF block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[TenderWell] page {page}: {exc}')
                break

            items = envelope.get('items', [])

            if total is None:
                total   = envelope.get('totalResults', 0)
                per_pg  = envelope.get('perPage', PAGE_SIZE)
                max_pages = min(MAX_PAGES, math.ceil(total / max(per_pg, 1)))
                print(f'(total={total}, pages≤{max_pages})', end='  ')

                # Debug: verify filter is working
                if items:
                    i0 = items[0]
                    country = self._field(i0, 'placeOfOrigin', {}).get('codeAlpha2', '?')
                    print(f'\n  [DEBUG] totalResults={total}')
                    print(f'  [DEBUG] item[0].id:          {self._field(i0, "id")}')
                    print(f'  [DEBUG] item[0].title:       {self._field(i0, "title","?")[:60]}')
                    print(f'  [DEBUG] item[0].slug:        {self._field(i0, "slug","?")}')
                    print(f'  [DEBUG] item[0].country:     {country}')
                    print(f'  [DEBUG] item[0].deadline:    {self._field(i0, "deadlineDate","?")}')
                    print(f'  [DEBUG] item[0].status:      {self._status(i0)}')
                    if total > 500_000:
                        print(f'  [WARN]  totalResults={total} is very high — placeOfOrigin filter may not be applied!')
                        print(f'  [WARN]  Check API payload — filter param name may differ.')
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
                if td.external_id and td.external_id in seen_ids:
                    continue
                if td.external_id:
                    seen_ids.add(td.external_id)
                page_new += 1
                combined = f'{td.title} {td.description}'
                if self.detect_relevance(combined):
                    results.append(td)
                    matched += 1

            print(f'-> {len(items)} items, {page_new} new, {matched} relevant')
            logger.info(
                f'[TenderWell] page {page}: '
                f'{len(items)} items, {page_new} new, {matched} relevant'
            )

            if len(items) < PAGE_SIZE:
                print(f'       > Partial page ({len(items)}) — end of data')
                break
            if total and page * PAGE_SIZE >= total:
                print(f'       > All {total} records fetched')
                break

            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant TenderWell Jordan tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[TenderWell] complete — total={len(results)}')
        return results

    # ── Authentication ────────────────────────────────────────────────────────

    def _login(self) -> str:
        """
        Get a fresh access token using TENDERWELL_REFRESH_TOKEN.

        Configure TENDERWELL_CLIENT_FINGERPRINT for your own authorized session.
        No browser credentials or session fingerprint are bundled with the code.
        """
        import os, json, base64
        from datetime import datetime

        # Supply your own refresh token through the environment.
        REFRESH_TOKEN = os.environ.get(
            'TENDERWELL_REFRESH_TOKEN',
            ''
        )
        FINGERPRINT = os.environ.get('TENDERWELL_CLIENT_FINGERPRINT', '')
        if not REFRESH_TOKEN:
            logger.info('[TenderWell] No refresh token configured')
            return ''

        # Check refresh token expiry
        try:
            rt_payload = json.loads(
                base64.b64decode(REFRESH_TOKEN.split('.')[1] + '==').decode()
            )
            rt_exp = datetime.fromtimestamp(rt_payload.get('exp', 0))
            if rt_exp < datetime.now():
                print(f'  [WARN] Refresh token expired at {rt_exp}')
                print(f'  [WARN] Update TENDERWELL_REFRESH_TOKEN in environment')
                logger.warning(f'[TenderWell] Refresh token expired at {rt_exp}')
                return ''
            print(f'  [DEBUG] Refresh token valid until {rt_exp}')
        except Exception:
            pass

        # Try refresh endpoints
        refresh_endpoints = [
            f'{BASE_URL}/api/frontend/auth/refresh',
            f'{BASE_URL}/api/frontend/auth/token/refresh',
            f'{BASE_URL}/api/frontend/auth/refresh-token',
            f'{BASE_URL}/api/auth/refresh',
        ]
        refresh_payloads = [
            {'refreshToken': REFRESH_TOKEN, 'fingerprint': FINGERPRINT},
            {'refreshToken': REFRESH_TOKEN},
            {'refresh_token': REFRESH_TOKEN},
        ]

        for endpoint in refresh_endpoints:
            for payload in refresh_payloads:
                try:
                    r = self.session.post(
                        endpoint, json=payload,
                        headers={**HEADERS, 'Referer': f'{BASE_URL}/tender/search'},
                        timeout=15
                    )
                    if r.status_code in (200, 201):
                        data = r.json()
                        print(f'  [DEBUG] Refresh OK: {endpoint}')
                        print(f'  [DEBUG] Response keys: {list(data.keys())[:10]}')
                        for key in ('token', 'accessToken', 'access_token', 'jwt'):
                            val = data.get(key)
                            if isinstance(val, str) and len(val) > 20:
                                return val
                        nested = data.get('data', {})
                        if isinstance(nested, dict):
                            for key in ('token', 'accessToken', 'access_token'):
                                val = nested.get(key)
                                if isinstance(val, str) and len(val) > 20:
                                    return val
                        logger.warning(f'[TenderWell] Refresh OK but no token in: {list(data.keys())}')
                        return ''
                    elif r.status_code == 401:
                        logger.warning(f'[TenderWell] Refresh 401 at {endpoint}')
                        break  # Wrong refresh token, no point trying other payloads
                    # 404/405 = wrong endpoint, try next
                except Exception as exc:
                    logger.debug(f'[TenderWell] Refresh error {endpoint}: {exc}')
                    continue

        logger.warning('[TenderWell] Token refresh failed — proceeding without auth')
        return ''



    # ── Item parser ───────────────────────────────────────────────────────

    def _parse_item(self, item: dict) -> Optional[TenderData]:
        try:
            title = self._field(item, 'title', '')
            if not title:
                return None

            tender_id = str(self._field(item, 'id', '') or '')
            slug      = self._field(item, 'slug', '')
            detail_url = f'{BASE_URL}/tender/{slug}' if slug else LIST_URL

            # Deadline
            dl_str   = self._field(item, 'deadlineDate', '')
            deadline = self._parse_iso(dl_str)

            today  = date.today()
            status_name = self._status(item)
            if status_name == 'OPEN' and deadline and deadline >= today:
                status        = 'OPEN'
                save_deadline = deadline
            elif status_name == 'OPEN':
                status        = 'OPEN'
                save_deadline = deadline  # let save_tender_data decide
            else:
                status        = 'CLOSED'
                save_deadline = None

            # Tender type as description
            tender_types = [
                t.get('prettyName', '')
                for t in item.get('tenderTypes', [])
                if t.get('prettyName')
            ]
            proc_types = [
                t.get('prettyName', '')
                for t in item.get('procedureTypes', [])
                if t.get('prettyName')
            ]
            desc_parts = tender_types + proc_types
            description = ' | '.join(desc_parts) if desc_parts else 'Tender'

            # Sectors
            sectors_raw = self._field(item, 'sectors', [])
            sector_str  = ', '.join(
                s.get('name', '') for s in (sectors_raw or []) if s.get('name')
            )

            return TenderData(
                title               = title,
                title_ar            = title,
                source_url          = detail_url,
                external_id         = tender_id,
                reference_no        = slug,
                entity              = 'TenderWell',
                entity_ar           = 'تيندرويل',
                description         = description,
                category            = self.detect_category(
                    f'{title} {description} {sector_str}'
                ),
                status              = status,
                currency            = 'USD',
                estimated_value     = None,
                publication_date    = None,   # anonymized without subscription
                submission_deadline = save_deadline,
                opening_date        = None,
            )

        except Exception as exc:
            logger.warning(f'[TenderWell] parse error: {exc}')
            return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _field(item: dict, key: str, default=None):
        """Safely get item[key].data if not anonymized."""
        f = item.get(key, {})
        if isinstance(f, dict):
            if f.get('anonymized'):
                return default
            val = f.get('data')
            return val if val is not None else default
        return default

    @staticmethod
    def _status(item: dict) -> str:
        """Get status name string."""
        s = item.get('status', {})
        if isinstance(s, dict) and not s.get('anonymized'):
            d = s.get('data', {})
            if isinstance(d, dict):
                return d.get('name', 'OPEN')
        return 'OPEN'

    @staticmethod
    def _parse_iso(val: str) -> Optional[date]:
        """Parse ISO datetime '2026-07-06T09:30:00Z' → date."""
        if not val or val.startswith('*'):
            return None
        try:
            return datetime.fromisoformat(
                val.replace('Z', '+00:00')
            ).date()
        except ValueError:
            try:
                return datetime.strptime(val[:10], '%Y-%m-%d').date()
            except ValueError:
                return None
