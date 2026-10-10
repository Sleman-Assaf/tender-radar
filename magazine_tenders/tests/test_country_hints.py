# magazine_tenders/tests/test_country_hints.py
"""
Which country a news story is filed under.

The trade feeds are regional: one Construction Week article can be about
Qatar and the next about Kenya, and neither says so in a field. The country
is read out of the TEXT, and anything unrecognised falls back to the
publisher's own country - so a missing entry does not merely lose the
country, it asserts the wrong one. A Cameroon notice filed as UAE also gets
the permissive Middle East classification scope, which is the bug that first
put Tunisia on this list.

Coverage is asserted over the whole of the Arab League and the whole of
Africa rather than a sample, because a list like this is only ever wrong by
omission and a sample would not notice.
"""
from django.test import TestCase

from magazine_tenders import regions
from magazine_tenders.scrapers._newsfeed import NewsFeedMagazineScraper
from tenders.country_names import NAMES


class _Resolver(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        class _Probe(NewsFeedMagazineScraper):
            CODE = 'PROBE'
            BASE_URL = 'https://example.test'
            DEFAULT_COUNTRY = 'AE'

        cls.probe = _Probe()

    def country_of(self, text):
        return self.probe._country(text)


class EveryArabAndAfricanCountryIsRecognisedTests(_Resolver):
    """The coverage test. Named by its own English name, each must resolve."""

    def test_every_arab_league_member(self):
        for code in sorted(regions.ARAB):
            with self.subTest(code=code, name=NAMES[code][0]):
                self.assertEqual(
                    self.country_of(f'A new hospital is planned in '
                                    f'{NAMES[code][0]} this year'),
                    code)

    def test_every_african_country(self):
        for code in sorted(regions.africa()):
            with self.subTest(code=code, name=NAMES[code][0]):
                self.assertEqual(
                    self.country_of(f'Consultants sought for a road in '
                                    f'{NAMES[code][0]}'),
                    code)

    def test_the_name_table_covers_both_groups(self):
        # The test above indexes NAMES, so a gap there would skip silently.
        missing = (regions.ARAB | regions.africa()) - set(NAMES)
        self.assertEqual(missing, set())


class NamesThatContainOtherCountriesTests(_Resolver):
    """
    The pairs a plain word match gets wrong, each one a real place.

    Order in the hint list is what settles these, so they are pinned.
    """

    def test_the_guineas(self):
        self.assertEqual(self.country_of('a bridge in Guinea'), 'GN')
        self.assertEqual(self.country_of('a bridge in Guinea-Bissau'), 'GW')
        self.assertEqual(self.country_of('a bridge in Equatorial Guinea'), 'GQ')
        self.assertEqual(self.country_of('a bridge in Papua New Guinea'), 'PG')

    def test_the_sudans(self):
        self.assertEqual(self.country_of('a road in Sudan'), 'SD')
        self.assertEqual(self.country_of('a road in South Sudan'), 'SS')

    def test_the_congos(self):
        self.assertEqual(self.country_of('a dam in Congo'), 'CG')
        self.assertEqual(self.country_of('a dam in DR Congo'), 'CD')
        self.assertEqual(self.country_of('a dam in the DRC'), 'CD')

    def test_niger_is_not_nigeria(self):
        self.assertEqual(self.country_of('a school in Niger'), 'NE')
        self.assertEqual(self.country_of('a school in Nigeria'), 'NG')

    def test_benin_city_is_in_nigeria(self):
        # A real trap: Benin City is a Nigerian city, not the country Benin.
        self.assertEqual(self.country_of('a clinic in Benin City'), 'NG')
        self.assertEqual(self.country_of('a clinic in Benin'), 'BJ')

    def test_mali_is_not_somalia(self):
        self.assertEqual(self.country_of('a well in Mali'), 'ML')
        self.assertEqual(self.country_of('a well in Somalia'), 'SO')


class CitiesAreRecognisedTooTests(_Resolver):
    """Headlines often name the city and never the country."""

    def test_capitals_and_major_cities(self):
        for text, code in (('a tower in Amman', 'JO'),
                           ('a metro in Cairo', 'EG'),
                           ('a port in Mombasa', 'KE'),
                           ('a stadium in Doha', 'QA'),
                           ('a rail link in Kampala', 'UG'),
                           ('a hospital in Yaounde', 'CM'),
                           ('a bridge in Ouagadougou', 'BF'),
                           ('a road in Antananarivo', 'MG')):
            with self.subTest(text=text):
                self.assertEqual(self.country_of(text), code)


class TheFallbackTests(_Resolver):
    def test_text_naming_nowhere_falls_back_to_the_publisher(self):
        self.assertEqual(self.country_of('Cement prices rise across the region'),
                         'AE')

    def test_empty_text_falls_back_rather_than_failing(self):
        self.assertEqual(self.country_of(''), 'AE')
        self.assertEqual(self.country_of(None), 'AE')
