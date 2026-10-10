"""App config for the DEPRECATED Dynamic Source Engine.

Kept installed only so its (empty) tables remain known to Django and the
migration state stays consistent. `ready()` is intentionally empty: this app
has no behaviour, starts no threads, and registers no signals.

See `sources_dynamic/__init__.py` and docs/SCRAPER_FACTORY_ARCHITECTURE.md.
"""

from django.apps import AppConfig


class SourcesDynamicConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sources_dynamic"
    verbose_name = "Dynamic Sources (deprecated)"

    def ready(self):
        # Deliberately empty — the app is retained for its tables only.
        return
