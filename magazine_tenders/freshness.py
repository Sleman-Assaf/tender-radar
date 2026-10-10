# magazine_tenders/freshness.py
"""
How old a News item may be before it is not news.

A rolling window rather than a fixed year. "2026 only" is right today and
wrong every January, and a window needs no deploy to tune — one setting.

Two callers, deliberately:

* `_newsfeed.py` turns the cutoff into a WordPress `after=` parameter, so the
  publisher filters and the old posts never travel. Construction Week returns
  24,425 posts unfiltered and 9 with a cutoff applied.
* `runner.py` applies it again at save time, as the backstop for the sources
  that cannot filter server-side. DevelopmentAid is the one that matters: its
  API has no date field and sorts by last-modified, so a 2017 procurement plan
  re-edited last week arrives looking recent. It was 58 of the 86 stale rows.

Undated items are ALWAYS kept. Every one of them is an Ashghal prospected
project — work that is coming, with no publication date by nature — and
reading "undated" as "old" would empty the Future Projects list.
"""
import calendar
import logging
from datetime import date, datetime
from typing import Optional

from django.conf import settings

logger = logging.getLogger('tenders')


def max_age_months() -> int:
    """
    The window, in months. 0 (or less) switches the whole thing off.

    Read at call time, not import time, so `override_settings` works in tests
    and an operator changing the value does not need a restart to see it.
    """
    try:
        return int(getattr(settings, 'MAGAZINE_MAX_AGE_MONTHS', 10) or 0)
    except (TypeError, ValueError):
        logger.warning('[magazine] MAGAZINE_MAX_AGE_MONTHS is not a number '
                       '\u2014 the freshness window is off')
        return 0


def subtract_months(anchor: date, months: int) -> date:
    """
    `anchor` minus `months`, clamped to the end of a shorter month.

    31 March minus one month is 28 February, not an invalid 31 February.
    Written out rather than pulled from dateutil, which this project does not
    depend on.
    """
    total = anchor.month - 1 - months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def cutoff_date(today: Optional[date] = None) -> Optional[date]:
    """
    The oldest publication date still accepted, or None when the window is off.

    An item published ON the cutoff is kept; the comparison is `<`.
    """
    months = max_age_months()
    if months <= 0:
        return None
    if today is None:
        # The project's clock, not the machine's. Everything else on the page
        # dates itself from django.utils.timezone, and mixing the two put the
        # cutoff a day out whenever the server's local date and the project
        # timezone disagreed.
        from django.utils import timezone
        today = timezone.localdate()
    return subtract_months(today, months)


def is_stale(published, today: Optional[date] = None) -> bool:
    """
    True when `published` falls outside the window.

    False for None \u2014 see the module docstring \u2014 and false for anything
    that is not a date, because a parsing failure elsewhere must not silently
    delete an item.
    """
    if published is None or not isinstance(published, date):
        return False
    # A datetime IS a date (it subclasses it), so the guard above lets one
    # through - and comparing a datetime to a date then raises TypeError,
    # which aborts the whole magazine's run inside runner.py. Narrow it to a
    # plain date first. The docstring promises this function never raises;
    # without this it did, for the one input the promise was written for.
    if isinstance(published, datetime):
        published = published.date()
    cut = cutoff_date(today)
    return cut is not None and published < cut
