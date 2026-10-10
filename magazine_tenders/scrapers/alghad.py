# magazine_tenders/scrapers/alghad.py
"""
Al-Ghad (alghad.com) — the اقتصاد category.

    URL          : https://alghad.com/Category/18/اقتصاد
    Technology   : ASP.NET, server-rendered. The fourth shape among the news
                   sources, after WordPress REST (the trade press), a widget
                   API (Al-Rai) and plain HTML (Ad-Dustour).
    Structure    : cards are `.block-card`, each holding an <a>, an <img> and
                   an <h3> headline. The link carries the desk in its path:

                       /Section-181/اقتصاد/<slug>

                   which matters more than it sounds — see below.
    Photographs  : served from imgy.pro, already absolute in the markup.
    Pagination   : none. `?page=2` returns a byte-identical page, so the
                   category is read in ONE request and yields ~28 articles.
    Date format  : «الإثنين 21 أيلول 2026», in `.story-date` on the ARTICLE
                   page. See the dateline section below.

    THE SECTION IN THE PATH IS THE FILTER
    -------------------------------------
    The economy page also promotes other desks down its side — sport,
    caricature, the World Cup, world news, translated columns. Those cards
    are built from the same markup as the economy ones and are
    indistinguishable by shape; only the section id in the href separates
    them. Reading every `.block-card` would put the football on the News
    page, so this scraper reads the economy desks and nothing else:

        181  اقتصاد            the main desk
        210  الاقتصاد المحلي   local economy — the tender and project seam
        146  شركات وأعمال      companies and business
        145  تحليل إقتصادي     economic analysis

    Adding a desk is adding its id to ECONOMY_SECTIONS. Adding محليات
    (local news) would widen this source considerably; it is not in the
    اقتصاد category, so it would need its own listing URL.

    THE DATELINE
    ------------
    The listing prints a date on some cards and not on the main grid, so it
    cannot be relied on. The article page always has one, and it is fetched
    for the items that are KEPT — one or two a run, not twenty-eight. That
    ordering matters: an undated row stores publication_date=None, and the
    News page sorts NULLs last, which buried Ad-Dustour's first article on
    page 9 of 12. A row nobody can find is not published.

    Investigated : 2026-09-21
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from ._newsfeed import NewsFeedMagazineScraper, arabic_date_from_text

logger = logging.getLogger('tenders')

BASE_URL = 'https://alghad.com'

#: The اقتصاد category page.
SECTION_URL = f'{BASE_URL}/Category/18/%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF'

#: The desks whose cards are economy news. Everything else on the page is
#: cross-promotion from another category — see the module docstring.
ECONOMY_SECTIONS = (181, 210, 146, 145)

_ECONOMY_HREF_RE = re.compile(
    r'/Section-(?:%s)/' % '|'.join(str(s) for s in ECONOMY_SECTIONS))


class AlGhadScraper(NewsFeedMagazineScraper):
    CODE            = 'ALGHAD'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'alghad_html'
    DEFAULT_COUNTRY = 'JO'
    IMPERSONATE     = False

    SECTION_URLS = (SECTION_URL,)

    country_code = 'JO'
    source_name  = 'Al-Ghad — economy'
    base_url     = BASE_URL

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetchers(self):
        fetchers = super()._fetchers()
        fetchers['alghad_html'] = self._fetch_alghad
        return fetchers

    def _fetch_alghad(self) -> list:
        raw: list = []
        for url in self.SECTION_URLS:
            try:
                # self.get(), never self.session.get(): the helper carries the
                # delay, the retries and the backoff.
                html = self.get(url).text
            except Exception as exc:
                print(f'  X  [MAG:{self.CODE}] {url}: {exc}')
                logger.warning(f'[MAG:{self.CODE}] {url}: {exc}')
                continue
            items = self.parse_section(html)
            print(f'       > {len(items)} article(s)')
            raw.extend(items)
        return raw

    # ── the dateline ────────────────────────────────────────────────────────
    def scrape(self) -> list:
        """The base run, then one request for each item that survived it."""
        results = super().scrape()
        self.add_datelines(results)
        return results

    def add_datelines(self, items: list) -> None:
        """
        Fill in publication_date for items that have none. Modifies in place.

        A failure is logged and dropped: an article page that will not load
        costs that item its date and nothing more. Losing the story over it
        would be the worse trade.
        """
        for td in items:
            if getattr(td, 'publication_date', None) is not None:
                continue
            url = getattr(td, 'source_url', '') or ''
            if not url:
                continue
            try:
                td.publication_date = self.parse_article_date(self.get(url).text)
            except Exception as exc:
                logger.info(f'[MAG:{self.CODE}] no dateline for {url}: {exc}')

    def parse_article_date(self, html: str) -> Optional[date]:
        """
        «الإثنين 21 أيلول 2026» from `.story-date` -> date(2026, 9, 21).

        Read off the element's text rather than the markup, and through the
        shared Arabic month table in _newsfeed.py — the same one Ad-Dustour
        uses, so a month name fixed for one paper is fixed for both.
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        for el in soup.select('.story-date, .DateTime'):
            parsed = arabic_date_from_text(el.get_text(' ', strip=True))
            if parsed is not None:
                return parsed
        return None

    # ── parsing (no network, so tests run offline) ──────────────────────────
    def _absolute(self, href: str) -> str:
        href = (href or '').strip()
        if href.startswith('http'):
            return href
        if not href.startswith('/'):
            href = '/' + href
        return BASE_URL + href

    def _photograph(self, anchor, card) -> str:
        """
        The card's real picture, which is not in the <img> tag.

        Al-Ghad lazy-loads. The <img src> is /App_Images/default.jpg on 54 of
        the 62 cards on the page, and the picture itself is in a
        <picture><source data-srcset>, offered at seven sizes from 228x129 to
        511x289. Reading src first is how the first Al-Ghad article reached
        the News page with no photograph on it.

        The largest size wins: the News page leads on a full-width featured
        card, and the media queries the sizes belong to are the browser's
        business, not ours. `src` is still read, but only when it is a real
        URL — eight cards do carry one.
        """
        scope = card if card is not None else anchor
        best, best_width = '', -1
        for source in scope.find_all('source'):
            url = (source.get('data-srcset') or source.get('srcset') or '')
            url = url.split(',')[0].strip().split(' ')[0]
            if not url:
                continue
            m = re.search(r'/(\d+)x(\d+)/', url)
            width = int(m.group(1)) if m else 0
            if width > best_width:
                best, best_width = url, width
        if best:
            return best

        img = anchor.find('img') or (scope.find('img') if scope else None)
        if img is None:
            return ''
        for attr in ('data-src', 'data-original', 'src'):
            url = (img.get(attr) or '').strip()
            if url and 'App_Images/default' not in url:
                return url
        return ''

    def parse_section(self, html: str) -> list:
        """
        One category page -> the raw dicts build_one() expects.

        A card counts when its link points at one of the economy desks AND it
        carries a headline. The picture is taken when there is one; the
        sister-paper promo and the navigation carry a picture but no article
        link, so the section test excludes them on its own.
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        out, seen = [], set()

        for anchor in soup.select('a[href*="/Section-"]'):
            href = anchor.get('href') or ''
            if not _ECONOMY_HREF_RE.search(href):
                continue

            card = anchor.find_parent(class_='block-card') or anchor.parent
            heading = (anchor.find(['h1', 'h2', 'h3', 'h4'])
                       or (card.find(['h1', 'h2', 'h3', 'h4']) if card else None))
            title = heading.get_text(' ', strip=True) if heading else ''
            if not title:
                continue

            url = self._absolute(href)
            if url in seen:
                continue
            seen.add(url)

            src = self._photograph(anchor, card)

            out.append({
                'guid':      url,
                'title':     ' '.join(title.split()),
                # The listing carries no standfirst; the filter sees the
                # headline alone, as it does for Ad-Dustour.
                'summary':   '',
                'body':      '',
                'url':       url,
                # Filled in by add_datelines() for the items that are kept.
                'published': None,
                'image':       self._absolute(src) if src else '',
                'image_large': self._absolute(src) if src else '',
            })
        return out
