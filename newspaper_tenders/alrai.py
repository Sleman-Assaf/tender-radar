# newspaper_tenders/alrai.py
# Al-Rai daily edition discovery — verified against the live site 2026-08-19.
#
# How the site actually works (do NOT guess URLs — parse everything):
#   * The advertisements section https://alrai.com/الاعلانات-الورقية is
#     server-rendered and lists one article per edition day. The edition date
#     is embedded in the Arabic URL slug: "…لليوم-19-اب-2026…" (Levantine
#     month names; no edition on the Fri/Sat weekend).
#   * Each edition article's body contains one <embed src="….pdf"> element
#     per scanned advertisement page (14 PDFs seen in one edition). The
#     article is often published BEFORE the PDFs are attached — an article
#     with zero embeds means "pending", not "empty".
#   * Cloudflare fronts the site: a bare User-Agent gets 403; a realistic
#     Chrome UA works, and the PDF assets additionally want a Referer.

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date

import requests
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger('tenders')

# Levantine Arabic month names → month number. Keys are normalised via
# _norm_ar (hamza/madda variants collapse to bare alef), so both "آب" and
# "اب" match.
ARABIC_MONTHS = {
    'كانون الثاني': 1, 'شباط': 2, 'اذار': 3, 'نيسان': 4,
    'ايار': 5, 'حزيران': 6, 'تموز': 7, 'اب': 8, 'أغسطس': 8,
    'ايلول': 9, 'تشرين الاول': 10, 'تشرين الثاني': 11, 'كانون الاول': 12,
}

# "لليوم-19-اب-2026" inside the URL-decoded slug (hyphen- or space-separated)
_SLUG_DATE_RE = re.compile(r'لليوم[-\s]+(\d{1,2})[-\s]+([^-\s]+(?:[-\s]+(?:الاول|الثاني))?)[-\s]+(\d{4})')

_ARTICLE_HREF_RE = re.compile(r'https?://(?:www\.)?alrai\.com/article/(\d+)/[^"\']*')


def _norm_ar(text: str) -> str:
    """Collapse alef variants + strip tatweel/diacritics for robust matching."""
    text = re.sub(r'[أإآٱ]', 'ا', text)
    text = re.sub(r'[ً-ْـ]', '', text)
    return text


@dataclass
class EditionArticle:
    article_id:   str
    url:          str
    edition_date: date


@dataclass
class DiscoveryResult:
    article:  EditionArticle
    pdf_urls: list = field(default_factory=list)


def _headers(referer: str = 'https://alrai.com/') -> dict:
    # A realistic browser UA is REQUIRED (bare UAs get Cloudflare 403).
    ua = getattr(settings, 'NEWSPAPER_USER_AGENT', '') or getattr(
        settings, 'SCRAPER_USER_AGENT',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    return {
        'User-Agent': ua,
        'Referer': referer,
        'Accept-Language': 'ar,en;q=0.9',
    }


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    timeout = int(getattr(settings, 'NEWSPAPER_DOWNLOAD_TIMEOUT', 60))
    resp = session.get(url, headers=_headers(), timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


def parse_slug_date(href: str):
    """Extract the edition date from an article URL slug, or None."""
    decoded = _norm_ar(urllib.parse.unquote(href))
    m = _SLUG_DATE_RE.search(decoded)
    if not m:
        return None
    day, month_name, year = m.groups()
    month_name = _norm_ar(month_name).replace('-', ' ').strip()
    month = ARABIC_MONTHS.get(month_name)
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def list_edition_articles(session: requests.Session) -> list[EditionArticle]:
    """Fetch the advertisements section page and return the dated articles."""
    section_url = getattr(settings, 'NEWSPAPER_SOURCE_URL',
                          'https://alrai.com/الاعلانات-الورقية')
    resp = _get(session, section_url)

    seen: dict[str, EditionArticle] = {}
    for m in _ARTICLE_HREF_RE.finditer(resp.text):
        href, article_id = m.group(0), m.group(1)
        edition_date = parse_slug_date(href)
        if edition_date is None:
            continue
        # Keep the highest article id per date (re-published articles)
        prev = seen.get(edition_date.isoformat())
        if prev is None or int(article_id) > int(prev.article_id):
            seen[edition_date.isoformat()] = EditionArticle(
                article_id=article_id, url=href, edition_date=edition_date)

    articles = sorted(seen.values(), key=lambda a: a.edition_date, reverse=True)
    logger.info(f'[newspaper.alrai] section page: {len(articles)} dated edition article(s)')
    return articles


def find_edition_for_date(session: requests.Session, target: date):
    """Return the EditionArticle for `target`, or None when not published."""
    for article in list_edition_articles(session):
        if article.edition_date == target:
            return article
    return None


def extract_pdf_urls(session: requests.Session, article: EditionArticle) -> list[str]:
    """
    Fetch the edition article and return the embedded PDF URLs, in page order.
    An empty list is a NORMAL state (PDFs not attached yet — retry later).

    Quirk (verified live 2026-08-19): the full Arabic-slug article URL renders
    the page WITHOUT the <embed> elements, while the short canonical form
    article/<id>/x/y renders WITH them. So the short form is fetched first and
    the original slug URL is only a fallback.
    """
    short_url = f'https://alrai.com/article/{article.article_id}/x/y'

    for candidate in (short_url, article.url):
        resp = _get(session, candidate)
        soup = BeautifulSoup(resp.text, 'html.parser')

        urls: list[str] = []
        for emb in soup.find_all('embed'):
            src = (emb.get('src') or '').strip()
            if not src:
                continue
            src = urllib.parse.urljoin(candidate, src)
            if src.lower().split('?')[0].endswith('.pdf') and src not in urls:
                urls.append(src)

        if urls:
            logger.info(f'[newspaper.alrai] article {article.article_id} '
                        f'({article.edition_date}): {len(urls)} PDF embed(s) '
                        f'via {candidate}')
            return urls

    logger.info(f'[newspaper.alrai] article {article.article_id} '
                f'({article.edition_date}): 0 PDF embeds (pending?)')
    return []


def discover(target: date) -> DiscoveryResult | None:
    """
    Full discovery for one date. Returns None when no edition article exists
    for that date (weekend / not published yet — a normal outcome).
    """
    session = requests.Session()
    article = find_edition_for_date(session, target)
    if article is None:
        return None
    pdf_urls = extract_pdf_urls(session, article)
    return DiscoveryResult(article=article, pdf_urls=pdf_urls)
