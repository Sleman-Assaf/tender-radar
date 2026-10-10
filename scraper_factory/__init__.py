"""
Scraper Factory — an AI scraper developer
=========================================

Automates what a developer does today when a new tender source is discovered:
writes a new Python scraper file, in this project's exact style, and adds it to
`tenders/scrapers/`.

The output is a real `.py` file — not a configuration, not a generic engine.
Once installed it is registered in `SCRAPER_REGISTRY` and run by the existing
APScheduler loop exactly like the 73 hand-written scrapers.

Ground truth
------------
`tenders/scrapers/` is the specification. Where this package's ideas and the
repository disagree, the repository wins. The factory never refactors,
modernises, reformats or "improves" anything: it reproduces the existing style,
including its duplication, its `print()` calls and its print/logger mixing.

Verification only
-----------------
The validator may VERIFY generated code. It must never rewrite, reformat or
normalise it. The model produces the final file directly.

See docs/SCRAPER_FACTORY_ARCHITECTURE.md (v3.0).
"""

default_app_config = "scraper_factory.apps.ScraperFactoryConfig"
