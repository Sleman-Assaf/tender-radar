"""Runtime registration of installed generated scrapers.

Registration strategy R1 (approved): the installer appends a permanent import
line and a `SCRAPER_REGISTRY` entry to `tenders/scrapers/__init__.py`, exactly
as a developer would. That makes the persistent registration indistinguishable
from a manual one.

This module provides the complementary *runtime* registration used for the
window between installing a file and the next process restart, so a newly
installed scraper is usable without restarting anything.

Both paths converge on the same dict: `tenders.scrapers.SCRAPER_REGISTRY`.
`get_scraper()` reads that dict at call time, so a runtime insertion is picked
up immediately by the existing runner.

The AST-safe editing of `__init__.py` (with backup, verification and rollback)
lives in the installer, shipped in increment F6.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("scraper_factory")


def register_runtime(module_name: str, class_name: str) -> bool:
    """Import a generated module and put its class in SCRAPER_REGISTRY.

    Returns True on success. Never raises: a failure here must not prevent the
    site from serving requests.
    """
    try:
        from importlib import import_module

        from tenders.scrapers import SCRAPER_REGISTRY

        module = import_module(f"tenders.scrapers.{module_name}")
        cls = getattr(module, class_name, None)
        if cls is None:
            logger.warning("[factory] %s not found in tenders.scrapers.%s",
                           class_name, module_name)
            return False

        SCRAPER_REGISTRY[class_name] = cls
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] runtime registration failed for %s: %s",
                       class_name, exc)
        return False


def reregister_installed_scrapers() -> int:
    """Ensure every INSTALLED generated scraper is present in the registry.

    Called from `apps.ready()`. Normally a no-op because the persistent
    `__init__.py` entry already imported the class. Kept as a safety net and
    written to tolerate an unmigrated database (e.g. during `migrate`).
    """
    try:
        from django.db import connection

        table = "scraper_factory_generatedscraper"
        if table not in connection.introspection.table_names():
            return 0

        from tenders.scrapers import SCRAPER_REGISTRY

        from .models import FactoryStatus, GeneratedScraper

        rows = GeneratedScraper.objects.filter(
            status=FactoryStatus.INSTALLED,
        ).values_list("module_name", "class_name")

        count = 0
        for module_name, class_name in rows:
            if class_name in SCRAPER_REGISTRY:
                continue
            if register_runtime(module_name, class_name):
                count += 1
        if count:
            logger.info("[factory] runtime-registered %s generated scraper(s)", count)
        return count
    except Exception as exc:  # noqa: BLE001
        logger.debug("[factory] reregister skipped: %s", exc)
        return 0
