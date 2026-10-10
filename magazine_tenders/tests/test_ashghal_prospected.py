"""
Ashghal المشاريع المتوقعة — forthcoming projects, in the News section.

Offline throughout: the saved fixture is the real REST response (394 rows, 298
Published), so these tests never touch the network.

The two properties worth pinning are the ones a careless change would break
silently:

  * PAST YEARS ARE EXCLUDED. Ashghal leaves previous years in the list — 297 of
    the 394 saved rows are 2025 — so a scraper that forgot the year filter would
    fill the section with work already tendered and nothing would report it.
  * DRAFTS ARE EXCLUDED. The list is edited in place, and unpublished rows are
    visible to anonymous callers.
"""
import io
import json
import os
from datetime import date
from unittest import mock

from django.test import TestCase

from magazine_tenders.scrapers.ashghal_prospected import (
    AshghalProspectedScraper, published_on, strip_html)

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def load_rows():
    with io.open(os.path.join(FIXTURES, 'ashghal_prospected.json'),
                 encoding='utf-8') as fh:
        return json.load(fh)


class ProspectedFixtureTests(TestCase):
    """The fixture is the real payload; assert on its actual shape."""

    @classmethod
    def setUpTestData(cls):
        cls.rows = load_rows()

    def test_the_fixture_looks_like_the_real_list(self):
        self.assertGreater(len(self.rows), 300)
        statuses = {r.get('Status') for r in self.rows}
        self.assertIn('Published', statuses)
        self.assertIn('Draft', statuses, 'fixture should include drafts to '
                                         'prove they are filtered out')
        for key in ('Id', 'Title', 'User_x0020_Department', 'Quarter', 'Year'):
            self.assertIn(key, self.rows[0])


class ProspectedFilterTests(TestCase):
    """_fetch_items decides what is in scope. Exercised without the network."""

    def _fetch(self, min_year):
        s = AshghalProspectedScraper()
        payload = json.dumps({'value': load_rows()})
        with mock.patch.object(AshghalProspectedScraper, '_get',
                              return_value=payload):
            return s._fetch_items(min_year)

    def test_drafts_are_dropped(self):
        # The OData filter asks for Published only, but the fixture holds the
        # unfiltered list, so this proves the code does not rely on the server.
        kept = self._fetch(2000)
        self.assertTrue(kept)
        for row in kept:
            self.assertEqual(row['Status'], 'Published')

    def test_previous_years_are_dropped(self):
        kept = self._fetch(2026)
        self.assertTrue(kept, 'no 2026 rows kept')
        for row in kept:
            self.assertGreaterEqual(row['_year'], 2026)

    def test_a_future_cutoff_keeps_nothing(self):
        self.assertEqual(self._fetch(2999), [])

    def test_the_year_is_read_from_a_decimal(self):
        # SharePoint stores Year as 2026.00000000000; int() on that string
        # raises, which is why the code goes through float().
        raw = [r for r in load_rows() if r.get('Status') == 'Published'][0]
        self.assertRegex(str(raw['Year']), r'^\d{4}(\.\d+)?$')
        kept = self._fetch(2000)
        self.assertIsInstance(kept[0]['_year'], int)


class ProspectedMappingTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.published = [r for r in load_rows() if r.get('Status') == 'Published']

    def test_a_row_becomes_a_usable_item(self):
        s = AshghalProspectedScraper()
        row = dict(self.published[0], _year=2026)
        td = s.build_one(row)
        self.assertIsNotNone(td)
        self.assertTrue(td.title.strip())
        self.assertEqual(td.country_code, 'QA')
        self.assertEqual(td.status, 'OPEN')
        self.assertTrue(td.source_url.startswith(
            'https://www.ashghal.gov.qa/en/Tenders/Pages/ProspectedTenders.aspx'))
        self.assertTrue(td.external_id.startswith('mag-ashghalpp-'))

    def test_no_deadline_is_invented(self):
        # These are announced, not open for bids. A fabricated closing date
        # would make save_tender_data drop them as expired.
        s = AshghalProspectedScraper()
        for row in self.published[:20]:
            td = s.build_one(dict(row, _year=2026))
            if td:
                self.assertIsNone(td.submission_deadline)

    def test_the_department_becomes_the_entity(self):
        s = AshghalProspectedScraper()
        row = next(r for r in self.published if r.get('User_x0020_Department'))
        td = s.build_one(dict(row, _year=2026))
        self.assertIn('Ashghal', td.entity)
        self.assertIn(strip_html(row['User_x0020_Department'])[:20], td.entity)

    def test_the_quarter_is_linked_back_to_its_page(self):
        s = AshghalProspectedScraper()
        row = next(r for r in self.published
                   if (r.get('Quarter') or '').lower() == 'second quarter')
        td = s.build_one(dict(row, _year=2026))
        self.assertIn('Quarter=2', td.source_url)
        self.assertIn('Second Quarter', td.description)

    def test_html_in_a_title_is_stripped(self):
        s = AshghalProspectedScraper()
        td = s.build_one({'Id': 1, 'Title': '<div>Design &amp; Build depot<br/></div>',
                          'User_x0020_Department': 'Buildings Projects Department',
                          'Quarter': 'First Quarter', '_year': 2026})
        self.assertNotIn('<', td.title)
        self.assertIn('Design', td.title)

    def test_an_empty_title_is_skipped(self):
        s = AshghalProspectedScraper()
        self.assertIsNone(s.build_one({'Id': 2, 'Title': '<div></div>',
                                       '_year': 2026}))

    def test_every_published_row_maps_without_raising(self):
        s = AshghalProspectedScraper()
        built = [s.build_one(dict(r, _year=2026)) for r in self.published]
        self.assertGreater(sum(1 for b in built if b), 250)


class ProspectedNoSourceTests(TestCase):

    def test_scrape_refuses_without_a_source(self):
        # Same guard every other scraper here has: no ScraperSource means no
        # run, rather than an exception halfway through a sweep.
        s = AshghalProspectedScraper()
        self.assertEqual(s.scrape(), [])

    def test_it_is_registered_as_a_news_source(self):
        from magazine_tenders import registry, sources
        self.assertIn('ASHGHALPROSPECTED', sources.all_codes())
        self.assertTrue(registry.scraper_exists('ASHGHALPROSPECTED'))
        cfg = sources.MAGAZINES['ASHGHALPROSPECTED']
        self.assertEqual(cfg['country_code'], 'QA')
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_ASHGHALPROSPECTED')


class ProspectedArabicTitleTests(TestCase):
    """
    The scraper fills title_ar itself.

    It has to, and the reason is easy to miss: the shared classification
    service skips translation for anything it marks non-AEC (the
    `if not is_aec: return updates` guard in tenders/ajibrain_client.py). That
    saving is fine in general, but this is a government project pipeline read
    by Arabic-speaking staff and three quarters of its rows classify as
    non-AEC — 73 of 97 on the first import — so they would stay English-only
    for ever.

    Nothing here changes the classifier. It calls the same translation service
    the rest of the app uses.
    """

    def _items(self, titles):
        from tenders.scrapers.base import TenderData
        return [TenderData(title=t, title_ar='', source_url='https://e.com',
                           external_id=f'x{i}', country_code='QA')
                for i, t in enumerate(titles)]

    def test_arabic_is_added(self):
        s = AshghalProspectedScraper()
        items = self._items(['The Design for the Park Projects package (1)'])
        with mock.patch('tenders.ajibrain_client.translate_title',
                        return_value='تصميم حزمة مشاريع الحدائق (1)'):
            s._add_arabic_titles(items)
        self.assertEqual(items[0].title_ar, 'تصميم حزمة مشاريع الحدائق (1)')

    def test_an_untranslated_echo_is_rejected(self):
        # The service returns the INPUT unchanged when it cannot translate.
        # Storing that would put an English string in the Arabic field, which
        # then renders twice on the card and looks like a data bug.
        s = AshghalProspectedScraper()
        items = self._items(['Design of a school'])
        with mock.patch('tenders.ajibrain_client.translate_title',
                        return_value='Design of a school'):
            s._add_arabic_titles(items)
        self.assertEqual(items[0].title_ar, '')

    def test_a_non_arabic_reply_is_rejected(self):
        s = AshghalProspectedScraper()
        items = self._items(['Design of a school'])
        with mock.patch('tenders.ajibrain_client.translate_title',
                        return_value='Conception dune ecole'):
            s._add_arabic_titles(items)
        self.assertEqual(items[0].title_ar, '')

    def test_a_service_failure_is_not_fatal(self):
        # The row must still be stored with its English title.
        s = AshghalProspectedScraper()
        items = self._items(['Design of a school'])
        with mock.patch('tenders.ajibrain_client.translate_title',
                        side_effect=RuntimeError('LLM unavailable')):
            s._add_arabic_titles(items)
        self.assertEqual(items[0].title_ar, '')
        self.assertEqual(items[0].title, 'Design of a school')

    def test_existing_arabic_is_not_overwritten(self):
        s = AshghalProspectedScraper()
        items = self._items(['Design of a school'])
        items[0].title_ar = 'تصميم مدرسة'
        with mock.patch('tenders.ajibrain_client.translate_title',
                        return_value='شيء آخر') as m:
            s._add_arabic_titles(items)
        self.assertEqual(items[0].title_ar, 'تصميم مدرسة')
        m.assert_not_called()


class PublicationDateTests(TestCase):
    """
    Every row must carry the date the site published it.

    These entries used to be stored with publication_date = None, which made
    them invisible to anything that reasons about dates — the dashboard's
    "published today" bar, and the freshness window. The list has always
    carried the date; the scraper simply was not asking for it.
    """

    def test_created_is_preferred(self):
        self.assertEqual(
            published_on({'Created': '2026-07-07T09:14:52Z',
                          'Modified': '2026-08-01T00:00:00Z'}),
            date(2026, 7, 7))

    def test_modified_is_the_fallback(self):
        # An older $select, or a stored fixture, returns no Created. A date
        # from Modified beats no date at all.
        self.assertEqual(published_on({'Modified': '2026-02-24T10:03:18Z'}),
                         date(2026, 2, 24))

    def test_nothing_parsable_gives_none_not_a_guess(self):
        # publication_date drives the CEO's daily bar; an invented date would
        # announce a project on a day nothing was published.
        self.assertIsNone(published_on({}))
        self.assertIsNone(published_on({'Created': '', 'Modified': None}))
        self.assertIsNone(published_on({'Created': 'not-a-date'}))

    def test_a_built_item_carries_the_date(self):
        s = AshghalProspectedScraper()
        row = {'Id': 42, 'Title': 'Supervision for a new school',
               'User_x0020_Department': 'Buildings Projects Department',
               'Status': 'Published', 'Quarter': 'First Quarter',
               'Year': '2026', '_year': 2026,
               'Created': '2026-01-11T08:00:00Z',
               'Modified': '2026-01-11T08:00:00Z'}
        td = s.build_one(row)
        self.assertEqual(td.publication_date, date(2026, 1, 11))

    def test_the_query_asks_the_site_for_it(self):
        # The date can only be preferred if it is requested.
        import inspect
        src = inspect.getsource(AshghalProspectedScraper._fetch_items)
        self.assertIn('Created', src)
