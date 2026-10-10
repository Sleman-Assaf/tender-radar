"""
DEPRECATED — Dynamic Source Engine (no longer used)
====================================================================

This app implemented a generic, JSON-configuration-driven scraping engine.
That approach was WITHDRAWN on 2026-07-30 in favour of the Scraper Factory,
which generates real Python scraper files in the existing house style
(see docs/SCRAPER_FACTORY_ARCHITECTURE.md).

Why the package still exists
----------------------------
Only for its 14 database tables, which are intentionally left in place
(all empty) until the Scraper Factory has been running successfully for a
while. Removing them now would mean a destructive migration for no benefit.

What is left here
-----------------
`models.py` (so the tables stay known to Django and the migration state stays
consistent) and `migrations/`. Every behavioural module — the generic engines,
field mapper, config store, JSON schema, storage seam, dynamic scheduler and
worker — has been deleted.

Do not add code here. New work belongs in `scraper_factory`.
"""

default_app_config = "sources_dynamic.apps.SourcesDynamicConfig"
