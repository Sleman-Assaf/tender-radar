# magazine_tenders/tests/test_runner.py
"""
Runner behaviour, with the classification service mocked.

The mock is the point of these tests: they assert that the runner routes every
item through `save_tender_data` (and therefore through the shared classifier)
and that `is_aec` comes back from that service — never from anything in this
app. A test that set `is_aec` itself would be asserting the wrong thing.
"""
from datetime import date, timedelta
from unittest import mock

from django.test import TestCase, override_settings

from tenders.models import Tender
from tenders.scrapers.base import TenderData

from magazine_tenders import runner, sources
from magazine_tenders.models import MagazineRun, MagazineTenderMeta

FUTURE = date.today() + timedelta(days=30)
PAST   = date.today() - timedelta(days=5)

# What the mocked classification service returns. Same dict shape as the real
# tenders.ajibrain_client.classify_and_translate_tender.
AEC_ENRICH     = {'is_aec_checked': True, 'is_aec': True,  'category': 'INFRA'}
NON_AEC_ENRICH = {'is_aec_checked': True, 'is_aec': False, 'category': 'OTHER'}


class _FakeScraper:
    """Stands in for a magazine scraper: returns fixed items, saves nothing."""

    def __init__(self, items=None, raises=None):
        self._items = items or []
        self._raises = raises
        self.source = None

    def scrape(self):
        if self._raises:
            raise self._raises
        return list(self._items)


def item(title='Design and supervision of a water network', **over):
    base = dict(
        title=title,
        source_url='https://example-magazine.com/tenders/1001',
        entity='Water Authority',
        reference_no='MAG-1/2026',
        description='design and supervision',
        submission_deadline=FUTURE,
        country_code='JO',
    )
    base.update(over)
    return TenderData(**base)


TEST_MAGAZINES = {
    'TESTMAG': {
        'code':          'TESTMAG',
        'name':          'Test Magazine — Tender Announcements',
        'name_ar':       'مجلة الاختبار',
        'display':       'الاختبار',
        'display_en':    'Test Magazine',
        'scraper_class': 'MAGAZINE_TESTMAG',
        'base_url':      'https://example-magazine.com',
        'country_code':  'JO',
        'id_prefix':     'mag-testmag',
        'scraper':       'magazine_tenders.tests.test_runner._FakeScraper',
        'listing_urls':  ['https://example-magazine.com/tenders'],
    },
}


class RunnerTests(TestCase):
    def run_with(self, items=None, raises=None, enrich=None, trigger=None):
        """Run TESTMAG with a fake scraper and a mocked classification service."""
        fake = _FakeScraper(items=items, raises=raises)
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True), \
             mock.patch('magazine_tenders.runner.get_magazine_scraper',
                        return_value=fake), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(enrich or AEC_ENRICH)) as m_enrich:
            run = runner.run_one('TESTMAG',
                                 trigger=trigger or MagazineRun.Trigger.MANUAL)
        self.last_enrich = m_enrich
        return run

    # ── happy path ───────────────────────────────────────────────────────────
    def test_saves_items_and_records_provenance(self):
        run = self.run_with(items=[item()])

        self.assertEqual(run.status, MagazineRun.Status.SUCCESS)
        self.assertEqual(run.found, 1)
        self.assertEqual(run.saved_new, 1)
        self.assertEqual(Tender.objects.count(), 1)

        meta = MagazineTenderMeta.objects.get()
        self.assertEqual(meta.magazine, 'TESTMAG')
        self.assertEqual(meta.run_id, run.pk)
        self.assertEqual(meta.listing_url,
                         'https://example-magazine.com/tenders/1001')

    def test_is_aec_comes_from_the_classification_service(self):
        # The service says AEC → the row is AEC, and the service was called.
        run = self.run_with(items=[item()], enrich=AEC_ENRICH)
        t = Tender.objects.get()
        self.assertTrue(t.is_aec)
        self.assertEqual(t.category, 'INFRA')
        self.assertEqual(self.last_enrich.call_count, 1)
        self.assertEqual(run.aec, 1)
        self.assertEqual(run.non_aec, 0)

    def test_non_aec_result_is_stored_not_discarded(self):
        # A non-AEC announcement is kept for audit; only the UI hides it.
        run = self.run_with(items=[item()], enrich=NON_AEC_ENRICH)
        t = Tender.objects.get()
        self.assertFalse(t.is_aec)
        self.assertEqual(run.aec, 0)
        self.assertEqual(run.non_aec, 1)
        self.assertEqual(MagazineTenderMeta.objects.count(), 1)

    def test_dashboard_required_fields_are_forced(self):
        # DashboardView filters on is_relevant + status='OPEN'; a magazine
        # tender missing either would be classified and then invisible.
        self.run_with(items=[item(status='UNKNOWN')])
        t = Tender.objects.get()
        self.assertTrue(t.is_relevant)
        self.assertEqual(t.status, 'OPEN')

    # ── save policy ──────────────────────────────────────────────────────────
    def test_past_deadline_is_counted_as_skipped(self):
        run = self.run_with(items=[item(submission_deadline=PAST)])
        self.assertEqual(run.found, 1)
        self.assertEqual(run.skipped, 1)
        self.assertEqual(run.saved_new, 0)
        self.assertEqual(Tender.objects.count(), 0)

    # ── idempotency ──────────────────────────────────────────────────────────
    def test_second_run_with_same_data_creates_no_duplicate(self):
        self.run_with(items=[item()])
        self.assertEqual(Tender.objects.count(), 1)
        run2 = self.run_with(items=[item()])
        self.assertEqual(Tender.objects.count(), 1)
        self.assertEqual(run2.saved_new, 0)

    def test_external_id_is_stable_without_one_from_the_site(self):
        a = runner.make_external_id('A tender', 'An entity', FUTURE, 'mag-x')
        b = runner.make_external_id('A  tender ', 'An entity', FUTURE, 'mag-x')
        self.assertEqual(a, b)                       # whitespace folded
        self.assertTrue(a.startswith('mag-x-'))
        c = runner.make_external_id('Another', 'An entity', FUTURE, 'mag-x')
        self.assertNotEqual(a, c)

    def test_site_supplied_external_id_is_respected(self):
        self.run_with(items=[item(external_id='site-id-42')])
        self.assertEqual(Tender.objects.get().external_id, 'site-id-42')

    # ── failure containment ──────────────────────────────────────────────────
    def test_scraper_exception_marks_run_failed_without_raising(self):
        run = self.run_with(raises=RuntimeError('site down'))
        self.assertEqual(run.status, MagazineRun.Status.FAILED)
        self.assertIn('site down', run.error_message)
        self.assertEqual(Tender.objects.count(), 0)

    def test_run_all_continues_past_a_broken_magazine(self):
        two = {
            'BROKEN': dict(TEST_MAGAZINES['TESTMAG'], code='BROKEN',
                           scraper_class='MAGAZINE_BROKEN', id_prefix='mag-broken'),
            'TESTMAG': TEST_MAGAZINES['TESTMAG'],
        }

        def pick(code):
            if code == 'BROKEN':
                return _FakeScraper(raises=RuntimeError('boom'))
            return _FakeScraper(items=[item()])

        with mock.patch.dict(sources.MAGAZINES, two, clear=True), \
             mock.patch('magazine_tenders.runner.get_magazine_scraper',
                        side_effect=pick), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(AEC_ENRICH)):
            runs = runner.run_all()

        self.assertEqual(runs['BROKEN'].status, MagazineRun.Status.FAILED)
        self.assertEqual(runs['TESTMAG'].status, MagazineRun.Status.SUCCESS)
        self.assertEqual(Tender.objects.count(), 1)

    # ── registry / config ────────────────────────────────────────────────────
    def test_run_all_with_nothing_configured_is_a_no_op(self):
        with mock.patch.dict(sources.MAGAZINES, {}, clear=True):
            self.assertEqual(runner.run_all(), {})

    @override_settings(MAGAZINE_TESTMAG_ENABLED=False)
    def test_disabled_magazine_is_skipped(self):
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True):
            self.assertEqual(sources.enabled_sources(), [])

    def test_unknown_code_raises_keyerror(self):
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True):
            with self.assertRaises(KeyError):
                sources.get_source_config('NOPE')

    # ── multi-country magazines ──────────────────────────────────────────────
    def test_countries_are_created_so_tenders_are_not_mislabelled(self):
        """
        Regression: save_tender_data resolves td.country_code against
        CountryConfig and falls back to the SOURCE's country when the row is
        missing. Gulf Construction covers six countries, so without pre-creating
        the rows every Qatar/Kuwait notice was stored as Bahrain.
        """
        from tenders.models import CountryConfig

        CountryConfig.objects.filter(code__in=['QA', 'KW']).delete()

        self.run_with(items=[
            item(title='Qatar road supervision', country_code='QA',
                 external_id='qa-1'),
            item(title='Kuwait sewage works', country_code='KW',
                 external_id='kw-1'),
        ])

        self.assertTrue(CountryConfig.objects.filter(code='QA').exists())
        self.assertTrue(CountryConfig.objects.filter(code='KW').exists())
        got = {t.external_id: t.country.code for t in
               Tender.objects.filter(magazine_meta__isnull=False)}
        self.assertEqual(got.get('qa-1'), 'QA')
        self.assertEqual(got.get('kw-1'), 'KW')

    # ── anchor row ───────────────────────────────────────────────────────────
    def test_anchor_source_is_inactive_so_run_all_scrapers_ignores_it(self):
        from magazine_tenders.anchor import get_magazine_source
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True):
            src = get_magazine_source('TESTMAG')
        self.assertFalse(src.is_active)
        self.assertTrue(src.scraper_class.startswith('MAGAZINE_'))

class RegistryIntegrityTests(TestCase):
    """
    Invariants that must hold for whatever is registered.

    Deliberately not pinned to a source count: the registry is meant to
    change, and asserting an exact number just breaks whenever a source is
    added or parked.
    """

    def test_every_registered_source_imports(self):
        from magazine_tenders import registry
        codes = sources.all_codes()
        self.assertGreaterEqual(len(codes), 1, 'no sources registered')
        for code in codes:
            with self.subTest(code=code):
                self.assertTrue(registry.scraper_exists(code),
                                f'{code} scraper is not importable')

    def test_scraper_classes_all_carry_the_magazine_prefix(self):
        # The MAGAZINE_ prefix is what every source-list exclusion keys
        # off; a missing one would leak an anchor row into "Run All
        # Scanners" and the Sources pages.
        for code, cfg in sources.MAGAZINES.items():
            with self.subTest(code=code):
                self.assertEqual(cfg['scraper_class'], f'MAGAZINE_{code}')
