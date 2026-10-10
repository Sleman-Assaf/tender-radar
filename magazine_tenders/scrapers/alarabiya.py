# magazine_tenders/scrapers/alarabiya.py
"""
Al Arabiya (alarabiya.net) — the sections that carry built work.

    URL          : https://www.alarabiya.net/<section>
    Technology   : server-rendered HTML behind Cloudflare. Not WordPress.
    Structure    : an article link is any href carrying a date —
                   /aswaq/cars/2026/09/21/<arabic-slug>. That is the whole
                   test: the site's furniture, its menus and its author
                   pages have no date in the path.
    Photographs  : vid.alarabiya.net, absolute in the markup.
    Date format  : IN THE URL. /2026/09/21/ is the publication date, so this
                   source costs nothing beyond the section pages themselves.
    Pagination   : not used. Each section renders a deep list already —
                   /aswaq alone gives about 110 articles.

    THE HEADLINE IS IN THE ALT TEXT
    -------------------------------
    The anchor's own text is decorated with the clock and the section name:
        "٠٩:١٦ سيارات سيارات رئيس «سير»: سيارات صنع في السعودية..."
    The picture's alt attribute carries the headline clean, so that is what
    is read. An anchor with no picture is skipped rather than guessed at.

    WHY SECTIONS AND NOT SEARCH
    ---------------------------
    The reader asked for the whole site. Al Arabiya's own search, its RSS
    and its sitemap all answer 403 to anything but a browser, so there is no
    index to walk; the sections are what can be read. They are also where
    the work is: this is a general news site whose politics, sport and
    entertainment desks will never carry a tender.

    Cloudflare challenges individual sections intermittently — /aswaq served
    twice and then refused, while /saudi-today kept answering throughout. A
    challenged section is logged and skipped, and the others still run. It
    is not worked around.

    WHAT THIS SOURCE IS
    -------------------
    Pan-Arab general news, Saudi-weighted. The engineering seam is thin and
    scattered: a school complex opening in Taif, an airport contract, a
    housing scheme. Expect single figures a day, and days with nothing.

    Investigated : 2026-09-22
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from ._newsfeed import NewsFeedMagazineScraper

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.alarabiya.net'

#: /YYYY/MM/DD/ in the path — what makes a link an article, and its date.
_URL_DATE_RE = re.compile(r'/(\d{4})/(\d{2})/(\d{2})/')


class AlArabiyaScraper(NewsFeedMagazineScraper):
    CODE            = 'ALARABIYA'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'alarabiya_html'
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = False

    #: The desks that can carry built work. Business first, then the
    #: national and regional news desks — the Taif school complex came off
    #: /saudi-today, not off the business pages.
    SECTION_URLS = (
        f'{BASE_URL}/aswaq',
        f'{BASE_URL}/aswaq/oil-and-gas',
        f'{BASE_URL}/saudi-today',
        f'{BASE_URL}/arab-and-world',
    )

    country_code = 'AE'
    source_name  = 'Al Arabiya — business and regional news'
    base_url     = BASE_URL

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetchers(self):
        fetchers = super()._fetchers()
        fetchers['alarabiya_html'] = self._fetch_alarabiya
        return fetchers

    def _fetch_alarabiya(self) -> list:
        raw, seen = [], set()
        for url in self.SECTION_URLS:
            name = url.rsplit('/', 1)[-1]
            try:
                # self.get(), never self.session.get(): the helper carries
                # the delay, the retries and the backoff, and this site
                # challenges a caller that does not pause.
                html = self.get(url).text
            except Exception as exc:
                print(f'  X  [MAG:{self.CODE}] {name}: {exc}')
                logger.warning(f'[MAG:{self.CODE}] {url}: {exc}')
                continue
            items = [i for i in self.parse_section(html) if i['guid'] not in seen]
            seen.update(i['guid'] for i in items)
            print(f'       > {name}: {len(items)} article(s)')
            raw.extend(items)
        return raw

    # ── parsing (no network, so tests run offline) ──────────────────────────
    @staticmethod
    def date_from_url(url: str) -> Optional[date]:
        """The publication date out of /YYYY/MM/DD/, or None."""
        m = _URL_DATE_RE.search(url or '')
        if not m:
            return None
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    def _absolute(self, href: str) -> str:
        """Resolve and percent-encode; the slugs are Arabic."""
        href = (href or '').strip()
        if not href:
            return ''
        url = href if href.startswith('http') else urljoin(BASE_URL, href)
        return quote(url, safe=':/?#[]@!$&\'()*+,;=%~')

    def parse_section(self, html: str) -> list:
        """
        One section page -> the raw dicts build_one() expects.

        A link is an article when its path carries a date. The headline is
        taken from the picture's alt, because the anchor's own text is
        prefixed with the clock and the section name; an anchor with no
        picture is skipped rather than guessed at.
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        out, seen = [], set()

        for anchor in soup.select('a[href]'):
            href = anchor.get('href') or ''
            published = self.date_from_url(href)
            if published is None:
                continue

            img = anchor.find('img')
            if img is None:
                continue
            title = ' '.join((img.get('alt') or '').split())
            if not title:
                continue

            url = self._absolute(href)
            if not url or url in seen:
                continue
            seen.add(url)

            src = (img.get('src') or img.get('data-src') or '').strip()
            out.append({
                'guid':      url,
                'title':     title,
                # The listing carries no standfirst; the filter sees the
                # headline alone, as it does for the Jordanian papers.
                'summary':   '',
                'body':      '',
                'url':       url,
                'published': published,
                'image':       self._absolute(src) if src else '',
                'image_large': self._absolute(src) if src else '',
            })
        return out
