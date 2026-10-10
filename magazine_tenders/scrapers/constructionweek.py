# magazine_tenders/scrapers/constructionweek.py
"""
Construction Week Online (constructionweekonline.com).

    URL          : https://www.constructionweekonline.com/wp-json/wp/v2/posts
                   ?categories=511  ("Projects and Tenders")
    Technology   : WordPress with an open REST API. The HTML pages 403 a plain
                   requests session, so the fetch impersonates a browser TLS
                   fingerprint via curl_cffi (already in requirements.txt).
    Request      : GET, params {categories: 511, per_page: 100, orderby: date}
    Structure    : standard WP post objects — title.rendered, excerpt.rendered,
                   content.rendered, link, date.
    Robots       : only /wp-admin/ is disallowed for `*`; the API and articles
                   are permitted, and the site publishes news-sitemap.xml.
    Date format  : ISO-8601 in `date` / `date_gmt`.
    Pagination   : `page` param; one page of 100 is plenty given the yield.

    WHAT THIS SOURCE IS
    -------------------
    A trade-news publication, NOT a tender board. Category 511 is named
    "Projects and Tenders" and holds ~25,000 posts, but the overwhelming
    majority report awarded contracts and project progress. Measured on
    2026-09-02: of the 100 most recent posts, exactly ONE was a genuine open
    tender ("RTA invites bids for Dubai Metro Gold Line project").

    The filter in _newsfeed.py is what keeps the other 99 out. Expect a trickle,
    not a feed.

    Investigated : 2026-09-02
"""
from ._newsfeed import NewsFeedMagazineScraper

BASE_URL = 'https://www.constructionweekonline.com'

#: "Projects and Tenders" — the closest thing the site has to a tender section.
CATEGORY_PROJECTS_AND_TENDERS = 511


class ConstructionWeekScraper(NewsFeedMagazineScraper):
    CODE            = 'CONSTRUCTIONWEEK'
    BASE_URL        = BASE_URL
    FEED_MODE       = 'wp_rest'
    DEFAULT_COUNTRY = 'AE'
    IMPERSONATE     = True

    # Two passes: the tender-ish category, then an explicit search, because an
    # announcement is sometimes filed under a country category instead.
    # All sections, not just the tender category: every part of a WordPress
    # site publishes into the same /wp-json/wp/v2/posts feed, so an unfiltered
    # query IS "check all the news in the nav". The two searches stay because
    # they reach further back than the 100 most recent posts for the wording
    # that matters most.
    WP_QUERIES = [
        {},                                             # all recent news
        {'categories': CATEGORY_PROJECTS_AND_TENDERS},  # the tender beat
        {'search': 'design'},
        {'search': 'supervision'},
        {'search': 'tender'},
    ]

    country_code = 'AE'
    source_name  = 'Construction Week Online — tender announcements'
    base_url     = BASE_URL
