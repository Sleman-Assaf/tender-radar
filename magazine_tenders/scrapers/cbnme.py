# magazine_tenders/scrapers/cbnme.py
"""
Construction Business News Middle East (cbnme.com).

    URL          : https://www.cbnme.com/wp-json/wp/v2/posts
    Technology   : WordPress with an open REST API; plain GET works.
    Request      : GET, params {search: ..., per_page: 100, orderby: date}
    Structure    : standard WP post objects.
    Robots       : `User-agent: *` is `Allow: /` (only /wp-admin/ is blocked).
                   The site also sets Content-Signal `search=yes, ai-train=no,
                   use=reference` — i.e. indexing and reference use are
                   permitted; training models on the content is not, which this
                   scraper does not do.
    Date format  : ISO-8601 in `date` / `date_gmt`.

    WHAT THIS SOURCE IS
    -------------------
    A construction/logistics trade-news magazine with NO tender section: its 58
    categories contain none for tenders, and /tenders, /bids, /procurement and
    ~20 similar paths all 404. Measured on 2026-09-02: of 100 posts matching
    "tender", exactly ONE was a genuine open tender (the same RTA Dubai Metro
    Gold Line prequalification Construction Week carried).

    Kept because a real announcement does occasionally appear, and the shared
    filter rejects the rest. Cross-magazine duplicates are expected — the same
    story runs in several titles.

    Investigated : 2026-09-02
"""
from ._newsfeed import NewsFeedMagazineScraper

BASE_URL = 'https://www.cbnme.com'


class CBNMEScraper(NewsFeedMagazineScraper):
    CODE            = 'CBNME'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'wp_rest'
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = False

    WP_QUERIES = [
        {},                          # all recent news across every section
        {'search': 'design'},
        {'search': 'supervision'},
        {'search': 'tender'},
    ]

    country_code = 'AE'
    source_name  = 'Construction Business News ME — tender announcements'
    base_url     = BASE_URL
