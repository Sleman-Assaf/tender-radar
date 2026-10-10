# magazine_tenders/quarters.py
"""
The quarter a forthcoming project is expected in.

Future Projects is a pipeline, not a news feed: nothing in it has happened
yet, so a publication date says nothing a reader wants and the useful order is
when the work is expected. This module produces one sortable key for that.

The key is 'YYYYQN' - '2026Q3' - deliberately. It sorts chronologically as
plain text, so the page can order on it in the database and keep pagination
honest, with no second sort in Python.

Two sources, because the two feeds state it differently:

* Ashghal writes it in the description: "expected Third Quarter 2026".
* DevelopmentAid gives a date instead - the donor's own estimate of when the
  notice will be published - which falls in a quarter like any other date.

An item that states neither gets '', and the page sorts those last: unknown
is a real answer for a pipeline and must not masquerade as Q1.
"""
import re
from datetime import date
from typing import Optional

#: "expected Third Quarter 2026", as Ashghal writes it.
_WORDED_QUARTER_RE = re.compile(
    r'expected\s+(First|Second|Third|Fourth)\s+Quarter\s+(\d{4})', re.I)

#: "Q3 2026" / "Q3-2026", as other notices sometimes write it.
_SHORT_QUARTER_RE = re.compile(r'\bQ([1-4])[\s\-/]*((?:19|20)\d{2})\b',
                               re.I)  # its sibling above is too

_WORD_TO_NUMBER = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4}


def quarter_of(day: date) -> int:
    """1-4 for any date. January is Q1, December is Q4."""
    return (day.month - 1) // 3 + 1


def quarter_key(description: str = '', fallback_date: Optional[date] = None) -> str:
    """
    'YYYYQN' for the quarter this project is expected in, or ''.

    The description wins when it states a quarter outright, because that is
    the publisher saying so. `fallback_date` - the donor's expected date - is
    used only when it does not.
    """
    text = description or ''

    m = _WORDED_QUARTER_RE.search(text)
    if m:
        return f'{m.group(2)}Q{_WORD_TO_NUMBER[m.group(1).lower()]}'

    m = _SHORT_QUARTER_RE.search(text)
    if m:
        return f'{m.group(2)}Q{m.group(1)}'

    if isinstance(fallback_date, date):
        return f'{fallback_date.year}Q{quarter_of(fallback_date)}'

    return ''


def current_key(today: Optional[date] = None) -> str:
    """'2026Q4' for today - the quarter the Future Projects page opens on."""
    today = today or date.today()
    return f'{today.year}Q{quarter_of(today)}'


def key_number(key: str) -> int:
    """'2026Q4' -> 20264, so quarters sort as integers; '' -> 0."""
    if not key or len(key) != 6 or not key[:4].isdigit() or not key[5].isdigit():
        return 0
    return int(key[:4]) * 10 + int(key[5])


def quarter_label(key: str) -> str:
    """'2026Q3' as 'Q3 2026', which is how a reader writes it."""
    if (not key or len(key) != 6 or key[4] != 'Q'
            or not key[5].isdigit() or not key[:4].isdigit()):
        return ''
    return f'Q{key[5]} {key[:4]}'
