# magazine_tenders/sources.py
# Registry of the magazine websites the feature reads. Adding a magazine means:
# one scraper module + one entry here — the runner, models, scheduler, views and
# the AI service are all source-agnostic.

from django.conf import settings


# The feature's display name. NOT wired up: the templates, apps.py
# verbose_name and urls.py comments all spell it out themselves, so
# renaming the feature currently means editing those too (it took 13
# files to go from 'Magazines Announcing' to 'News' on
# 2 September 2026). Kept in step with them by hand; wire the nav and
# page titles to these two names if you want the one-place rename the
# previous comment here claimed.
MAGAZINE_NAV_LABEL = 'News'
MAGAZINE_NAV_LABEL_AR = 'الأخبار'


MAGAZINES = {
    # ── The six websites supplied by the business ─────────────────────────────
    # This registry is the whole feature: these six and nothing else. Five
    # further sources were researched and built during the same work (MEED,
    # Utilities ME, ME Construction News, and the African and Islamic
    # Development Bank EOI registers), then removed at the business's request
    # along with their scrapers, tests and fixtures. Adding a source back means
    # writing it again, so do not expect to find a parked module for it.

    # Gulf Construction — the monthly "Tenders" column, and the only one of the
    # six that is a genuine tender listing rather than a news feed. Verified
    # 2026-09-02: 67 notices in the August issue across six Gulf countries,
    # every one with a reference number and closing date. MONTHLY cadence, so a
    # daily sweep mostly re-reads the same issue — harmless, because the
    # reference number keys a stable external_id.
    # Ashghal — المشاريع المتوقعة. Not a magazine and not a news feed: the
    # Qatar Public Works Authority's own list of projects it intends to put out
    # to tender, published a quarter ahead. Advance notice of work coming,
    # which is why it sits in News; the resulting tenders arrive separately
    # through tenders/scrapers/ashghal.py.
    'ASHGHALPROSPECTED': {
        'code':          'ASHGHALPROSPECTED',
        'name':          'Ashghal — Prospected Projects',
        'name_ar':       'أشغال — المشاريع المتوقعة',
        'display':       'أشغال — المشاريع المتوقعة',
        'display_en':    'Ashghal Prospected',
        # Shown in the photo slot; this source has no photos.
        'kind':          'Public Works Authority · Qatar',
        'scraper_class': 'MAGAZINE_ASHGHALPROSPECTED',
        'base_url':      'https://www.ashghal.gov.qa',
        'country_code':  'QA',
        'id_prefix':     'mag-ashghalpp',
        # Publishes work that is COMING, not work open now. Drives the
        # "Future Projects" button on the News page.
        'forthcoming':   True,
        'scraper':       'magazine_tenders.scrapers.ashghal_prospected.AshghalProspectedScraper',
        'listing_urls':  ['https://www.ashghal.gov.qa/en/Tenders/Pages/ProspectedTenders.aspx?Quarter=1'],
    },

    # DevelopmentAid, forecast filter (statuses=2): donor-funded work that has
    # been announced but is not yet open for bids. Same character as the
    # Ashghal pipeline, so it sits under Future Projects.
    #
    # Reuses tenders/scrapers/developmentaid.py — the OPEN notices from the
    # same site keep arriving through DevelopmentAidScraper in the Tenders
    # section. One site, two stages, two places.
    'DEVAIDFORECAST': {
        'code':          'DEVAIDFORECAST',
        'name':          'DevelopmentAid — Forecast Notices',
        'name_ar':       'ديفلوبمنت إيد — إشعارات متوقعة',
        'display':       'ديفلوبمنت إيد — متوقعة',
        'display_en':    'DevelopmentAid Forecast',
        'kind':          'Donor forecast notice',
        'scraper_class': 'MAGAZINE_DEVAIDFORECAST',
        'base_url':      'https://www.developmentaid.org',
        # International: the country is resolved per notice from its own
        # location, so this is only the anchor row's country.
        'country_code':  'JO',
        'id_prefix':     'mag-devaidfc',
        'scraper':       'magazine_tenders.scrapers.developmentaid_forecast.DevelopmentAidForecastScraper',
        'forthcoming':   True,
        'listing_urls':  ['https://www.developmentaid.org/tenders/search'
                          '?hiddenAdvancedFilters=0&statuses=2'],
    },

    'GULFCONSTRUCTION': {
        'code':          'GULFCONSTRUCTION',
        'name':          'Gulf Construction — Tenders Column',
        'name_ar':       'مجلة الخليج للإنشاءات — إعلانات العطاءات',
        'display':       'الخليج للإنشاءات',
        'display_en':    'Gulf Construction',
        # One web page parsed into ~50 items: there is no per-item photo,
        # and the block says so rather than pretending one failed to load.
        'kind':          'Monthly tenders column',
        'scraper_class': 'MAGAZINE_GULFCONSTRUCTION',
        'base_url':      'https://gulfconstructiononline.com',
        # The column spans BH/KW/OM/QA/SA/AE and the scraper sets each item's
        # country itself; this is only the anchor row's country.
        'country_code':  'BH',
        'id_prefix':     'mag-gulfcon',
        'scraper':       'magazine_tenders.scrapers.gulfconstruction.GulfConstructionScraper',
        'listing_urls':  ['https://gulfconstructiononline.com/Section/518/Tenders'],
    },

    # ── The remaining five are NEWS publications, not tender boards ──────────
    # They have no tender listing page: they publish articles, a small minority
    # of which announce a tender open for bids. The shared reader in
    # scrapers/_newsfeed.py keeps only those and rejects the rest. Measured
    # yield per 100 recent items is in each module's docstring — it is a
    # trickle, and that is the honest shape of these sources.

    # Zawya: a genuine tenders beat, but reachable only through a news sitemap
    # covering a rolling ~2-day window, so a single daily sweep will miss items.
    'ZAWYA': {
        'code':          'ZAWYA',
        'name':          'Zawya — Tender Announcements',
        'name_ar':       'زاوية — إعلانات العطاءات',
        'display':       'زاوية',
        'display_en':    'Zawya',
        'scraper_class': 'MAGAZINE_ZAWYA',
        'base_url':      'https://www.zawya.com',
        'country_code':  'AE',
        'id_prefix':     'mag-zawya',
        'scraper':       'magazine_tenders.scrapers.zawya.ZawyaScraper',
        'listing_urls':  ['https://www.zawya.com/sitemaps/en/sitemap-news.xml',
                          'https://www.zawya.com/sitemaps/ar/sitemap-news.xml'],
    },

    # Construction Week: category 511 "Projects and Tenders" (~25,000 posts),
    # of which roughly 1 in 100 recent items is an open tender.
    # Al-Rai — the newspaper's WEBSITE, not its printed tender adverts.
    # Those are a different feature entirely (newspaper_tenders, which reads
    # the published PDF), so this one is labelled "الموقع" to keep the two
    # apart in the source filter.
    #
    # Two sections: اقتصاد is a money desk and contributes rarely; محليات is
    # where Jordanian project news actually appears. Both are read and the
    # Arabic keep-rules decide - see magazine_tenders/scrapers/alrai.py.
    # Ad-Dustour - the newspaper's WEBSITE. Its printed tender adverts are a
    # different feature (newspaper_tenders), hence "الموقع" here too.
    #
    # اقتصاد only: the section is one server-rendered page of ~51 articles and
    # its paging does not work, so this is one request per run.
    'ADDUSTOUR': {
        'code':          'ADDUSTOUR',
        'name':          'Ad-Dustour (website) - economy',
        'name_ar':       'الدستور (الموقع) - اقتصاد',
        'display':       'الدستور - الموقع',
        'display_en':    'Ad-Dustour (web)',
        'scraper_class': 'MAGAZINE_ADDUSTOUR',
        'base_url':      'https://www.addustour.com',
        'country_code':  'JO',
        'id_prefix':     'mag-dustour',
        'scraper':       'magazine_tenders.scrapers.addustour.AdDustourScraper',
        'listing_urls':  ['https://www.addustour.com/categories/4-%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF'],
        # Faces the classifier on the News page: no PROJECT exemption. The
        # Arabic project rule matches government talk - "developing the
        # roads", "launching a programme in schools" - far too often to
        # stand in for the AI the way the English rule does. See
        # magazine_tenders.views.news_exemption_q.
        'news_exemption': False,
    },

    # اقتصاد only. The category page is one server-rendered page and its
    # paging does not work, so this is one request per run, plus one per
    # KEPT item to read the dateline off the article page.
    'ALGHAD': {
        'code':          'ALGHAD',
        'name':          'Al-Ghad (website) - economy',
        'name_ar':       'الغد (الموقع) - اقتصاد',
        'display':       'الغد — الموقع',
        'display_en':    'Al-Ghad (web)',
        'scraper_class': 'MAGAZINE_ALGHAD',
        'base_url':      'https://alghad.com',
        'country_code':  'JO',
        'id_prefix':     'mag-alghad',
        'scraper':       'magazine_tenders.scrapers.alghad.AlGhadScraper',
        'listing_urls':  ['https://alghad.com/Category/18/%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF'],
        # Faces the classifier on the News page: no PROJECT exemption. The
        # Arabic project rule matches government talk - "developing the
        # roads", "launching a programme in schools" - far too often to
        # stand in for the AI the way the English rule does. See
        # magazine_tenders.views.news_exemption_q.
        'news_exemption': False,
    },

    # Pan-Arab general news, Saudi-weighted. Read by section: the site's own
    # search, RSS and sitemap all answer 403 to anything but a browser, so
    # there is no index to walk. The date is in the article URL, so a section
    # page is the only request this source makes.
    'ALARABIYA': {
        'code':          'ALARABIYA',
        'name':          'Al Arabiya - business and regional news',
        'name_ar':       'العربية - أسواق وأخبار',
        'display':       'العربية',
        'display_en':    'Al Arabiya',
        'scraper_class': 'MAGAZINE_ALARABIYA',
        'base_url':      'https://www.alarabiya.net',
        'country_code':  'AE',
        'id_prefix':     'mag-alarabiya',
        'scraper':       'magazine_tenders.scrapers.alarabiya.AlArabiyaScraper',
        'listing_urls':  ['https://www.alarabiya.net/aswaq',
                          'https://www.alarabiya.net/aswaq/oil-and-gas',
                          'https://www.alarabiya.net/saudi-today',
                          'https://www.alarabiya.net/arab-and-world'],
        # Faces the classifier on the News page: no PROJECT exemption. The
        # Arabic project rule matches government talk - "developing the
        # roads", "launching a programme in schools" - far too often to
        # stand in for the AI the way the English rule does. See
        # magazine_tenders.views.news_exemption_q.
        'news_exemption': False,
    },

    # The state wire. Three sections, one request each, and no second
    # request for a dateline: Petra puts the date in the photo's path.
    # محافظات is read as well as اقتصاد — the built work is filed there.
    'PETRA': {
        'code':          'PETRA',
        'name':          'Petra - Jordan News Agency',
        'name_ar':       'وكالة الأنباء الأردنية (بترا)',
        'display':       'بترا — وكالة الأنباء',
        'display_en':    'Petra (JNA)',
        'scraper_class': 'MAGAZINE_PETRA',
        'base_url':      'https://petra.gov.jo',
        'country_code':  'JO',
        'id_prefix':     'mag-petra',
        'scraper':       'magazine_tenders.scrapers.petra.PetraScraper',
        'listing_urls':  ['https://petra.gov.jo/ar/news/search/category/governorates',
                          'https://petra.gov.jo/ar/news/search/category/business-economy',
                          'https://petra.gov.jo/ar/news/search/category/local-news'],
        # Faces the classifier on the News page: no PROJECT exemption. The
        # Arabic project rule matches government talk - "developing the
        # roads", "launching a programme in schools" - far too often to
        # stand in for the AI the way the English rule does. See
        # magazine_tenders.views.news_exemption_q.
        'news_exemption': False,
    },

    'ALRAI': {
        'code':          'ALRAI',
        'name':          'Al-Rai (website) — economy and local news',
        'name_ar':       'الرأي (الموقع) — اقتصاد ومحليات',
        'display':       'الرأي — الموقع',
        'display_en':    'Al-Rai (web)',
        'scraper_class': 'MAGAZINE_ALRAI',
        'base_url':      'https://alrai.com',
        'country_code':  'JO',
        'id_prefix':     'mag-alrai',
        'scraper':       'magazine_tenders.scrapers.alrai.AlRaiScraper',
        'listing_urls':  ['https://alrai.com/api/pb/widget/1809/json?section_id=4',
                          'https://alrai.com/api/pb/widget/1809/json?section_id=1'],
        # Faces the classifier on the News page: no PROJECT exemption. The
        # Arabic project rule matches government talk - "developing the
        # roads", "launching a programme in schools" - far too often to
        # stand in for the AI the way the English rule does. See
        # magazine_tenders.views.news_exemption_q.
        'news_exemption': False,
    },

    'CONSTRUCTIONWEEK': {
        'code':          'CONSTRUCTIONWEEK',
        'name':          'Construction Week Online — Tender Announcements',
        'name_ar':       'كونستراكشن ويك — إعلانات العطاءات',
        'display':       'كونستراكشن ويك',
        'display_en':    'Construction Week',
        'scraper_class': 'MAGAZINE_CONSTRUCTIONWEEK',
        'base_url':      'https://www.constructionweekonline.com',
        'country_code':  'AE',
        'id_prefix':     'mag-cwo',
        'scraper':       'magazine_tenders.scrapers.constructionweek.ConstructionWeekScraper',
        'listing_urls':  ['https://www.constructionweekonline.com/wp-json/wp/v2/posts?categories=511'],
    },

    # CBNME: no tender category at all; the same stories as Construction Week
    # often appear here, so cross-magazine duplicates are expected.
    'CBNME': {
        'code':          'CBNME',
        'name':          'Construction Business News ME — Tender Announcements',
        'name_ar':       'أخبار أعمال الإنشاءات — إعلانات العطاءات',
        'display':       'أخبار الإنشاءات',
        'display_en':    'CBNME',
        'scraper_class': 'MAGAZINE_CBNME',
        'base_url':      'https://www.cbnme.com',
        'country_code':  'AE',
        'id_prefix':     'mag-cbnme',
        'scraper':       'magazine_tenders.scrapers.cbnme.CBNMEScraper',
        'listing_urls':  ['https://www.cbnme.com/wp-json/wp/v2/posts?search=tender'],
    },

    # Arabian Business: general business news, with no tender route or taxonomy.
    # Zero open tenders in the 100 most recent "tender" matches when assessed —
    # expect this one to contribute rarely.
    'ARABIANBUSINESS': {
        'code':          'ARABIANBUSINESS',
        'name':          'Arabian Business — Tender Announcements',
        'name_ar':       'أرابيان بزنس — إعلانات العطاءات',
        'display':       'أرابيان بزنس',
        'display_en':    'Arabian Business',
        'scraper_class': 'MAGAZINE_ARABIANBUSINESS',
        'base_url':      'https://www.arabianbusiness.com',
        'country_code':  'AE',
        'id_prefix':     'mag-ab',
        'scraper':       'magazine_tenders.scrapers.arabianbusiness.ArabianBusinessScraper',
        'listing_urls':  ['https://www.arabianbusiness.com/wp-json/wp/v2/posts?search=tender'],
    },

    # Healthcare MEA: supplied specifically for healthcare tenders. It does not
    # publish them — its whole archive holds nine posts mentioning "tender" and
    # none announce one. Kept registered so the source is covered if the
    # publication ever starts running procurement notices.
    'HEALTHCAREMEA': {
        'code':          'HEALTHCAREMEA',
        'name':          'Healthcare MEA — Tender Announcements',
        'name_ar':       'هيلثكير الشرق الأوسط — إعلانات العطاءات',
        'display':       'هيلثكير',
        'display_en':    'Healthcare MEA',
        'scraper_class': 'MAGAZINE_HEALTHCAREMEA',
        'base_url':      'https://www.healthcaremea.com',
        'country_code':  'AE',
        'id_prefix':     'mag-hcmea',
        'scraper':       'magazine_tenders.scrapers.healthcaremea.HealthcareMEAScraper',
        'listing_urls':  ['https://www.healthcaremea.com/wp-json/wp/v2/posts?categories=4'],
    },
}

def forthcoming_codes() -> list:
    """
    Codes of the sources that publish FORTHCOMING work.

    A source property rather than a data one on purpose. The tempting test —
    "has no closing date" — is useless here: most trade-press items carry no
    date either, so it would sweep the ordinary news in with the pipeline.
    """
    return [c for c, cfg in MAGAZINES.items() if cfg.get('forthcoming')]


def enabled_sources() -> list[dict]:
    """
    Magazines to process, honouring per-source enable flags.

    `MAGAZINE_<CODE>_ENABLED=0` disables one magazine without touching this file,
    which is how a site that starts failing is parked without a deploy.
    """
    out = []
    for code, cfg in MAGAZINES.items():
        flag = f'MAGAZINE_{code}_ENABLED'
        if getattr(settings, flag, True):
            out.append(cfg)
    return out


def get_source_config(code: str) -> dict:
    """One magazine's config. Raises KeyError for an unknown code."""
    key = (code or '').strip().upper()
    if key not in MAGAZINES:
        raise KeyError(f'Unknown magazine code {code!r}. '
                       f'Known: {", ".join(sorted(MAGAZINES)) or "(none configured)"}')
    return MAGAZINES[key]


def news_exemption_off_codes() -> list[str]:
    """
    Sources whose classifier-rejected PROJECT news stays OFF the News page.

    The default is the exemption ON: a source that says nothing keeps it, and
    so does any code the registry does not know (the test doubles). Only a
    source that sets `news_exemption: False` is listed here.
    """
    return sorted(code for code, cfg in MAGAZINES.items()
                  if cfg.get('news_exemption', True) is False)


def all_codes() -> list[str]:
    return sorted(MAGAZINES)
