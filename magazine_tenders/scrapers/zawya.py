# magazine_tenders/scrapers/zawya.py
"""
Zawya (zawya.com) — LSEG's MENA business wire, including a real TENDERS beat.

    URL          : https://www.zawya.com/sitemaps/en/sitemap-news.xml
                   https://www.zawya.com/sitemaps/ar/sitemap-news.xml
    Technology   : static Google-news sitemap XML. A plain GET with a browser
                   User-Agent returns 200 — no anti-bot, no challenge.
    Request      : GET, no params.
    Structure    : `urlset/url` entries carrying `loc`, `lastmod` and a
                   `news:news` block with `news:title` + `news:publication_date`.
    Robots       : `*` allows /en/, /ar/ and /company/ and blocks /api/,
                   /account/ and the login routes. The sitemaps and articles
                   are permitted. robots.txt also sets Crawl-delay for `*`,
                   which the base class honours through SCRAPER_DELAY_BETWEEN_
                   REQUESTS between fetches.
    Date format  : ISO-8601 UTC with milliseconds, e.g. 2026-09-02T07:09:49.685Z
    Pagination   : none — a single unpaginated file.

    WHAT THIS SOURCE IS
    -------------------
    Unlike the four trade-news magazines, Zawya's Projects desk runs a genuine
    TENDERS beat: items are real biddable notices carrying a tender number
    (e.g. Kuwait PAHW substations, ref PAHW/M/1532-2026/2027).

    But there is NO tender listing page — every candidate path 404s and the HTML
    list pages render empty (client-side). The only reliable way in is this news
    sitemap, and it has one important property:

        IT COVERS A ROLLING ~2-DAY WINDOW.

    Items older than that are gone from it. A once-a-day sweep will therefore
    miss tenders published and rolled out between runs. If this source matters,
    schedule the magazine sweep several times a day (MAGAZINE_SCHEDULE_TIME
    drives one daily run; the manual button and the Celery task can add more).

    Some Zawya articles are also subscriber-only; the sitemap still lists them,
    the filter reads the headline, and the stored link goes to the article.

    Investigated : 2026-09-02
"""
from ._newsfeed import NewsFeedMagazineScraper

BASE_URL = 'https://www.zawya.com'


class ZawyaScraper(NewsFeedMagazineScraper):
    CODE            = 'ZAWYA'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'sitemap'
    # The sitemap names each photo by a bare path; the files are served from
    # the media host, not the site (the same path on www.zawya.com is a 404).
    IMAGE_BASE_URL  = 'https://media.zawya.com'
    # Every sitemap photo is /prod/images/original/<file>; the site's own
    # pages use /prod/images/gm_preview/<file> (950px) for the same picture.
    ORIGINAL_DIR    = '/prod/images/original/'
    PREVIEW_DIR     = '/prod/images/gm_preview/'
    # A MENA-wide wire: the base class infers each item's country from its text
    # and only falls back to this when nothing matches.
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = False

    SITEMAP_URLS = [
        f'{BASE_URL}/sitemaps/en/sitemap-news.xml',
        f'{BASE_URL}/sitemaps/ar/sitemap-news.xml',
    ]

    country_code = 'AE'
    source_name  = 'Zawya — tender announcements'
    base_url     = BASE_URL

    def _sitemap_image_sizes(self, img_url: str) -> tuple[str, str]:
        """Card: the 950px preview; hero: the original (often 1-3 MB)."""
        if self.ORIGINAL_DIR in img_url:
            return img_url.replace(self.ORIGINAL_DIR, self.PREVIEW_DIR, 1), img_url
        return img_url, img_url
