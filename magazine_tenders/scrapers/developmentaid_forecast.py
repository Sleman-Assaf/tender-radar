# magazine_tenders/scrapers/developmentaid_forecast.py
"""
DevelopmentAid — FORECAST notices, for News → Future Projects.

    https://www.developmentaid.org/tenders/search?hiddenAdvancedFilters=0&statuses=2

Reuses tenders/scrapers/developmentaid.py rather than duplicating it. That
module already handles the parts worth not rewriting: the POST payload the
API expects, paging, the country-name-to-code resolution across the twelve
locations the business tracks, category mapping, and date parsing. The only
difference here is one filter value.

Why it belongs in News and not in Tenders
-----------------------------------------
`statuses=2` is DevelopmentAid's "forecast" stage: work a donor has announced
but not yet opened for bids. Same character as the Ashghal
المشاريع المتوقعة list — advance notice, not an opportunity you can respond to
today — so it belongs beside it under Future Projects. The OPEN notices from
the same site (statuses=3) continue to arrive through the existing
DevelopmentAidScraper in the Tenders section.

A forecast notice DOES often carry a deadline — the date the donor expects to
publish, months out — so unlike the Ashghal list these rows have one. That is
the donor's own estimate, not a bid deadline, and it is why the status is set
to OPEN rather than being judged against today's date.

Whether a notice is AEC is decided by the shared classification service.
"""
import logging
import re

from tenders.scrapers.developmentaid import DevelopmentAidScraper

logger = logging.getLogger('tenders')

#: DevelopmentAid status ids. 2 = forecast, 3 = open.
FORECAST_STATUS = 2

#: The World Bank project code a notice belongs to, e.g. "P160865".
PROJECT_CODE_RE = re.compile(r'\bP\d{6}\b')

#: Procurement-notice boilerplate. Stripped from a title to see whether the
#: notice actually names a project underneath it.
BOILERPLATE_RE = re.compile(
    r'\bP\d{6}\b'
    r'|\b(?:PPM|GPN|RFIN|SPN|EOI)\b'
    r'|general\s+procurement\s+notice'
    r'|request\s+for\s+information\s+notice'
    r'|specific\s+procurement\s+notice'
    r'|procurement\s+plan(?:\s*n[o°]?\s*\d+)?'
    r'|plan\s+de\s+passation\s+des\s+march[eé]s'
    r'|\(.*?\)',
    re.I)


def names_a_project(title: str) -> bool:
    """
    True when a title identifies something beyond the boilerplate.

    Measured against the live feed: this drops "General Procurement Notice -
    Procurement Plan n°30" and keeps "Kandadji Project - Procurement Plan".
    The threshold is four letters rather than a word count, because a real
    project can be named as briefly as "Dzap" (Digital Zambia Acceleration
    Project) and dropping that would lose a genuine notice.
    """
    core = BOILERPLATE_RE.sub(' ', title or '')
    return len(re.sub(r'[^A-Za-z\u00c0-\u024f]', '', core)) >= 4


class DevelopmentAidForecastScraper(DevelopmentAidScraper):
    """The same source, asked for forecast notices instead of open ones."""

    CODE         = 'DEVAIDFORECAST'
    source_name  = 'DevelopmentAid — Forecast Notices'
    #: Per-item, resolved from the notice's own location by the parent class.
    country_code = 'ZZ'
    STATUSES     = [FORECAST_STATUS]

    def scrape(self):
        """
        The parent's fetch and parse, then two feed-specific filters.

        Deliberately wraps scrape() rather than overriding _parse_items: the
        parsing stays inherited, and what is added here is clearly a filter
        over the result.
        """
        items = super().scrape() or []
        return self._drop_boilerplate_and_revisions(items)

    def _drop_boilerplate_and_revisions(self, items: list) -> list:
        """
        Remove notices that name no project, and keep one per project code.

        Both problems are particular to this feed. It carries a fresh notice
        for every revision of a World Bank procurement plan, so a single
        project arrived up to four times (P160865 appeared 4x, P177816 3x —
        11 duplicate rows out of 100). And some notices are pure boilerplate
        with no project named at all.

        The API sorts by lastModifiedDate descending, so the first notice seen
        for a code is the newest and is the one kept.
        """
        kept, seen_codes = [], set()
        dropped_boilerplate = dropped_revision = 0

        for td in items:
            if not names_a_project(td.title):
                dropped_boilerplate += 1
                continue
            code = PROJECT_CODE_RE.search(td.title or '')
            if code:
                key = code.group(0).upper()
                if key in seen_codes:
                    dropped_revision += 1
                    continue
                seen_codes.add(key)
            kept.append(td)

        if dropped_boilerplate or dropped_revision:
            print(f'       > dropped {dropped_boilerplate} boilerplate, '
                  f'{dropped_revision} plan revision(s)')
            logger.info(f'[MAG:{self.CODE}] dropped '
                        f'{dropped_boilerplate} boilerplate + '
                        f'{dropped_revision} revision(s); kept {len(kept)}')
        return kept
