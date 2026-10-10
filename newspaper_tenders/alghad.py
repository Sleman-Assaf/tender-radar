# newspaper_tenders/alghad.py
# Al-Ghad daily edition discovery — verified against the live site 2026-08-20.
#
# How the site works (an Angular app over a WordPress API — no HTML parsing):
#   * GET https://legacy.alghad.com/wp-json/pdf/v1/list?path=/YYYY/YYYY-MM&v=451
#     returns {"groups": [...{"files": [{"pdf": "...YYYY-MM-DD.pdf",
#     "thumb": ..., "name": "YYYY-MM-DD"}, ...]}]}
#   * One PDF per day = the WHOLE printed newspaper (~24 pages, ~26 MB),
#     published every day including Fridays/Saturdays.
#   * Unlike Al-Rai's scans, the PDF has a real TEXT layer — which the
#     pipeline uses to pick out the tender pages before any AI work
#     (see splitter.py).

import logging
from datetime import date

import requests
from django.conf import settings

from .alrai import DiscoveryResult, EditionArticle, _headers

logger = logging.getLogger('tenders')

API_LIST = 'https://legacy.alghad.com/wp-json/pdf/v1/list'
DASHBOARD_URL = 'https://legacy.alghad.com/newspaper/dashboard'


def _api_base() -> str:
    return getattr(settings, 'NEWSPAPER_ALGHAD_API', API_LIST)


def list_month_files(target: date) -> list[dict]:
    """All published PDFs for the target's month: [{'pdf', 'name'}, ...]."""
    timeout = int(getattr(settings, 'NEWSPAPER_DOWNLOAD_TIMEOUT', 60))
    path = f'/{target.year}/{target.year}-{target.month:02d}'
    resp = requests.get(_api_base(), params={'path': path, 'v': '451'},
                        headers=_headers('https://legacy.alghad.com/newspaper/'),
                        timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    files: list[dict] = []

    def _walk(node):
        for f in (node.get('files') or []):
            if isinstance(f, dict) and f.get('pdf'):
                files.append(f)
        for g in (node.get('groups') or []):
            if isinstance(g, dict) and g.get('child'):
                _walk(g['child'])

    _walk(data if isinstance(data, dict) else {})
    logger.info(f'[newspaper.alghad] {path}: {len(files)} edition file(s) listed')
    return files


def discover(target: date) -> DiscoveryResult | None:
    """
    Discovery for one date. Returns None when the edition is not published
    yet (a normal outcome — the retry sweep checks again).
    """
    wanted = target.isoformat()
    for f in list_month_files(target):
        if str(f.get('name', '')).strip() == wanted:
            pdf_url = str(f['pdf']).strip()
            article = EditionArticle(article_id=wanted,
                                     url=DASHBOARD_URL, edition_date=target)
            logger.info(f'[newspaper.alghad] edition {wanted}: {pdf_url}')
            return DiscoveryResult(article=article, pdf_urls=[pdf_url])
    logger.info(f'[newspaper.alghad] no edition listed for {wanted}')
    return None
