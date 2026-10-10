# magazine_tenders/scrapers/petra.py
"""
Petra (petra.gov.jo) — the Jordan News Agency.

    URL          : https://petra.gov.jo/ar/news/search/category/<slug>
    Technology   : Laravel, server-rendered. No API and no XHR — the browser's
                   network log on a category page shows nothing but CSS and
                   JS, so everything there is is in the first response.
    Sections     : business-economy (مال وأعمال)
                   local-news       (محليات)
                   governorates     (محافظات)
    Photographs  : /storage/media/YYYY/MM/DD/<uuid>.jpg — and see below.
    Pagination   : ?page=N does not page. It returns a different slice on
                   some sections and the same one on others, so it is not
                   used; each section is one request.

    A CARD IS TWO ANCHORS
    ---------------------
    The picture and the headline are separate <a class="anchor-none"> tags
    pointing at the same article. A parser that takes one anchor per card
    gets a title with no photograph, or a photograph with no title — which
    is exactly what the first attempt here did. They are merged on the href.

    The page also opens with a breaking-news ticker (a.urgent-ticker-lines)
    carrying the same /ar/news/ links. The ticker has no pictures, so
    requiring a photograph is what keeps the King's diary off the News page.

    THE DATE COMES FREE
    -------------------
    Petra puts the publication date in the image path. That makes this the
    only Jordanian source that needs no second request: Ad-Dustour and
    Al-Ghad must open each KEPT article to find out when it ran, Petra does
    not. Three requests a run, total.

    WHAT THIS SOURCE IS
    -------------------
    A state wire covering everything — royal audiences, diplomacy, sport,
    weather, agriculture. The engineering seam is thin and lives mostly in
    محافظات: the article that prompted this source, an agreement to build a
    60-bed hospital in Zarqa, is filed there and not under اقتصاد. Reading
    the economy desk alone would have missed it.

    Coverage is shallower than the newspapers': each section shows roughly
    6 to 16 cards with no way to page further, so a day missed is a day
    lost. That is the trade for three requests, and it is why this source
    matters most on the daily sweep.

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

BASE_URL = 'https://petra.gov.jo'

_CATEGORY = f'{BASE_URL}/ar/news/search/category/'

#: YYYY/MM/DD out of the media path — the publication date, free.
_MEDIA_DATE_RE = re.compile(r'/storage/media/(\d{4})/(\d{2})/(\d{2})/')


class PetraScraper(NewsFeedMagazineScraper):
    CODE            = 'PETRA'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'petra_html'
    DEFAULT_COUNTRY = 'JO'
    IMPERSONATE     = False

    #: The front page first: it carries ~58 articles against 6 to 16 on a
    #: category page, so it is where most of the day actually is. The three
    #: desks are read after it for what it left out, محافظات first because
    #: that is where the built work is filed.
    SECTION_URLS = (
        f'{BASE_URL}/ar',
        f'{_CATEGORY}governorates',
        f'{_CATEGORY}business-economy',
        f'{_CATEGORY}local-news',
    )

    country_code = 'JO'
    source_name  = 'Petra — Jordan News Agency'
    base_url     = BASE_URL

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetchers(self):
        fetchers = super()._fetchers()
        fetchers['petra_html'] = self._fetch_petra
        return fetchers

    def _fetch_petra(self) -> list:
        raw, seen = [], set()
        for url in self.SECTION_URLS:
            try:
                # self.get(), never self.session.get(): the helper carries the
                # delay, the retries and the backoff.
                html = self.get(url).text
            except Exception as exc:
                # One dead section must not cost the others.
                print(f'  X  [MAG:{self.CODE}] {url.rsplit("/", 1)[-1]}: {exc}')
                logger.warning(f'[MAG:{self.CODE}] {url}: {exc}')
                continue
            items = [i for i in self.parse_section(html) if i['guid'] not in seen]
            seen.update(i['guid'] for i in items)
            print(f'       > {url.rsplit("/", 1)[-1]}: {len(items)} article(s)')
            raw.extend(items)
        return raw

    # ── filling the gaps, for kept items only ───────────────────────────────
    def scrape(self) -> list:
        """The base run, then one request for each kept item still missing a photo."""
        results = super().scrape()
        self.add_photos_and_dates(results)
        return results

    def add_photos_and_dates(self, items: list) -> None:
        """
        Give a kept item its photograph and date when the listing had none.

        The listing attaches a picture inconsistently — the same article
        carries one under محليات and none under محافظات — and the date
        lives in the picture's path, so a card without a picture has
        neither. The article page always has an og:image on that same dated
        path, which settles both in one request.

        Only KEPT items pay for it: one or two a run, against seventy if
        this were done before the filter. A failure costs that item its
        photograph, not its place on the page.
        """
        for td in items:
            has_photo = bool(getattr(td, 'image_url', ''))
            if has_photo and getattr(td, 'publication_date', None):
                continue
            url = getattr(td, 'source_url', '') or ''
            if not url:
                continue
            try:
                soup = BeautifulSoup(self.get(url).text, 'html.parser')
            except Exception as exc:
                logger.info(f'[MAG:{self.CODE}] no article page for {url}: {exc}')
                continue
            tag = soup.find('meta', property='og:image')
            src = (tag.get('content') or '').strip() if tag else ''
            if not src:
                continue
            if not has_photo:
                td.image_url = self._absolute(src)
            if not getattr(td, 'publication_date', None):
                td.publication_date = self.date_from_image(src)

    # ── parsing (no network, so tests run offline) ──────────────────────────
    @staticmethod
    def date_from_image(src: str) -> Optional[date]:
        """
        The publication date out of /storage/media/YYYY/MM/DD/.

        None when the path carries no date — the site's own furniture, the
        weather icons and the logos all live elsewhere — and None when the
        numbers are not a real date, rather than raising in the middle of a
        sweep.
        """
        m = _MEDIA_DATE_RE.search(src or '')
        if not m:
            return None
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    def _absolute(self, href: str) -> str:
        """
        Resolve and encode a listing link.

        Petra writes its slugs in Arabic and some of them carry a stray
        space, so the href has to be percent-encoded or the request fails on
        the space alone.
        """
        href = (href or '').strip()
        if not href:
            return ''
        url = href if href.startswith('http') else urljoin(BASE_URL, href)
        return quote(url, safe=':/?#[]@!$&\'()*+,;=%~')

    def parse_section(self, html: str) -> list:
        """
        One category page -> the raw dicts build_one() expects.

        Anchors are merged on their href: one of the pair holds the picture,
        the other the headline. An entry needs both to survive — the ticker
        supplies headlines with no picture, and a card with no picture has
        no date either, since the date is in the picture's path.
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        merged: dict = {}

        for anchor in soup.select('a[href*="/ar/news/"]'):
            href = (anchor.get('href') or '').strip()
            if not href or '/search/' in href:
                continue
            if 'urgent-ticker-lines' in (anchor.get('class') or []):
                continue                       # the breaking-news ticker

            rec = merged.setdefault(href, {'title': '', 'image': ''})

            text = ' '.join(anchor.get_text(' ', strip=True).split())
            if len(text) > len(rec['title']):
                rec['title'] = text

            img = anchor.find('img')
            if img is not None and not rec['image']:
                src = (img.get('src') or img.get('data-src') or '').strip()
                if self.date_from_image(src):
                    rec['image'] = src
                elif not rec['image']:
                    rec['image'] = src if '/storage/media/' in src else ''

        out = []
        for href, rec in merged.items():
            title, image = rec['title'].strip(), rec['image']
            if not title:
                continue                       # the picture half of a card
            url = self._absolute(href)
            if not url:
                continue
            image_url = self._absolute(image)
            out.append({
                'guid':      url,
                'title':     title,
                # The listing carries no standfirst; the filter sees the
                # headline alone, as it does for Ad-Dustour and Al-Ghad.
                'summary':   '',
                'body':      '',
                'url':       url,
                'published': self.date_from_image(image),
                'image':       image_url,
                'image_large': image_url,
            })
        return out
