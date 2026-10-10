# magazine_tenders/scrapers/alrai.py
"""
Al-Rai (alrai.com) — the اقتصاد and محليات sections.

    URL          : https://alrai.com/اقتصاد  and  https://alrai.com/محليات
    Technology   : NOT WordPress. The section page ships almost no article
                   markup — four hrefs and a page of chrome — because the list
                   is drawn by JavaScript. Behind it is the site's own widget
                   API, which is what this scraper reads:

                       GET /api/pb/widget/1809/json?section_id=<n>
                           first page, plus the paging cursor in
                           widget_data_info {limit, offset, start, offsetArchive}
                       GET /api/pb/fetch-more-articles?v=<ms>&sectionId=…
                           each subsequent page, carrying the cursor forward

                   Both return JSON and need a Referer header.
    Sections     : 4 = اقتصاد (economy), 1 = محليات (local).
    Structure    : data[] of article objects. The fields worth having are
                   title, summary (a usable standfirst), short_url,
                   publish_time ("YYYY-MM-DD HH:MM:SS") and image.full.
                   `hasImage` is unreliable — it reads 0 on rows that do have
                   image.full — so the image dict is what decides.
    Photographs  : every one of 504 articles read on 2026-09-21 had one.
    Date format  : ISO-like in publish_time; issue_date is Arabic prose and is
                   deliberately ignored.

    WHAT THIS SOURCE IS — READ THIS BEFORE EXPECTING RESULTS
    --------------------------------------------------------
    اقتصاد is a money desk. Measured over 252 of its articles: gold prices,
    interest rates, inflation, bank lending, currency, exports. Roughly one in
    ten touches built work — an investment to build a hotel, the National
    Water Carrier, major schemes with a value attached. محليات is the richer
    seam: school tenders, hospital extensions, sewerage schemes, road works.

    Both are read, and the Arabic keep-rules in _newsfeed.py decide. Expect
    this source to be quiet on the economy side and steady on the local one;
    a day with nothing kept from اقتصاد is normal, not a fault.

    Investigated : 2026-09-21
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from ._newsfeed import NewsFeedMagazineScraper

logger = logging.getLogger('tenders')

BASE_URL = 'https://alrai.com'

#: The widget the section pages themselves use for their article list.
WIDGET_ID = 1809

SECTION_NAMES = {4: 'اقتصاد', 1: 'محليات'}


class AlRaiScraper(NewsFeedMagazineScraper):
    CODE            = 'ALRAI'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'alrai_api'
    DEFAULT_COUNTRY = 'JO'
    IMPERSONATE     = False

    #: اقتصاد first, then محليات. Order only decides the log.
    SECTION_IDS = (4, 1)

    #: Pages per section. 18 articles a page, so 5 pages is about a fortnight
    #: on the local desk and rather more on the economy one — comfortably
    #: inside MAGAZINE_MAX_AGE_MONTHS without asking the site for its archive.
    MAX_PAGES = 5

    country_code = 'JO'
    source_name  = 'Al-Rai — economy and local news'
    base_url     = BASE_URL

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetchers(self):
        fetchers = super()._fetchers()
        fetchers['alrai_api'] = self._fetch_alrai
        return fetchers

    def _api_get(self, url: str) -> dict:
        """
        GET a widget endpoint and parse it.

        Through `self.get()`, never `self.session.get()`. The helper is what
        applies SCRAPER_DELAY_BETWEEN_REQUESTS, the retry ladder and the
        backoff, and the first version of this scraper skipped it: five
        sweeps and a corpus pull inside an hour, with no pause between any of
        the requests, and alrai.com stopped answering. A paging scraper is
        exactly the kind that must not hold the throttle open.

        The API refuses without a Referer, so one is always sent. A page that
        does not parse ends that section rather than the run — the other
        section, and every other source, still has its own chance.
        """
        headers = dict(self.headers or {})
        headers.update({'Referer': f'{BASE_URL}/',
                        'Accept': 'application/json, text/plain, */*',
                        'X-Requested-With': 'XMLHttpRequest'})
        resp = self.get(url, headers=headers)
        return json.loads(resp.text)

    def _fetch_alrai(self) -> list:
        raw: list = []
        for section_id in self.SECTION_IDS:
            name = SECTION_NAMES.get(section_id, section_id)
            try:
                raw.extend(self._fetch_section(section_id))
            except Exception as exc:
                # One dead section must not cost the other one.
                print(f'  X  [MAG:{self.CODE}] section {name}: {exc}')
                logger.warning(f'[MAG:{self.CODE}] section {name}: {exc}')
        return raw

    def _fetch_section(self, section_id: int) -> list:
        payload = self._api_get(
            f'{BASE_URL}/api/pb/widget/{WIDGET_ID}/json'
            f'?morein=loadmore&section_id={section_id}')
        out, seen = [], set()

        for _ in range(self.MAX_PAGES):
            items = self.parse_payload(payload)
            fresh = [i for i in items if i['guid'] not in seen]
            if not fresh:
                break
            seen.update(i['guid'] for i in fresh)
            out.extend(fresh)

            info = payload.get('widget_data_info') or {}
            params = {
                'v': int(time.time() * 1000),
                'sectionId': str(section_id),
                'subsectionId': '',
                'limit': info.get('limit', 18),
                'offset': info.get('offset', 0),
                'start': info.get('start') or 0,
                'offsetArchive': info.get('offsetArchive') or 0,
                'custom_field': '',
                'widget_id': WIDGET_ID,
            }
            query = '&'.join(f'{k}={v}' for k, v in params.items())
            try:
                payload = self._api_get(
                    f'{BASE_URL}/api/pb/fetch-more-articles?{query}')
            except Exception as exc:
                logger.info(f'[MAG:{self.CODE}] paging stopped: {exc}')
                break

        name = SECTION_NAMES.get(section_id, section_id)
        print(f'       > {name}: {len(out)} article(s)')
        return out

    # ── parsing (no network, so tests run offline) ──────────────────────────
    def parse_payload(self, payload: dict) -> list:
        """
        One widget response -> the raw dicts build_one() expects.

        Anything without a headline or a link is dropped here rather than
        later: those are the widget's own filler rows, and they carry no
        article to read.
        """
        out = []
        for art in (payload.get('data') or []):
            title = (art.get('title') or '').strip()
            url = (art.get('short_url') or art.get('link') or '').strip()
            if not title or not url:
                continue
            out.append({
                'guid':      art.get('cms_article_id') or url,
                'title':     title,
                'summary':   (art.get('summary') or '').strip(),
                'body':      (art.get('body') or '').strip(),
                'url':       url,
                'published': self._published(art.get('publish_time')),
                # hasImage lies; the dict is what counts.
                'image':       ((art.get('image') or {}).get('full') or ''),
                'image_large': ((art.get('image') or {}).get('full') or ''),
            })
        return out

    @staticmethod
    def _published(raw):
        """`publish_time` is "YYYY-MM-DD HH:MM:SS"; None when it is anything else."""
        if not raw:
            return None
        try:
            return datetime.strptime(str(raw)[:19], '%Y-%m-%d %H:%M:%S').date()
        except ValueError:
            try:
                return datetime.strptime(str(raw)[:10], '%Y-%m-%d').date()
            except ValueError:
                return None
