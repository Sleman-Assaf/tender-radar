# magazine_tenders/scrapers/healthcaremea.py
"""
Healthcare Middle East & Africa (healthcaremea.com) — healthcare AEC tenders.

    URL          : https://www.healthcaremea.com/wp-json/wp/v2/posts
    Technology   : WordPress with an open REST API; plain GET works.
    Request      : GET, params {categories: 4 | search: ..., per_page: 100}
    Structure    : standard WP post objects.
    Robots       : `User-agent: *` with an empty `Disallow:` — everything is
                   permitted.
    Date format  : ISO-8601 in `date` / `date_gmt`.

    WHAT THIS SOURCE IS — READ THIS BEFORE EXPECTING RESULTS
    --------------------------------------------------------
    A healthcare industry-news publication. Category 4 is "Hospitals" (335
    posts), which sounds promising, but measured on 2026-09-02:

        * 100 most recent "Hospitals" posts  -> 0 tender announcements
        * the site's ENTIRE archive contains  9 posts mentioning "tender",
          and none of them announce one.

    So healthcare AEC tenders are not published here. Where they DO arrive is
    Gulf Construction's Tenders column, which carries hospital work directly —
    e.g. "Design and construction of an orthopaedic clinic at Khoula Hospital",
    which the classification service files as HEALTH. That is the source
    actually feeding healthcare AEC tenders into TenderRadar today.

    This scraper is kept so the source is covered if the publication starts
    running procurement notices, and because the queries below also catch a
    hospital construction/design tender if one is ever posted.

    Investigated : 2026-09-02
"""
from ._newsfeed import NewsFeedMagazineScraper

BASE_URL = 'https://www.healthcaremea.com'

#: "Hospitals" — the category most likely to carry a build/design notice.
CATEGORY_HOSPITALS = 4


class HealthcareMEAScraper(NewsFeedMagazineScraper):
    CODE            = 'HEALTHCAREMEA'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'wp_rest'
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = False

    # Deliberately wider than the other magazines: healthcare AEC work is
    # described as hospital/clinic construction rather than as "a tender", so
    # the search terms aim at the build vocabulary as well.
    WP_QUERIES = [
        {},                                   # all recent news
        {'categories': CATEGORY_HOSPITALS},
        {'search': 'design'},
        {'search': 'supervision'},
        {'search': 'tender'},
        {'search': 'invites bids'},
        {'search': 'hospital construction'},
        {'search': 'hospital design'},
    ]

    country_code = 'AE'
    source_name  = 'Healthcare MEA — tender announcements'
    base_url     = BASE_URL
