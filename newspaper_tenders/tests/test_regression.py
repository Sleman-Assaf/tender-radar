# newspaper_tenders/tests/test_regression.py
# Regression safety: the Newspaper Tenders feature must not change any
# existing TenderRadar behaviour.

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender
from tenders.scrapers.base import TenderData
from tenders.services import save_tender_data

from newspaper_tenders.pipeline import get_newspaper_source


class SaveTenderDataRegressionTests(TestCase):
    """save_tender_data default behaviour is bit-for-bit what it was."""

    @classmethod
    def setUpTestData(cls):
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'currency': 'JOD'})
        cls.source = ScraperSource.objects.create(
            name='Web Portal', country=jo,
            base_url='https://portal.example.com',
            scraper_class='JordanGTDScraper')

    def _td(self, **over):
        base = dict(
            title='Engineering supervision services',
            source_url='https://portal.example.com/t/1',
            external_id='t-1',
            submission_deadline=date.today() + timedelta(days=10),
        )
        base.update(over)
        return TenderData(**base)

    @mock.patch('tenders.services._run_ajibrain_enrichment')
    def test_default_save_still_runs_enrichment(self, m_enrich):
        created, updated = save_tender_data(self._td(), self.source)
        self.assertTrue(created)
        m_enrich.assert_called_once()          # unchanged default behaviour

    @mock.patch('tenders.services._run_ajibrain_enrichment')
    def test_enrich_false_skips_enrichment_only(self, m_enrich):
        created, updated = save_tender_data(self._td(), self.source, enrich=False)
        self.assertTrue(created)
        m_enrich.assert_not_called()
        # …but everything else (row creation, dedup fields) is identical
        t = Tender.objects.get(source=self.source, external_id='t-1')
        self.assertEqual(t.title, 'Engineering supervision services')
        self.assertTrue(t.content_hash)

    @mock.patch('tenders.services._run_ajibrain_enrichment')
    def test_past_deadline_skip_policy_unchanged(self, m_enrich):
        td = self._td(submission_deadline=date.today() - timedelta(days=1))
        created, updated = save_tender_data(td, self.source)
        self.assertFalse(created)
        self.assertEqual(Tender.objects.count(), 0)


class ScraperSurfaceRegressionTests(TestCase):
    """The newspaper source row never leaks into scraper surfaces."""

    @classmethod
    def setUpTestData(cls):
        cls.np_source = get_newspaper_source()
        cls.admin = User.objects.create_superuser('boss', 'b@x.jo', 'pw12345!')

    def test_scraper_registry_untouched(self):
        # Every registered scraper still resolves; the newspaper class is absent.
        from tenders.scrapers import SCRAPER_REGISTRY, get_scraper
        self.assertNotIn('NEWSPAPER_ALRAI', SCRAPER_REGISTRY)
        self.assertGreater(len(SCRAPER_REGISTRY), 50)

    def test_run_all_scrapers_skips_inactive_newspaper_source(self):
        # run_all_scrapers only queues is_active=True sources
        from tenders import tasks
        with mock.patch.object(tasks.run_scraper, 'delay') as m_delay:
            tasks.run_all_scrapers()
            queued = {c.args[0] for c in m_delay.call_args_list}
        self.assertNotIn(self.np_source.pk, queued)

    def test_sources_page_hides_newspaper_row(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('tenders:sources'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'NEWSPAPER_ALRAI')

    def test_existing_tender_routes_still_resolve(self):
        self.client.force_login(self.admin)
        for name in ('tenders:dashboard', 'tenders:list'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)


class NonAECKeywordGuardTests(TestCase):
    """Rent/sale/auction ads are forced NON-AEC for newspaper tenders only,
    no matter what the AI answered (deterministic guard, 24 Aug 2026)."""

    @classmethod
    def setUpTestData(cls):
        cls.np_source = get_newspaper_source('ALRAI')
        jo = CountryConfig.objects.get(code='JO')
        cls.web_source = ScraperSource.objects.create(
            name='Web Portal 2', country=jo,
            base_url='https://p2.example.com', scraper_class='JordanGTDScraper')

    def _classify(self, source, title):
        from tenders import ajibrain_client
        t = Tender(source=source, title=title, title_ar=title,
                   entity='وزارة التنمية الاجتماعية')
        with mock.patch.object(ajibrain_client, 'classify_tender',
                               return_value=(True, 'BUILDING')):
            return ajibrain_client.classify_and_translate_tender(t)

    def test_rental_ad_forced_non_aec_for_newspaper(self):
        updates = self._classify(
            self.np_source,
            'طلب استئجار مبنى بديل لمركز تنمية المجتمع المحلي الطرة')
        self.assertFalse(updates['is_aec'])

    def test_auction_ad_forced_non_aec_for_newspaper(self):
        updates = self._classify(self.np_source, 'إعلان بيع بالمزاد العلني')
        self.assertFalse(updates['is_aec'])

    def test_guard_does_not_touch_scraper_tenders(self):
        updates = self._classify(self.web_source, 'استئجار معدات حفر للمشروع')
        self.assertTrue(updates['is_aec'])

    def test_normal_newspaper_aec_still_aec(self):
        updates = self._classify(self.np_source, 'عطاء إشراف هندسي على مبنى')
        self.assertTrue(updates['is_aec'])
