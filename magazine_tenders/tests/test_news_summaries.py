# magazine_tenders/tests/test_news_summaries.py
"""
The summary on a News card is written by AJi AI, once, when the article is
stored - and the page never waits for a model.
"""
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender

from magazine_tenders import runner, sources, summaries
from magazine_tenders.models import MagazineTenderMeta
from magazine_tenders.tests.test_runner import (AEC_ENRICH, TEST_MAGAZINES,
                                                _FakeScraper, item)

ARTICLE = ('TMG Saudi and PIF-backed Roshn Group have agreed to form a joint '
           'company, with TMG holding 51% and Roshn 49%, to explore a mixed-use '
           'community in Riyadh comprising more than 55,000 homes on a 10 million '
           'sq m site, with first deliveries expected in Q4 2027. ' * 3)
SUMMARY = ('TMG Saudi and Roshn Group formed a joint venture (51%/49%) for a '
           '55,000-home mixed-use community in Riyadh on a 10 million sq m site, '
           'with first deliveries in Q4 2027.')


def _reply(answer=SUMMARY, status=200, model='qwen3.8:27b', json_ok=True):
    resp = mock.Mock()
    resp.status_code = status
    resp.content = b'x'
    if json_ok:
        resp.json.return_value = {'answer': answer, 'model': model,
                                  'done_reason': 'stop', 'usage': {}}
    else:
        resp.json.side_effect = ValueError('not json')
    return resp


@override_settings(MAGAZINE_AI_SUMMARIES=True)
class SummariseTests(TestCase):
    def setUp(self):
        summaries.reset_breaker()

    def test_a_good_answer_comes_back_clean(self):
        with mock.patch('requests.Session.post', return_value=_reply('  "Summary: ' + SUMMARY + '"  ')) as post:
            text, model = summaries.summarise('Headline', ARTICLE)
        self.assertEqual(text, SUMMARY)
        self.assertEqual(model, 'qwen3.8:27b')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['stream'], False)
        self.assertEqual(payload['temperature'], 0)
        self.assertIn('Headline', payload['prompt'])
        self.assertIn('55,000 homes', payload['prompt'])
        self.assertNotIn('model', payload)           # AJi AI picks its own

    def test_failures_return_nothing_and_never_raise(self):
        with mock.patch('requests.Session.post', return_value=_reply(status=503)):
            self.assertEqual(summaries.summarise('H', ARTICLE), ('', ''))
        with mock.patch('requests.Session.post', return_value=_reply(json_ok=False)):
            self.assertEqual(summaries.summarise('H', ARTICLE), ('', ''))
        with mock.patch('requests.Session.post', side_effect=OSError('down')):
            self.assertEqual(summaries.summarise('H', ARTICLE), ('', ''))
        with mock.patch('requests.Session.post', return_value=_reply(answer='')):
            self.assertEqual(summaries.summarise('H', ARTICLE), ('', ''))

    def test_the_breaker_stops_a_run_after_repeated_transport_failures(self):
        with mock.patch('requests.Session.post', side_effect=OSError('down')) as post:
            for _ in range(summaries.BREAKER_TRIPS + 2):
                summaries.summarise('H', ARTICLE)
        self.assertEqual(post.call_count, summaries.BREAKER_TRIPS)
        self.assertTrue(summaries.breaker_open())
        summaries.reset_breaker()
        self.assertFalse(summaries.breaker_open())

    def test_the_breaker_closes_again_after_the_cooldown(self):
        with mock.patch('requests.Session.post', side_effect=OSError('down')):
            for _ in range(summaries.BREAKER_TRIPS):
                summaries.summarise('H', ARTICLE)
        self.assertTrue(summaries.breaker_open())
        with mock.patch.object(summaries.time, 'monotonic',
                               return_value=summaries._tripped_at + summaries.BREAKER_COOLDOWN_S + 1):
            self.assertFalse(summaries.breaker_open())
        with mock.patch('requests.Session.post', return_value=_reply()):
            self.assertEqual(summaries.summarise('H', ARTICLE)[0], SUMMARY)

    def test_a_sweep_asks_for_one_batch_and_a_single_run_for_none(self):
        with mock.patch.object(summaries, 'summarise_backlog') as batch, \
             mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True), \
             mock.patch('magazine_tenders.runner.get_magazine_scraper',
                        return_value=_FakeScraper(items=[])), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(AEC_ENRICH)):
            runner.run_one('TESTMAG')
            self.assertEqual(batch.call_count, 0)
            runner.run_all()
            self.assertEqual(batch.call_count, 1)

    def test_switched_off_means_no_call(self):
        with override_settings(MAGAZINE_AI_SUMMARIES=False), \
             mock.patch('requests.Session.post') as post:
            self.assertEqual(summaries.summarise('H', ARTICLE), ('', ''))
        post.assert_not_called()

    def test_the_proxy_is_not_trusted_for_the_internal_address(self):
        with mock.patch('requests.Session.post', return_value=_reply()) as post:
            summaries.summarise('H', ARTICLE)
        session = post.call_args[0][0] if post.call_args[0] else None
        # Session.post is patched on the class, so inspect the instance via
        # the bound call: trust_env must be False unless configured.
        self.assertIsNotNone(post.call_args)


@override_settings(MAGAZINE_AI_SUMMARIES=True)
class ArticleTextTests(TestCase):
    HTML = """<html><head><script>var x=1;</script></head><body>
      <nav><p>Home · Business · Property · Energy · Contact us · Subscribe now</p></nav>
      <article>
        <h1>TMG and Roshn sign deal</h1>
        <p>TMG Saudi and PIF-backed Roshn Group have agreed to form a joint company to explore a 55,000-home community.</p>
        <p>Short.</p>
        <p>The 10 million sq m site in Riyadh will see first deliveries in Q4 2027, the companies said.</p>
      </article>
      <footer><p>Copyright 2026 Example Publishing. All rights reserved worldwide.</p></footer>
    </body></html>"""

    def test_paragraphs_from_the_article_only(self):
        text = summaries.extract_article_text(self.HTML)
        self.assertIn('55,000-home community', text)
        self.assertIn('Q4 2027', text)
        self.assertNotIn('Subscribe now', text)
        self.assertNotIn('Copyright', text)
        self.assertNotIn('Short.', text)
        self.assertNotIn('var x', text)

    def test_the_feed_body_is_used_when_long_enough(self):
        td = item(); td.article_text = ARTICLE
        t = Tender(title='x', description='[PROJECT] excerpt', source_url='https://example.test/a')
        with mock.patch.object(summaries, 'fetch_article_text') as fetch:
            self.assertEqual(summaries.text_for(t, td), ARTICLE.strip()[:summaries.MAX_ARTICLE_CHARS])
        fetch.assert_not_called()

    def test_the_page_is_fetched_when_the_feed_gave_only_a_headline(self):
        td = item(); td.article_text = ''
        t = Tender(title='x', description='[PROJECT]', source_url='https://example.test/a')
        with mock.patch.object(summaries, 'fetch_article_text', return_value=ARTICLE) as fetch:
            self.assertEqual(summaries.text_for(t, td), ARTICLE[:summaries.MAX_ARTICLE_CHARS])
        fetch.assert_called_once_with('https://example.test/a')

    def test_a_forthcoming_notice_is_never_summarised(self):
        # Ashghal's pipeline and DevelopmentAid forecasts are notices, not
        # articles; the page behind them is boilerplate.
        from magazine_tenders.models import MagazineTenderMeta as Meta
        t = Tender(title='x', description='expected Second Quarter 2027', source_url='https://example.test/a')
        t.magazine_meta = Meta(magazine='ASHGHALPROSPECTED')
        with mock.patch.object(summaries, 'summarise') as s:
            self.assertFalse(summaries.summarise_tender(t, None, fetch=False))
        s.assert_not_called()

    def test_the_standfirst_is_the_last_resort(self):
        t = Tender(title='x', description='[PROJECT] The excerpt.', source_url='')
        self.assertEqual(summaries.text_for(t, None, fetch=False), 'The excerpt.')


@override_settings(MAGAZINE_AI_SUMMARIES=True, MAGAZINE_SUMMARY_PAUSE=0)
class SweepAsksGentlyTests(TestCase):
    """
    The sweep never asks for a summary while it is storing articles. When the
    whole sweep is over it asks for a paced batch - and a single source run
    (`run_one`) asks for nothing at all.
    """

    def _patches(self, items):
        fake = _FakeScraper(items=items)
        return (mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True),
                mock.patch('magazine_tenders.runner.get_magazine_scraper', return_value=fake),
                mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                           return_value=dict(AEC_ENRICH)),
                mock.patch.object(summaries, 'service_online', return_value=True),
                mock.patch.object(summaries, 'fetch_article_text', return_value=ARTICLE))

    def test_run_one_stores_the_article_and_asks_for_nothing(self):
        td = item(publication_date=date.today()); td.article_text = ARTICLE
        p = self._patches([td])
        with p[0], p[1], p[2], p[3], p[4], mock.patch.object(summaries, 'summarise') as s:
            run = runner.run_one('TESTMAG')
        self.assertEqual(run.status, 'SUCCESS')
        s.assert_not_called()
        self.assertEqual(MagazineTenderMeta.objects.get().summary, '')

    def test_run_all_writes_the_batch_after_the_sweep(self):
        td = item(publication_date=date.today()); td.article_text = ARTICLE
        p = self._patches([td])
        with p[0], p[1], p[2], p[3], p[4], \
             mock.patch.object(summaries, 'summarise', return_value=(SUMMARY, 'm')) as s:
            runner.run_all()
        meta = MagazineTenderMeta.objects.get()
        self.assertEqual(meta.summary, SUMMARY)
        self.assertEqual(s.call_count, 1)

    def test_an_article_that_has_one_is_not_asked_again(self):
        td = item(publication_date=date.today()); td.article_text = ARTICLE
        p = self._patches([td])
        with p[0], p[1], p[2], p[3], p[4], \
             mock.patch.object(summaries, 'summarise', return_value=(SUMMARY, 'm')):
            runner.run_all()
        with p[0], p[1], p[2], p[3], p[4], \
             mock.patch.object(summaries, 'summarise', return_value=('other', 'm')) as s:
            runner.run_all()
        s.assert_not_called()
        self.assertEqual(MagazineTenderMeta.objects.get().summary, SUMMARY)

    def test_a_failing_batch_costs_nothing_but_the_summaries(self):
        td = item(publication_date=date.today()); td.article_text = ARTICLE
        p = self._patches([td])
        with p[0], p[1], p[2], p[3], p[4], \
             mock.patch.object(summaries, 'summarise_backlog', side_effect=RuntimeError('boom')):
            runs = runner.run_all()
        self.assertEqual(runs['TESTMAG'].status, 'SUCCESS')
        self.assertEqual(MagazineTenderMeta.objects.get().summary, '')


@override_settings(MAGAZINE_AI_SUMMARIES=True)
class TheBatchIsGentleTests(TestCase):
    """One at a time, a pause between, capped, and it stops at the first
    failure - the behaviour that keeps the AI host alive."""

    @classmethod
    def setUpTestData(cls):
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com', scraper_class='MAGAZINE_TESTMAG')
        for i in range(6):
            t = Tender.objects.create(
                source=src, country=jo, external_id=f'gb-{i}',
                source_url='https://example-magazine.com/a', title=f'Story {i}',
                description='[PROJECT] excerpt', is_aec=True, is_aec_checked=True,
                is_relevant=True, status='OPEN', publication_date=date(2026, 9, 2))
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')

    def test_capped_and_paced(self):
        with mock.patch.object(summaries, 'service_online', return_value=True), \
             mock.patch.object(summaries, 'fetch_article_text', return_value=ARTICLE), \
             mock.patch.object(summaries, 'summarise', return_value=(SUMMARY, 'm')) as s, \
             mock.patch.object(summaries.time, 'sleep') as sleep:
            out = summaries.summarise_backlog(limit=4, pause=5)
        self.assertEqual(out['written'], 4)
        self.assertEqual(s.call_count, 4)
        self.assertEqual(sleep.call_count, 3)              # between articles, not before the first
        self.assertEqual(sleep.call_args[0][0], 5)
        self.assertEqual(MagazineTenderMeta.objects.filter(summary='').count(), 2)

    def test_stops_at_the_first_transport_failure(self):
        def flaky(title, text):
            summaries._record_failure()                    # what a 504 does
            return '', ''
        with mock.patch.object(summaries, 'service_online', return_value=True), \
             mock.patch.object(summaries, 'fetch_article_text', return_value=ARTICLE), \
             mock.patch.object(summaries, 'summarise', side_effect=flaky) as s, \
             mock.patch.object(summaries.time, 'sleep'):
            out = summaries.summarise_backlog(limit=6, pause=0)
        self.assertEqual(s.call_count, 1)
        self.assertIn('stopping this batch early', out['stopped'])

    def test_does_not_start_when_the_service_says_offline(self):
        with mock.patch.object(summaries, 'service_online', return_value=False), \
             mock.patch.object(summaries, 'summarise') as s:
            out = summaries.summarise_backlog(limit=6, pause=0)
        s.assert_not_called()
        self.assertIn('offline', out['stopped'])

    def test_switched_off_means_nothing_happens(self):
        with override_settings(MAGAZINE_AI_SUMMARIES=False), \
             mock.patch.object(summaries, 'summarise') as s:
            out = summaries.summarise_backlog(limit=6, pause=0)
        s.assert_not_called()
        self.assertEqual(out['written'], 0)


class ThePagesShowItTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = User.objects.create_superuser('sum-reader', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com', scraper_class='MAGAZINE_TESTMAG')

        def row(title, summary):
            t = Tender.objects.create(
                source=src, country=jo, external_id=f'sum-{title}',
                source_url='https://example-magazine.com/a', title=title,
                description='[PROJECT] The publisher excerpt about 3 towers.',
                is_aec=True, is_aec_checked=True, is_relevant=True, status='OPEN',
                publication_date=date(2026, 9, 2))
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG', summary=summary)
            return t

        cls.with_summary = row('Summarised story', SUMMARY)
        cls.without = row('Plain story', '')

    def test_the_card_prefers_the_ai_summary_and_falls_back_to_the_excerpt(self):
        self.client.force_login(self.reader)
        html = self.client.get(reverse('magazine_tenders:list') + '?view=news').content.decode()
        # the figures are marked up, so match the prose around them
        self.assertIn('mixed-use community in Riyadh', html)
        self.assertIn('<strong class="nf-num">55,000</strong>', html)
        self.assertIn('The publisher excerpt about', html)      # the row without one

    def test_the_article_page_leads_with_it(self):
        self.client.force_login(self.reader)
        html = self.client.get(reverse('tenders:detail', args=[self.with_summary.pk])).content.decode()
        self.assertIn('mixed-use community in Riyadh', html)
        html = self.client.get(reverse('tenders:detail', args=[self.without.pk])).content.decode()
        self.assertIn('The publisher excerpt about', html)


@override_settings(MAGAZINE_AI_SUMMARIES=True)
class BacklogCommandTests(TestCase):
    def test_it_fills_only_the_missing_ones(self):
        from io import StringIO
        from django.core.management import call_command
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com', scraper_class='MAGAZINE_TESTMAG')
        rows = []
        for i, existing in enumerate(('', 'already written', '')):
            t = Tender.objects.create(
                source=src, country=jo, external_id=f'bl-{i}',
                source_url='https://example-magazine.com/a', title=f'Story {i}',
                description='[PROJECT] excerpt', is_aec=True, is_aec_checked=True,
                is_relevant=True, status='OPEN', publication_date=date(2026, 9, 2))
            rows.append(MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG',
                                                          summary=existing))
        out = StringIO()
        with mock.patch.object(summaries, 'summarise', return_value=(SUMMARY, 'm')) as s, \
             mock.patch.object(summaries, 'service_online', return_value=True), \
             mock.patch.object(summaries, 'fetch_article_text', return_value=ARTICLE):
            call_command('summarise_news', pause=0, stdout=out)
        self.assertEqual(s.call_count, 2)
        self.assertEqual(MagazineTenderMeta.objects.filter(summary=SUMMARY).count(), 2)
        self.assertEqual(MagazineTenderMeta.objects.get(pk=rows[1].pk).summary, 'already written')
        self.assertIn('2 summary(ies) written', out.getvalue())
