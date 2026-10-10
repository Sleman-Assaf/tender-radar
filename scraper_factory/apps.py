"""App config for the Scraper Factory.

`ready()` starts no scheduler, no threads and no network calls. The APScheduler
loop in `tenders/apps.py` remains the only scheduler in the project and is not
modified by this app.

It does attach one read-only observer to ScrapeLog (Phase 4A). That is how
scheduler integration is kept inside the Factory: the Factory reacts to the
record production already writes, rather than production calling into the
Factory. Runs of hand-written scrapers are ignored by the handler.
"""

from django.apps import AppConfig


class ScraperFactoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scraper_factory"
    verbose_name = "Scraper Factory"

    def ready(self):
        # Re-register previously installed generated scrapers into
        # tenders.scrapers.SCRAPER_REGISTRY at runtime.
        #
        # Under R1 the installer also appends a permanent import + registry
        # entry to tenders/scrapers/__init__.py, so this call is normally a
        # no-op. It exists as a safety net for the window between writing the
        # file and the next process restart, and it is deliberately failure
        # tolerant: a problem here must never prevent the site from booting.
        try:
            from .registration import reregister_installed_scrapers
            reregister_installed_scrapers()
        except Exception:  # noqa: BLE001 — never break site startup
            pass

        # Health observer. Same failure tolerance: telemetry must never stop the
        # site booting, and it can be switched off entirely by configuration.
        try:
            from django.conf import settings
            if getattr(settings, "FACTORY_HEALTH_OBSERVER_ENABLED", True):
                from .observers import connect
                connect()
        except Exception:  # noqa: BLE001
            pass
