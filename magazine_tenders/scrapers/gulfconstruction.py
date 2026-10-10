# magazine_tenders/scrapers/gulfconstruction.py
"""
Gulf Construction (gulfconstructiononline.com) — "Tenders" column.

    URL          : https://gulfconstructiononline.com/Section/518/Tenders
                   → links to the current issue's Tenders article, e.g.
                     /Article/465769/Tenders
    Technology   : static HTML, fully server-rendered. A plain GET with a
                   browser User-Agent returns 200; no anti-bot, no login, no
                   paywall, no JS. There is no robots.txt (404).
    Request      : GET, no params. Two steps — discover the article, then parse it.
    Structure    : Section 518 lists exactly one article (the current issue).
                   Inside it, `div.blog-content` holds ~65-70 notices as
                   `p.post-excerpt` bullets, grouped under all-caps country
                   headers which are themselves `p.post-excerpt` elements.
                   Per notice: "Tender No <ref>." then a <b> scope-of-work
                   description, with the issuing body written inline as the
                   trailing "… for <Entity>". A per-country italic line names
                   the tender board and its portal URL.
    Sample item  : "Tender No 353/2026/BTB (RDS-26/0014). <b>Construction of
                   access roads … for Ministry of Works</b> … CD August 12"
    Date format  : issue date  — `time.entry-date.published` = "Sat, 01 Aug 2026"
                   deadline    — inline "CD August 12" (CD = closing date). The
                                 YEAR IS ABSENT and is inferred from the issue
                                 date, rolling forward when the month is well
                                 behind the issue month.
                   The page's meta publish_date is unreliable — ignore it.
    Pagination   : none. `?page=2` returns a byte-identical response, and there
                   is no archive of past columns (/Archives 404s). Everything
                   for the month is inside the one article.
    Cadence      : MONTHLY, not a live feed. Re-running mid-month re-reads the
                   same issue, which is harmless: the reference number gives
                   every notice a stable external_id, so a re-run updates
                   instead of duplicating.
    Investigated : 2026-09-02 (August 2026 issue: 67 notices, every one with a
                   reference number, title, country and closing date)
"""
import logging
import re
from datetime import date, datetime
from typing import Optional

from django.conf import settings

from tenders.scrapers.base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

CODE = 'GULFCONSTRUCTION'

BASE_URL    = 'https://gulfconstructiononline.com'
SECTION_URL = f'{BASE_URL}/Section/518/Tenders'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    'Referer':         BASE_URL,
}

_MONTHS = {m: i for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July',
     'August', 'September', 'October', 'November', 'December'], start=1)}

# The column groups its notices under these all-caps headers. Mapping each to a
# CountryConfig code lets one magazine feed six countries, which is why
# `country_code` is set per item rather than once for the scraper.
_COUNTRY_CODES = {
    'BAHRAIN':      'BH',
    'KUWAIT':       'KW',
    'OMAN':         'OM',
    'QATAR':        'QA',
    'SAUDI ARABIA': 'SA',
    'UAE':          'AE',
}

_CD_RE     = re.compile(r'\bCD\s+([A-Z][a-z]+)\s+(\d{1,2})\b')
_REF_RE    = re.compile(r'(?is)Tender\s*No\.?\s*:?\s*(.+?)\.\s')
_BOARD_RE  = re.compile(r'(?i)(?:details|documents) for the following tenders')
_OBTAIN_RE = re.compile(r'(?i)obtained from\s+(?:the\s+)?(.+?)(?:,|\.|\s+website|\s+tel\b)')
_URL_RE    = re.compile(r'https?://[^\s,]+')


class GulfConstructionScraper(BaseScraper):
    """Parses the monthly Tenders column into one TenderData per notice."""

    country_code = 'BH'          # the magazine's home country; overridden per item
    source_name  = 'Gulf Construction — Tenders column'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print(f'  X  [MAG:{CODE}] beautifulsoup4 not installed')
            return []

        # The runner sets this. The anchor row is inactive by design, so the
        # is_active=True lookup the web scrapers do would never find it.
        source = getattr(self, 'source', None)
        if source is None:
            print(f'  X  [MAG:{CODE}] self.source not set — skipping')
            logger.error(f'[MAG:{CODE}] scrape() called without a source')
            return []

        ua = getattr(settings, 'MAGAZINE_USER_AGENT', '') or HEADERS['User-Agent']
        self.session.headers.update({**HEADERS, 'User-Agent': ua})

        rel_src, cat_src = self._reload_keywords_from_db()
        rel_count = len(self._relevance_keywords)
        cat_count = sum(len(v) for v in self._category_keywords.values())

        print(f'\n{"_" * 62}')
        print(f'  SOURCE  : {source.name}')
        print(f'  SCRAPER : {type(self).__name__}')
        print(f'  MODE    : HTML scraping (monthly Tenders column, 2-step)')
        print(f'  URL     : {SECTION_URL}')
        print(f'  FILTER  : AI decides AEC — no keyword gate here')
        print(f'  Keywords: {rel_count} relevance [{rel_src}]'
              f'  |  {cat_count} category [{cat_src}]')
        print(f'{"_" * 62}')

        # ── step 1: find the current issue's Tenders article ──
        try:
            resp = self.session.get(SECTION_URL, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
        except Exception as exc:
            err = str(exc)
            if '403' in err or '401' in err:
                print(f'  X  [MAG:{CODE}] Blocked: {exc}')
            else:
                print(f'  X  [MAG:{CODE}] Error: {exc}')
            logger.error(f'[MAG:{CODE}] section fetch error: {exc}')
            return []

        article_url = self.find_article_url(resp.text)
        if not article_url:
            print(f'  !  [MAG:{CODE}] no Tenders article linked from the section page')
            logger.warning(f'[MAG:{CODE}] section page had no article link')
            return []
        print(f'       > issue article: {article_url}')

        # ── step 2: parse it ──
        try:
            art = self.session.get(article_url, timeout=self.timeout)
            art.raise_for_status()
            art.encoding = 'utf-8'
        except Exception as exc:
            print(f'  X  [MAG:{CODE}] Error fetching article: {exc}')
            logger.error(f'[MAG:{CODE}] article fetch error: {exc}')
            return []

        results = self.parse_article(art.text, article_url)

        print(f'  TOTAL   : {len(results)} tender notice(s)')
        logger.info(f'[MAG:{CODE}] complete — total={len(results)}')
        return results

    # ── parsing (kept separate from fetching so tests run offline) ───────────
    def find_article_url(self, section_html: str) -> Optional[str]:
        """
        Absolute URL of the current issue's Tenders article.

        Selector: `div.main-content-wrapper-box h3.news-title a[href^="/Article/"]`.
        The sidebar carries the same markup, so the search is scoped to the main
        wrapper — otherwise a "related articles" link could win.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(section_html, 'html.parser')
        main = soup.select_one('div.main-content-wrapper-box') or soup
        link = main.select_one('h3.news-title a[href^="/Article/"]')
        if link is None:
            return None
        href = link.get('href') or ''
        return href if href.startswith('http') else BASE_URL + href

    def parse_article(self, article_html: str, detail_url: str) -> list[TenderData]:
        """Every `p.post-excerpt` bullet carrying a tender number becomes one item."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(article_html, 'html.parser')
        issue_date = self._issue_date(soup)
        body = soup.select_one('div.blog-content') or soup

        results: list[TenderData] = []
        country = board = board_url = None

        for para in body.select('p.post-excerpt'):
            text = ' '.join(para.get_text(' ', strip=True).split())
            if not text:
                continue

            # A country header resets the grouping context.
            if text.upper() in _COUNTRY_CODES and 'Tender No' not in text:
                country, board, board_url = text.upper(), None, None
                continue

            if 'Tender No' not in text:
                # The per-country italic line names the tender board + portal;
                # it is the fallback entity when a notice does not name one.
                if _BOARD_RE.search(text):
                    m = _OBTAIN_RE.search(text)
                    board = m.group(1).strip() if m else None
                    u = _URL_RE.search(text)
                    board_url = u.group(0).rstrip(':.,') if u else None
                continue

            td = self._parse_notice(para, text, issue_date, country, board,
                                    board_url, detail_url)
            if td is not None:
                results.append(td)

        return results

    def _parse_notice(self, para, text, issue_date, country, board, board_url,
                      detail_url) -> Optional[TenderData]:
        clean = text.lstrip('•●*- \t')

        ref = ''
        m = _REF_RE.match(clean + ' ')
        if m:
            ref = m.group(1).strip()

        # The <b> run is the scope-of-work description — the real title.
        bold = para.find('b')
        title = (' '.join(bold.get_text(' ', strip=True).split())
                 if bold else clean)
        if not title:
            return None

        entity = self._entity(title) or (board or '')
        deadline = self._closing_date(clean, issue_date)

        today = date.today()
        status = 'OPEN' if deadline is None or deadline >= today else 'CLOSED'

        # Provisional only — the classification service overwrites `category`
        # and decides is_aec after the runner saves the row.
        category = self._safe_category(f'{title} {clean}')

        return TenderData(
            title               = title[:500],
            title_ar            = '',
            source_url          = board_url or detail_url,
            external_id         = (f'mag-gulfcon-{ref}'[:200] if ref else ''),
            reference_no        = ref[:120],
            entity              = entity[:200],
            entity_ar           = '',
            description         = clean[:4000],
            description_ar      = '',
            category            = category,
            status              = status,
            publication_date    = issue_date,
            submission_deadline = deadline,
            country_code        = _COUNTRY_CODES.get(country or '', self.country_code),
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _safe_category(self, text: str) -> str:
        """
        Provisional category, or 'OTHER' when the keyword tables are not loaded.

        `detect_category` needs `_reload_keywords_from_db()` to have run, which
        happens in `scrape()`. Parsing is deliberately callable on its own (the
        tests parse a saved fixture with no database and no fetch), so a missing
        keyword table must degrade rather than raise — and it costs nothing,
        because the classification service replaces this value anyway.
        """
        try:
            return self.detect_category(text)
        except AttributeError:
            return 'OTHER'

    def _issue_date(self, soup) -> Optional[date]:
        """`<time class="entry-date published">Sat, 01 Aug 2026</time>`"""
        node = soup.select_one('time.entry-date.published') or soup.select_one('time')
        if node is None:
            return None
        raw = ' '.join(node.get_text(' ', strip=True).split())
        for fmt in ('%a, %d %b %Y', '%d %b %Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        logger.debug(f'[MAG:{CODE}] unrecognised issue date {raw!r}')
        return None

    @staticmethod
    def _entity(title: str) -> str:
        """
        The issuing body is written inline as the trailing "… for <Entity>".

        Scanned rightmost-first: in "…access roads upgrade for Ministry of
        Works" the entity is the ministry, not the earlier scope phrase.
        """
        for m in reversed(list(re.finditer(r'(?i),?\s+for\s+(?:the\s+)?', title))):
            tail = title[m.end():].strip()
            if re.fullmatch(r'[A-Z][^.,]{3,90}', tail):
                return tail
        return ''

    @staticmethod
    def _closing_date(text: str, issue_date: Optional[date]) -> Optional[date]:
        """
        "CD August 12" → a date. The source carries no year, so it is taken
        from the issue date and rolled forward when the month sits well behind
        the issue month (a December issue listing a January closing date).
        """
        m = _CD_RE.search(text)
        if not m or m.group(1) not in _MONTHS:
            return None
        month, day = _MONTHS[m.group(1)], int(m.group(2))
        year = (issue_date or date.today()).year
        if issue_date and month < issue_date.month - 6:
            year += 1
        try:
            return date(year, month, day)
        except ValueError:
            logger.debug(f'[MAG:{CODE}] impossible closing date {year}-{month}-{day}')
            return None
