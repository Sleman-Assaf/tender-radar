# magazine_tenders/tests/test_country_tabs.py
"""
The country tabs, and the promise they make.

Thirteen sources across four countries made the source filter dishonest:
it offered every source whatever country you were looking at, so picking
Jordan and then Construction Week returned nothing — the country filter had
already removed every row that option could match.

The tabs fix that by narrowing BOTH the rows and the source options, and
these tests exist because the two halves are in different places (a
queryset and a context entry) and nothing else would notice them drifting
apart.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from magazine_tenders.sources import MAGAZINES
from magazine_tenders.views import MagazineTenderListView


class SourceOptionsFollowTheCountryTests(TestCase):
    """The filter may only offer sources that country can actually produce."""

    def setUp(self):
        self.view = MagazineTenderListView()

    def test_no_country_offers_every_source(self):
        self.assertEqual(len(self.view._magazines_for('')), len(MAGAZINES))

    def test_jordan_offers_only_jordanian_sources(self):
        codes = {c for c, _ in self.view._magazines_for('JO')}
        self.assertTrue(codes)
        for code in codes:
            self.assertEqual(MAGAZINES[code]['country_code'], 'JO', code)

    def test_jordan_does_not_offer_the_gulf_trade_press(self):
        codes = {c for c, _ in self.view._magazines_for('JO')}
        for gulf in ('CONSTRUCTIONWEEK', 'ZAWYA', 'ASHGHALPROSPECTED'):
            self.assertNotIn(gulf, codes)

    def test_every_registered_country_offers_at_least_one_source(self):
        countries = {cfg['country_code'] for cfg in MAGAZINES.values()
                     if cfg.get('country_code')}
        for country in countries:
            with self.subTest(country=country):
                self.assertTrue(self.view._magazines_for(country),
                                f'{country} has a tab but no sources')

    def test_an_unknown_country_offers_nothing_rather_than_everything(self):
        # A typo must narrow to nothing, not silently show the whole list —
        # the same choice get_queryset makes for an unrecognised country.
        self.assertEqual(self.view._magazines_for('XX'), [])


class TheNewsPageRendersTests(TestCase):
    """
    Every tab renders. This is the test that would have caught the
    multi-line {# #} comment, which Django prints into the page as text
    rather than treating as a comment.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='qa-reader', password='x')

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_news_page_renders(self):
        response = self.client.get(reverse('magazine_tenders:list'))
        self.assertEqual(response.status_code, 200)

    def test_each_country_tab_renders(self):
        countries = {cfg['country_code'] for cfg in MAGAZINES.values()
                     if cfg.get('country_code')}
        for country in sorted(countries):
            with self.subTest(country=country):
                response = self.client.get(
                    reverse('magazine_tenders:list'), {'country': country})
                self.assertEqual(response.status_code, 200)

    def test_the_tabs_are_in_the_context_with_counts(self):
        response = self.client.get(reverse('magazine_tenders:list'))
        tabs = response.context['country_tabs']
        self.assertGreaterEqual(len(tabs), 2)
        self.assertEqual(tabs[0]['code'], '', 'the first tab is All')
        for tab in tabs:
            self.assertIn('count', tab)
            self.assertIsInstance(tab['count'], int)

    def test_exactly_one_tab_is_active(self):
        response = self.client.get(
            reverse('magazine_tenders:list'), {'country': 'JO'})
        active = [t for t in response.context['country_tabs'] if t['active']]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['code'], 'JO')

    def test_the_comment_block_is_not_printed_into_the_page(self):
        # A multi-line {# #} would appear here as visible text.
        response = self.client.get(reverse('magazine_tenders:list'))
        body = response.content.decode('utf-8', 'replace')
        self.assertNotIn('Country tabs. Above the view tabs', body)

    def test_a_country_with_no_rows_still_renders(self):
        response = self.client.get(
            reverse('magazine_tenders:list'), {'country': 'XX'})
        self.assertEqual(response.status_code, 200)

    def test_the_tab_count_matches_what_the_tab_shows(self):
        # The count is produced by re-running get_queryset with the parameter
        # overridden. If it ever stops agreeing with the page it promises a
        # number the click does not deliver.
        response = self.client.get(reverse('magazine_tenders:list'))
        for tab in response.context['country_tabs']:
            with self.subTest(country=tab['code'] or 'all'):
                params = {'country': tab['code']} if tab['code'] else {}
                listed = self.client.get(reverse('magazine_tenders:list'), params)
                self.assertEqual(tab['count'],
                                 listed.context['paginator'].count)
