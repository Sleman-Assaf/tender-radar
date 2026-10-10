# magazine_tenders/__init__.py
"""
News — الأخبار
======================================

Scrapes AEC tender announcements from magazine websites, classifies each one
with the EXISTING AJiAI service, and surfaces the AEC results in this app's own
page and in the shared Dashboard.

Why a separate app
------------------
Registering these scrapers in `tenders.scrapers.SCRAPER_REGISTRY` with active
`ScraperSource` rows would pull them into "Run All Scanners" and into the All
Tenders browsing list — a change to existing behaviour. Keeping them here means
the feature is additive and removable: delete this folder, its `INSTALLED_APPS`
entry, its URL include and the nav lines, and TenderRadar is untouched. This is
the same containment the `newspaper_tenders` app uses, and this app deliberately
mirrors its structure so there is one pattern to learn, not two.

How a magazine tender reaches the user
--------------------------------------
    sources.MAGAZINES          one entry per website
      -> registry.get_magazine_scraper(code)
      -> scrapers/<code>.py    subclasses tenders.scrapers.base.BaseScraper,
                               returns list[TenderData]; NEVER saves
      -> runner.run_one(code)  saves via tenders.services.save_tender_data
      -> save_tender_data      calls the standard classification service
                               (tenders.ajibrain_client) which sets is_aec,
                               category and title_ar
      -> MagazineTenderMeta    provenance row (which magazine, which listing page)
      -> views                 AEC rows on /magazine-tenders/, non-AEC rows on
                               the admin-only audit page

Two rules that must not drift
-----------------------------
1. A scraper never writes to the database. Persistence and classification are
   the runner's job, exactly as `tenders.apps._scrape_one_source` does it for
   the 80 web scrapers.
2. Classification is the shared service, unchanged. There is no
   magazine-specific AI rule anywhere: whether a tender is AEC is decided by
   `/api/classify-tender/` and nothing else. The magazine page simply filters
   on the `is_aec` flag the service produced.

The anchor `ScraperSource` rows carry `scraper_class='MAGAZINE_<CODE>'` and
`is_active=False`, so the 6-hourly scraper schedule and every source list skip
them. `MAGAZINE_` is the prefix every exclusion filter keys off.
"""
