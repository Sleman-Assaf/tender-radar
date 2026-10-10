# magazine_tenders/anchor.py
"""
The hidden `ScraperSource` row each magazine hangs off.

`tenders.Tender` requires a source FK, so every magazine needs a row — but a
magazine is not a scraper in the "Run All Scanners" sense. The row is therefore
created with `is_active=False`, which is what keeps `run_all_scrapers` from ever
picking it up, and its `scraper_class` starts with `MAGAZINE_`, which is what
every source-list exclusion filters on. Same device the newspaper app uses.
"""
import logging

from tenders import country_names
from tenders.models import CountryConfig, ScraperSource

from . import sources

logger = logging.getLogger('tenders')


# Minimal country seeds, used only when the row does not exist yet. Keeps a
# first run on an empty database from failing on a missing FK.
_COUNTRY_DEFAULTS = {
    'JO': {'name_en': 'Jordan',               'name_ar': 'الأردن',          'flag_emoji': '🇯🇴', 'currency': 'JOD'},
    'AE': {'name_en': 'United Arab Emirates', 'name_ar': 'الإمارات',        'flag_emoji': '🇦🇪', 'currency': 'AED'},
    'SA': {'name_en': 'Saudi Arabia',         'name_ar': 'السعودية',        'flag_emoji': '🇸🇦', 'currency': 'SAR'},
    'BH': {'name_en': 'Bahrain',              'name_ar': 'البحرين',         'flag_emoji': '🇧🇭', 'currency': 'BHD'},
    'KW': {'name_en': 'Kuwait',               'name_ar': 'الكويت',          'flag_emoji': '🇰🇼', 'currency': 'KWD'},
    'QA': {'name_en': 'Qatar',                'name_ar': 'قطر',             'flag_emoji': '🇶🇦', 'currency': 'QAR'},
    'OM': {'name_en': 'Oman',                 'name_ar': 'عمان',            'flag_emoji': '🇴🇲', 'currency': 'OMR'},
}


def _defaults(code: str) -> dict:
    """
    How a country is created the first time it is seen.

    The local table only ever held the seven Gulf states, and everything else
    fell back to the CODE as the name - which is how forty-four rows came to
    be called "CI", "NG" and "ZZ" on screen. The shared table names far more
    and derives a flag for the rest; the currencies stay here, because they
    are this project's concern rather than a fact about the country.
    """
    code = (code or '').strip().upper()
    base = country_names.defaults_for(code)
    local = _COUNTRY_DEFAULTS.get(code)
    if local:
        base = {**base, **{k: v for k, v in local.items() if v}}
    return base


def _get_country(code: str) -> CountryConfig:
    code = (code or 'JO').strip().upper()
    defaults = _defaults(code)
    country, _ = CountryConfig.objects.get_or_create(code=code, defaults=defaults)
    return country


def ensure_countries(codes) -> int:
    """
    Make sure a CountryConfig row exists for each of `codes`.

    Without this, `save_tender_data` cannot resolve a per-tender country and
    silently falls back to the SOURCE's country — which mislabels every tender
    from a magazine that covers several countries. Gulf Construction's column
    spans six, so a Qatar notice was being stored as Bahrain.
    """
    created = 0
    for code in {(c or '').strip().upper() for c in codes if c}:
        _, was_created = CountryConfig.objects.get_or_create(
            code=code, defaults=_defaults(code),
        )
        if was_created:
            created += 1
            logger.info(f'[magazine] created CountryConfig row for {code}')
    return created


def get_magazine_source(code: str) -> ScraperSource:
    """
    The magazine's dedicated `ScraperSource` row, created on first use.

    `is_active=False` so run_all_scrapers never picks it up.
    """
    cfg = sources.get_source_config(code)
    country = _get_country(cfg.get('country_code', 'JO'))

    source, created = ScraperSource.objects.get_or_create(
        scraper_class=cfg['scraper_class'],
        defaults={
            'name':        cfg['name'],
            'name_ar':     cfg.get('name_ar', ''),
            'country':     country,
            'base_url':    cfg.get('base_url', ''),
            'is_active':   False,   # never picked up by run_all_scrapers
            'description': 'Internal source row for the News feature '
                           '(trade press and news sites). Managed by the '
                           'magazine_tenders app — not a scraper.',
        },
    )
    if created:
        logger.info(f'[magazine] created dedicated ScraperSource row ({code})')
    return source
