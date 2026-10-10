# magazine_tenders/tests/test_news_cards.py
"""
The News wall: one card, one size.

Photo, headline, a summary with its numbers picked out, and the way to the
publisher's article. No source badge, flag, date or Arabic headline on the
card - the card is for scanning, the article is for detail.
"""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender
from tenders.templatetags.tender_extras import focus_numbers

from magazine_tenders.models import MagazineTenderMeta
from magazine_tenders.views import read_url_of


class FocusNumbersFilterTests(TestCase):
    def _marks(self, text):
        import re
        return re.findall(r'<strong class="nf-num">(.*?)</strong>', str(focus_numbers(text)))

    def test_money_with_currency_before_or_after(self):
        self.assertEqual(self._marks('A $1.6bn contract and AED 2.3 billion of works'),
                         ['$1.6bn', 'AED 2.3 billion'])
        self.assertEqual(self._marks('worth 450 million dirhams'), ['450 million dirhams'])

    def test_lengths_areas_capacities_and_counts(self):
        self.assertEqual(self._marks('a 400km rail link, 2.3m sq ft of space, 1,200 homes and 500MW'),
                         ['400km', '2.3m sq ft', '1,200 homes', '500MW'])

    def test_dates_and_shares(self):
        self.assertEqual(self._marks('due in Q1 2027, 35% complete, opening in 2028'),
                         ['Q1 2027', '35%', '2028'])

    def test_ordinals_and_codes_are_left_alone(self):
        self.assertEqual(self._marks('submitted by September 21st, the A350 fleet'), [])
        self.assertEqual(self._marks('September 21, 2026'), ['21', '2026'])

    def test_arabic_indic_digits(self):
        self.assertEqual(self._marks('بكلفة ٢٥٠ مليون دينار'), ['٢٥٠'])

    def test_html_is_escaped_and_entities_are_not_mistaken_for_numbers(self):
        out = str(focus_numbers("<b>it's</b> 3 towers"))
        self.assertNotIn('<b>', out)
        self.assertIn('&lt;b&gt;', out)
        self.assertEqual(self._marks("<b>it's</b> 3 towers"), ['3 towers'])

    def test_no_numbers_no_marks_and_empty_is_empty(self):
        self.assertEqual(self._marks('A consultancy was appointed.'), [])
        self.assertIn('appointed', str(focus_numbers('A consultancy was appointed.')))
        self.assertEqual(focus_numbers(''), '')
        self.assertEqual(focus_numbers(None), '')


class TheCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = User.objects.create_superuser('card-reader', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        cls.src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, desc, listing='', title_ar=''):
            t = Tender.objects.create(
                source=cls.src, country=jo, external_id=f'card-{title}',
                source_url='https://example-magazine.com/fallback', title=title,
                title_ar=title_ar, description=desc, is_aec=True,
                is_aec_checked=True, is_relevant=True, status='OPEN',
                publication_date=date(2026, 9, 2))
            MagazineTenderMeta.objects.create(
                tender=t, magazine='TESTMAG', listing_url=listing,
                image_url='https://cdn.example.com/p.jpg')
            return t

        cls.story = row('A $1.6bn contract for 400km of rail',
                        '[PROJECT] The 400km line will cost $1.6bn and open in Q1 2027.',
                        listing='https://example-magazine.com/rail-story',
                        title_ar='عقد سكة حديد')
        cls.bare = row('A story with no summary', '[PROJECT]',
                       listing='https://example-magazine.com/bare')

    def _page(self):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list') + '?view=news')
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_every_item_is_a_card_and_nothing_is_a_lead(self):
        html = self._page().content.decode()
        self.assertEqual(html.count('<article class="nf-card"'), 2)
        self.assertNotIn('nf-lead', html)
        self.assertNotIn('nf-side', html)

    def test_the_card_reads_to_the_publisher(self):
        html = self._page().content.decode()
        self.assertIn('href="https://example-magazine.com/rail-story" target="_blank"', html)
        # the registry's display name; an unregistered code shows as itself
        self.assertIn('Read more at TESTMAG', html)

    def test_the_summary_has_its_numbers_picked_out(self):
        html = self._page().content.decode()
        for figure in ('400km', '$1.6bn', 'Q1 2027'):
            self.assertIn(f'<strong class="nf-num">{figure}</strong>', html)
        self.assertNotIn('[PROJECT]', html)

    def test_no_details_on_the_card(self):
        html = self._page().content.decode()
        self.assertNotIn('class="nf-src"', html)       # no source badge
        self.assertNotIn('class="nf-meta"', html)      # no meta line
        self.assertNotIn('عقد سكة حديد', html)         # no Arabic repeat of the headline

    def test_the_card_shows_the_publication_date(self):
        html = self._page().content.decode()
        self.assertIn('class="nf-date"', html)
        self.assertIn('02 Sep 2026', html)

    def test_a_story_without_a_summary_says_so(self):
        html = self._page().content.decode()
        self.assertIn('The publisher gave no summary', html)

    def test_read_url_falls_back_to_the_article_page(self):
        t = Tender.objects.create(
            source=self.src, country=self.src.country, external_id='card-nourl',
            source_url='', title='No address at all', is_aec=True,
            is_aec_checked=True, is_relevant=True, status='OPEN')
        MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
        url, external = read_url_of(t)
        self.assertEqual(url, reverse('tenders:detail', args=[t.pk]))
        self.assertFalse(external)
        self.assertEqual(read_url_of(self.story),
                         ('https://example-magazine.com/rail-story', True))
