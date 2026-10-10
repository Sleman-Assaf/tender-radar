# magazine_tenders/regions.py
"""
Groups of countries a reader can filter the news by.

The database has no notion of a region - `CountryConfig` is a flat list of
codes - so the grouping lives here, in one place both lists read.

AFRICA IS NOT DEFINED HERE. `tenders/aec_scope.py` already names the 54
African countries, because the classification rule is different there: AJi
takes design and desk consultancy in Africa but not supervision, having no
presence on the ground. That list is overridable through the
`AEC_AFRICA_COUNTRIES` setting, so an office in Cairo changes what counts
without a code change. A second copy here would be a second answer to "is
Egypt in Africa", and the two would drift the first time somebody edited one.
This module asks that one.

Two groups, and they OVERLAP on purpose. Egypt, Algeria, Morocco, Tunisia,
Libya, Sudan, Somalia, Djibouti, Mauritania and the Comoros are Arab AND
African, and a reader who picks "Africa" means the continent. Making the
groups exclusive would have hidden Egypt from Africa, the more surprising of
the two answers. So an Egyptian announcement is in both.

Membership is by ISO 3166-1 alpha-2 code. `ZZ` - the international bucket for
a multi-country notice - is in no region by design: a regional World Bank
programme is not "in" Africa in the sense a reader filtering by it means.
"""
from typing import Iterable, Optional

from tenders.aec_scope import africa_codes

#: The 22 members of the Arab League. Defined here because nothing else in
#: the project needed the grouping before.
ARAB = frozenset({
    'DZ', 'BH', 'KM', 'DJ', 'EG', 'IQ', 'JO', 'KW', 'LB', 'LY', 'MR', 'MA',
    'OM', 'PS', 'QA', 'SA', 'SO', 'SD', 'SY', 'TN', 'AE', 'YE',
})


def africa() -> frozenset:
    """
    The continent, from the one list the project already keeps.

    Read at call time, not import time, so the `AEC_AFRICA_COUNTRIES`
    override reaches this menu as well as the classification rule.
    """
    return africa_codes()


def arab_african() -> frozenset:
    """The countries in both groups - the overlap, named so it is visible."""
    return ARAB & africa()


def all_regions() -> tuple:
    """
    (key, label, codes) in the order the menu shows them.

    A function rather than a constant because Africa can be overridden by a
    setting, and a constant would freeze whatever it was at import.
    """
    return (
        ('arab', 'Arab countries', ARAB),
        ('africa', 'Africa', africa()),
    )


def _by_key() -> dict:
    return {key: (label, codes) for key, label, codes in all_regions()}


def is_region(value: str) -> bool:
    """
    True when `value` names a region rather than a country.

    Region keys are words and country codes are two letters, so the two
    cannot collide - which is why the filter takes both in one parameter
    instead of two.
    """
    return (value or '').strip().lower() in _by_key()


def codes_for(value: str) -> Optional[frozenset]:
    """The codes in a region, or None when `value` is not one."""
    entry = _by_key().get((value or '').strip().lower())
    return entry[1] if entry else None


def label_for(value: str) -> str:
    """The region's display name, or '' when `value` is not one."""
    entry = _by_key().get((value or '').strip().lower())
    return entry[0] if entry else ''


def regions_present(country_codes: Iterable[str]) -> list:
    """
    (key, label, count) for each region holding at least one of `country_codes`.

    A region with nothing behind it is left out of the menu for the same
    reason an empty country is: every option a reader can pick should lead
    somewhere. The count here is of COUNTRIES; the view reports rows instead,
    which is the number a reader wants beside the name.
    """
    present = {c for c in country_codes if c}
    return [(key, label, len(present & codes))
            for key, label, codes in all_regions()
            if present & codes]
