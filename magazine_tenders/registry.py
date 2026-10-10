# magazine_tenders/registry.py
"""
Resolves a magazine code to its scraper instance.

Deliberately NOT wired into `tenders.scrapers.SCRAPER_REGISTRY`: being in that
registry is what makes a scraper part of "Run All Scanners", and magazines must
stay out of it.
"""
import importlib
import logging

from . import sources

logger = logging.getLogger('tenders')


def get_magazine_scraper(code: str):
    """
    Import the dotted path in `cfg['scraper']` and return an instance.

    Raises KeyError for an unknown code, and ImportError/AttributeError if the
    configured path does not resolve — both are configuration mistakes worth
    failing loudly on, since a silent no-op would look like "the site has no
    tenders today".
    """
    cfg = sources.get_source_config(code)
    dotted = cfg['scraper']
    module_path, _, class_name = dotted.rpartition('.')
    if not module_path:
        raise ImportError(f'[magazine] {code}: scraper path {dotted!r} is not a dotted path')

    module = importlib.import_module(module_path)
    try:
        scraper_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise AttributeError(
            f'[magazine] {code}: {module_path} has no class {class_name!r}'
        ) from exc

    return scraper_cls()


def scraper_exists(code: str) -> bool:
    """True when the configured scraper for `code` can actually be imported."""
    try:
        get_magazine_scraper(code)
        return True
    except Exception as exc:
        logger.warning(f'[magazine] {code}: scraper not importable — {exc}')
        return False
