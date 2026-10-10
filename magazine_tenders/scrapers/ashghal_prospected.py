# magazine_tenders/scrapers/ashghal_prospected.py
"""
Ashghal — المشاريع المتوقعة (Prospected Projects), Qatar Public Works Authority.

    Page  : /en/Tenders/Pages/ProspectedTenders.aspx?Quarter=1
    Data  : SharePoint list "Prospected Tenders", read over its REST API

What this source is, and why it belongs in News rather than in Tenders
----------------------------------------------------------------------
Ashghal publishes, a quarter at a time, the projects it INTENDS to put out to
tender. They are not open for bids and carry no closing date — they are advance
notice of work coming, which is exactly the "what is about to happen" value the
News section exists for. The tenders that eventually result appear separately
through tenders/scrapers/ashghal.py, which reads the live e-Tenders listing.

Why the REST API and not the page
---------------------------------
ProspectedTenders.aspx renders nothing server-side. Its markup contains one
empty table and the message "Please enable scripts and reload this page"; the
rows are fetched by Style Library/en-us/CEP/Scripts/ProspectedProjects.js, which
runs a CAML query against a list named "Prospected Tenders". That same list is
anonymously readable over REST, so this scraper asks for the data directly
rather than driving a browser:

    /en/Tenders/_api/web/lists/getbytitle('Prospected Tenders')/items

Verified 2026-09-06: 394 items, of which 298 are Published and 97 are for 2026.

No announcement filter runs here
-------------------------------
The other News sources read general trade press, where most items are articles
and a filter has to reject the majority. This is the opposite: a first-party,
curated list where every published row IS a real forthcoming project. Filtering
it would only discard genuine entries whose wording happened not to match a
regex — the same reasoning the development-bank registers used.

Whether a project is AEC is decided by the shared classification service.
"""
import logging
import re
from datetime import date
from typing import Optional

from tenders.scrapers.base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.ashghal.gov.qa'
WEB_URL  = f'{BASE_URL}/en/Tenders'
LIST_API = (f"{WEB_URL}/_api/web/lists/getbytitle('Prospected Tenders')/items")
PAGE_URL = f'{BASE_URL}/en/Tenders/Pages/ProspectedTenders.aspx'

#: SharePoint returns JSON only when asked; without this header it answers XML.
_JSON = {'Accept': 'application/json;odata=nometadata'}

#: 'First Quarter' -> the ?Quarter= value the public page uses, so each row can
#: link back to the quarter it was announced in.
_QUARTER_PARAM = {
    'first quarter':  '1',
    'second quarter': '2',
    'third quarter':  '3',
    'fourth quarter': '4',
}

_TAG_RE = re.compile(r'<[^>]+>')


def strip_html(text: str) -> str:
    return ' '.join(_TAG_RE.sub(' ', text or '').split())


def _has_arabic(text: str) -> bool:
    return any('؀' <= ch <= 'ۿ' for ch in (text or ''))



def published_on(row: dict) -> Optional[date]:
    """
    The date this entry appeared on the site.

    SharePoint stamps both Created and Modified. Created is the publication
    date proper — when Ashghal added the row — and Modified is only a fallback
    for a response that lacks it (an older $select, a stored fixture). On the
    live list the two are identical for all 97 current rows: these entries are
    added once, in batches, and never edited afterwards.

    Returns None rather than guessing when neither parses. A wrong date is
    worse than no date here: publication_date drives the dashboard's "published
    today" bar and the freshness window, so an invented one would put a project
    in front of the CEO on a day nothing happened.
    """
    for field in ('Created', 'Modified'):
        raw = (row.get(field) or '').strip()
        if raw:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                logger.debug(f'[MAG:ASHGHALPROSPECTED] unparsable {field}={raw!r}')
    return None


class AshghalProspectedScraper(BaseScraper):
    """Forthcoming Ashghal projects, a quarter at a time."""

    CODE            = 'ASHGHALPROSPECTED'
    DEFAULT_COUNTRY = 'QA'

    #: Rows older than this many years are history, not a forecast. Ashghal
    #: leaves previous years in the list (297 of the 394 rows are 2025), so
    #: without this the section would fill with work already tendered.
    KEEP_FROM_YEAR_OFFSET = 0     # 0 = current year and later

    def scrape(self) -> list[TenderData]:
        source = getattr(self, 'source', None)
        if source is None:
            print(f'  X  [MAG:{self.CODE}] self.source not set — skipping')
            logger.error(f'[MAG:{self.CODE}] scrape() called without a source')
            return []

        rel_src, cat_src = self._reload_keywords_from_db()
        min_year = date.today().year + self.KEEP_FROM_YEAR_OFFSET

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : AshghalProspectedScraper')
        print(f'  MODE    : SharePoint list "Prospected Tenders" over REST')
        print(f'  FILTER  : Status=Published and Year >= {min_year}; no '
              f'announcement filter (every row is a real project)')
        print(f'  Keywords: {len(self._relevance_keywords)} relevance [{rel_src}]')
        print(f'{"_" * 62}')

        try:
            rows = self._fetch_items(min_year)
        except Exception as exc:
            print(f'  X  [MAG:{self.CODE}] fetch failed: {exc}')
            logger.warning(f'[MAG:{self.CODE}] fetch failed: {exc}')
            return []

        results = []
        for row in rows:
            td = self.build_one(row)
            if td is not None:
                results.append(td)

        self._add_arabic_titles(results)

        print(f'  TOTAL   : {len(results)} forthcoming project(s)')
        logger.info(f'[MAG:{self.CODE}] complete — total={len(results)}')
        return results

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetch_items(self, min_year: int) -> list[dict]:
        """
        Published rows for `min_year` onward.

        The year filter is applied here rather than in the OData query: Year is
        stored as a decimal (2026.00000000000) and SharePoint's comparison on it
        is unreliable across versions, so the query asks only for Published and
        the year is checked in Python where the behaviour is obvious.
        """
        params = {
            '$select': ('Id,Title,User_x0020_Department,Status,Quarter,'
                        'Year,Created,Modified'),
            '$filter': "Status eq 'Published'",
            '$top':    '1000',
        }
        text = self._get(LIST_API, params)
        import json
        rows = json.loads(text).get('value', [])

        kept = []
        for r in rows:
            # Status is re-checked here even though the query asked for
            # Published. The filter is the server's promise; this is ours. An
            # unfiltered response — a changed query, an ignored $filter — would
            # otherwise publish Ashghal's unapproved drafts, and nothing
            # downstream would notice.
            if (r.get('Status') or '').strip().lower() != 'published':
                continue
            try:
                year = int(float(r.get('Year') or 0))
            except (TypeError, ValueError):
                year = 0
            if year >= min_year:
                r['_year'] = year
                kept.append(r)
        print(f'       > {len(rows)} row(s) returned, {len(kept)} published '
              f'and dated {min_year}+')
        return kept

    def _get(self, url: str, params: dict) -> str:
        from curl_cffi import requests as cr
        r = cr.get(url, params=params, headers=_JSON,
                   impersonate='chrome', timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code} for {url}')
        return r.text

    def _add_arabic_titles(self, items: list) -> None:
        """
        Fill title_ar using the project's existing translation service.

        Needed because the shared classification service deliberately skips
        translation for anything it marks non-AEC — see the `if not is_aec:
        return updates` guard in tenders/ajibrain_client.py. That is a sensible
        saving in general, but this source is a government project pipeline
        read by Arabic-speaking staff, and three quarters of its rows classify
        as non-AEC (73 of 97 on the first import), so they would stay
        English-only for ever.

        This calls the SAME service the rest of the app uses. It does not
        change the classifier, and it does not touch is_aec.

        A failure is not fatal: the row is stored with an empty title_ar and
        the English title still displays, which is what happened before.
        """
        try:
            from tenders.ajibrain_client import translate_title
        except Exception as exc:                            # pragma: no cover
            logger.warning(f'[MAG:{self.CODE}] no translation service: {exc}')
            return

        done = 0
        for td in items:
            if td.title_ar or not td.title:
                continue
            try:
                arabic = translate_title(td.title)
            except Exception as exc:
                logger.warning(f'[MAG:{self.CODE}] translate failed: {exc}')
                continue
            # The service returns the input unchanged when it cannot translate.
            if arabic and arabic.strip() != td.title.strip() and _has_arabic(arabic):
                td.title_ar = arabic[:500]
                done += 1
        print(f'       > Arabic titles: {done} of {len(items)}')
        logger.info(f'[MAG:{self.CODE}] translated {done}/{len(items)} title(s)')

    # ── mapping ──────────────────────────────────────────────────────────────
    def build_one(self, row: dict) -> Optional[TenderData]:
        title = strip_html(row.get('Title', ''))
        if not title:
            return None

        dept    = strip_html(row.get('User_x0020_Department', ''))
        quarter = (row.get('Quarter') or '').strip()
        year    = row.get('_year') or ''
        qparam  = _QUARTER_PARAM.get(quarter.lower(), '')

        url = PAGE_URL
        if qparam:
            url = f'{PAGE_URL}?Quarter={qparam}'
            if year:
                url += f'&Year={year}'

        when = ' '.join(x for x in (quarter, str(year)) if x)
        desc = f'Forthcoming Ashghal project, expected {when}.' if when else \
               'Forthcoming Ashghal project.'
        if dept:
            desc += f' Department: {dept}.'

        return TenderData(
            title               = title[:500],
            title_ar            = '',
            source_url          = url,
            external_id         = f'mag-ashghalpp-{row.get("Id")}'[:200],
            reference_no        = '',
            entity              = (f'Public Works Authority (Ashghal) — {dept}'
                                   if dept else
                                   'Public Works Authority (Ashghal)')[:200],
            entity_ar           = 'هيئة الأشغال العامة',
            description         = desc[:4000],
            description_ar      = '',
            category            = self._safe_category(f'{title} {dept}'),
            country_code        = self.DEFAULT_COUNTRY,
            # Announced, not yet tendered: there is no closing date to give.
            submission_deadline = None,
            # The date Ashghal published the entry, read from the list itself.
            publication_date    = published_on(row),
            status              = 'OPEN',
            force_relevant      = True,
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _safe_category(self, text: str) -> str:
        """Provisional; the classification service overwrites it."""
        try:
            return self.detect_category(text)
        except AttributeError:
            return 'OTHER'
