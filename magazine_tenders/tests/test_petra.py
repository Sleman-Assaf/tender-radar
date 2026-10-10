# magazine_tenders/tests/test_petra.py
"""
Petra (petra.gov.jo) — the Jordan News Agency.

The state wire, and the fifth shape of news source. Two things make it
different from the three newspapers:

* A CARD IS TWO ANCHORS. The picture and the headline are separate <a>
  elements pointing at the same article, so a parser that reads one anchor
  per card gets either a title with no photograph or a photograph with no
  title. They are merged on the href.

* THE DATE IS IN THE PHOTO. Petra serves images from
  /storage/media/YYYY/MM/DD/<uuid>.jpg, so the publication date is already
  in the listing. This source costs ONE request per section and none at all
  for datelines — unlike Ad-Dustour and Al-Ghad, which must open each kept
  article to find out when it ran.

Captured 22 September 2026.
"""
from datetime import date
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers.petra import PetraScraper

FIXTURES = Path(__file__).parent / 'fixtures'


def economy_html():
    return (FIXTURES / 'petra_economy.html').read_text(
        encoding='utf-8', errors='replace')


def governorates_html():
    return (FIXTURES / 'petra_governorates.html').read_text(
        encoding='utf-8', errors='replace')


class PetraParseTests(TestCase):

    def setUp(self):
        self.scraper = PetraScraper()
        self.items = self.scraper.parse_section(economy_html())

    def test_the_article_cards_are_found(self):
        self.assertGreaterEqual(len(self.items), 10)

    def test_each_item_has_what_build_one_needs(self):
        for key in ('title', 'summary', 'url', 'published', 'image'):
            self.assertIn(key, self.items[0])

    def test_every_item_has_a_headline(self):
        # The picture anchor carries no text. If the merge fails, half the
        # items come through titleless.
        for item in self.items:
            self.assertTrue(item['title'].strip(), item['url'])

    def test_titles_are_arabic(self):
        for item in self.items:
            self.assertTrue(any('؀' <= ch <= 'ۿ' for ch in item['title']),
                            item['title'])

    def test_urls_are_absolute_and_encoded(self):
        for item in self.items:
            self.assertTrue(item['url'].startswith('https://petra.gov.jo/ar/news/'),
                            item['url'])
            self.assertNotIn(' ', item['url'])

    def test_no_duplicate_articles(self):
        urls = [i['url'] for i in self.items]
        self.assertEqual(len(urls), len(set(urls)))

    def test_the_breaking_news_ticker_is_not_read_as_articles(self):
        # The page opens with a ticker of royal and diplomatic headlines
        # carrying the same /ar/news/ links as the cards. It is excluded by
        # its class, not by whether it has a picture: the listing attaches a
        # photograph inconsistently — the same article carries one under
        # محليات and none under محافظات — so a photo test would throw away
        # real articles and keep no ticker lines out.
        for item in self.items:
            self.assertTrue(item['title'].strip())
        ticker = PetraScraper().parse_section(
            '<a class="urgent-ticker-lines" href="/ar/news/x">'
            'الملك يلتقي</a>')
        self.assertEqual(ticker, [])


class PetraDateTests(TestCase):
    """The date comes free, out of the photograph's path."""

    def setUp(self):
        self.scraper = PetraScraper()

    def test_the_date_is_read_from_the_media_path(self):
        self.assertEqual(
            self.scraper.date_from_image(
                'https://petra.gov.jo//storage/media/2026/09/21/abc-def.jpg'),
            date(2026, 9, 21))

    def test_an_image_with_no_date_gives_none(self):
        self.assertIsNone(self.scraper.date_from_image('/images/Light.svg'))
        self.assertIsNone(self.scraper.date_from_image(''))

    def test_an_impossible_date_gives_none(self):
        self.assertIsNone(self.scraper.date_from_image(
            'https://petra.gov.jo/storage/media/2026/13/45/x.jpg'))

    def test_the_listing_items_carry_their_date(self):
        items = self.scraper.parse_section(economy_html())
        dated = [i for i in items if i['published'] is not None]
        self.assertGreaterEqual(len(dated), 8)
        for i in dated:
            self.assertIsInstance(i['published'], date)

    def test_parsing_never_fetches_anything(self):
        scraper = PetraScraper()

        def forbidden(url, **kw):
            raise AssertionError('parsing must not fetch anything')

        scraper.get = forbidden
        scraper.build_items(scraper.parse_section(economy_html()))


class PetraEnrichmentTests(TestCase):
    """
    Filling the gaps, for kept items only.

    Most cards carry their photograph and therefore their date. The ones
    that do not are not lost — the article page has an og:image on the same
    dated path — but only the items that SURVIVED the filter pay for that
    second request, which is one or two a run rather than seventy.
    """

    ARTICLE = ('<meta property="og:image" content="https://petra.gov.jo//storage'
               '/media/2026/09/21/f478a965-850f-469b-9dec-91821238651c.jpg">')

    def test_a_missing_photo_and_date_are_filled_from_the_article(self):
        scraper = PetraScraper()
        calls = []
        article = self.ARTICLE

        class FakeResponse:
            text = article

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]

        items = scraper.build_items([{
            'guid': 'u', 'title': 'اتفاقية لإنشاء مستشفى ميراس بسعة 60 سريرا في الزرقاء',
            'summary': '', 'body': '', 'url': 'https://petra.gov.jo/ar/news/x',
            'published': None, 'image': '', 'image_large': '',
        }])
        self.assertEqual(len(items), 1, 'the hospital should survive the filter')

        scraper.add_photos_and_dates(items)

        self.assertEqual(len(calls), 1)
        self.assertEqual(items[0].publication_date, date(2026, 9, 21))
        self.assertIn('/storage/media/2026/09/21/', items[0].image_url)

    def test_an_item_that_already_has_both_costs_nothing(self):
        scraper = PetraScraper()
        calls = []
        scraper.get = lambda url, **kw: calls.append(url)

        items = scraper.build_items(scraper.parse_section(economy_html()))
        complete = [i for i in items
                    if i.publication_date and getattr(i, 'image_url', '')]
        scraper.add_photos_and_dates(complete)

        self.assertEqual(calls, [], 'nothing to fetch when the listing gave both')

    def test_a_dead_article_page_does_not_lose_the_item(self):
        scraper = PetraScraper()

        def broken(url, **kw):
            raise RuntimeError('read timed out')

        scraper.get = broken
        items = scraper.build_items([{
            'guid': 'u', 'title': 'اتفاقية لإنشاء مستشفى ميراس بسعة 60 سريرا في الزرقاء',
            'summary': '', 'body': '', 'url': 'https://petra.gov.jo/ar/news/x',
            'published': None, 'image': '', 'image_large': '',
        }])
        before = len(items)
        scraper.add_photos_and_dates(items)
        self.assertEqual(len(items), before)


class PetraFilterTests(TestCase):

    def setUp(self):
        self.scraper = PetraScraper()
        raw = (self.scraper.parse_section(economy_html())
               + self.scraper.parse_section(governorates_html()))
        self.kept = self.scraper.build_items(raw)

    def test_the_wire_is_mostly_refused(self):
        self.assertLess(len(self.kept), 8, [t.title for t in self.kept])

    def test_kept_items_are_labelled(self):
        for td in self.kept:
            self.assertRegex(td.description, r'^\[(?:TENDER|DESIGN|PROJECT)\]')

    def test_country_is_jordan(self):
        for td in self.kept:
            self.assertEqual(td.country_code, 'JO')

    def test_parsing_writes_nothing_to_the_database(self):
        from tenders.models import Tender
        before = Tender.objects.count()
        self.scraper.build_items(self.scraper.parse_section(economy_html()))
        self.assertEqual(Tender.objects.count(), before)


class PetraHospitalTests(TestCase):
    """
    The article that prompted this source.

        اتفاقية لإنشاء مستشفى ميراس بسعة 60 سريرا في الزرقاء

    An agreement to build a 60-bed hospital. It is filed under محافظات, not
    اقتصاد, which is why that section is read too — the economy desk alone
    would have missed the one story the reader pointed at.
    """

    def test_the_hospital_agreement_is_kept(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(why_keep(
            'اتفاقية لإنشاء '
            'مستشفى ميراس بسعة '
            '60 سريرا في الزرقاء'),
            'PROJECT')

    def test_the_governorates_section_is_read(self):
        self.assertIn('governorates',
                      ' '.join(PetraScraper.SECTION_URLS))


class PetraPolitenessTests(TestCase):
    """One request per section, through the rate-limited helper."""

    def test_one_request_per_section_and_no_more(self):
        scraper = PetraScraper()
        calls = []

        class FakeResponse:
            text = '<html></html>'

            def raise_for_status(self):
                pass

        scraper.get = lambda url, **kw: (calls.append(url), FakeResponse())[1]
        scraper._fetch_petra()

        self.assertEqual(len(calls), len(PetraScraper.SECTION_URLS))

    def test_a_dead_section_does_not_lose_the_others(self):
        scraper = PetraScraper()
        seen = []

        def half_broken(url, **kw):
            seen.append(url)
            raise RuntimeError('read timed out')

        scraper.get = half_broken
        out = scraper._fetch_petra()

        self.assertEqual(len(seen), len(PetraScraper.SECTION_URLS),
                         'every section should still be attempted')
        self.assertEqual(out, [])


class PetraConfigurationTests(TestCase):

    def test_registered_in_sources(self):
        from magazine_tenders import sources
        cfg = sources.MAGAZINES['PETRA']
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_PETRA')
        self.assertEqual(cfg['country_code'], 'JO')

    def test_the_registry_entry_resolves_to_this_scraper(self):
        from magazine_tenders.registry import get_magazine_scraper
        self.assertIsInstance(get_magazine_scraper('PETRA'), PetraScraper)

    def test_scrape_without_a_source_returns_empty(self):
        self.assertEqual(PetraScraper().scrape(), [])
