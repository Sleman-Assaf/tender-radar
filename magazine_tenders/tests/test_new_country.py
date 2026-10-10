# magazine_tenders/tests/test_new_country.py
"""
What happens the first time an announcement arrives from a country the system
has never seen.

It must appear in the country filter by itself, under its own name, with its
flag, and inside whichever regions it belongs to - with nobody editing a list.

The menu is rebuilt per request from the page's own rows, so the "appears by
itself" half is free. The half that was broken is the NAME: a new country was
created with its ISO code as its name, which is how forty-four rows came to
read "CI", "NG" and "ZZ" on screen.
"""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from magazine_tenders import regions
from magazine_tenders.anchor import ensure_countries
from magazine_tenders.models import MagazineTenderMeta
from tenders.country_names import defaults_for, flag_for
from tenders.models import CountryConfig, ScraperSource, Tender


class ANewCountryIsNamedNotCodedTests(TestCase):
    """The row a scraper's first sighting creates."""

    def test_a_known_country_arrives_named_and_flagged(self):
        ensure_countries(['TN'])
        tn = CountryConfig.objects.get(code='TN')
        self.assertEqual(tn.name_en, 'Tunisia')
        self.assertEqual(tn.name_ar, 'تونس')
        self.assertEqual(tn.flag_emoji, '🇹🇳')

    def test_the_international_bucket_is_not_called_ZZ(self):
        # This is the one the reader actually saw: a card reading
        # "DevelopmentAid Forecast . ZZ".
        ensure_countries(['ZZ'])
        zz = CountryConfig.objects.get(code='ZZ')
        self.assertEqual(zz.name_en, 'International')
        self.assertNotEqual(zz.name_en, 'ZZ')

    def test_an_unknown_code_still_gets_a_flag(self):
        # Nothing better than the code to call it, but the flag is derivable
        # and the caller logs the gap rather than leaving it silent.
        ensure_countries(['XK'])
        xk = CountryConfig.objects.get(code='XK')
        self.assertEqual(xk.name_en, 'XK')
        self.assertEqual(xk.flag_emoji, '🇽🇰')

    def test_the_flag_is_derived_so_it_cannot_disagree(self):
        for code in ('JO', 'KE', 'PG', 'TR'):
            with self.subTest(code=code):
                self.assertEqual(len(flag_for(code)), 2)
        self.assertEqual(flag_for('ZZZ'), '')
        self.assertEqual(flag_for(''), '')

    def test_a_currency_the_project_cares_about_is_kept(self):
        # The shared table names countries; currencies stay a local concern.
        ensure_countries(['JO'])
        self.assertEqual(CountryConfig.objects.get(code='JO').currency, 'JOD')

    def test_an_existing_row_is_never_overwritten(self):
        CountryConfig.objects.create(code='KE', name_en='Kenya (custom)',
                                     name_ar='x', flag_emoji='', currency='')
        ensure_countries(['KE'])
        self.assertEqual(CountryConfig.objects.get(code='KE').name_en,
                         'Kenya (custom)')

    def test_every_name_in_the_table_is_usable(self):
        from tenders.country_names import NAMES
        for code, (en, ar) in NAMES.items():
            with self.subTest(code=code):
                self.assertEqual(len(code), 2)
                self.assertTrue(en.strip() and en != code)
                self.assertTrue(ar.strip())
                self.assertEqual(defaults_for(code)['flag_emoji'],
                                 flag_for(code))


class ANewCountryReachesTheFilterTests(TestCase):
    """The question asked: a Jordan story, and Jordan appears by itself."""

    @classmethod
    def setUpTestData(cls):
        cls.reader = User.objects.create_superuser('new-country',
                                                   password='pw12345!')
        ensure_countries(['QA'])
        cls.src = ScraperSource.objects.create(
            name='Test Magazine', is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG',
            country=CountryConfig.objects.get(code='QA'))
        cls._row('QA', 'A Qatar supervision contract')

    @classmethod
    def _row(cls, code, title):
        ensure_countries([code])
        t = Tender.objects.create(
            source=cls.src, country=CountryConfig.objects.get(code=code),
            external_id=f'nc-{code}', source_url='https://example.com/x',
            title=title, is_aec=True, is_aec_checked=True, is_relevant=True,
            status='OPEN', submission_deadline=None,
            publication_date=date.today(),
            description='[DESIGN] A consultancy was appointed.')
        MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
        return t

    def menu(self):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list'))
        self.assertEqual(resp.status_code, 200)
        return resp.context['country_options'], resp.context['region_options']

    def test_jordan_is_absent_until_a_jordan_story_arrives(self):
        countries, _ = self.menu()
        self.assertNotIn('JO', {c['code'] for c in countries})

    def test_one_jordan_story_puts_jordan_in_the_menu(self):
        self._row('JO', 'Design consultancy for the Amman ring road')
        countries, _ = self.menu()
        jordan = [c for c in countries if c['code'] == 'JO']
        self.assertEqual(len(jordan), 1)
        self.assertEqual(jordan[0]['name'], 'Jordan')
        self.assertEqual(jordan[0]['flag'], '🇯🇴')
        self.assertEqual(jordan[0]['count'], 1)

    def test_it_joins_the_regions_it_belongs_to(self):
        self._row('JO', 'Design consultancy for the Amman ring road')
        _, regions_ = self.menu()
        counts = {r['key']: r['count'] for r in regions_}
        self.assertEqual(counts['arab'], 2)          # Qatar + Jordan
        self.assertNotIn('africa', counts)           # Jordan is not African
        self.assertIn('JO', regions.ARAB)

    def test_a_country_in_a_region_nobody_has_seen_opens_that_region(self):
        # Africa is not offered at all until something African arrives.
        _, before = self.menu()
        self.assertNotIn('africa', {r['key'] for r in before})
        self._row('KE', 'Design consultancy services for a Nairobi road')
        _, after = self.menu()
        self.assertIn('africa', {r['key'] for r in after})

    def test_filtering_by_the_new_country_works_immediately(self):
        self._row('JO', 'Design consultancy for the Amman ring road')
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list')
                               + '?country=JO')
        self.assertEqual({t.title for t in resp.context['tenders']},
                         {'Design consultancy for the Amman ring road'})

    def test_it_leaves_the_menu_again_when_the_last_story_goes(self):
        t = self._row('JO', 'Design consultancy for the Amman ring road')
        self.assertIn('JO', {c['code'] for c in self.menu()[0]})
        t.delete()
        self.assertNotIn('JO', {c['code'] for c in self.menu()[0]})
