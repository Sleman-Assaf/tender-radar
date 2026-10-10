# newspaper_tenders/dustour.py
# Ad-Dustour (الدستور) daily edition discovery — verified live 2026-08-20.
#
# How the site works:
#   * https://www.addustour.com/pdf-viewer/pdf-flip-book-viewer.php renders a
#     flip-book viewer whose archive side panel lists recent editions:
#       <a href="/pdf-viewer/pdf-flip-book-viewer.php?fileid=768213"
#          title="الخميس 7 ربيع الأول 1448 هـ الموافق 20 آب 2026م العدد 21188">
#     The Gregorian date follows "الموافق" (Levantine month names — the same
#     names Al-Rai uses, so the alrai month map is reused).
#   * The full edition PDF downloads from
#     /pdf-viewer/download-pdf.php?fileid=<id>  (~22 MB, ~29 pages, WITH a
#     text layer → same page-splitting flow as Al-Ghad).

import logging
import re
from datetime import date

import requests
from django.conf import settings

from .alrai import ARABIC_MONTHS, DiscoveryResult, EditionArticle, _headers, _norm_ar

logger = logging.getLogger('tenders')

VIEWER_URL = 'https://www.addustour.com/pdf-viewer/pdf-flip-book-viewer.php'
DOWNLOAD_URL = 'https://www.addustour.com/pdf-viewer/download-pdf.php'

# fileid link followed by the dated title on the same anchor
_ITEM_RE = re.compile(
    r'pdf-flip-book-viewer\.php\?fileid=(\d+)"\s+title="([^"]*?الموافق[^"]*?)"')
# "الموافق 20 آب 2026" (normalised text, madda collapsed)
_GREG_RE = re.compile(r'الموافق\s+(\d{1,2})\s+(\S+(?:\s+(?:الاول|الثاني))?)\s+(\d{4})')


def _viewer_url() -> str:
    return getattr(settings, 'NEWSPAPER_DUSTOUR_URL', VIEWER_URL)


def parse_title_date(title: str):
    """Gregorian date out of an edition title, or None."""
    m = _GREG_RE.search(_norm_ar(title))
    if not m:
        return None
    day, month_name, year = m.groups()
    month = ARABIC_MONTHS.get(_norm_ar(month_name).strip())
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def list_editions() -> list[tuple[date, str]]:
    """[(edition_date, fileid), ...] from the viewer's archive panel."""
    timeout = int(getattr(settings, 'NEWSPAPER_DOWNLOAD_TIMEOUT', 60))
    resp = requests.get(_viewer_url(),
                        headers=_headers('https://www.addustour.com/'),
                        timeout=timeout)
    resp.raise_for_status()

    editions: dict[str, tuple[date, str]] = {}
    for fileid, title in _ITEM_RE.findall(resp.text):
        d = parse_title_date(title)
        if d is None:
            continue
        # Keep the highest fileid per date (re-uploads)
        prev = editions.get(d.isoformat())
        if prev is None or int(fileid) > int(prev[1]):
            editions[d.isoformat()] = (d, fileid)

    out = sorted(editions.values(), key=lambda e: e[0], reverse=True)
    logger.info(f'[newspaper.dustour] archive panel: {len(out)} dated edition(s)')
    return out


def discover(target: date) -> DiscoveryResult | None:
    """Discovery for one date; None when not published (normal outcome)."""
    for d, fileid in list_editions():
        if d == target:
            pdf_url = f'{DOWNLOAD_URL}?fileid={fileid}'
            article = EditionArticle(
                article_id=fileid,
                url=f'{VIEWER_URL}?fileid={fileid}',
                edition_date=target)
            logger.info(f'[newspaper.dustour] edition {target}: fileid={fileid}')
            return DiscoveryResult(article=article, pdf_urls=[pdf_url])
    logger.info(f'[newspaper.dustour] no edition listed for {target}')
    return None
