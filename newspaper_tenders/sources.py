# newspaper_tenders/sources.py
# Registry of the newspapers the feature reads. Adding a newspaper means:
# one discovery module + one entry here — the pipeline, models, scheduler,
# dashboard and AI service are all source-agnostic.

from django.conf import settings


NEWSPAPERS = {
    'ALRAI': {
        'code':          'ALRAI',
        'name':          'Al-Rai Newspaper — عطاءات الجريدة',
        'name_ar':       'صحيفة الرأي — عطاءات الجريدة',
        'display':       'الرأي',
        'display_en':    'Al-Rai',
        'scraper_class': 'NEWSPAPER_ALRAI',
        'base_url':      'https://alrai.com',
        'id_prefix':     'alrai',
        # Al-Rai: one article per day whose body embeds one PDF per ads page.
        'discover':      'newspaper_tenders.alrai.discover',
        # Each PDF is already a single scanned ads page — no splitting.
        'split_pages':   False,
        # Al-Rai prints the section title in every page's banner; pages headed
        # "إعلانات قضائية" (court notices) are skipped before the AI — see
        # page_gate.py. Al-Ghad / Ad-Dustour select pages by text layer instead.
        'page_header_gate': True,
    },
    'ALGHAD': {
        'code':          'ALGHAD',
        'name':          'Al-Ghad Newspaper — جريدة الغد',
        'name_ar':       'جريدة الغد — عطاءات الجريدة',
        'display':       'الغد',
        'display_en':    'Al-Ghad',
        'scraper_class': 'NEWSPAPER_ALGHAD',
        'base_url':      'https://legacy.alghad.com',
        'id_prefix':     'alghad',
        # Al-Ghad: one full-newspaper PDF per day (24 pages, WITH text layer).
        'discover':      'newspaper_tenders.alghad.discover',
        # The daily PDF is split locally: only pages whose text contains
        # tender markers are sent to the AI — same per-page flow as Al-Rai.
        'split_pages':   True,
    },
    'DUSTOUR': {
        'code':          'DUSTOUR',
        'name':          'Ad-Dustour Newspaper — جريدة الدستور',
        'name_ar':       'جريدة الدستور — عطاءات الجريدة',
        'display':       'الدستور',
        'display_en':    'Ad-Dustour',
        'scraper_class': 'NEWSPAPER_DUSTOUR',
        'base_url':      'https://www.addustour.com',
        'id_prefix':     'dustour',
        # Ad-Dustour: flip-book viewer, one full-newspaper PDF per day
        # (~29 pages, WITH text layer) — same splitting flow as Al-Ghad.
        'discover':      'newspaper_tenders.dustour.discover',
        'split_pages':   True,
    },
}


def enabled_sources() -> list[dict]:
    """Newspapers to process, honouring per-source enable flags."""
    out = []
    for code, cfg in NEWSPAPERS.items():
        flag = f'NEWSPAPER_{code}_ENABLED'
        if getattr(settings, flag, True):
            out.append(cfg)
    return out


def get_source_config(code: str) -> dict:
    return NEWSPAPERS[code]


def discover_for(cfg: dict, target):
    """Resolve and call the source's discovery function."""
    from importlib import import_module
    module_path, func_name = cfg['discover'].rsplit('.', 1)
    return getattr(import_module(module_path), func_name)(target)
