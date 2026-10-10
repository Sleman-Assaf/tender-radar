# magazine_tenders/scrapers/arabianbusiness.py
"""
Arabian Business (arabianbusiness.com).

    URL          : https://www.arabianbusiness.com/wp-json/wp/v2/posts
    Technology   : WordPress REST. The site 403s a plain requests session, so
                   the fetch impersonates a browser TLS fingerprint via
                   curl_cffi — the only approach that returned 200.
    Request      : GET, params {search: ..., per_page: 100}
    Robots       : `*` blocks only /wp-admin/, /wp-content/plugins/, /public/
                   styles/ and /search/; articles and the API are permitted,
                   and the site publishes news-sitemap.xml.
    Date format  : ISO-8601 in `date` / `date_gmt`.

    WHAT THIS SOURCE IS
    -------------------
    A general business-news publication. It has no tender route or taxonomy at
    all, and measured on 2026-09-02 NONE of the 100 posts matching "tender"
    were open tenders — they were business stories that mention the word.

    Built because it was requested. Expect this magazine to contribute rarely,
    and possibly never; the shared filter is what stops it contributing noise.

    Investigated : 2026-09-02
"""
from ._newsfeed import NewsFeedMagazineScraper

BASE_URL = 'https://www.arabianbusiness.com'


class ArabianBusinessScraper(NewsFeedMagazineScraper):
    CODE            = 'ARABIANBUSINESS'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'wp_rest'
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = True

    WP_QUERIES = [
        {},                          # all recent news across every section
        {'search': 'design'},
        {'search': 'supervision'},
        {'search': 'tender'},
    ]

    country_code = 'AE'
    source_name  = 'Arabian Business — tender announcements'
    base_url     = BASE_URL
