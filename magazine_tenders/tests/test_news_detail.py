# magazine_tenders/tests/test_news_detail.py
"""
A News item opens as an article; a tender still opens as a tender.

The two promises worth pinning are opposite ones. The article page must NOT
carry tender furniture — no status pill, no "Tender lifecycle" with an
AEC-match and a deadline, no "source portal" — because an article has none of
those. And the tender page must be untouched: the article template extends
it and overrides named blocks, so a real tender renders exactly as before,
lifecycle and all.

The shared machinery — my notes, team notes, the share modal — is inherited,
so it must be present on BOTH pages and defined in only one file.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender

from magazine_tenders.models import MagazineTenderMeta

IMG = 'https://cdn.example.com/2026/09/site-photo.jpg'


class NewsDetailTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('art-boss', password='pw12345!')
        ae, _ = CountryConfig.objects.get_or_create(
            code='AE', defaults={'name_en': 'United Arab Emirates', 'name_ar': 'الإمارات',
                                 'flag_emoji': '🇦🇪', 'currency': 'AED'})
        qa, _ = CountryConfig.objects.get_or_create(
            code='QA', defaults={'name_en': 'Qatar', 'name_ar': 'قطر',
                                 'flag_emoji': '🇶🇦', 'currency': 'QAR'})

        cls.news_src = ScraperSource.objects.create(
            name='Construction Week', country=ae, is_active=False,
            base_url='https://www.constructionweekonline.com',
            scraper_class='MAGAZINE_CONSTRUCTIONWEEK')
        cls.ash_src = ScraperSource.objects.create(
            name='Ashghal', country=qa, is_active=False,
            base_url='https://www.ashghal.gov.qa',
            scraper_class='MAGAZINE_ASHGHALPROSPECTED')
        cls.portal_src = ScraperSource.objects.create(
            name='Ashghal e-Tenders', country=qa, is_active=True,
            base_url='https://www.ashghal.gov.qa', scraper_class='AshghalScraper')

        def row(src, title, code=None, **extra):
            img = extra.pop('_img', '')
            defaults = dict(
                source=src, country=src.country, external_id=f'art-{title}',
                source_url='https://example.com/a', title=title,
                is_aec=True, is_aec_checked=True, is_relevant=True, status='OPEN')
            defaults.update(extra)
            t = Tender.objects.create(**defaults)
            if code:
                MagazineTenderMeta.objects.create(
                    tender=t, magazine=code, image_url=img,
                    listing_url='https://example.com/list')
            return t

        cls.article = row(cls.news_src, 'SOM unveils Alam Al Roum masterplan',
                          'CONSTRUCTIONWEEK', reference_no='REF-99',
                          description='[DESIGN] Qatari Diar reveals a $600 mn masterplan.',
                          publication_date=date(2026, 7, 21),
                          submission_deadline=date.today() + timedelta(days=20),
                          _img=IMG)
        for n in range(4):
            row(cls.news_src, f'Another Construction Week story {n}', 'CONSTRUCTIONWEEK',
                publication_date=date(2026, 7, 1) + timedelta(days=n))
        cls.rejected = row(cls.news_src, 'Rejected CW story', 'CONSTRUCTIONWEEK',
                           is_aec=False, publication_date=date(2026, 7, 30))
        cls.project = row(cls.ash_src, 'Supervision for a new school', 'ASHGHALPROSPECTED',
                          description='Forthcoming Ashghal project, expected Third Quarter 2026.',
                          entity='Public Works Authority (Ashghal) — Buildings')
        cls.portal = row(cls.portal_src, 'Supervision of deep injection wells',
                         submission_deadline=date.today() + timedelta(days=20))

    def _get(self, t):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('tenders:detail', args=[t.pk]))
        self.assertEqual(resp.status_code, 200)
        return resp

    # ── which page ──────────────────────────────────────────────────────────
    def test_a_news_item_uses_the_article_template(self):
        resp = self._get(self.article)
        self.assertEqual(resp.templates[0].name, 'magazine/detail.html')
        # and the parent is in the chain — inherited, not copied
        self.assertIn('tenders/detail.html', [t.name for t in resp.templates])

    def test_a_tender_still_uses_the_tender_template(self):
        resp = self._get(self.portal)
        self.assertEqual(resp.templates[0].name, 'tenders/detail.html')
        self.assertContains(resp, 'Tender lifecycle')
        self.assertContains(resp, 'Submission deadline')

    # ── no tender furniture on an article ───────────────────────────────────
    def test_the_article_has_no_lifecycle_deadline_or_status(self):
        html = self._get(self.article).content.decode()
        self.assertNotIn('Tender lifecycle', html)
        self.assertNotIn('Submission deadline', html)
        self.assertNotIn('<span class="badge badge-open">Open</span>', html)
        self.assertNotIn('REF-99', html)
        self.assertNotIn('Source Portal', html)

    def test_the_article_reads_on_the_publisher(self):
        html = self._get(self.article).content.decode()
        self.assertIn('Read on Construction Week', html)
        self.assertIn('Back to News', html)

    # ── the hero ────────────────────────────────────────────────────────────
    def test_the_photo_is_the_hero_hotlinked_safely(self):
        html = self._get(self.article).content.decode()
        self.assertIn(f'src="{IMG}"', html)
        self.assertIn('referrerpolicy="no-referrer"', html)
        self.assertIn('class="nd-media ', html)

    def test_the_hero_uses_the_large_rendition_with_srcset(self):
        big = IMG.replace('site-photo', 'site-photo-1536x1536')
        MagazineTenderMeta.objects.filter(tender=self.article).update(image_url_large=big)
        html = self._get(self.article).content.decode()
        self.assertIn(f'src="{big}"', html)
        self.assertIn(f'srcset="{IMG} 768w, {big} 1536w"', html)

    def test_without_a_large_rendition_the_card_image_is_used_alone(self):
        html = self._get(self.article).content.decode()
        self.assertIn(f'src="{IMG}"', html)
        self.assertNotIn('srcset=', html)

    def test_first_seen_is_not_on_the_article(self):
        html = self._get(self.article).content.decode()
        self.assertNotIn('First seen', html)
        self.assertIn('>Published<', html)

    def test_the_standfirst_is_the_lede_without_its_tag(self):
        html = self._get(self.article).content.decode()
        self.assertIn('Qatari Diar reveals a $600 mn masterplan.', html)
        self.assertNotIn('[DESIGN]', html)

    def test_a_forthcoming_project_shows_its_quarter_and_goes_back_to_future(self):
        resp = self._get(self.project)
        self.assertTrue(resp.context['is_forthcoming'])
        html = resp.content.decode()
        self.assertIn('Q3 2026', html)
        self.assertIn('Expected', html)
        self.assertIn('Back to Future Projects', html)
        self.assertIn('?view=future', html)
        self.assertIn('Public Works Authority · Qatar', html)

    # ── more from the same publisher ────────────────────────────────────────
    def test_more_from_source_is_three_others_newest_first_aec_only(self):
        resp = self._get(self.article)
        more = resp.context['more_from_source']
        self.assertEqual(len(more), 3)
        self.assertNotIn(self.article.pk, [m.pk for m in more])
        self.assertNotIn(self.rejected.pk, [m.pk for m in more])
        dates = [m.publication_date for m in more]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertContains(resp, 'More from Construction Week')

    def test_more_from_source_is_absent_when_alone(self):
        resp = self._get(self.project)
        self.assertEqual(resp.context['more_from_source'], [])
        self.assertNotContains(resp, 'More from Ashghal Prospected</span>')

    # ── inherited machinery, on both pages, from one file ───────────────────
    def test_notes_and_team_notes_and_share_are_inherited(self):
        for t in (self.article, self.portal):
            html = self._get(t).content.decode()
            self.assertIn('name="save_notes"', html, t.title)
            self.assertIn('id="team-notes"', html, t.title)
            self.assertIn('id="shareModal"', html, t.title)
            self.assertIn('function openShareModal', html, t.title)

    def test_the_article_template_defines_none_of_that_itself(self):
        import os
        from django.conf import settings
        path = os.path.join(settings.BASE_DIR, 'templates', 'magazine', 'detail.html')
        src = open(path, encoding='utf-8').read()
        for marker in ('save_notes', 'id="team-notes"', 'id="shareModal"',
                       'function openShareModal', 'function toggleBookmark'):
            self.assertNotIn(marker, src, marker)
