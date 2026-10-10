# magazine_tenders/tests/test_news_photos.py
"""
The photo-led News page.

Three promises, and the third is the one that matters most.

The image URL is read out of the WordPress response that we already fetch —
one query parameter, no extra request per article. Get the size preference
wrong and every visit hotlinks a 4 MB original.

The layout is chosen by the view, not the template: the magazine layout on
every public tab, the compact table only on the audit page. Future Projects
has no photos — the Ashghal list carries none — so its blocks show the
expected quarter, which is the fact a pipeline reader wants anyway.

And a missing photo is a NORMAL case with a designed answer, never an error
and never a broken-image icon. Roughly a fifth of News items have none.
"""
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
import re

from django.test import TestCase
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender

from magazine_tenders import runner, sources
from magazine_tenders.models import MagazineTenderMeta
from magazine_tenders.scrapers import _newsfeed
from magazine_tenders.scrapers._newsfeed import (NewsFeedMagazineScraper,
                                                 looks_like_stock,
                                                 wp_featured_image)
from magazine_tenders.tests.test_runner import (AEC_ENRICH, TEST_MAGAZINES,
                                                _FakeScraper, item)
from magazine_tenders.views import (expected_quarter,
                                    standfirst_of)

IMG = 'https://cdn.example.com/2026/09/site-photo.jpg'


def _post(sizes=None, top_url=IMG, embedded=True):
    """A WordPress post as ?_embed returns it."""
    media = {'source_url': top_url}
    if sizes is not None:
        media['media_details'] = {'sizes': {k: {'source_url': v}
                                            for k, v in sizes.items()}}
    p = {'title': {'rendered': 'Design contract awarded'},
         'excerpt': {'rendered': 'A standfirst.'},
         'content': {'rendered': 'Body.'},
         'link': 'https://example.com/a', 'date': '2026-09-02T08:00:00'}
    if embedded:
        p['_embedded'] = {'wp:featuredmedia': [media]}
    return p


class FeaturedImageExtractionTests(TestCase):
    def test_prefers_the_card_sized_rendition(self):
        # ~768px, the right size for a 400px card. Not the original.
        p = _post(sizes={'full': 'https://x/full.jpg',
                         'medium_large': 'https://x/ml.jpg',
                         'thumbnail': 'https://x/t.jpg'})
        self.assertEqual(wp_featured_image(p), 'https://x/ml.jpg')

    def test_falls_through_the_size_chain(self):
        self.assertEqual(wp_featured_image(_post(sizes={'medium': 'https://x/m.jpg'})),
                         'https://x/m.jpg')
        self.assertEqual(wp_featured_image(_post(sizes={'large': 'https://x/l.jpg',
                                                        'medium': 'https://x/m.jpg'})),
                         'https://x/l.jpg')

    def test_the_hero_asks_for_a_large_rendition(self):
        from magazine_tenders.scrapers._newsfeed import _WP_HERO_SIZES
        p = _post(sizes={'medium_large': 'https://x/ml.jpg', 'large': 'https://x/l.jpg',
                         '1536x1536': 'https://x/1536.jpg', 'full': 'https://x/full.jpg'})
        self.assertEqual(wp_featured_image(p, _WP_HERO_SIZES), 'https://x/1536.jpg')
        p = _post(sizes={'medium_large': 'https://x/ml.jpg', 'full': 'https://x/full.jpg'})
        self.assertEqual(wp_featured_image(p, _WP_HERO_SIZES), 'https://x/full.jpg')
        # the card rendition is untouched by the second preference list
        self.assertEqual(wp_featured_image(p), 'https://x/ml.jpg')

    def test_the_original_is_the_last_resort(self):
        self.assertEqual(wp_featured_image(_post(sizes={})), IMG)
        self.assertEqual(wp_featured_image(_post()), IMG)

    def test_no_photo_is_an_empty_string_not_an_error(self):
        self.assertEqual(wp_featured_image(_post(embedded=False)), '')
        self.assertEqual(wp_featured_image({'_embedded': {'wp:featuredmedia': []}}), '')
        self.assertEqual(wp_featured_image({}), '')

    def test_a_malformed_media_record_is_tolerated(self):
        # WordPress returns an error OBJECT in place of the media when the
        # attachment was deleted: {"code": "rest_forbidden", ...} as a list
        # of one string in some installs. Neither may crash a sweep.
        self.assertEqual(wp_featured_image({'_embedded': {'wp:featuredmedia': ['oops']}}), '')
        self.assertEqual(wp_featured_image({'_embedded': {'wp:featuredmedia': [None]}}), '')


class FeedCarriesTheImageTests(TestCase):
    class _Probe(NewsFeedMagazineScraper):
        CODE = 'PROBE'
        BASE_URL = 'https://example.test'
        FEED_MODE = 'wp_rest'
        WP_QUERIES = [{'categories': '1'}]

    def test_the_fetch_asks_for_embedded_media(self):
        probe = self._Probe()
        with mock.patch.object(self._Probe, '_client_get', return_value='[]') as m:
            probe._fetch_wp()
        self.assertEqual(m.call_args.kwargs['params'].get('_embed'), 'wp:featuredmedia')
        # and the slow-host ceiling, not the portal default
        self.assertGreaterEqual(m.call_args.kwargs['timeout'], 90)

    def test_a_wp_post_yields_an_image(self):
        raw = self._Probe()._from_wp_post(_post(sizes={'medium_large': 'https://x/ml.jpg'}))
        self.assertEqual(raw['image'], 'https://x/ml.jpg')

    def test_the_item_carries_it_to_the_runner(self):
        probe = self._Probe()
        with mock.patch.object(_newsfeed, 'why_keep', return_value='DESIGN'):
            td = probe.build_one({'title': 'Design contract for a new hospital',
                                  'summary': 'Standfirst.', 'body': '',
                                  'url': 'https://example.test/a',
                                  'published': date(2026, 9, 2),
                                  'image': IMG})
        self.assertEqual(td.image_url, IMG)

    def test_an_item_without_an_image_carries_an_empty_string(self):
        probe = self._Probe()
        with mock.patch.object(_newsfeed, 'why_keep', return_value='DESIGN'):
            td = probe.build_one({'title': 'Design contract', 'summary': '',
                                  'body': '', 'url': 'https://example.test/b',
                                  'published': None})
        self.assertEqual(td.image_url, '')

    def test_a_sitemap_image_is_read_when_present(self):
        class _Sitemap(NewsFeedMagazineScraper):
            CODE = 'SM'
            BASE_URL = 'https://example.test'
            FEED_MODE = 'sitemap'
            SITEMAP_URLS = ['https://example.test/sitemap.xml']

        xml = '''<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>https://example.test/story</loc>
    <news:news><news:title>A story</news:title>
      <news:publication_date>2026-09-02</news:publication_date></news:news>
    <image:image><image:loc>https://example.test/pic.jpg</image:loc></image:image>
  </url>
</urlset>'''
        with mock.patch.object(_Sitemap, '_client_get', return_value=xml):
            items = _Sitemap()._fetch_sitemap()
        self.assertEqual(items[0]['image'], 'https://example.test/pic.jpg')


class StockPhotoFilterTests(TestCase):
    """A library frame is a picture of a city, not of the project."""

    def test_agency_names_in_the_filename_are_caught(self):
        for name in ('534df4aa1f49-gettyimages-2265176591-1.jpg',
                     'charles-adrien-fournier-lDxs85UO7lU-unsplash-768x512.jpg',
                     'pexels-sirokad-37687676-768x512.jpg',
                     'shutterstock_333291023-768x511.jpg',
                     'iStock-1234567.jpg'):
            self.assertTrue(looks_like_stock(f'https://x/uploads/2026/09/{name}'), name)

    def test_a_project_render_is_not(self):
        for name in ('Azizi-Florence.jpg', '51IkYs9u-1_Makkah-768x511.jpg',
                     'Screenshot-2026-04-16-094558-768x510.png', ''):
            self.assertFalse(looks_like_stock(f'https://x/{name}'), name)

    def test_the_path_is_ignored_only_the_filename_counts(self):
        # A site literally named "stockholm-…" in its path must not trip it.
        self.assertFalse(looks_like_stock('https://stockholm.example/img/tower.jpg'))

    def test_a_stock_image_is_not_stored_on_the_item(self):
        class _Probe(NewsFeedMagazineScraper):
            CODE = 'PROBE'
            BASE_URL = 'https://example.test'
        with mock.patch.object(_newsfeed, 'why_keep', return_value='DESIGN'):
            td = _Probe().build_one({'title': 'Design contract', 'summary': '',
                                     'body': '', 'url': 'https://example.test/a',
                                     'published': None,
                                     'image': 'https://x/shutterstock_1.jpg'})
        self.assertEqual(td.image_url, '')


class SitemapImageUrlTests(TestCase):
    def test_a_bare_path_is_resolved_against_the_site(self):
        class _Sitemap(NewsFeedMagazineScraper):
            CODE = 'SM'
            BASE_URL = 'https://www.zawya.com'
            FEED_MODE = 'sitemap'
            SITEMAP_URLS = ['https://www.zawya.com/sitemaps/en/sitemap-news.xml']

        xml = '''<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>https://www.zawya.com/en/story</loc>
    <news:news><news:title>A story</news:title>
      <news:publication_date>2026-09-09</news:publication_date></news:news>
    <image:image><image:loc>/prod/images/original/abc-photo.jpg</image:loc></image:image>
  </url>
</urlset>'''
        with mock.patch.object(_Sitemap, '_client_get', return_value=xml):
            items = _Sitemap()._fetch_sitemap()
        self.assertEqual(items[0]['image'],
                         'https://www.zawya.com/prod/images/original/abc-photo.jpg')

    def test_a_bare_path_goes_to_the_image_host_when_the_site_has_one(self):
        # Zawya: the sitemap path exists on media.zawya.com, not on the site.
        class _Sitemap(NewsFeedMagazineScraper):
            CODE = 'SM'
            BASE_URL = 'https://www.zawya.com'
            IMAGE_BASE_URL = 'https://media.zawya.com'
            FEED_MODE = 'sitemap'
            SITEMAP_URLS = ['https://www.zawya.com/sitemaps/en/sitemap-news.xml']

        xml = (
            '<?xml version="1.0"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
            '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
            '  <url><loc>https://www.zawya.com/en/story</loc>'
            '    <image:image><image:loc>/prod/images/original/abc-photo.jpg</image:loc></image:image>'
            '  </url>'
            '  <url><loc>https://www.zawya.com/en/other</loc>'
            '    <image:image><image:loc>https://cdn.example.test/full.jpg</image:loc></image:image>'
            '  </url>'
            '</urlset>')
        with mock.patch.object(_Sitemap, '_client_get', return_value=xml):
            items = _Sitemap()._fetch_sitemap()
        self.assertEqual(items[0]['image'],
                         'https://media.zawya.com/prod/images/original/abc-photo.jpg')
        # an absolute URL is left alone
        self.assertEqual(items[1]['image'], 'https://cdn.example.test/full.jpg')

    def test_zawya_names_its_media_host(self):
        from magazine_tenders.scrapers.zawya import ZawyaScraper
        self.assertEqual(ZawyaScraper.IMAGE_BASE_URL, 'https://media.zawya.com')

    def test_zawya_card_takes_the_preview_and_hero_the_original(self):
        from magazine_tenders.scrapers.zawya import ZawyaScraper
        original = 'https://media.zawya.com/prod/images/original/abc-photo.jpg'
        small, large = ZawyaScraper()._sitemap_image_sizes(original)
        self.assertEqual(small, 'https://media.zawya.com/prod/images/gm_preview/abc-photo.jpg')
        self.assertEqual(large, original)
        # anything else passes through untouched
        other = 'https://cdn.example.test/full.jpg'
        self.assertEqual(ZawyaScraper()._sitemap_image_sizes(other), (other, other))


class ZawyaImageHostMigrationTests(TestCase):
    """0005 moves rows saved with the site host onto the media host."""

    def _row(self, title, image):
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src, _ = ScraperSource.objects.get_or_create(
            scraper_class='MAGAZINE_ZTEST',
            defaults=dict(name='Z Test', country=jo, is_active=False,
                          base_url='https://example-magazine.com'))
        t = Tender.objects.create(
            source=src, country=jo, external_id=f'z-{title}',
            source_url='https://example.com/t', title=title,
            is_aec=True, is_aec_checked=True, is_relevant=True,
            status='OPEN', submission_deadline=None, publication_date=date.today())
        return MagazineTenderMeta.objects.create(tender=t, magazine='ZTEST',
                                                 image_url=image, image_url_large=image)

    def test_rows_are_rehosted_and_others_untouched(self):
        from importlib import import_module
        from django.apps import apps
        mig = import_module('magazine_tenders.migrations.0005_zawya_image_host')
        z = self._row('zawya one', 'https://www.zawya.com/prod/images/original/a.jpg')
        cw = self._row('cw one', 'https://www.constructionweekonline.com/x.jpg')
        mig.forwards(apps, None)
        z.refresh_from_db(); cw.refresh_from_db()
        self.assertEqual(z.image_url, 'https://media.zawya.com/prod/images/gm_preview/a.jpg')
        self.assertEqual(z.image_url_large, 'https://media.zawya.com/prod/images/original/a.jpg')
        self.assertEqual(cw.image_url, 'https://www.constructionweekonline.com/x.jpg')
        # running it twice changes nothing more
        mig.forwards(apps, None)
        z.refresh_from_db()
        self.assertEqual(z.image_url, 'https://media.zawya.com/prod/images/gm_preview/a.jpg')
        mig.backwards(apps, None)
        z.refresh_from_db()
        self.assertEqual(z.image_url, 'https://www.zawya.com/prod/images/original/a.jpg')
        self.assertEqual(z.image_url_large, 'https://www.zawya.com/prod/images/original/a.jpg')


class RunnerStoresTheImageTests(TestCase):
    def _run(self, td):
        fake = _FakeScraper(items=[td])
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True), \
             mock.patch('magazine_tenders.runner.get_magazine_scraper', return_value=fake), \
             mock.patch('tenders.ajibrain_client.classify_and_translate_tender',
                        return_value=dict(AEC_ENRICH)):
            return runner.run_one('TESTMAG')

    def test_the_url_lands_on_the_meta_row(self):
        td = item(publication_date=date.today())
        td.image_url = IMG
        td.image_url_large = IMG.replace('site-photo', 'site-photo-1536')
        self._run(td)
        meta = MagazineTenderMeta.objects.get()
        self.assertEqual(meta.image_url, IMG)
        self.assertEqual(meta.image_url_large, IMG.replace('site-photo', 'site-photo-1536'))

    def test_an_item_without_one_stores_an_empty_field(self):
        # The scrapers that never carry an image (Ashghal, Gulf Construction)
        # set nothing at all; the runner must not need the attribute.
        self._run(item(publication_date=date.today()))
        self.assertEqual(MagazineTenderMeta.objects.get().image_url, '')


class ExpectedQuarterTests(TestCase):
    """The photo slot's text for a source with no photos."""

    def test_the_scrapers_phrase_becomes_a_short_label(self):
        self.assertEqual(
            expected_quarter('Forthcoming Ashghal project, expected Fourth Quarter 2026. '
                             'Department: External Entities.'),
            'Q4 2026')
        self.assertEqual(expected_quarter('expected first quarter 2027.'), 'Q1 2027')

    def test_news_articles_have_none(self):
        self.assertEqual(expected_quarter('[DESIGN] Dar al Omran secures the design.'), '')
        self.assertEqual(expected_quarter(''), '')
        self.assertEqual(expected_quarter(None), '')

    def test_the_label_reaches_the_card_block(self):
        admin = User.objects.create_superuser('q-boss', password='pw12345!')
        qa, _ = CountryConfig.objects.get_or_create(
            code='QA', defaults={'name_en': 'Qatar', 'name_ar': 'قطر',
                                 'flag_emoji': '🇶🇦', 'currency': 'QAR'})
        src = ScraperSource.objects.create(
            name='Ashghal', country=qa, is_active=False,
            base_url='https://www.ashghal.gov.qa',
            scraper_class='MAGAZINE_ASHGHALPROSPECTED')
        t = Tender.objects.create(
            source=src, country=qa, external_id='q-1',
            source_url='https://www.ashghal.gov.qa/x',
            title='Supervision for a new school',
            description='Forthcoming Ashghal project, expected Third Quarter 2026.',
            is_aec=True, is_aec_checked=True, is_relevant=True, status='OPEN')
        MagazineTenderMeta.objects.create(tender=t, magazine='ASHGHALPROSPECTED')
        self.client.force_login(admin)
        html = self.client.get(reverse('magazine_tenders:list') + '?view=future'
                               ).content.decode()
        self.assertIn('Q3 2026', html)
        self.assertIn('nf-fb-big', html)
        # and it says what it is, in the block, rather than looking broken
        self.assertIn('Expected</span>', html)
        self.assertIn('Public Works Authority · Qatar', html)


class StandfirstTests(TestCase):
    def test_every_keep_label_is_removed_not_just_the_first_two(self):
        # The pattern listed TENDER and DESIGN. PROJECT was added later and
        # every project card then showed a literal "[PROJECT]" in front of
        # its text - 126 of them on the live page. Written as "any uppercase
        # label" so the next one cannot leak the same way.
        from magazine_tenders.views import standfirst_of
        for tag in ('TENDER', 'DESIGN', 'PROJECT', 'NOT-PROJECT', 'ANYTHING'):
            with self.subTest(tag=tag):
                self.assertEqual(standfirst_of(f'[{tag}] The work begins.'),
                                 'The work begins.')

    def test_something_that_merely_looks_like_a_tag_is_kept(self):
        # A headline may legitimately open with a bracket.
        from magazine_tenders.views import standfirst_of
        self.assertEqual(standfirst_of('[2026] was a record year.'),
                         '[2026] was a record year.')
        self.assertEqual(standfirst_of('[Not a tag] stays.'),
                         '[Not a tag] stays.')

    def test_the_keep_tag_is_removed(self):
        self.assertEqual(standfirst_of('[DESIGN] Dar al Omran secures the design.'),
                         'Dar al Omran secures the design.')
        self.assertEqual(standfirst_of('[TENDER]  Bids invited.'), 'Bids invited.')

    def test_whitespace_is_flattened_and_empty_is_safe(self):
        self.assertEqual(standfirst_of('  a\n\n b   c '), 'a b c')
        self.assertEqual(standfirst_of(''), '')
        self.assertEqual(standfirst_of(None), '')



class TemplateCommentsAreRealCommentsTests(TestCase):
    """
    No multi-line `{# #}` in the magazine templates.

    Django lexes `{# #}` without DOTALL, so one that spans lines is not a
    comment at all - it is literal text printed into the page. Two were
    visible on screen before this was caught: a note about Key dates on every
    Ashghal article, and a note about the vote buttons on every audit card.
    `{% comment %}` is the multi-line form.

    Asserted against the template SOURCE, not a rendered page, because a leak
    only shows on the pages that happen to render that block.
    """

    #: Written the way Django writes it, so the two cannot drift apart.
    PATTERN = re.compile(r"\{#((?:[^#]|#(?!\}))*?)#\}", re.S)

    def test_the_detector_actually_detects(self):
        # Without this the suite could pass because the pattern was mangled
        # rather than because the templates are clean - which is exactly what
        # happened on the first attempt at this test.
        self.assertTrue(self.PATTERN.search("{# one line #}"))
        spanning = "{# first line\nsecond line #}"
        m = self.PATTERN.search(spanning)
        self.assertIsNotNone(m, 'the pattern cannot see a multi-line comment')
        self.assertIn("\n", m.group(0))

    def test_no_multi_line_hash_comment_in_the_templates(self):
        from pathlib import Path

        from django.conf import settings

        root = Path(settings.BASE_DIR) / 'templates' / 'magazine'
        checked = 0
        for path in sorted(root.glob('*.html')):
            text = path.read_text(encoding='utf-8')
            checked += 1
            for m in self.PATTERN.finditer(text):
                if '\n' in m.group(0):
                    line = text[:m.start()].count('\n') + 1
                    self.fail(
                        f'{path.name}:{line} has a multi-line {{# #}}, which '
                        f'Django prints into the page as text. Use '
                        f'{{% comment %}}: {m.group(0)[:60]!r}')
        self.assertGreater(checked, 0, 'no templates were checked')


class NewsPageLayoutTests(TestCase):
    """Which surfaces get the magazine layout, and what the cards omit."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('photo-boss', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        cls.src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, image, pub, is_aec=True):
            t = Tender.objects.create(
                source=cls.src, country=jo, external_id=f'ph-{title}',
                source_url='https://example-magazine.com/a', title=title,
                description='[DESIGN] The firm secures the design contract.',
                reference_no='REF-77', is_aec=is_aec, is_aec_checked=True,
                is_relevant=True, status='OPEN',
                submission_deadline=date.today() + timedelta(days=30),
                publication_date=pub)
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG',
                                              image_url=image,
                                              listing_url='https://example-magazine.com/a')
            return t

        cls.with_photo = row('Photo story', IMG, date(2026, 9, 2))
        cls.no_photo   = row('No photo story', '', date(2026, 9, 1))
        cls.non_aec    = row('Rejected story', IMG, date(2026, 9, 1), is_aec=False)

    def _get(self, url):
        self.client.force_login(self.admin)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_news_and_all_get_the_magazine_layout(self):
        for q in ('', '?view=news'):
            resp = self._get(reverse('magazine_tenders:list') + q)
            self.assertTrue(resp.context['photo_layout'], q)
            self.assertContains(resp, 'class="nf-grid"')
            self.assertNotContains(resp, 'class="tc-head"')

    def test_future_projects_gets_the_magazine_layout_too(self):
        # The fixture source is not a forthcoming one, and the Future tab
        # filters to those — so give it one Ashghal row, or the page is empty
        # and there is no strip to inspect.
        qa, _ = CountryConfig.objects.get_or_create(
            code='QA', defaults={'name_en': 'Qatar', 'name_ar': 'قطر',
                                 'flag_emoji': '🇶🇦', 'currency': 'QAR'})
        ash = ScraperSource.objects.create(
            name='Ashghal', country=qa, is_active=False,
            base_url='https://www.ashghal.gov.qa',
            scraper_class='MAGAZINE_ASHGHALPROSPECTED')
        t = Tender.objects.create(
            source=ash, country=qa, external_id='fut-1',
            source_url='https://www.ashghal.gov.qa/x',
            title='Supervision for a new school',
            description='Forthcoming Ashghal project, expected Second Quarter 2027.',
            is_aec=True, is_aec_checked=True, is_relevant=True, status='OPEN')
        MagazineTenderMeta.objects.create(tender=t, magazine='ASHGHALPROSPECTED')
        resp = self._get(reverse('magazine_tenders:list') + '?view=future')
        self.assertTrue(resp.context['photo_layout'])
        self.assertNotContains(resp, 'class="tc-head"')
        # and the photo slot of a project with no photo shows its quarter
        self.assertContains(resp, 'Q2 2027')

    def test_the_audit_page_uses_the_cards_too(self):
        # It kept the compact table until the cards were asked for. The table
        # showed a title and some columns; a reviewer judging whether the
        # classifier was right needs the headline, the standfirst and the
        # photograph, because that is what the decision was made from.
        resp = self._get(reverse('magazine_tenders:non_aec'))
        self.assertTrue(resp.context['photo_layout'])
        self.assertNotContains(resp, 'class="tc-head"')
        self.assertContains(resp, 'Rejected story')

    def test_nothing_is_called_featured_any_more(self):
        # Every item is a card of the same size: no lead, no "Featured"
        # label - which on the audit page would have read as a
        # recommendation of a row the classifier rejected.
        for url in (reverse('magazine_tenders:list'),
                    reverse('magazine_tenders:non_aec')):
            resp = self._get(url)
            self.assertNotContains(resp, 'Featured</span>')
            self.assertNotContains(resp, 'class="nf-strip"')
            self.assertNotContains(resp, '>More news</div>')

    def test_the_audit_page_still_behaves_like_a_review_tool(self):
        # Only the presentation is shared. Its ordering stays plain and it
        # still shows rows the News page would hide - those are what make it
        # an audit rather than a second front page.
        resp = self._get(reverse('magazine_tenders:non_aec'))
        view = resp.context['view']
        self.assertTrue(view.non_aec_mode)

    def test_the_photo_story_comes_first(self):
        # Photo cards first, then the rest: the ordering the wall keeps.
        resp = self._get(reverse('magazine_tenders:list'))
        self.assertEqual([t.pk for t in resp.context['tenders']],
                         [self.with_photo.pk, self.no_photo.pk])

    def test_the_image_is_hotlinked_safely(self):
        html = self._get(reverse('magazine_tenders:list')).content.decode()
        self.assertIn(f'src="{IMG}"', html)
        self.assertIn('referrerpolicy="no-referrer"', html)
        # a blocked hotlink swaps to the designed block, never a broken icon
        self.assertIn("classList.add('nf-noimg')", html)

    def test_a_missing_photo_renders_the_fallback_block(self):
        html = self._get(reverse('magazine_tenders:list')).content.decode()
        self.assertIn('nf-media nf-noimg', html)
        self.assertIn('nf-fallback', html)

    def test_tender_furniture_is_hidden_on_the_cards(self):
        # Deadline, reference and status are what an article does not have.
        html = self._get(reverse('magazine_tenders:list') + '?view=news').content.decode()
        self.assertNotIn('REF-77', html)
        self.assertNotIn('>Deadline<', html)
        # The rendered status pill, not the class name: base.html's stylesheet
        # defines .badge-open, so the bare string is on every page.
        self.assertNotIn('<span class="badge badge-open">Open</span>', html)

    def test_the_standfirst_shows_without_its_tag(self):
        html = self._get(reverse('magazine_tenders:list')).content.decode()
        self.assertIn('The firm secures the design contract.', html)
        self.assertNotIn('[DESIGN]', html)

    def test_page_two_is_the_same_wall(self):
        # 30 more rows → two pages; both are the same grid of cards.
        for n in range(30):
            t = Tender.objects.create(
                source=self.src, country=self.src.country, external_id=f'pg-{n}',
                source_url='https://example-magazine.com/p', title=f'Filler {n}',
                is_aec=True, is_aec_checked=True, is_relevant=True, status='OPEN',
                publication_date=date(2026, 1, 1) + timedelta(days=n))
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
        resp = self._get(reverse('magazine_tenders:list') + '?page=2')
        self.assertContains(resp, 'class="nf-grid"')
        self.assertNotContains(resp, 'class="nf-strip"')
