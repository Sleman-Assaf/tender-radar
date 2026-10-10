# magazine_tenders/tests/test_gulfconstruction.py
"""
Gulf Construction scraper, parsed offline from the saved August 2026 issue.

No network: the fixture is the contract. When the site's markup changes these
tests fail, which is the signal to re-save the fixture and fix the selector —
a scraper test that hits the live site would instead fail for reasons that have
nothing to do with the code.
"""
from datetime import date
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers.gulfconstruction import GulfConstructionScraper

FIXTURES = Path(__file__).parent / 'fixtures'
ARTICLE = (FIXTURES / 'gulfconstruction_article.html').read_text(
    encoding='utf-8', errors='replace')
SECTION = (FIXTURES / 'gulfconstruction_section.html').read_text(
    encoding='utf-8', errors='replace')

ARTICLE_URL = 'https://gulfconstructiononline.com/Article/465769/Tenders'


class DiscoveryTests(TestCase):
    def test_finds_the_current_issue_article(self):
        url = GulfConstructionScraper().find_article_url(SECTION)
        self.assertIsNotNone(url)
        self.assertTrue(url.startswith('https://gulfconstructiononline.com/Article/'))

    def test_missing_link_returns_none_rather_than_raising(self):
        url = GulfConstructionScraper().find_article_url('<html><body></body></html>')
        self.assertIsNone(url)


class ParseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.items = GulfConstructionScraper().parse_article(ARTICLE, ARTICLE_URL)

    def test_parses_the_whole_column(self):
        # The August 2026 issue carries 67 notices. A big drop here means the
        # item selector or the "Tender No" marker changed.
        self.assertGreaterEqual(len(self.items), 60)

    def test_every_item_has_a_title(self):
        self.assertTrue(all(i.title.strip() for i in self.items))

    def test_reference_numbers_are_extracted(self):
        with_ref = [i for i in self.items if i.reference_no.strip()]
        self.assertGreaterEqual(len(with_ref), int(len(self.items) * 0.9))

    def test_countries_are_mapped_to_country_codes(self):
        codes = {i.country_code for i in self.items}
        # The column groups by Gulf country; at least a few must be resolved,
        # and nothing may be left blank.
        self.assertTrue(codes.issubset({'BH', 'KW', 'OM', 'QA', 'SA', 'AE'}), codes)
        self.assertGreaterEqual(len(codes), 3)

    def test_publication_date_is_the_issue_date(self):
        dates = {i.publication_date for i in self.items if i.publication_date}
        self.assertEqual(len(dates), 1, 'one issue → one publication date')
        self.assertIsInstance(next(iter(dates)), date)

    def test_deadlines_are_parsed_and_get_a_year(self):
        withdl = [i for i in self.items if i.submission_deadline]
        self.assertGreaterEqual(len(withdl), int(len(self.items) * 0.8))
        issue_year = next(i.publication_date.year for i in self.items
                          if i.publication_date)
        for i in withdl:
            # The source omits the year; it must be inferred to the issue year
            # or the one after it, never left at some default.
            self.assertIn(i.submission_deadline.year, (issue_year, issue_year + 1))

    def test_status_reflects_the_deadline(self):
        today = date.today()
        for i in self.items:
            if i.submission_deadline and i.submission_deadline < today:
                self.assertEqual(i.status, 'CLOSED')
            else:
                self.assertEqual(i.status, 'OPEN')

    def test_external_id_is_stable_across_two_parses(self):
        again = GulfConstructionScraper().parse_article(ARTICLE, ARTICLE_URL)
        self.assertEqual([i.external_id for i in self.items],
                         [i.external_id for i in again])

    def test_scraper_writes_nothing_to_the_database(self):
        # Persistence belongs to the runner. Parsing must not create tenders.
        from tenders.models import Tender
        before = Tender.objects.count()
        GulfConstructionScraper().parse_article(ARTICLE, ARTICLE_URL)
        self.assertEqual(Tender.objects.count(), before)

    def test_country_headers_are_not_returned_as_tenders(self):
        titles = {i.title.strip().upper() for i in self.items}
        for header in ('BAHRAIN', 'KUWAIT', 'OMAN', 'QATAR', 'SAUDI ARABIA', 'UAE'):
            self.assertNotIn(header, titles)


class HelperTests(TestCase):
    def test_entity_takes_the_rightmost_for_phrase(self):
        entity = GulfConstructionScraper._entity(
            'Construction of access roads upgrade for Ministry of Works')
        self.assertEqual(entity, 'Ministry of Works')

    def test_entity_absent_returns_empty(self):
        self.assertEqual(GulfConstructionScraper._entity('Supply of pipes'), '')

    def test_closing_date_uses_the_issue_year(self):
        got = GulfConstructionScraper._closing_date('… CD August 12',
                                                    date(2026, 8, 1))
        self.assertEqual(got, date(2026, 8, 12))

    def test_closing_date_rolls_into_next_year(self):
        # A December issue listing a January closing date means next January.
        got = GulfConstructionScraper._closing_date('… CD January 15',
                                                    date(2026, 12, 1))
        self.assertEqual(got, date(2027, 1, 15))

    def test_closing_date_absent_returns_none(self):
        self.assertIsNone(
            GulfConstructionScraper._closing_date('no closing date here',
                                                  date(2026, 8, 1)))

    def test_impossible_closing_date_returns_none(self):
        self.assertIsNone(
            GulfConstructionScraper._closing_date('CD February 31',
                                                  date(2026, 2, 1)))


class NoSourceTests(TestCase):
    def test_scrape_without_a_source_returns_empty(self):
        # The runner always sets .source; if it is missing the scraper must
        # decline rather than reach for an is_active=True row that cannot exist.
        self.assertEqual(GulfConstructionScraper().scrape(), [])
