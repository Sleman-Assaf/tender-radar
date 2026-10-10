# tenders/scrapers/dtvp_giz.py
"""
DTVP — Deutsches Vergabeportal (GIZ international tenders via search API)
URL: https://www.dtvp.de/Center/common/project/search.do?method=showExtendedSearch

What is this?
-------------
DTVP (Deutsches Vergabeportal) is Germany's main e-procurement portal.
This scraper uses the DTVP JSON search API to retrieve GIZ international
development cooperation tenders only (searchText="GIZ").

Technology: REST JSON API (v2), requires a short-lived JWT token extracted
from the search page HTML.

Workflow:
  1. GET search page to extract JWT token from hidden <input id="token">
  2. POST /Center/api/v2/project/search with token + GIZ filter
  3. Paginate via page=1,2,3... until page >= allPages

API Endpoint:
    POST https://www.dtvp.de/Center/api/v2/project/search
    Content-Type: application/x-www-form-urlencoded
    X-Requested-With: XMLHttpRequest

Payload (confirmed from page source Handlebars template):
    searchText          = "GIZ"
    page                = "1"
    sortField           = "PROJECT_PUBLICATION_DATE_LNG"
    order               = "0"  (0=DESC newest first)
    publicationTypes[]  = "ExAnte", "Tender", "ExPost"
    contractingRules[]  = "VOL", "VOB", "VSVGV", "SEKTVO", "OTHER"
    distanceRadio       = "national"
    postalCode          = ""
    distance            = "0"
    token               = <JWT from page hidden input>

Response JSON (confirmed from Handlebars template in page HTML):
    {
        "countOfAllDataSets": 42,
        "allPages": 3,
        "searchParameter": {"page": 1},
        "projects": [
            {
                "title":            "10009137-Women Entrepreneurs Finance Code In Jordan",
                "organisationName": "Deutsche Gesellschaft für Internationale Zusammenarbeit (GIZ) GmbH",
                "publishingDate":   "12.06.2026",
                "publishingDateLong": "12.06.2026 at 12:07 o'clock",
                "relevantDate":     "03.07.2026",
                "relevantDateLong": "03.07.2026 at 14:00 o'clock",
                "contractingRule":  "UVgO",
                "publicationType":  "<br/>Notice",
                "projectId":        "51160",
                "earmarked":        false,
                "links": {
                    "ENTER_PROJECTROOM": "/Center/public/company/projectForwarding.do?pid=51160"
                }
            }
        ]
    }

Type filtering (publicationType):
    KEEP: contains "Notice", "notice", "Ausschreibung", "TNW", "ExAnte"
    SKIP: contains "Contract award", "Vergebener Auftrag",
          "modification", "Auftragsänderung"

Country detection: same keyword-based approach as GIZTendersScraper.
"""

import logging
import re
from datetime import datetime, date
from typing import Optional

from bs4 import BeautifulSoup

from .base import BaseScraper, TenderData
from .giz_tenders import GIZTendersScraper

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.dtvp.de'
SEARCH_PAGE = f'{BASE_URL}/Center/common/project/search.do?method=showExtendedSearch&fromExternal=true&lang=en'
API_URL    = f'{BASE_URL}/Center/api/v2/project/search'
MAX_PAGES  = 3    # scan latest 3 pages (60 entries) for GIZ tenders

SKIP_TYPE_FRAGMENTS = [
    'contract award', 'vergebener auftrag',
    'modification', 'auftragsänderung', 'ex post', 'expost',
]

HEADERS_PAGE = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':             'text/html,application/xhtml+xml,*/*;q=0.9,*/*;q=0.8',
    'Accept-Language':    'en-US,en;q=0.9',
    'sec-ch-ua':          '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    'sec-ch-ua-mobile':   '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest':     'document',
    'sec-fetch-mode':     'navigate',
    'sec-fetch-site':     'none',
    'sec-fetch-user':     '?1',
    'upgrade-insecure-requests': '1',
}

HEADERS_API = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':             'application/json, text/javascript, */*; q=0.01',
    'Content-Type':       'application/json',
    'X-Requested-With':   'XMLHttpRequest',
    'Referer':            SEARCH_PAGE,
    'Origin':             BASE_URL,
    'Accept-Language':    'en-US,en;q=0.9',
    'sec-ch-ua':          '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    'sec-ch-ua-mobile':   '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest':     'empty',
    'sec-fetch-mode':     'cors',
    'sec-fetch-site':     'same-origin',
}


class DTVPGIZScraper(BaseScraper):
    """
    Scraper for DTVP central portal — GIZ international tenders via JSON API.

    Step 1: GET search page → extract JWT token
    Step 2: POST /Center/api/v2/project/search with searchText="GIZ"
    Step 3: Paginate via page param, skip award/modification notices
    Step 4: Country detection from title using GIZTendersScraper._detect_country()
    """

    country_code = 'ZZ'
    source_name  = 'DTVP — GIZ International Tenders'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='DTVPGIZScraper', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [DTVPGIZ] No active ScraperSource — run seed_data')
            return []
        except ScraperSource.MultipleObjectsReturned:
            source = ScraperSource.objects.filter(
                scraper_class='DTVPGIZScraper', is_active=True
            ).first()

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : DTVPGIZScraper')
        print(f'  MODE    : POST JSON API — latest notices, GIZ org filter')
        print(f'  FILTER  : organisationName=GIZ (Python filter, empty searchText)')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # ── Step 1: Prime session with cookies, then fetch JWT token
        # The API requires both JSESSIONID (session cookie) AND the token (CSRF)
        # Both are obtained by GETting the search page — session carries them automatically
        self.session.cookies.set('locale', 'ENGLISH', domain='www.dtvp.de')
        token = self._fetch_token()
        if token:
            jsid = (
                self.session.cookies.get('JSESSIONID', domain='www.dtvp.de')
                or self.session.cookies.get('JSESSIONID')
                or '(none)'
            )
            print(f'  TOKEN   : obtained ({len(token)} chars)')
            print(f'  SESSION : JSESSIONID={jsid[:20]}...' if jsid != '(none)' else '  SESSION : JSESSIONID not set!')
        else:
            print('  TOKEN   : not obtained — API will return 403')

        # ── Step 2: Paginate search API ───────────────────────────────────
        self.session.headers.update(HEADERS_API)
        # Also set Authorization header with token
        if token:
            self.session.headers['Authorization'] = f'Bearer {token}'

        results:   list[TenderData] = []
        seen_pids: set[str]        = set()
        page       = 1
        all_pages  = MAX_PAGES

        while page <= all_pages:
            print(f'       > page {page:>3}', end='  ', flush=True)

            payload = {
                'searchText':        '',   # empty = browser-like; GIZ filtered in Python
                'page':              str(page),
                'sortField':         'PROJECT_PUBLICATION_DATE_LNG',
                'order':             '0',
                'cpvCodes':          [],
                'publicationTypes':  ['Tender'],  # open notices only
                'contractingRules':  ['VOL', 'VOB', 'VSVGV', 'SEKTVO', 'OTHER'],
                'postalCode':        '',
                'distance':          0,
                'token':             token,
            }

            try:
                resp = self.session.post(API_URL, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                err = str(exc)
                if '403' in err or '401' in err:
                    print('(blocked)')
                    logger.warning(f'[DTVPGIZ] block page {page}: {exc}')
                else:
                    print(f'X ERROR: {exc}')
                    logger.error(f'[DTVPGIZ] page {page}: {exc}')
                break

            # Update pagination on first page
            if page == 1:
                all_pages = min(int(data.get('allPages', 1)), MAX_PAGES)
                total     = data.get('countOfAllDataSets', 0)
                print(f'(total={total}, pages={all_pages})', end='  ')

            projects = data.get('projects', [])
            if not projects:
                print('(empty — stopping)')
                break

            # Debug on page 1
            if page == 1 and projects:
                p0 = projects[0]
                print(f'\n  [DEBUG] title:    {p0.get("title","?")[:65]}')
                print(f'  [DEBUG] org:      {p0.get("organisationName","?")}')
                print(f'  [DEBUG] pub:      {p0.get("publishingDate","?")}')
                print(f'  [DEBUG] deadline: {p0.get("relevantDate","?")}')
                print(f'  [DEBUG] type:     {repr(p0.get("publicationType","?"))}')
                print(f'  [DEBUG] pid:      {p0.get("projectId","?")}')
                print()

            matched  = 0
            page_new = 0
            skipped  = 0

            for proj in projects:
                # publicationType: plain string e.g. "Notice", "Contract award notice"
                # May contain HTML abbr for CFC: '<abbr title="Call for competition">CFC</abbr>'
                pub_type_clean = re.sub(r'<[^>]+>', '', proj.get('publicationType') or '').strip().lower()

                # Skip award/modification notices
                if any(frag in pub_type_clean for frag in SKIP_TYPE_FRAGMENTS):
                    skipped += 1
                    continue

                title = (proj.get('title') or '').strip()
                if not title:
                    continue

                pid = str(proj.get('projectId') or '').strip().split('.')[0]  # int → str
                if pid and pid in seen_pids:
                    continue
                if pid:
                    seen_pids.add(pid)
                page_new += 1

                # Detail URL from links (already full URL in confirmed response)
                links     = proj.get('links') or {}
                detail_url = (
                    links.get('ENTER_PROJECTROOM') or
                    links.get('enterprojectroom') or
                    f'{BASE_URL}/Center/public/company/projectForwarding.do?pid={pid}'
                )

                # Dates
                pub_date = self._parse_dtvp_date(proj.get('publishingDate', ''))
                deadline = self._parse_dtvp_date(proj.get('relevantDate', ''))

                today  = date.today()
                if deadline and deadline < today:
                    status        = 'CLOSED'
                    save_deadline = None
                else:
                    status        = 'OPEN'
                    save_deadline = deadline

                # Country from title
                detected_cc = GIZTendersScraper._detect_country(title)

                # Type label (strip any HTML abbr tags)
                rule  = proj.get('contractingRule', '')
                ptype = re.sub(r'<[^>]+>', '', proj.get('publicationType', '')).strip()
                type_label = f'{rule} {ptype}'.strip()

                combined = f'{title} {type_label}'

                if self.detect_relevance(combined):
                    results.append(TenderData(
                        title               = title,
                        title_ar            = title,
                        source_url          = detail_url,
                        external_id         = pid,
                        reference_no        = pid,
                        entity              = proj.get('organisationName', 'GIZ'),
                        entity_ar           = 'GIZ — الشركة الألمانية للتعاون الدولي',
                        description         = type_label,
                        category            = self.detect_category(combined),
                        status              = status,
                        currency            = 'EUR',
                        estimated_value     = None,
                        publication_date    = pub_date,
                        submission_deadline = save_deadline,
                        opening_date        = None,
                        country_code        = detected_cc,
                    ))
                    matched += 1

            print(f'-> {len(projects)} projects, {page_new} new, {skipped} skipped, {matched} relevant')
            logger.info(f'[DTVPGIZ] page {page}: {page_new} new, {skipped} skipped, {matched} relevant')

            if page >= all_pages:
                break
            page += 1

        print(f'\n  {"=" * 58}')
        print(f'  TOTAL : {len(results)} relevant DTVP/GIZ tender(s)')
        print(f'  {"=" * 58}\n')
        logger.info(f'[DTVPGIZ] complete — total={len(results)}')
        return results

    # ── Helpers ───────────────────────────────────────────────────────────

    def _fetch_token(self) -> str:
        """
        Get JWT token required by DTVP search API.

        The token is NOT in the initial HTML (value="" in static page).
        It is fetched by JavaScript from a token API endpoint.

        To find the exact endpoint:
          1. Open DevTools → Network tab
          2. Load https://www.dtvp.de/Center/common/project/search.do?...
          3. Filter by "token" in the Network tab search box
          4. Look for a GET request returning {"token": "eyJ..."} or similar
          5. Update TOKEN_ENDPOINTS below with the correct URL

        Known: token is JWT (ES256), sub="0" (anonymous), expires in 10 min.
        """
        # Known token API endpoint candidates (JS-populated, not in static HTML)
        TOKEN_ENDPOINTS = [
            f"{BASE_URL}/Center/api/v2/token",
            f"{BASE_URL}/Center/api/v2/auth/token",
            f"{BASE_URL}/Center/api/v1/token",
            f"{BASE_URL}/Center/public/api/v2/token",
            f"{BASE_URL}/Center/api/token",
        ]
        api_hdrs = {
            "User-Agent":    HEADERS_PAGE["User-Agent"],
            "Accept":        "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer":       SEARCH_PAGE,
        }

        # Step A: Try dedicated token API endpoints
        for endpoint in TOKEN_ENDPOINTS:
            try:
                r = self.session.get(endpoint, headers=api_hdrs, timeout=10)
                print(f"  [DEBUG] Token endpoint [{r.status_code}]: {endpoint.replace(BASE_URL,'')}")
                if r.status_code == 200:
                    # Try JSON response first
                    try:
                        data = r.json()
                        for key in ("token", "accessToken", "jwt", "value"):
                            val = data.get(key, "")
                            if isinstance(val, str) and len(val) > 20:
                                print(f"  [DEBUG] Token via API key={key} ({len(val)} chars)")
                                return val
                    except Exception:
                        pass
                    # Try raw JWT in response text
                    m = re.search(
                        r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
                        r.text
                    )
                    if m:
                        print(f"  [DEBUG] Token via API JWT scan ({len(m.group(1))} chars)")
                        return m.group(1)
            except Exception as exc:
                logger.debug(f"[DTVPGIZ] token endpoint {endpoint}: {exc}")
                continue

        # Step B: Fallback — GET search page (token may be in HTML if session active)
        try:
            resp = self.session.get(
                SEARCH_PAGE,
                headers={
                    "User-Agent":      HEADERS_PAGE["User-Agent"],
                    "Accept":          HEADERS_PAGE["Accept"],
                    "Accept-Language": HEADERS_PAGE["Accept-Language"],
                },
                timeout=self.timeout
            )
            print(f"  [DEBUG] Search page: [{resp.status_code}] size={len(resp.text)}")
            if resp.status_code == 200:
                resp.encoding = "iso-8859-1"
                text = resp.text
                # BS4
                soup = BeautifulSoup(text, "html.parser")
                el = soup.find("input", {"id": "token"})
                if el and el.get("value", ""):
                    val = el["value"]
                    print(f"  [DEBUG] Token via page BS4 ({len(val)} chars)")
                    return val
                # JWT scan
                m = re.search(
                    r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
                    text
                )
                if m:
                    print(f"  [DEBUG] Token via page JWT ({len(m.group(1))} chars)")
                    return m.group(1)
                idx = text.find("token")
                ctx = text[idx:idx+200] if idx > -1 else text[:200]
                print(f"  [WARN] Token not in page. Snippet: {repr(ctx[:120])}")
        except Exception as exc:
            print(f"  [WARN] Page fetch error: {exc}")

        logger.warning("[DTVPGIZ] All token methods failed")
        print("  [WARN] Could not obtain token — API will return 403")
        return ""


    @staticmethod
    def _parse_dtvp_date(val: str) -> Optional[date]:
        """Parse 'DD.MM.YYYY' → date."""
        if not val or val.strip() in ('nv', 'n.a.', ''):
            return None
        try:
            return datetime.strptime(val.strip()[:10], '%d.%m.%Y').date()
        except ValueError:
            return None
