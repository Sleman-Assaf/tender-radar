# magazine_tenders/tests/test_alghad.py
"""
Al-Ghad (alghad.com) — the اقتصاد category.

The fourth shape of news source, after WordPress REST (the trade press), a
widget API (Al-Rai) and plain HTML (Ad-Dustour). Al-Ghad is an ASP.NET site
whose article links carry the section in the path — /Section-181/اقتصاد/… —
and that path is the only thing separating an economy story from the sport,
caricature and world-news cards the same page promotes down the side.

The Arabic keep-rules are shared and tested in test_arabic_rules.py; what
these pin is the scraper. Captured 21 September 2026.
"""
from datetime import date
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers.alghad import AlGhadScraper

FIXTURES = Path(__file__).parent / 'fixtures'


def section_html():
    return (FIXTURES / 'alghad_economy.html').read_text(
        encoding='utf-8', errors='replace')


class AlGhadParseTests(TestCase):

    def setUp(self):
        self.scraper = AlGhadScraper()
        self.items = self.scraper.parse_section(section_html())

    def test_the_article_cards_are_found(self):
        self.assertGreaterEqual(len(self.items), 20)

    def test_each_item_has_what_build_one_needs(self):
        for key in ('title', 'summary', 'url', 'published', 'image'):
            self.assertIn(key, self.items[0])

    def test_titles_are_arabic(self):
        for item in self.items:
            self.assertTrue(any('؀' <= ch <= 'ۿ' for ch in item['title']),
                            item['title'])

    def test_urls_are_absolute(self):
        for item in self.items:
            self.assertTrue(item['url'].startswith('https://alghad.com/'),
                            item['url'])

    def test_every_card_comes_from_an_economy_section(self):
        for item in self.items:
            self.assertRegex(item['url'], r'/Section-(?:181|210|146|145)/',
                             item['url'])

    def test_the_sport_and_caricature_cards_are_not_read(self):
        # The economy page promotes other desks down the side. Those cards
        # look exactly like economy ones; the section in the path is what
        # tells them apart.
        joined = ' '.join(i['url'] for i in self.items)
        for section in ('/Section-180/', '/Section-200/', '/Section-226/',
                        '/Section-162/', '/Section-171/'):
            self.assertNotIn(section, joined)

    def test_the_sister_paper_promo_is_not_an_article(self):
        for item in self.items:
            self.assertNotIn('jordannews.jo', item['url'])

    def test_photographs_are_absolute_and_present(self):
        withphoto = [i for i in self.items if i['image']]
        self.assertGreaterEqual(len(withphoto), 20)
        for item in withphoto:
            self.assertTrue(item['image'].startswith('http'), item['image'])

    def test_no_duplicate_articles(self):
        urls = [i['url'] for i in self.items]
        self.assertEqual(len(urls), len(set(urls)))


class AlGhadFilterTests(TestCase):

    def setUp(self):
        self.scraper = AlGhadScraper()
        self.kept = self.scraper.build_items(
            self.scraper.parse_section(section_html()))

    def test_the_money_desk_is_mostly_refused(self):
        self.assertLess(len(self.kept), 8, [t.title for t in self.kept])

    def test_nothing_about_the_bourse_is_kept(self):
        for td in self.kept:
            self.assertNotIn('بورصة', td.title)

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


class AlGhadDatelineTests(TestCase):
    """
    The date, which the listing does not carry on every card.

    Same lesson Ad-Dustour taught: publication_date=None sorts last on the
    News page, so an undated row is published nine pages down. The article
    page prints «الإثنين 21 أيلول 2026» in .story-date, and only the kept
    items go and read it.
    """

    def setUp(self):
        self.scraper = AlGhadScraper()
        self.html = (FIXTURES / 'alghad_article.html').read_text(
            encoding='utf-8', errors='replace')

    def test_the_dateline_is_read_off_the_article_page(self):
        self.assertEqual(self.scraper.parse_article_date(self.html),
                         date(2026, 9, 21))

    def test_a_page_without_a_dateline_gives_none(self):
        self.assertIsNone(self.scraper.parse_article_date('<html></html>'))

    def test_the_shared_month_table_is_used(self):
        # Al-Ghad writes the Levantine names; Ad-Dustour writes both. One
        # table serves them, so a month fixed for one is fixed for both.
        html = ('<span class="story-date">الإثنين 3 '
                'كانون الثاني 2027</span>')
        self.assertEqual(self.scraper.parse_article_date(html),
                         date(2027, 1, 3))


class AlGhadEnrichmentTests(TestCase):
    """Only the kept items pay for a second request."""

    DATELINE = ('<span class="story-date">الإثنين 21 '
                'أيلول 2026</span>')

    def test_one_extra_request_per_kept_item_and_no_more(self):
        scraper = AlGhadScraper()
        calls = []
        dateline = self.DATELINE

        class FakeResponse:
            text = dateline

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]
        kept = scraper.build_items(scraper.parse_section(section_html()))
        self.assertGreater(len(kept), 0, 'need at least one keep to test this')

        scraper.add_datelines(kept)

        self.assertEqual(len(calls), len(kept))
        for td in kept:
            self.assertEqual(td.publication_date, date(2026, 9, 21))

    def test_a_dead_article_page_does_not_lose_the_item(self):
        scraper = AlGhadScraper()

        def broken(url, **kw):
            raise RuntimeError('read timed out')

        scraper.get = broken
        kept = scraper.build_items(scraper.parse_section(section_html()))
        before = len(kept)
        scraper.add_datelines(kept)
        self.assertEqual(len(kept), before)

    def test_build_items_makes_no_requests(self):
        scraper = AlGhadScraper()

        def forbidden(url, **kw):
            raise AssertionError('build_items must stay offline')

        scraper.get = forbidden
        scraper.build_items(scraper.parse_section(section_html()))


class AlGhadPolitenessTests(TestCase):
    """One request per run for the listing, through the rate-limited helper."""

    def test_fetch_goes_through_the_rate_limited_helper(self):
        scraper = AlGhadScraper()
        calls = []

        class FakeResponse:
            text = '<html></html>'

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]
        scraper._fetch_alghad()

        self.assertEqual(len(calls), 1,
                         'the section should cost exactly one request')


class AlGhadConfigurationTests(TestCase):

    def test_registered_in_sources(self):
        from magazine_tenders import sources
        cfg = sources.MAGAZINES['ALGHAD']
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_ALGHAD')
        self.assertEqual(cfg['country_code'], 'JO')

    def test_the_registry_entry_resolves_to_this_scraper(self):
        from magazine_tenders.registry import get_magazine_scraper
        self.assertIsInstance(get_magazine_scraper('ALGHAD'), AlGhadScraper)

    def test_scrape_without_a_source_returns_empty(self):
        self.assertEqual(AlGhadScraper().scrape(), [])


class AlGhadPhotographTests(TestCase):
    """
    The photograph, which is NOT in the <img> tag.

    Al-Ghad lazy-loads: the <img src> is a placeholder, /App_Images/default.jpg,
    on 54 of the 62 cards on the page, and the real picture sits in a
    <picture><source data-srcset>. Reading src first stored the placeholder,
    so the first Al-Ghad article on the News page appeared with no photo.
    """

    PLACEHOLDER = 'App_Images/default.jpg'

    def setUp(self):
        self.items = AlGhadScraper().parse_section(section_html())

    def test_no_item_stores_the_lazy_load_placeholder(self):
        for item in self.items:
            self.assertNotIn(self.PLACEHOLDER, item['image'], item['title'])
            self.assertNotIn(self.PLACEHOLDER, item['image_large'], item['title'])

    def test_the_real_photograph_is_taken_from_the_srcset(self):
        for item in self.items:
            self.assertTrue(item['image'].startswith('http'), item['title'])

    def test_the_largest_offered_size_is_chosen(self):
        # The card offers the same picture at 228x129 up to 511x289. The
        # biggest is the one worth storing - the News page leads on a
        # full-width featured card.
        hotel = [i for i in self.items
                 if '\u0641\u0646\u062f\u0642' in i['title']]
        if hotel:
            self.assertIn('511x289', hotel[0]['image'], hotel[0]['image'])

    def test_a_card_with_a_real_src_and_no_srcset_still_works(self):
        html = ('<div class="block-card"><a href="/Section-181/x/y">'
                '<h3>\u0639\u0646\u0648\u0627\u0646</h3>'
                '<img src="https://imgy.pro/alghad/438x247/1.jpg"></a></div>')
        items = AlGhadScraper().parse_section(html)
        self.assertEqual(items[0]['image'],
                         'https://imgy.pro/alghad/438x247/1.jpg')

    def test_a_card_with_no_picture_at_all_is_still_read(self):
        html = ('<div class="block-card"><a href="/Section-181/x/y">'
                '<h3>\u0639\u0646\u0648\u0627\u0646</h3></a></div>')
        items = AlGhadScraper().parse_section(html)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['image'], '')
