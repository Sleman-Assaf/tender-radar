# magazine_tenders/tests/test_freshness.py
"""
The rolling freshness window.

Three things are worth protecting, and the middle one is the reason this file
exists rather than a single arithmetic test.

The window is applied where every source passes, not only where the site can
filter — DevelopmentAid has no date field in its API at all, and it was 58 of
the 86 stale rows that had to be deleted by hand.

Undated items are NEVER dropped. Every undated News row is an Ashghal
prospected project: forthcoming work with no publication date by nature. A
guard that read "undated" as "old" would silently empty Future Projects, and
it would do it quietly, on a schedule, at 07:00.

And the window is off when set to 0, so a site that needs its whole archive
read once is one setting away.
"""
from datetime import date
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from tenders.models import Tender

from magazine_tenders import freshness, runner, sources
from magazine_tenders.models import MagazineRun
from magazine_tenders.tests.test_runner import (AEC_ENRICH, TEST_MAGAZINES,
                                                _FakeScraper, item)

TODAY = date(2026, 9, 10)


class WindowArithmeticTests(TestCase):
    @override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
    def test_ten_months_back(self):
        self.assertEqual(freshness.cutoff_date(TODAY), date(2025, 11, 10))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=1)
    def test_a_short_month_is_clamped_not_invalid(self):
        # 31 March minus one month is 28 February, not 31 February.
        self.assertEqual(freshness.cutoff_date(date(2026, 3, 31)),
                         date(2026, 2, 28))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=12)
    def test_a_leap_day_survives_the_clamp(self):
        self.assertEqual(freshness.cutoff_date(date(2025, 2, 28)),
                         date(2024, 2, 28))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=0)
    def test_zero_switches_the_window_off(self):
        self.assertIsNone(freshness.cutoff_date(TODAY))
        self.assertFalse(freshness.is_stale(date(2017, 1, 1), TODAY))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS='not-a-number')
    def test_a_bad_setting_switches_it_off_rather_than_crashing(self):
        # A typo in production.env must not stop the sweep.
        self.assertIsNone(freshness.cutoff_date(TODAY))
        self.assertFalse(freshness.is_stale(date(2017, 1, 1), TODAY))


@override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
class IsStaleTests(TestCase):
    def test_older_than_the_window_is_stale(self):
        self.assertTrue(freshness.is_stale(date(2025, 11, 9), TODAY))
        self.assertTrue(freshness.is_stale(date(2017, 11, 23), TODAY))

    def test_the_cutoff_day_itself_is_kept(self):
        self.assertFalse(freshness.is_stale(date(2025, 11, 10), TODAY))

    def test_recent_is_kept(self):
        self.assertFalse(freshness.is_stale(date(2026, 9, 2), TODAY))

    def test_undated_is_kept(self):
        # The Ashghal prospected list. See the module docstring.
        self.assertFalse(freshness.is_stale(None, TODAY))

    def test_a_non_date_is_kept(self):
        # A parsing failure upstream must not silently delete an item.
        self.assertFalse(freshness.is_stale('2017-01-01', TODAY))
        self.assertFalse(freshness.is_stale(0, TODAY))


@override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
class RunnerAppliesTheWindowTests(TestCase):
    """The backstop, for the sources whose site cannot filter by date."""

    def run_with(self, items):
        fake = _FakeScraper(items=items)
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True), \
             mock.patch('magazine_tenders.runner.get_magazine_scraper',
                        return_value=fake), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(AEC_ENRICH)):
            return runner.run_one('TESTMAG', trigger=MagazineRun.Trigger.MANUAL)

    def test_a_stale_item_is_never_saved(self):
        run = self.run_with([item(title='A 2017 procurement plan',
                                  publication_date=date(2017, 11, 23))])
        self.assertEqual(run.found, 1)
        self.assertEqual(run.saved_new, 0)
        self.assertEqual(run.skipped, 1)
        self.assertEqual(Tender.objects.count(), 0)

    def test_a_stale_item_is_not_even_classified(self):
        # Dropping it before save_tender_data is what saves the AI call.
        fake = _FakeScraper(items=[item(publication_date=date(2017, 1, 1))])
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True), \
             mock.patch('magazine_tenders.runner.get_magazine_scraper',
                        return_value=fake), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(AEC_ENRICH)) as m_enrich:
            runner.run_one('TESTMAG', trigger=MagazineRun.Trigger.MANUAL)
        m_enrich.assert_not_called()

    def test_a_fresh_item_is_saved(self):
        run = self.run_with([item(publication_date=date.today())])
        self.assertEqual(run.saved_new, 1)
        self.assertEqual(Tender.objects.count(), 1)

    def test_an_undated_item_is_saved(self):
        # The Ashghal case: no publication date, must still arrive.
        run = self.run_with([item(title='Supervision for 2 schools',
                                  publication_date=None)])
        self.assertEqual(run.saved_new, 1)
        self.assertEqual(Tender.objects.count(), 1)

    def test_a_mixed_batch_keeps_only_what_belongs(self):
        run = self.run_with([
            item(title='Fresh one',  publication_date=date.today()),
            item(title='Stale one',  publication_date=date(2019, 7, 26)),
            item(title='Undated one', publication_date=None),
        ])
        self.assertEqual(run.found, 3)
        self.assertEqual(run.saved_new, 2)
        self.assertEqual(run.skipped, 1)
        self.assertEqual(
            sorted(Tender.objects.values_list('title', flat=True)),
            ['Fresh one', 'Undated one'])

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=0)
    def test_the_window_off_saves_everything(self):
        run = self.run_with([item(publication_date=date(2017, 11, 23))])
        self.assertEqual(run.saved_new, 1)


class WordPressAsksForRecentPostsTests(TestCase):
    """Layer one: let the publisher filter, so the old posts never travel."""

    def _fetch(self):
        from magazine_tenders.scrapers._newsfeed import NewsFeedMagazineScraper

        class _Probe(NewsFeedMagazineScraper):
            CODE = 'PROBE'
            BASE_URL = 'https://example.test'
            FEED_MODE = 'wp_rest'
            WP_QUERIES = [{'categories': '511'}]

        probe = _Probe()
        with mock.patch.object(_Probe, '_client_get',
                               return_value='[]') as m_get:
            probe._fetch_wp()
        return m_get.call_args.kwargs.get('params')

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
    def test_the_cutoff_is_sent_as_after(self):
        params = self._fetch()
        cut = freshness.cutoff_date()
        self.assertEqual(params.get('after'), f'{cut.isoformat()}T00:00:00')
        # the source's own query is still there
        self.assertEqual(params.get('categories'), '511')

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=0)
    def test_no_after_when_the_window_is_off(self):
        self.assertNotIn('after', self._fetch())


@override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
class NewsPageUsesThePublicationDateTests(TestCase):
    """
    Layer three: the window applied to what is DISPLAYED.

    The save-time guard stops old items arriving; it cannot expire a row that
    grew old in place. Nothing else on the page can either — it filters on
    submission_deadline, and most news articles have none — so without this an
    item stored today is still presented as news two years later.
    """

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        from tenders.models import CountryConfig, ScraperSource
        from magazine_tenders.models import MagazineTenderMeta

        cls.admin = User.objects.create_superuser('boss2', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        cls.src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, pub):
            t = Tender.objects.create(
                source=cls.src, country=jo, external_id=f'ext-{title}',
                source_url='https://example.com/t', title=title,
                is_aec=True, is_aec_checked=True, is_relevant=True,
                status='OPEN', submission_deadline=None, publication_date=pub)
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
            return t

        # No deadlines anywhere — that is the real shape of news, and the
        # reason the existing deadline filter cannot expire any of these.
        cls.fresh = row('Fresh design contract', date.today())
        cls.aged  = row('Aged design contract', date(2019, 3, 4))
        cls.undated = row('Ashghal style undated project', None)

    def _titles(self, url):
        self.client.force_login(self.admin)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return {t.title for t in resp.context['tenders']}

    def test_an_aged_row_is_not_shown_as_news(self):
        titles = self._titles(reverse('magazine_tenders:list'))
        self.assertIn('Fresh design contract', titles)
        self.assertNotIn('Aged design contract', titles)

    def test_an_undated_row_is_still_shown(self):
        # Future Projects lives entirely on undated rows.
        self.assertIn('Ashghal style undated project',
                      self._titles(reverse('magazine_tenders:list')))

    def test_nothing_is_deleted(self):
        self.client.get(reverse('magazine_tenders:list'))
        self.assertEqual(Tender.objects.filter(pk=self.aged.pk).count(), 1)

    def test_the_audit_page_applies_the_window_too(self):
        # It used to be exempt, so an admin asking "why is this not on the
        # News page?" could always find the row. It follows the same window
        # now, by request: the two pages behave alike.
        #
        # The cost is stated here rather than left to be discovered - an
        # announcement older than the window is listed nowhere in the UI. It
        # is still in the database, still reachable by search, and widening
        # MAGAZINE_MAX_AGE_MONTHS brings it back.
        Tender.objects.filter(pk=self.aged.pk).update(is_aec=False)
        titles = self._titles(reverse('magazine_tenders:non_aec'))
        self.assertNotIn('Aged design contract', titles)

    def test_a_rejected_row_inside_the_window_is_still_audited(self):
        # The window hides the OLD, not the rejected. Anything recent enough
        # must still be reviewable, or the page stops doing its job.
        Tender.objects.filter(pk=self.fresh.pk).update(is_aec=False)
        self.assertIn('Fresh design contract',
                      self._titles(reverse('magazine_tenders:non_aec')))

    def test_the_window_off_restores_the_aged_row_to_the_audit(self):
        # The stated way back, asserted so it stays true.
        from django.test import override_settings
        Tender.objects.filter(pk=self.aged.pk).update(is_aec=False)
        with override_settings(MAGAZINE_MAX_AGE_MONTHS=0):
            self.assertIn('Aged design contract',
                          self._titles(reverse('magazine_tenders:non_aec')))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=0)
    def test_the_window_off_shows_the_aged_row_again(self):
        self.assertIn('Aged design contract',
                      self._titles(reverse('magazine_tenders:list')))


class IsStaleNeverRaisesTests(TestCase):
    """
    The promise in the docstring: this predicate never raises.

    It did. A datetime IS a date - it subclasses it - so the type guard let
    one through and the comparison below raised TypeError, which aborts the
    whole magazine's run inside runner.py rather than skipping one item.
    """

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
    def test_a_datetime_is_handled_like_its_date(self):
        from datetime import datetime

        from magazine_tenders import freshness
        self.assertTrue(freshness.is_stale(datetime(2017, 1, 1), TODAY))
        self.assertFalse(freshness.is_stale(datetime(2026, 9, 1), TODAY))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
    def test_rubbish_is_still_refused_quietly(self):
        from magazine_tenders import freshness
        for bad in ('2017-01-01', 0, [], object()):
            with self.subTest(bad=type(bad).__name__):
                self.assertFalse(freshness.is_stale(bad, TODAY))

    @override_settings(MAGAZINE_MAX_AGE_MONTHS=10)
    def test_the_cutoff_uses_the_project_clock(self):
        # Everything else on the page dates itself from django.utils.timezone.
        # date.today() is the machine's date, and the two disagree at the edge.
        from django.utils import timezone

        from magazine_tenders import freshness
        self.assertEqual(freshness.cutoff_date(),
                         freshness.subtract_months(timezone.localdate(), 10))
