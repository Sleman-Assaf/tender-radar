# tenders/scrapers/developmentaid.py
# Path: TenderRadar/tenders/scrapers/developmentaid.py
"""
DevelopmentAid -- https://www.developmentaid.org/tenders/search

Confirmed working endpoint: GET https://www.developmentaid.org/api/tenders

The server IP running Django may be blocked by the egress proxy.
If you get 403, fix options:

  Option 1 - Paste browser cookies into settings.py:
    1. Open https://www.developmentaid.org/tenders/search in Chrome
    2. F12 -> Network -> click the api/tenders request
    3. Copy the full "cookie:" header value
    4. In settings.py:  DA_COOKIES = "cf_clearance=...; _ga=...;"

  Option 2 - Run scraper directly on your machine:
    python manage.py scrape_source DevelopmentAidScraper

  Option 3 - Set a local proxy in settings.py:
    DA_PROXY = "http://127.0.0.1:8888"
"""

import json
import logging
import os
import re
import unicodedata
import time
from datetime import datetime
from typing import Optional

import requests

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL   = 'https://www.developmentaid.org'
MAX_PAGES  = 10
PAGE_SIZE  = 50
PAGE_DELAY = 1.2

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/147.0.0.0 Safari/537.36'
    ),
    'Accept':         'application/json, text/plain, */*',
    'Accept-Language':'en-US,en;q=0.9',
    'Origin':          BASE_URL,
    'Referer':         f'{BASE_URL}/tenders/search',
    'Sec-Fetch-Dest':  'empty',
    'Sec-Fetch-Mode':  'cors',
    'Sec-Fetch-Site':  'same-origin',
}

# Default location IDs confirmed from DevTools (AEC-relevant countries)
# 3=Jordan, 139=Kenya, 150=Morocco, 153=Mozambique, 154=Nigeria, 155=Niger,
# 166=Pakistan, 170=Palestine, 171=Philippines, 175=Rwanda, 181=Senegal, 184=Somalia
DEFAULT_LOCATIONS = [3, 139, 150, 153, 155, 154, 166, 170, 171, 175, 181, 184]

# Status 3 = Open tenders only
DEFAULT_STATUSES = [3]


def _build_payload(page: int, size: int, location_id=None,
                   statuses=None) -> dict:
    """
    Build the confirmed POST payload.

    location_id: single int (for subclass filters) or None (uses DEFAULT_LOCATIONS)
    statuses:    list of status ids, or None for DEFAULT_STATUSES.
                 3 = Open, 2 = Forecast. A subclass changes this the same way
                 the Rwanda variant changes `locations`.
    """
    locations = [location_id] if location_id else DEFAULT_LOCATIONS
    return {
        'filter': {
            'keyword':               {'searchedText': '', 'searchedFields': []},
            'tenderTypes':           [],
            'typesIsStrict':         False,
            'sectors':               [],
            'sectorsIsStrict':       False,
            'locations':             locations,
            'locationIsStrict':      False,
            'donors':                [],
            'contractingAuthorities': [],
            'statuses':              statuses or DEFAULT_STATUSES,
            'languages':             [],
            'budgetInEuroRange':     {'min': 0, 'max': 20000000},
            'ownPosts':              False,
        },
        'pageNr':   page,
        'pageSize': size,
        'sort':     'lastModifiedDate.desc',
    }

# ✓ Confirmed endpoint from DevTools:
#   POST https://www.developmentaid.org/api/frontend/tender/search
#   Note: "tender" (singular) not "tenders"
_ATTEMPTS = [
    ('POST', f'{BASE_URL}/api/frontend/tender/search'),
]

# DevelopmentAid location ID -> ISO country code
# IDs confirmed from DevTools payload: locations: [3,139,150,153,155,154,166,170,171,175,181,184]
_LOCATION_ID_MAP = {
    1:   'AF',  # Afghanistan
    2:   'AL',  # Albania
    3:   'JO',  # Jordan
    4:   'DZ',  # Algeria
    5:   'AO',  # Angola
    6:   'AR',  # Argentina
    7:   'AM',  # Armenia
    8:   'AZ',  # Azerbaijan
    9:   'BD',  # Bangladesh
    10:  'BY',  # Belarus
    11:  'BJ',  # Benin
    12:  'BO',  # Bolivia
    13:  'BA',  # Bosnia
    14:  'BW',  # Botswana
    15:  'BR',  # Brazil
    16:  'BF',  # Burkina Faso
    17:  'BI',  # Burundi
    18:  'KH',  # Cambodia
    19:  'CM',  # Cameroon
    20:  'CF',  # Central African Republic
    21:  'TD',  # Chad
    22:  'CO',  # Colombia
    23:  'CG',  # Congo
    24:  'CD',  # DR Congo
    25:  'CI',  # Cote d'Ivoire
    26:  'HR',  # Croatia
    27:  'CU',  # Cuba
    28:  'DJ',  # Djibouti
    29:  'DO',  # Dominican Republic
    30:  'EC',  # Ecuador
    31:  'EG',  # Egypt
    32:  'SV',  # El Salvador
    33:  'ER',  # Eritrea
    34:  'ET',  # Ethiopia
    35:  'FJ',  # Fiji
    36:  'GM',  # Gambia
    37:  'GE',  # Georgia
    38:  'GH',  # Ghana
    39:  'GT',  # Guatemala
    40:  'GN',  # Guinea
    41:  'GW',  # Guinea-Bissau
    42:  'GY',  # Guyana
    43:  'HT',  # Haiti
    44:  'HN',  # Honduras
    45:  'IN',  # India
    46:  'ID',  # Indonesia
    47:  'IQ',  # Iraq
    48:  'IL',  # Israel
    49:  'JM',  # Jamaica
    51:  'RW',  # Rwanda
    52:  'KZ',  # Kazakhstan
    53:  'KE',  # Kenya (some systems)
    54:  'KI',  # Kiribati
    55:  'KG',  # Kyrgyzstan
    56:  'LA',  # Laos
    57:  'LB',  # Lebanon
    58:  'LS',  # Lesotho
    59:  'LR',  # Liberia
    60:  'LY',  # Libya
    61:  'MG',  # Madagascar
    62:  'MW',  # Malawi
    63:  'ML',  # Mali
    64:  'MR',  # Mauritania
    65:  'MX',  # Mexico
    66:  'MD',  # Moldova
    67:  'MN',  # Mongolia
    68:  'ME',  # Montenegro
    69:  'MZ',  # Mozambique (some)
    70:  'MM',  # Myanmar
    71:  'NA',  # Namibia
    72:  'NP',  # Nepal
    73:  'NI',  # Nicaragua
    74:  'NE',  # Niger (some)
    75:  'NG',  # Nigeria (some)
    76:  'MK',  # North Macedonia
    77:  'PK',  # Pakistan (some)
    78:  'PA',  # Panama
    79:  'PG',  # Papua New Guinea
    80:  'PY',  # Paraguay
    81:  'PE',  # Peru
    82:  'PH',  # Philippines (some)
    83:  'PS',  # Palestine (some)
    84:  'RU',  # Russia
    85:  'ST',  # Sao Tome
    86:  'SN',  # Senegal (some)
    87:  'RS',  # Serbia
    88:  'SL',  # Sierra Leone
    89:  'SB',  # Solomon Islands
    90:  'SO',  # Somalia (some)
    91:  'ZA',  # South Africa
    92:  'SS',  # South Sudan
    93:  'LK',  # Sri Lanka
    94:  'SD',  # Sudan
    95:  'SR',  # Suriname
    96:  'SZ',  # Swaziland
    97:  'SY',  # Syria
    98:  'TJ',  # Tajikistan
    99:  'TZ',  # Tanzania
    100: 'TH',  # Thailand
    101: 'TL',  # Timor-Leste
    102: 'TG',  # Togo
    103: 'TO',  # Tonga
    104: 'TN',  # Tunisia
    105: 'TR',  # Turkey
    106: 'TM',  # Turkmenistan
    107: 'UG',  # Uganda
    108: 'UA',  # Ukraine
    109: 'UZ',  # Uzbekistan
    110: 'VU',  # Vanuatu
    111: 'VE',  # Venezuela
    112: 'VN',  # Vietnam
    113: 'YE',  # Yemen
    114: 'ZM',  # Zambia
    115: 'ZW',  # Zimbabwe
    116: 'MA',  # Morocco (some)
    139: 'KE',  # Kenya ✓ confirmed in DevTools
    150: 'MA',  # Morocco ✓ confirmed
    153: 'MZ',  # Mozambique ✓ confirmed
    154: 'NG',  # Nigeria ✓ confirmed
    155: 'NE',  # Niger ✓ confirmed
    166: 'PK',  # Pakistan ✓ confirmed
    170: 'PS',  # Palestine ✓ confirmed
    171: 'PH',  # Philippines ✓ confirmed
    175: 'RW',  # Rwanda ✓ confirmed (location_id=51 also maps to RW)
    181: 'SN',  # Senegal ✓ confirmed
    184: 'SO',  # Somalia ✓ confirmed
}


# Default cookies captured from DevTools — update DA_COOKIES in settings.py when expired
_DEFAULT_COOKIES = (
    '_ga=GA1.1.363487121.1775421817; '
    '_gcl_au=1.1.976529594.1775421817; '
    '_fbp=fb.1.1775421819249.895795203456313723; '
    'development_aid_banners_leftSideMenu1=eyJpdiI6IllNNGFydWJNVjdxUGkwV1NJUjgwTWc9PSIsInZhbHVlIjoieFBZaEo2VmdWcTBQSVNhcjRJbXlCM0drMEpPdzhWL2JrMmFNR3k2a2gyVm5tV3ZBUDRoV0tUdDJzUGh3ejFtUyIsIm1hYyI6IjQ4ZTg5MzMyYTcyMWUyMGMxMGY4YzllYTEzZjMzNzViMTBhMTUwN2M5MDQzZDFhYmU1ZjA2ZGMyMDczMmIxMTIiLCJ0YWciOiIifQ%3D%3D; '
    'development_aid_banners_top=eyJpdiI6IlVYYWw1Zyt3eUhDQng2bEtaSEdZcnc9PSIsInZhbHVlIjoiS1B4QkNGeGlRWHowcHJpcFNrMnRKSHBRbUlZRXBGOE1uSHJKbWtHWEFpWnZNUlU1NTZudEZhNjN2NmwzRGQ5eSIsIm1hYyI6IjJkOTBlNmFlNWY0NmUyOWQzMTI1ODNjNzJkYTI5OTliODVlYjM1NTNlZGJjZjM2YjhkOWE5OWMzYWI5Y2NmOTQiLCJ0YWciOiIifQ%3D%3D'
)


def _get_session_extras() -> dict:
    """Return proxy and cookies from Django settings / env, falling back to built-in cookies."""
    proxies = {}
    cookies = ''
    try:
        from django.conf import settings
        proxy = getattr(settings, 'DA_PROXY', '') or os.environ.get('DA_PROXY', '')
        cookies = (
            getattr(settings, 'DA_COOKIES', '')
            or os.environ.get('DA_COOKIES', '')
        )
    except Exception:
        proxy   = os.environ.get('DA_PROXY', '')
        cookies = os.environ.get('DA_COOKIES', '')

    if not cookies:
        cookies = _DEFAULT_COOKIES
        logger.info('[DevAid] Using built-in cookies (set DA_COOKIES in settings.py to refresh)')
    else:
        logger.info('[DevAid] Using DA_COOKIES from settings')

    if proxy:
        proxies = {'http': proxy, 'https': proxy}
        logger.info(f'[DevAid] Using proxy: {proxy}')

    return {'proxies': proxies, 'cookies': cookies.strip()}


def fold_accents(text: str) -> str:
    """
    'Cote d'Ivoire' from 'Côte d'Ivoire'.

    The country lookup is keyed on unaccented names and the feed sends the
    accented spelling, so without this every match fails on a character the
    reader cannot see. NFKD splits a letter from its diacritic; dropping the
    combining marks leaves the base letters.
    """
    if not text:
        return ''
    flat = ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c))
    # The typographic apostrophe is not a diacritic, so NFKD leaves it and
    # "Cote d<U+2019>Ivoire" still missed the key spelt with a plain quote.
    # JSON feeds carry the curly one routinely.
    return (flat.replace('\u2019', "'").replace('\u2018', "'")
                .replace('\u02bc', "'").replace('\u00b4', "'"))


#: A procurement reference welded to the front of a title. The source's `name`
#: field sometimes contains both with no separator:
#:
#:   MCA-CI-Regional/CONSULTANT/QCBS/CFPCRCI-26-4016Services juridiques externes
#:
#: A slash is required, because that is what a reference looks like and what
#: an ordinary title does not - 'P513144- Cote d'Ivoire Public Resource
#: Management' must come through untouched. The reference has to end on a
#: digit and be followed by a capital that starts a real word, which is the
#: only visible seam between the two fields.
#: A date written with slashes is not a reference - '12/09/2026Deadline
#: extended...' was storing the date as one. Digits and slashes alone
#: are refused; a real reference carries letters.
_GLUED_REFERENCE_RE = re.compile(r'^(\S*/\S*\d)(?=[A-Z][a-z])')
_DATE_LIKE_RE = re.compile(r'^[\d/\-.]+$')


def split_glued_reference(title):
    """
    Return (reference, title). Both empty-safe; the reference is '' when the
    title does not carry one.

    A title that is ONLY a reference is left whole: splitting it would leave
    an empty headline, which is worse than an ugly one.
    """
    raw = (title or '').strip()
    if not raw:
        return '', ''
    m = _GLUED_REFERENCE_RE.match(raw)
    if not m:
        return '', raw
    rest = raw[m.end():].strip()
    if not rest:
        return '', raw
    if _DATE_LIKE_RE.match(m.group(1)):
        # Digits and separators only - a date, not a reference. The title is
        # still split off, because the headline is the part that matters.
        return '', rest
    return m.group(1), rest


class DevelopmentAidScraper(BaseScraper):
    """
    Scraper for DevelopmentAid.org public tender listing.
    Confirmed endpoint: GET /api/tenders?pageNr=N&pageSize=25
    """

    source_name  = 'DevelopmentAid -- International Tenders'
    country_code = 'ZZ'  # international source — per-tender country resolved per item
    base_url     = BASE_URL
    location_id  = None  # None = global; set in subclasses for filtered sources
    #: 3 = Open, 2 = Forecast. Subclasses override.
    STATUSES     = DEFAULT_STATUSES

    def _make_session(self):
        """
        A session the API will actually answer.

        Measured 2026-09-06: a plain requests session gets HTTP 403 from
        /api/frontend/tender/search for every filter, so this source had been
        returning nothing at all. curl_cffi with browser impersonation gets
        200 — but only after the search PAGE has been fetched first, which is
        what sets the cookies the API checks.

        Falls back to requests if curl_cffi is unavailable, so the scraper
        degrades rather than raising on a machine without it.
        """
        try:
            from curl_cffi import requests as cr
        except ImportError:                                 # pragma: no cover
            logger.warning('[DevAid] curl_cffi missing — the API will likely '
                           'answer 403')
            session = requests.Session()
            session.headers.update(_HEADERS)
            return session

        session = cr.Session(impersonate='chrome')
        session.headers.update({k: v for k, v in _HEADERS.items()
                                if k.lower() != 'user-agent'})
        try:
            session.get(f'{BASE_URL}/tenders/search', timeout=30)
        except Exception as exc:
            logger.warning(f'[DevAid] cookie warm-up failed: {exc}')
        return session

    def scrape(self) -> list[TenderData]:
        session = self._make_session()

        loc      = self.location_id
        statuses = self.STATUSES

        # ── Find working endpoint (no cookies) ───────────────────────────
        working_method = None
        working_url    = None
        first_body     = None

        payload = _build_payload(1, PAGE_SIZE, loc, statuses)
        for method, url in _ATTEMPTS:
            try:
                if method == 'POST':
                    session.headers['Content-Type'] = 'application/json'
                    r = session.post(url, json=payload, timeout=15)
                else:
                    session.headers.pop('Content-Type', None)
                    r = session.get(url, params={'pageNr': 1, 'pageSize': PAGE_SIZE,
                                                  **({'locations': loc} if loc else {})},
                                    timeout=15)

                ct = r.headers.get('content-type', '')
                logger.info(f'[DevAid] {method} {url} -> {r.status_code} [{ct[:35]}]')

                if r.status_code == 200:
                    if 'json' not in ct:
                        logger.info(f'[DevAid] 200 but HTML (Angular shell) on {url}')
                        continue
                    body = r.json()
                    items = self._extract_items(body)
                    if items:
                        logger.info(f'[DevAid] Working: {method} {url} ({len(items)} items)')
                        logger.info(f'[DevAid] Item[0] keys: {list(items[0].keys())}')
                        working_method = method
                        working_url    = url
                        first_body     = body
                        break
                    else:
                        logger.info(f'[DevAid] 200 JSON but no items. '
                                    f'Keys: {list(body.keys()) if isinstance(body, dict) else type(body).__name__} '
                                    f'Preview: {str(body)[:200]}')
                elif r.status_code == 404:
                    logger.info(f'[DevAid] 404 on {url}')
                elif r.status_code == 403:
                    logger.info(f'[DevAid] 403 on {url} (IP blocked)')
                elif r.status_code == 405:
                    logger.info(f'[DevAid] 405 Method Not Allowed on {url}')
            except Exception as exc:
                logger.info(f'[DevAid] Error {url}: {exc}')

        if not working_url:
            logger.error(
                '[DevAid] No working endpoint. If all returned 403, run locally:\n'
                '  python manage.py run_scrapers --source DevelopmentAidScraper\n'
                '  python manage.py run_scrapers --source DevelopmentAidRwandaScraper'
            )
            return []

        # ── Paginate ──────────────────────────────────────────────────────
        results:  list[TenderData] = []
        seen_ids: set[str]         = set()

        for page_num in range(1, MAX_PAGES + 1):
            body = first_body if page_num == 1 else None

            if body is None:
                payload = _build_payload(page_num, PAGE_SIZE, loc, statuses)
                try:
                    if working_method == 'POST':
                        r = session.post(working_url, json=payload, timeout=20)
                    else:
                        r = session.get(working_url,
                                        params={'pageNr': page_num, 'pageSize': PAGE_SIZE,
                                                **({'locations': loc} if loc else {})},
                                        timeout=20)
                    r.raise_for_status()
                    body = r.json()
                except Exception as exc:
                    logger.error(f'[DevAid] Page {page_num} error: {exc}')
                    break

            items = self._extract_items(body)
            if not items:
                logger.info(f'[DevAid] Page {page_num}: empty — done')
                break

            if page_num == 1 and items:
                logger.info(f'[DevAid] Item[0] keys: {list(items[0].keys())}')

            new_count = 0
            for t in self._parse_items(items):
                eid = t.external_id or ''
                if eid and eid in seen_ids:
                    continue
                if eid:
                    seen_ids.add(eid)
                results.append(t)
                new_count += 1

            logger.info(
                f'[DevAid] Page {page_num}: {len(items)} items, {new_count} new'
            )

            if len(items) < PAGE_SIZE:
                break

            time.sleep(PAGE_DELAY)

        logger.info(f'[DevAid] Done — {len(results)} tenders')
        return results

    @staticmethod
    def _extract_items(body) -> list:
        """Unwrap common API envelope shapes."""
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ('data', 'items', 'tenders', 'results',
                        'records', 'procurementNotices', 'notices', 'content'):
                val = body.get(key)
                if isinstance(val, list):
                    return val
                if isinstance(val, dict):
                    for inner in ('items', 'tenders', 'data', 'results', 'records'):
                        if isinstance(val.get(inner), list):
                            return val[inner]
        return []

    def _parse_items(self, raw: list) -> list[TenderData]:
        results = []
        for item in raw:
            if not isinstance(item, dict):
                continue

            # ── Confirmed field names from API response ────────────────────
            title = str(item.get('name') or '').strip()
            if not title or len(title) < 4:
                continue
            # Some notices arrive as reference+title in one string. Keep the
            # reference - it is the only real one this API offers - and take
            # it out of the headline.
            glued_ref, title = split_glued_reference(title)

            tid  = str(item.get('id') or '')
            slug = item.get('slug') or tid

            # DA detail URL: /tenders/view/{id}/{slug}
            source_url = f'{BASE_URL}/tenders/view/{tid}/{slug}' if tid and slug else ''
            if not source_url:
                source_url = f'{BASE_URL}/tenders/search'

            # The API carries no reference field, so the id is the fallback.
            # A reference welded into the title is the real thing and wins.
            reference = glued_ref or tid

            # Funding org
            entity = str(item.get('organizationName') or '').strip()
            if not entity:
                donors = item.get('abbreviatedDonorNames') or []
                if isinstance(donors, list):
                    entity = ', '.join(str(d) for d in donors if d)
                else:
                    entity = str(donors)

            description = ''  # not in listing API — only in detail page

            # Category from types list
            types = item.get('types') or []
            if isinstance(types, list):
                raw_cat = ', '.join(
                    (t.get('name', '') if isinstance(t, dict) else str(t))
                    for t in types
                )
            else:
                raw_cat = str(types)

            # Country from locationNames list — map first entry to ISO code
            location_names = item.get('locationNames') or []
            if isinstance(location_names, list) and location_names:
                location = location_names[0]
            else:
                location = str(location_names)

            pub_raw  = item.get('postedDate') or item.get('lastModifiedDate') or ''
            dead_raw = item.get('deadline') or ''

            # The API sends a dict: {'id': 2, 'name': 'forecast', 'stage': ...}.
            # It was previously read via str(dict), which returned OPEN for
            # everything that did not happen to contain the word "closed" —
            # right answer, wrong reason. Read the fields.
            raw_status = item.get('status')
            if isinstance(raw_status, dict):
                name = str(raw_status.get('name') or '').lower()
                sid  = raw_status.get('id')
                # 2 = forecast: announced, not yet open. OPEN is correct for
                # it — there is nothing to have closed yet.
                status = ('CLOSED'
                          if any(w in name for w in ('clos', 'expir', 'award',
                                                     'cancel'))
                          else 'OPEN')
                if sid == 3:
                    status = 'OPEN'
            elif isinstance(raw_status, int):
                status = 'OPEN' if raw_status in (2, 3) else 'CLOSED'
            else:
                s = str(raw_status or '').lower()
                status = ('CLOSED'
                          if any(w in s for w in ('clos', 'expir', 'award',
                                                 'cancel'))
                          else 'OPEN')

            currency = str(item.get('currency') or 'USD')

            results.append(TenderData(
                title               = title[:300],
                source_url          = source_url,
                external_id         = tid or slug,
                reference_no        = reference[:100],
                entity              = entity[:200],
                entity_ar           = '',
                description         = description,
                category            = self._map_category(raw_cat, title),
                status              = status,
                currency            = currency,
                publication_date    = self._parse_date(pub_raw),
                submission_deadline = self._parse_date(dead_raw),
                # The organisation is the fallback when the notice
                # carries no usable location - it frequently names the
                # country that the location field left out.
                country_code        = self._resolve_country(
                    str(location).strip(), entity),
            ))
        return results

    @staticmethod
    def _map_category(raw_cat: str, title: str = '') -> str:
        t = (raw_cat + ' ' + title).lower()
        if any(w in t for w in (
            'consult', 'advisory', 'audit', 'feasibility',
            'study', 'survey', 'supervision', 'technical assistance',
        )):
            return 'CONSULTANCY'
        if any(w in t for w in (
            'works', 'civil', 'construction', 'infrastructure',
            'rehabilitation', 'road', 'building', 'installation',
        )):
            return 'WORKS'
        if any(w in t for w in (
            'supply', 'goods', 'equipment', 'material', 'vehicles', 'furniture',
        )):
            return 'GOODS'
        return 'SERVICES'

    _COUNTRY_MAP = {
        'guinea': 'GN', "cote d'ivoire": 'CI', 'ivory coast': 'CI',
        'democratic republic of congo': 'CD', 'dem. rep. congo': 'CD',
        'congo': 'CG', 'jordan': 'JO', 'kenya': 'KE', 'ethiopia': 'ET',
        'tanzania': 'TZ', 'mozambique': 'MZ', 'uganda': 'UG', 'ghana': 'GH',
        'nigeria': 'NG', 'senegal': 'SN', 'mali': 'ML', 'niger': 'NE',
        'burkina faso': 'BF', 'cameroon': 'CM', 'south sudan': 'SS',
        'sudan': 'SD', 'somalia': 'SO', 'egypt': 'EG', 'morocco': 'MA',
        'algeria': 'DZ', 'tunisia': 'TN', 'libya': 'LY', 'iraq': 'IQ',
        'syria': 'SY', 'lebanon': 'LB', 'palestine': 'PS', 'yemen': 'YE',
        'afghanistan': 'AF', 'pakistan': 'PK', 'bangladesh': 'BD',
        'india': 'IN', 'nepal': 'NP', 'myanmar': 'MM', 'cambodia': 'KH',
        'haiti': 'HT', 'ukraine': 'UA', 'georgia': 'GE', 'moldova': 'MD',
        'zambia': 'ZM', 'zimbabwe': 'ZW', 'malawi': 'MW', 'rwanda': 'RW',
        'burundi': 'BI', 'madagascar': 'MG', 'togo': 'TG', 'benin': 'BJ',
        'liberia': 'LR', 'sierra leone': 'SL', 'gambia': 'GM',
        'mauritania': 'MR', 'chad': 'TD', 'central african': 'CF',
        # Compound names that CONTAIN another country's name. Longest-first
        # matching settles them, but only if the long one is listed at all -
        # without these, Papua New Guinea was filed under Guinea.
        'papua new guinea': 'PG', 'guinea-bissau': 'GW', 'somaliland': 'SO',
        'south africa': 'ZA', 'south african': 'ZA', 'angola': 'AO',
        'namibia': 'NA', 'botswana': 'BW', 'lesotho': 'LS', 'eswatini': 'SZ',
        'guinea-bissau': 'GW', 'equatorial guinea': 'GQ', 'gabon': 'GA',
        'eritrea': 'ER', 'djibouti': 'DJ', 'comoros': 'KM',
        'seychelles': 'SC', 'mauritius': 'MU', 'cabo verde': 'CV',
        'cape verde': 'CV', 'turkiye': 'TR', 'turkey': 'TR',
        'sao tome': 'ST', 'peru': 'PE', 'panama': 'PA',
        'congo, dem. rep.': 'CD', 'congo, rep.': 'CG',
        'global': 'ZZ', 'worldwide': 'ZZ', 'multiple': 'ZZ',
    }

    def _resolve_country(self, name: str, fallback_text: str = '') -> str:
        """
        The notice's location as an ISO code, or 'ZZ' for international.

        `fallback_text` is consulted ONLY when the location yields nothing -
        typically the funding organisation, which often names the country
        outright ("Millennium Challenge Account Côte d'Ivoire"). A genuinely
        regional notice names no country in either field and stays ZZ.
        """
        code = self._resolve_country_name(name)
        if code == 'ZZ' and fallback_text:
            # names_only: the fallback is an ORGANISATION, and the two-letter
            # shortcut would read a donor abbreviation - EU, UN, AU - as a
            # country and have ensure_countries() invent a row for it.
            code = self._resolve_country_name(fallback_text, names_only=True)
        return code

    def _resolve_country_name(self, name: str, names_only: bool = False) -> str:
        if not name:
            return 'ZZ'
        if not names_only:
            # Already a 2-char ISO code. Only trusted for a LOCATION field;
            # see _resolve_country for why an organisation never gets this.
            if len(name) == 2 and name.isupper():
                return name
            # Numeric location ID
            if name.isdigit():
                return _LOCATION_ID_MAP.get(int(name), 'ZZ')
        # Accents folded before matching: the table is keyed on unaccented
        # names and the feed sends 'Cote d'Ivoire' as 'Côte d'Ivoire'.
        nl = fold_accents(name).lower().strip()
        # Longest key first, so 'Congo, Dem. Rep.' cannot be answered by the
        # 'congo' entry - that is a different country (CG, not CD).
        for k in sorted(self._COUNTRY_MAP, key=len, reverse=True):
            # On a word boundary, not anywhere in the string: a bare `in`
            # filed Somaliland under Mali and Papua New Guinea under Guinea.
            # Longest-first still settles the real overlaps (Congo, Niger).
            if re.search(rf'(?<![a-z]){re.escape(k)}(?![a-z])', nl):
                return self._COUNTRY_MAP[k]
        return 'ZZ'  # international / unknown

    @staticmethod
    def _parse_date(val) -> Optional[datetime.date]:
        if not val:
            return None
        s = str(val).strip()
        if not s or s in ('null', 'None', '0'):
            return None
        s = re.sub(r'\.\d+', '', s).rstrip('Z')
        for fmt in (
            '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M',
            '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y',
            '%d %b %Y', '%B %d, %Y', '%b %d, %Y', '%d.%m.%Y',
        ):
            try:
                return datetime.strptime(s[:20].strip(), fmt).date()
            except ValueError:
                continue
        return None


class DevelopmentAidRwandaScraper(DevelopmentAidScraper):
    """
    DevelopmentAid tenders filtered to Rwanda (locations=51).
    URL: https://www.developmentaid.org/tenders/search?locations=51
    """
    source_name  = 'DevelopmentAid -- Rwanda Tenders'
    country_code = 'RW'
    location_id  = 51
