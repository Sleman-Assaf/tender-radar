# newspaper_tenders/__init__.py
# Newspaper Tenders — عطاءات الجريدة.
#
# Isolated feature that discovers the daily Al-Rai newspaper advertisements
# edition, downloads its scanned PDF pages, sends them to the AJiAI Newspaper
# Vision API, and saves the extracted tenders through the EXISTING TenderRadar
# save pipeline (tenders.services.save_tender_data).
#
# Design rules (see docs in each module):
#   * Tender records live in the existing tenders.Tender table so every
#     existing behaviour (dedup, bookmarks, notes, digest, detail page)
#     keeps working. Newspaper provenance lives in this app's own tables.
#   * The feature is NOT a scraper: its ScraperSource row is inactive and
#     hidden from the Scrapers UI; processing is driven by this app's own
#     Celery Beat schedule.
#   * Removing this app (and its INSTALLED_APPS entry + URL include) leaves
#     the rest of TenderRadar fully functional.
