# magazine_tenders/scrapers/addustour.py
"""
Ad-Dustour (addustour.com) — the اقتصاد section.

    URL          : https://www.addustour.com/categories/4-اقتصاد
    Technology   : Not WordPress, no JSON API — plain server-rendered HTML.
                   The third shape among the news sources, after WordPress
                   REST (the trade press) and a widget API (Al-Rai).
    Structure    : cards are `a.sectionHero_element`, each holding an <img>
                   and a heading. Links are written RELATIVE with a leading
                   `../`, which has to be stripped or every URL 404s.
    Photographs  : /file.php?fileid=<n>&width=<w>&height=<h>, served by the
                   site itself; the listing already asks for a sensible size.
    Pagination   : none that works. `?page=2` returns byte-for-byte the same
                   229,948-byte page, so the section is read in ONE request
                   and yields ~51 articles. That is a day's worth and it keeps
                   this scraper to a single hit per run.
    Date format  : not published in the listing at all — see below.

    TWO THINGS THE LISTING DOES NOT GIVE US
    ---------------------------------------
    * No standfirst. The keep-filter therefore sees the headline alone, where
      for Al-Rai it sees headline + summary. That costs recall: several
      Al-Rai items are kept on an event word that appears only in the
      standfirst. Fetching each article page would recover it at the cost of
      51 extra requests per run, which is exactly the traffic that got this
      project throttled once already. Not worth it for a daily sweep.
    * No date. `published` is None, and undated rows are always kept by the
      freshness window (magazine_tenders/freshness.py) — the same treatment
      Future Projects relies on.

    Investigated : 2026-09-21
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from ._newsfeed import (NewsFeedMagazineScraper,
                        arabic_date_from_text)

logger = logging.getLogger('tenders')

BASE_URL = 'https://www.addustour.com'

#: The اقتصاد section. Its numeric id is part of the path, not a parameter.
SECTION_URL = f'{BASE_URL}/categories/4-%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF'



class AdDustourScraper(NewsFeedMagazineScraper):
    CODE            = 'ADDUSTOUR'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'addustour_html'
    DEFAULT_COUNTRY = 'JO'
    IMPERSONATE     = False

    SECTION_URLS = (SECTION_URL,)

    country_code = 'JO'
    source_name  = 'Ad-Dustour — economy'
    base_url     = BASE_URL


    # ── the dateline, which the listing does not carry ──────────────────────
    def scrape(self) -> list:
        """
        The base run, then one extra request for each item that survived it.

        The date has to come from somewhere. Without it every row stored
        publication_date=None, and the News page orders by that date with
        NULLs last - so the first article this source ever kept sorted to
        position 199 of 275, on page 9. It was in the database, on the page,
        and unfindable.

        The article page carries the dateline; the listing does not. Reading
        it for all 50 articles would be 50 requests a run, which is the
        traffic that got this project throttled once. Reading it for the one
        or two that are KEPT costs one or two. So the enrichment happens
        here, after the filter, and not in the fetch.
        """
        results = super().scrape()
        self.add_datelines(results)
        return results

    def add_datelines(self, items: list) -> None:
        """
        Fill in publication_date for items that have none. Modifies in place.

        A failure is logged and dropped: an article page that will not load
        costs that item its date, nothing more. Losing the story over it
        would be the worse trade - an undated row still reaches the page.
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
        «نشر في: الأحد 20 أيلول / سبتمبر 2026. 05:05 مـساءً» -> date(2026, 9, 20).

        The page prints the published line and then a last-modified one in the
        same class. The first is what we want, so the search stops there; a
        corrected article is still news on the day it ran.

        Parsed off the element's TEXT, not the markup: the site puts the
        label and the date in separate tags inside the span, so a regex over
        raw HTML has to cross a tag to join them and breaks the first time
        that markup changes.
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        for el in soup.select('.timeDate_element'):
            text = ' '.join(el.get_text(' ', strip=True).split())
            if 'نشر' not in text:
                continue                      # the last-modified line
            parsed = self._date_from(text)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _date_from(text):
        """day + a recognised month name + year, or None. Shared with Al-Ghad."""
        return arabic_date_from_text(text)

    # ── fetching ─────────────────────────────────────────────────────────────
    def _fetchers(self):
        fetchers = super()._fetchers()
        fetchers['addustour_html'] = self._fetch_addustour
        return fetchers

    def _fetch_addustour(self) -> list:
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

    # ── parsing (no network, so tests run offline) ──────────────────────────
    def _absolute(self, href: str) -> str:
        """
        Resolve a listing link.

        The cards write `../articles/123-slug`. Joining that naively leaves
        the `..` in place and the link 404s, so the prefix is stripped and the
        path rebuilt from the site root.
        """
        href = (href or '').strip()
        if href.startswith('http'):
            return href
        href = href.lstrip('.')            # ../articles/… -> /articles/…
        if not href.startswith('/'):
            href = '/' + href
        return BASE_URL + href

    def parse_section(self, html: str) -> list:
        """
        One section page -> the raw dicts build_one() expects.

        A card is an anchor that has BOTH a picture and a heading. The page
        also carries a news ticker and a navigation menu pointing at
        /articles/, and neither has a picture — which is what tells them
        apart.
        """
        soup = BeautifulSoup(html or '', 'html.parser')
        out, seen = [], set()

        for anchor in soup.select('a[href*="/articles/"]'):
            img = anchor.find('img')
            if img is None:
                continue                    # ticker or menu entry, not a card
            heading = anchor.find(['h1', 'h2', 'h3', 'h4'])
            title = (heading.get_text(strip=True) if heading
                     else (img.get('alt') or '').strip())
            if not title:
                continue

            url = self._absolute(anchor.get('href'))
            if url in seen:
                continue
            seen.add(url)

            src = (img.get('src') or img.get('data-original') or '').strip()
            out.append({
                'guid':      url,
                'title':     title,
                # The listing carries no standfirst; see the module docstring.
                'summary':   '',
                'body':      '',
                'url':       url,
                'published': None,
                'image':       self._absolute(src) if src else '',
                'image_large': self._absolute(src) if src else '',
            })
        return out
