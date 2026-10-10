# magazine_tenders/tests/test_addustour.py
"""
Ad-Dustour (addustour.com) — the اقتصاد section.

The third shape of news source: not WordPress and not a JSON API, but plain
server-rendered HTML. The parse is tested offline against the section page as
it was on 21 September 2026.

The Arabic keep-rules are shared with Al-Rai and tested in
test_arabic_rules.py; what these pin is the scraper.
"""
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers.addustour import AdDustourScraper

FIXTURES = Path(__file__).parent / 'fixtures'


def section_html():
    return (FIXTURES / 'addustour_economy.html').read_text(
        encoding='utf-8', errors='replace')


class AdDustourParseTests(TestCase):

    def setUp(self):
        self.scraper = AdDustourScraper()
        self.items = self.scraper.parse_section(section_html())

    def test_the_article_cards_are_found(self):
        self.assertGreaterEqual(len(self.items), 20)

    def test_each_item_has_what_build_one_needs(self):
        for key in ('title', 'summary', 'url', 'published', 'image'):
            self.assertIn(key, self.items[0])

    def test_titles_are_arabic(self):
        title = self.items[0]['title']
        self.assertTrue(any('؀' <= ch <= 'ۿ' for ch in title), title)

    def test_urls_are_absolute(self):
        for item in self.items:
            self.assertTrue(item['url'].startswith('https://www.addustour.com/'),
                            item['url'])

    def test_the_relative_prefix_is_resolved(self):
        # The listing writes ../articles/... - a naive join leaves the ".." in
        # and every link 404s.
        for item in self.items:
            self.assertNotIn('..', item['url'])

    def test_photographs_are_absolute_and_present(self):
        withphoto = [i for i in self.items if i['image']]
        self.assertGreaterEqual(len(withphoto), 20)
        for item in withphoto:
            self.assertTrue(item['image'].startswith('https://www.addustour.com/'),
                            item['image'])

    def test_no_duplicate_articles(self):
        urls = [i['url'] for i in self.items]
        self.assertEqual(len(urls), len(set(urls)))

    def test_navigation_links_are_not_treated_as_articles(self):
        # The page carries a news ticker and a menu using the same /articles/
        # path. A card is a card because it has a picture and a heading.
        for item in self.items:
            self.assertTrue(item['title'].strip())


class AdDustourFilterTests(TestCase):

    def setUp(self):
        self.scraper = AdDustourScraper()
        raw = self.scraper.parse_section(section_html())
        self.kept = self.scraper.build_items(raw)

    def test_the_money_desk_is_mostly_refused(self):
        self.assertLess(len(self.kept), 8, [t.title for t in self.kept])

    def test_nothing_about_the_bourse_is_kept(self):
        for td in self.kept:
            self.assertNotIn('بورصة', td.title)  # بورصة

    def test_kept_items_are_labelled(self):
        for td in self.kept:
            self.assertRegex(td.description, r'^\[(?:TENDER|DESIGN|PROJECT)\]')

    def test_country_is_jordan(self):
        for td in self.kept:
            self.assertEqual(td.country_code, 'JO')

    def test_parsing_writes_nothing_to_the_database(self):
        from tenders.models import Tender
        before = Tender.objects.count()
        self.scraper.build_items(self.scraper.parse_section(section_html()))
        self.assertEqual(Tender.objects.count(), before)


class AdDustourPolitenessTests(TestCase):
    """One request per run, through the rate-limited helper."""

    def test_fetch_goes_through_the_rate_limited_helper(self):
        scraper = AdDustourScraper()
        calls = []

        class FakeResponse:
            text = '<html></html>'

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]
        scraper._fetch_addustour()

        self.assertEqual(len(calls), 1,
                         'the section should cost exactly one request')


class AdDustourConfigurationTests(TestCase):

    def test_registered_in_sources(self):
        from magazine_tenders import sources
        cfg = sources.MAGAZINES['ADDUSTOUR']
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_ADDUSTOUR')
        self.assertEqual(cfg['country_code'], 'JO')

    def test_the_registry_entry_resolves_to_this_scraper(self):
        from magazine_tenders.registry import get_magazine_scraper
        self.assertIsInstance(get_magazine_scraper('ADDUSTOUR'), AdDustourScraper)

    def test_scrape_without_a_source_returns_empty(self):
        self.assertEqual(AdDustourScraper().scrape(), [])


class AdDustourDatelineTests(TestCase):
    """
    The date, which the listing does not carry.

    Without it every Ad-Dustour row stored publication_date=None, and NULL
    sorts last on the News page: the first article this source ever kept
    landed at position 199 of 275, on page 9, which is the same as not being
    published at all. The article page has the dateline the listing lacks -
    «نشر في: الأحد 20 أيلول / سبتمبر 2026» - so kept items go and read it.
    """

    def setUp(self):
        self.scraper = AdDustourScraper()
        self.html = (FIXTURES / 'addustour_article.html').read_text(
            encoding='utf-8', errors='replace')

    def test_the_dateline_is_read_off_the_article_page(self):
        from datetime import date
        self.assertEqual(self.scraper.parse_article_date(self.html),
                         date(2026, 9, 20))

    def test_the_levantine_month_names_are_understood(self):
        # Ad-Dustour prints both systems side by side; either alone must work.
        from datetime import date
        cases = {
            'كانون الثاني': 1, 'شباط': 2, 'آذار': 3, 'نيسان': 4,
            'أيار': 5, 'حزيران': 6, 'تموز': 7, 'آب': 8,
            'أيلول': 9, 'تشرين الأول': 10, 'تشرين الثاني': 11,
            'كانون الأول': 12,
        }
        for name, month in cases.items():
            with self.subTest(month=name):
                html = f'<span class="timeDate_element">نشر في: الأحد 7 {name} 2026. 05:05 مـساءً</span>'
                self.assertEqual(self.scraper.parse_article_date(html),
                                 date(2026, month, 7))

    def test_the_western_month_names_are_understood(self):
        from datetime import date
        html = ('<span class="timeDate_element">نشر في: الأحد 20 سبتمبر 2026.'
                ' 05:05 مـساءً</span>')
        self.assertEqual(self.scraper.parse_article_date(html), date(2026, 9, 20))

    def test_a_page_without_a_dateline_gives_none(self):
        self.assertIsNone(self.scraper.parse_article_date('<html></html>'))

    def test_the_last_modified_line_is_not_mistaken_for_the_dateline(self):
        # The page prints «نشر في» and then «آخر تعديل». The first one wins.
        from datetime import date
        html = ('<span class="timeDate_element">نشر في: الأحد 20 أيلول 2026.</span>'
                '<span class="timeDate_element">آخر تعديل: الإثنين 21 أيلول 2026.</span>')
        self.assertEqual(self.scraper.parse_article_date(html), date(2026, 9, 20))


class AdDustourEnrichmentTests(TestCase):
    """Only the kept items pay for a second request."""

    def test_one_extra_request_per_kept_item_and_no_more(self):
        from datetime import date
        scraper = AdDustourScraper()
        calls = []

        class FakeResponse:
            text = ('<span class="timeDate_element">نشر في: الأحد 20 أيلول '
                    '/ سبتمبر 2026. 05:05 مـساءً</span>')

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]

        raw = scraper.parse_section(section_html())
        kept = scraper.build_items(raw)
        self.assertGreater(len(kept), 0, 'need at least one keep to test this')

        scraper.add_datelines(kept)

        self.assertEqual(len(calls), len(kept),
                         'one request per kept item, not per fetched item')
        for td in kept:
            self.assertEqual(td.publication_date, date(2026, 9, 20))

    def test_a_dead_article_page_does_not_lose_the_item(self):
        scraper = AdDustourScraper()

        def broken(url, **kw):
            raise RuntimeError('read timed out')

        scraper.get = broken
        kept = scraper.build_items(scraper.parse_section(section_html()))
        before = len(kept)
        scraper.add_datelines(kept)

        self.assertEqual(len(kept), before, 'the item must survive a failed fetch')
        self.assertIsNone(kept[0].publication_date)

    def test_build_items_still_makes_no_requests(self):
        scraper = AdDustourScraper()

        def forbidden(url, **kw):
            raise AssertionError('build_items must stay offline')

        scraper.get = forbidden
        scraper.build_items(scraper.parse_section(section_html()))
