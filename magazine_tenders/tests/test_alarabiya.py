# magazine_tenders/tests/test_alarabiya.py
"""
Al Arabiya (alarabiya.net).

A pan-Arab general news site, and the sixth shape of source. Two things
define the parse: an article is any link whose PATH carries a date, and the
headline lives in the picture's alt because the anchor's own text is
prefixed with the clock and the section name.

Captured 22 September 2026 from /saudi-today. The business section was
answering 403 to a non-browser at the time — Cloudflare challenges
individual paths — which is itself part of what these tests describe.
"""
from datetime import date
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers.alarabiya import AlArabiyaScraper

FIXTURES = Path(__file__).parent / 'fixtures'


def saudi_html():
    return (FIXTURES / 'alarabiya_saudi.html').read_text(
        encoding='utf-8', errors='replace')


class AlArabiyaParseTests(TestCase):

    def setUp(self):
        self.scraper = AlArabiyaScraper()
        self.items = self.scraper.parse_section(saudi_html())

    def test_the_article_cards_are_found(self):
        self.assertGreaterEqual(len(self.items), 20)

    def test_each_item_has_what_build_one_needs(self):
        for key in ('title', 'summary', 'url', 'published', 'image'):
            self.assertIn(key, self.items[0])

    def test_titles_are_arabic_and_clean(self):
        for item in self.items:
            self.assertTrue(any('؀' <= ch <= 'ۿ' for ch in item['title']),
                            item['title'])
            # The anchor text starts "٠٩:١٦ سيارات سيارات ..." — the clock and
            # the section, twice. The alt does not, and that is why it is used.
            self.assertFalse(item['title'].startswith('٠'), item['title'])
            self.assertNotIn(':', item['title'][:6])

    def test_urls_are_absolute_and_encoded(self):
        for item in self.items:
            self.assertTrue(item['url'].startswith('https://www.alarabiya.net/'),
                            item['url'])
            self.assertNotIn(' ', item['url'])

    def test_every_item_carries_a_photograph(self):
        for item in self.items:
            self.assertTrue(item['image'], item['title'])

    def test_no_duplicate_articles(self):
        urls = [i['url'] for i in self.items]
        self.assertEqual(len(urls), len(set(urls)))

    def test_menus_and_author_pages_are_not_articles(self):
        # The date in the path is the whole test. Nothing without one gets in.
        for item in self.items:
            self.assertRegex(item['url'], r'/\d{4}/\d{2}/\d{2}/')


class AlArabiyaDateTests(TestCase):
    """The date comes free, out of the URL."""

    def setUp(self):
        self.scraper = AlArabiyaScraper()

    def test_the_date_is_read_from_the_path(self):
        self.assertEqual(
            self.scraper.date_from_url('/aswaq/cars/2026/09/21/some-slug'),
            date(2026, 9, 21))

    def test_a_path_with_no_date_gives_none(self):
        self.assertIsNone(self.scraper.date_from_url('/authors/someone'))
        self.assertIsNone(self.scraper.date_from_url(''))

    def test_an_impossible_date_gives_none(self):
        self.assertIsNone(self.scraper.date_from_url('/aswaq/2026/13/45/x'))

    def test_every_parsed_item_is_dated(self):
        for item in self.scraper.parse_section(saudi_html()):
            self.assertIsInstance(item['published'], date)

    def test_parsing_never_fetches_anything(self):
        scraper = AlArabiyaScraper()

        def forbidden(url, **kw):
            raise AssertionError('parsing must not fetch anything')

        scraper.get = forbidden
        scraper.build_items(scraper.parse_section(saudi_html()))


class AlArabiyaFilterTests(TestCase):

    def setUp(self):
        self.scraper = AlArabiyaScraper()
        self.kept = self.scraper.build_items(
            self.scraper.parse_section(saudi_html()))

    def test_a_general_news_site_is_mostly_refused(self):
        self.assertLess(len(self.kept), 6, [t.title for t in self.kept])

    def test_kept_items_are_labelled(self):
        for td in self.kept:
            self.assertRegex(td.description, r'^\[(?:TENDER|DESIGN|PROJECT)\]')

    def test_parsing_writes_nothing_to_the_database(self):
        from tenders.models import Tender
        before = Tender.objects.count()
        self.scraper.build_items(self.scraper.parse_section(saudi_html()))
        self.assertEqual(Tender.objects.count(), before)


class AlArabiyaPolitenessTests(TestCase):
    """
    One request per section, through the rate-limited helper.

    This site challenges a caller that does not pause: /aswaq served twice
    during investigation and then began refusing.
    """

    def test_one_request_per_section_and_no_more(self):
        scraper = AlArabiyaScraper()
        calls = []

        class FakeResponse:
            text = '<html></html>'

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]
        scraper._fetch_alarabiya()

        self.assertEqual(len(calls), len(AlArabiyaScraper.SECTION_URLS))

    def test_a_challenged_section_does_not_lose_the_others(self):
        scraper = AlArabiyaScraper()
        seen = []

        def half_broken(url, **kw):
            seen.append(url)
            raise RuntimeError('403 Forbidden')

        scraper.get = half_broken
        out = scraper._fetch_alarabiya()

        self.assertEqual(len(seen), len(AlArabiyaScraper.SECTION_URLS),
                         'every section should still be attempted')
        self.assertEqual(out, [])


class AlArabiyaConfigurationTests(TestCase):

    def test_registered_in_sources(self):
        from magazine_tenders import sources
        cfg = sources.MAGAZINES['ALARABIYA']
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_ALARABIYA')

    def test_the_registry_entry_resolves_to_this_scraper(self):
        from magazine_tenders.registry import get_magazine_scraper
        self.assertIsInstance(get_magazine_scraper('ALARABIYA'), AlArabiyaScraper)

    def test_scrape_without_a_source_returns_empty(self):
        self.assertEqual(AlArabiyaScraper().scrape(), [])
