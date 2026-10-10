# magazine_tenders/tests/test_alrai.py
"""
Al-Rai (alrai.com) — the اقتصاد and محليات sections.

The first wholly Arabic news source, and the first that is not WordPress. It
reads the site's own widget API, so the parse is tested offline against two
saved responses captured on 21 September 2026.

The filter itself is tested in test_arabic_rules.py; what these tests pin is
the scraper: that it reads the payload, carries the photograph, keeps the
Arabic headline, and refuses the money desk.
"""
import json
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers.alrai import AlRaiScraper

FIXTURES = Path(__file__).parent / 'fixtures'


def payload(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


class AlRaiParseTests(TestCase):
    """Turning the widget payload into TenderData."""

    def setUp(self):
        self.scraper = AlRaiScraper()

    def raw(self, name):
        return self.scraper.parse_payload(payload(name))

    def test_every_article_in_the_payload_is_read(self):
        self.assertEqual(len(self.raw('alrai_economy.json')), 18)

    def test_raw_item_carries_the_fields_build_one_needs(self):
        item = self.raw('alrai_economy.json')[0]
        for key in ('title', 'summary', 'url', 'published', 'image'):
            self.assertIn(key, item)

    def test_the_arabic_headline_is_kept_as_the_title(self):
        item = self.raw('alrai_economy.json')[0]
        self.assertTrue(any('؀' <= ch <= 'ۿ' for ch in item['title']),
                        f'expected Arabic, got {item["title"]!r}')

    def test_the_photograph_is_carried(self):
        items = self.raw('alrai_economy.json')
        withphoto = [i for i in items if i['image']]
        self.assertEqual(len(withphoto), 18)
        self.assertTrue(withphoto[0]['image'].startswith('https://'))

    def test_the_publication_date_is_parsed(self):
        from datetime import date
        item = self.raw('alrai_economy.json')[0]
        self.assertIsInstance(item['published'], date)

    def test_the_article_url_is_absolute(self):
        item = self.raw('alrai_economy.json')[0]
        self.assertTrue(item['url'].startswith('https://alrai.com/'))


class AlRaiFilterTests(TestCase):
    """What survives the keep-filter, end to end through build_items()."""

    def setUp(self):
        self.scraper = AlRaiScraper()

    def kept(self, name):
        raw = self.scraper.parse_payload(payload(name))
        return self.scraper.build_items(raw)

    def test_the_money_desk_is_mostly_refused(self):
        # The economy section is a finance desk. Most of it must not reach the
        # News page - if this ever passes with a high number, the filter has
        # come loose.
        kept = self.kept('alrai_economy.json')
        self.assertLess(len(kept), 6, [t.title for t in kept])

    def test_local_news_yields_engineering_items(self):
        self.assertGreater(len(self.kept('alrai_local.json')), 0)

    def test_nothing_about_gold_or_interest_rates_is_kept(self):
        for td in self.kept('alrai_economy.json'):
            self.assertNotIn('الذهب', td.title)   # الذهب
            self.assertNotIn('الفائدة', td.title)  # الفائدة

    def test_kept_items_carry_their_photograph(self):
        for td in self.kept('alrai_local.json'):
            self.assertTrue(td.image_url, f'no photo on {td.title!r}')

    def test_kept_items_are_labelled_with_the_rule_that_kept_them(self):
        for td in self.kept('alrai_local.json'):
            self.assertRegex(td.description, r'^\[(?:TENDER|DESIGN|PROJECT)\]')

    def test_country_is_jordan(self):
        for td in self.kept('alrai_local.json'):
            self.assertEqual(td.country_code, 'JO')

    def test_parsing_writes_nothing_to_the_database(self):
        from tenders.models import Tender
        before = Tender.objects.count()
        self.kept('alrai_economy.json')
        self.kept('alrai_local.json')
        self.assertEqual(Tender.objects.count(), before)


class AlRaiPolitenessTests(TestCase):
    """
    Every request must go through BaseScraper.get().

    That helper is what applies SCRAPER_DELAY_BETWEEN_REQUESTS, the retry
    ladder and the backoff. The first version of this scraper called
    self.session.get() directly and so had none of them; five sweeps and a
    corpus pull in one hour made alrai.com stop answering altogether.
    """

    def test_api_get_goes_through_the_rate_limited_helper(self):
        scraper = AlRaiScraper()
        calls = []

        class FakeResponse:
            text = '{"data": [], "widget_data_info": {}}'

            def raise_for_status(self):
                pass

        def recording_get(url, **kwargs):
            calls.append(url)
            return FakeResponse()

        scraper.get = recording_get
        scraper._api_get('https://alrai.com/api/pb/widget/1809/json')

        self.assertEqual(len(calls), 1,
                         'the request did not go through self.get()')

    def test_a_dead_section_does_not_lose_the_other(self):
        scraper = AlRaiScraper()
        seen = []

        def half_broken(section_id):
            seen.append(section_id)
            if section_id == 4:
                raise RuntimeError('read timed out')
            return [{'guid': 1, 'title': 'x', 'summary': '', 'body': '',
                     'url': 'u', 'published': None, 'image': '',
                     'image_large': ''}]

        scraper._fetch_section = half_broken
        out = scraper._fetch_alrai()

        self.assertEqual(seen, [4, 1], 'both sections should be attempted')
        self.assertEqual(len(out), 1, 'the healthy section should still return')


class AlRaiConfigurationTests(TestCase):
    """The registry entry and the section wiring."""

    def test_both_sections_are_configured(self):
        self.assertEqual(AlRaiScraper.SECTION_IDS, (4, 1))

    def test_registered_in_sources(self):
        from magazine_tenders import sources
        cfg = sources.MAGAZINES['ALRAI']
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_ALRAI')
        self.assertEqual(cfg['country_code'], 'JO')

    def test_the_registry_entry_resolves_to_this_scraper(self):
        from magazine_tenders.registry import get_magazine_scraper
        self.assertIsInstance(get_magazine_scraper('ALRAI'), AlRaiScraper)

    def test_scrape_without_a_source_returns_empty(self):
        self.assertEqual(AlRaiScraper().scrape(), [])
