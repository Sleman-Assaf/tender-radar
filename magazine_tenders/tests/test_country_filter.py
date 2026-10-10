# magazine_tenders/tests/test_country_filter.py
"""
The country and region filter on News, Future Projects and the audit page.

The decision that shapes all of it: Africa is GEOGRAPHIC, so the six Arab
African countries are in both groups and the two overlap. Picking Africa and
getting no Egypt would be the more surprising answer.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from magazine_tenders import regions
from magazine_tenders.models import MagazineTenderMeta
from tenders.models import CountryConfig, ScraperSource, Tender


class RegionMembershipTests(TestCase):
    """The groups themselves, before any query touches them."""

    def test_africa_is_the_projects_one_list_not_a_second_copy(self):
        # tenders/aec_scope.py already names the 54 African countries, and it
        # is overridable by setting. A copy here would be a second answer to
        # "is Egypt in Africa" and the two would drift.
        from tenders.aec_scope import africa_codes
        self.assertEqual(regions.africa(), africa_codes())

    def test_an_override_reaches_the_menu_as_well_as_the_classifier(self):
        from django.test import override_settings
        with override_settings(AEC_AFRICA_COUNTRIES=['KE', 'NG']):
            self.assertEqual(regions.africa(), {'KE', 'NG'})
            self.assertEqual(regions.codes_for('africa'), {'KE', 'NG'})

    def test_the_arab_african_countries_are_in_both(self):
        for code in ('EG', 'DZ', 'SD', 'SO', 'DJ', 'MR'):
            with self.subTest(code=code):
                self.assertIn(code, regions.africa())
                self.assertIn(code, regions.ARAB)
        self.assertEqual(regions.arab_african(),
                         {'EG', 'DZ', 'SD', 'SO', 'DJ', 'MR', 'KM', 'LY',
                          'MA', 'TN'})

    def test_a_gulf_country_is_arab_and_not_african(self):
        for code in ('QA', 'AE', 'SA', 'KW', 'BH', 'OM'):
            with self.subTest(code=code):
                self.assertIn(code, regions.ARAB)
                self.assertNotIn(code, regions.africa())

    def test_a_sub_saharan_country_is_african_and_not_arab(self):
        for code in ('KE', 'NG', 'ZA', 'GH', 'TZ', 'CI'):
            with self.subTest(code=code):
                self.assertIn(code, regions.africa())
                self.assertNotIn(code, regions.ARAB)

    def test_the_international_bucket_belongs_to_neither(self):
        # A multi-country programme is not "in" Africa the way a reader
        # filtering by Africa means it.
        self.assertNotIn('ZZ', regions.africa())
        self.assertNotIn('ZZ', regions.ARAB)

    def test_a_region_key_and_a_country_code_cannot_collide(self):
        # Why one parameter carries both: keys are words, codes are two
        # letters.
        for key, _label, _codes in regions.all_regions():
            with self.subTest(key=key):
                self.assertTrue(regions.is_region(key))
                self.assertNotEqual(len(key), 2)
        self.assertFalse(regions.is_region('QA'))
        self.assertFalse(regions.is_region(''))

    def test_lookups_are_case_insensitive(self):
        self.assertEqual(regions.codes_for('AFRICA'), regions.africa())
        self.assertEqual(regions.label_for('Arab'), 'Arab countries')
        self.assertIsNone(regions.codes_for('atlantis'))

    def test_an_empty_region_is_left_out_of_the_menu(self):
        # Every option a reader can pick must lead somewhere.
        self.assertEqual(regions.regions_present(['QA', 'AE']),
                         [('arab', 'Arab countries', 2)])
        self.assertEqual(regions.regions_present([]), [])
        both = dict((k, n) for k, _l, n in regions.regions_present(['EG']))
        self.assertEqual(both, {'arab': 1, 'africa': 1})


class _Rows(TestCase):
    """Shared data: a few announcements spread across the two groups."""

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        cls.reader = User.objects.create_superuser('country-filter',
                                                   password='pw12345!')
        cls.src = ScraperSource.objects.create(
            name='Test Magazine', is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG',
            country=cls._country('JO', 'Jordan'))

        cls.rows = {}
        for code, name, title, aec in (
            ('QA', 'Qatar', 'A Qatar supervision contract', True),
            ('AE', 'UAE', 'A Dubai design commission', True),
            ('EG', 'Egypt', 'Design of a new bridge in Cairo', True),
            ('KE', 'Kenya', 'Design consultancy services for a Nairobi road',
             True),
            ('ZZ', 'International', 'A regional programme', True),
            ('NG', 'Nigeria', 'A Nigeria story the classifier rejected', False),
        ):
            t = Tender.objects.create(
                source=cls.src, country=cls._country(code, name),
                external_id=f'cf-{code}', source_url='https://example.com/x',
                title=title, is_aec=aec, is_aec_checked=True, is_relevant=True,
                status='OPEN', submission_deadline=None,
                publication_date=_date.today())
            t.description = '[DESIGN] A consultancy was appointed.'
            t.save(update_fields=['description'])
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
            cls.rows[code] = t

    @staticmethod
    def _country(code, name):
        cfg, _ = CountryConfig.objects.get_or_create(
            code=code, defaults={'name_en': name, 'name_ar': name,
                                 'flag_emoji': '', 'currency': 'USD'})
        return cfg

    def titles(self, url):
        self.client.force_login(self.reader)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return {t.title for t in resp.context['tenders']}


class FilteringByRegionTests(_Rows):
    def url(self, value):
        return reverse('magazine_tenders:list') + f'?country={value}'

    def test_arab_countries(self):
        titles = self.titles(self.url('arab'))
        self.assertEqual(titles, {'A Qatar supervision contract',
                                  'A Dubai design commission',
                                  'Design of a new bridge in Cairo'})

    def test_africa_includes_egypt(self):
        # The decision this whole feature turns on.
        titles = self.titles(self.url('africa'))
        self.assertEqual(titles, {'Design of a new bridge in Cairo',
                                  'Design consultancy services for a Nairobi road'})

    def test_the_groups_overlap_rather_than_partition(self):
        self.assertIn('Design of a new bridge in Cairo', self.titles(self.url('arab')))
        self.assertIn('Design of a new bridge in Cairo', self.titles(self.url('africa')))

    def test_the_international_bucket_is_in_no_region(self):
        for value in ('arab', 'africa'):
            with self.subTest(value=value):
                self.assertNotIn('A regional programme',
                                 self.titles(self.url(value)))

    def test_one_country_on_its_own(self):
        self.assertEqual(self.titles(self.url('QA')),
                         {'A Qatar supervision contract'})

    def test_a_lowercase_country_code_still_works(self):
        self.assertEqual(self.titles(self.url('qa')),
                         {'A Qatar supervision contract'})

    def test_an_unknown_value_narrows_to_nothing_rather_than_everything(self):
        # A typo must not silently show the unfiltered list.
        self.assertEqual(self.titles(self.url('atlantis')), set())

    def test_no_filter_shows_everything(self):
        self.assertEqual(len(self.titles(reverse('magazine_tenders:list'))), 5)


class TheFilterWorksOnEveryListTests(_Rows):
    def test_on_the_news_tab(self):
        titles = self.titles(reverse('magazine_tenders:list')
                             + '?view=news&country=arab')
        self.assertIn('A Qatar supervision contract', titles)
        self.assertNotIn('Design consultancy services for a Nairobi road', titles)

    def test_on_the_audit_page(self):
        # The rejected Nigeria row lives there, and Nigeria is African.
        titles = self.titles(reverse('magazine_tenders:non_aec')
                             + '?country=africa')
        self.assertEqual(titles, {'A Nigeria story the classifier rejected'})
        self.assertEqual(self.titles(reverse('magazine_tenders:non_aec')
                                     + '?country=arab'), set())

    def test_it_combines_with_the_other_filters(self):
        titles = self.titles(reverse('magazine_tenders:list')
                             + '?country=arab&q=supervision')
        self.assertEqual(titles, {'A Qatar supervision contract'})


class TheMenuTests(_Rows):
    def context(self, url=None):
        self.client.force_login(self.reader)
        resp = self.client.get(url or reverse('magazine_tenders:list'))
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_only_countries_that_have_something_are_listed(self):
        offered = {c['code'] for c in self.context().context['country_options']}
        self.assertEqual(offered, {'QA', 'AE', 'EG', 'KE', 'ZZ', 'NG'}
                         - {'NG'})   # NG is Non-AEC, so not on this list

    def test_each_country_appears_once(self):
        # The page queryset carries sort annotations - has_photo,
        # unread_first, is_new - and Django puts every one into the GROUP BY
        # of a values().annotate(). Counting on it listed Egypt three times
        # and offered 148 countries where the data held 28.
        codes = [c['code'] for c in self.context().context['country_options']]
        self.assertEqual(len(codes), len(set(codes)), codes)

    def test_the_counts_add_up_to_the_page(self):
        resp = self.context()
        total = sum(c['count'] for c in resp.context['country_options'])
        self.assertEqual(total, resp.context['paginator'].count)

    def test_each_country_carries_its_count(self):
        rows = {c['code']: c['count']
                for c in self.context().context['country_options']}
        self.assertEqual(rows['QA'], 1)
        self.assertEqual(rows['EG'], 1)

    def test_regions_are_offered_first_and_only_when_populated(self):
        opts = self.context().context['region_options']
        self.assertEqual([o['key'] for o in opts], ['arab', 'africa'])
        counts = {o['key']: o['count'] for o in opts}
        self.assertEqual(counts['arab'], 3)     # QA, AE, EG
        self.assertEqual(counts['africa'], 2)   # EG, KE

    def test_the_menu_reflects_the_page_it_is_on(self):
        # The audit page holds only the Nigeria row, so Arab must not be
        # offered there - it would lead to an empty list.
        opts = self.context(reverse('magazine_tenders:non_aec')
                            ).context['region_options']
        self.assertEqual([o['key'] for o in opts], ['africa'])

    def test_the_choice_is_echoed_back_so_the_control_stays_set(self):
        ctx = self.context(reverse('magazine_tenders:list')
                           + '?country=africa').context
        self.assertEqual(ctx['active_filters']['country'], 'africa')
        self.assertEqual(ctx['country_filter_label'], 'Africa')

    def test_a_country_choice_is_echoed_with_its_name(self):
        ctx = self.context(reverse('magazine_tenders:list')
                           + '?country=QA').context
        self.assertEqual(ctx['country_filter_label'], 'Qatar')
