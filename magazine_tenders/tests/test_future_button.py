"""
The News page's All / News / Future Projects buttons.

"Future" is a SOURCE property, not a data property, and that is the whole point
of these tests. The obvious definition — "has no closing date" — is wrong here:
most trade-press items carry no date either, so it would sweep ordinary news in
with the forthcoming-projects pipeline. The flag lives in the registry instead.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from tenders.models import CountryConfig, ScraperSource, Tender

from magazine_tenders import sources
from magazine_tenders.models import MagazineTenderMeta
from magazine_tenders.views import MagazineTenderListView

FUTURE = date.today() + timedelta(days=40)


class FutureProjectsButtonTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('news-reader', password='pw12345!')
        cls.country = CountryConfig.objects.create(
            code='QA', name_en='Qatar', name_ar='قطر', is_active=True)

        def src(code):
            return ScraperSource.objects.create(
                name=code, scraper_class=f'MAGAZINE_{code}',
                country=cls.country, base_url='https://example.com',
                is_active=False)

        cls.future_src = src('ASHGHALPROSPECTED')
        cls.news_src = src('GULFCONSTRUCTION')

        def add(source, title, deadline):
            t = Tender.objects.create(
                source=source, country=cls.country,
                external_id=f'x-{title[:40]}', source_url='https://e.com/t',
                title=title, is_aec=True, is_aec_checked=True,
                is_relevant=True, status='OPEN', submission_deadline=deadline)
            MagazineTenderMeta.objects.create(tender=t, magazine='X',
                                              listing_url='https://e.com')
            return t

        # Forthcoming projects: no deadline, by nature.
        for n in range(4):
            add(cls.future_src, f'Supervision of forthcoming project {n}', None)
        # Ordinary news: ALSO no deadline for two of them — the case that makes
        # a deadline-based definition useless.
        add(cls.news_src, 'Design consultancy tender open now', FUTURE)
        add(cls.news_src, 'Design news with no closing date A', None)
        add(cls.news_src, 'Design news with no closing date B', None)

    def _rows(self, query=''):
        req = RequestFactory().get('/magazine-tenders/?' + query)
        req.user = self.user
        view = MagazineTenderListView()
        view.request = req
        view.kwargs = {}
        return list(view.get_queryset())

    # ── the registry flag ────────────────────────────────────────────────────
    def test_the_forthcoming_flag_is_wired_to_the_registry(self):
        # Deliberately NOT an exact list. The flag exists so a second pipeline
        # source can join the button by adding one key — DevelopmentAid's
        # forecast filter did exactly that — so pinning the membership would
        # break on the change it was designed to allow.
        codes = sources.forthcoming_codes()
        self.assertIn('ASHGHALPROSPECTED', codes)
        for code in codes:
            with self.subTest(code=code):
                self.assertIn(code, sources.all_codes())
                self.assertTrue(sources.MAGAZINES[code].get('forthcoming'))
        # and it is a subset: ordinary news sources must not be flagged
        self.assertNotIn('GULFCONSTRUCTION', codes)
        self.assertLess(len(codes), len(sources.all_codes()))

    # ── the three modes ──────────────────────────────────────────────────────
    def test_no_parameter_shows_everything(self):
        self.assertEqual(len(self._rows()), 7)

    def test_future_shows_only_the_forthcoming_source(self):
        rows = self._rows('view=future')
        self.assertEqual(len(rows), 4)
        for t in rows:
            self.assertEqual(t.source.scraper_class,
                             'MAGAZINE_ASHGHALPROSPECTED')

    def test_news_excludes_the_forthcoming_source(self):
        rows = self._rows('view=news')
        self.assertEqual(len(rows), 3)
        for t in rows:
            self.assertNotEqual(t.source.scraper_class,
                                'MAGAZINE_ASHGHALPROSPECTED')

    def test_the_two_modes_partition_the_page(self):
        # Nothing may be in both, and nothing may fall through the gap.
        everything = {t.pk for t in self._rows()}
        future = {t.pk for t in self._rows('view=future')}
        news = {t.pk for t in self._rows('view=news')}
        self.assertEqual(future & news, set())
        self.assertEqual(future | news, everything)

    def test_a_deadline_based_rule_would_have_been_wrong(self):
        # Guards the reasoning, not just the result: two ordinary news items
        # also have no deadline, so "no deadline" would have captured 6 rows
        # instead of the 4 forthcoming ones.
        no_deadline = [t for t in self._rows() if t.submission_deadline is None]
        self.assertEqual(len(no_deadline), 6)
        self.assertEqual(len(self._rows('view=future')), 4)

    def test_an_unknown_view_value_is_ignored(self):
        self.assertEqual(len(self._rows('view=banana')), 7)

    # ── the buttons themselves ───────────────────────────────────────────────
    def _ctx(self, query=''):
        req = RequestFactory().get('/magazine-tenders/?' + query)
        req.user = self.user
        view = MagazineTenderListView()
        view.request = req
        view.kwargs = {}
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def test_the_counts_match_what_a_click_shows(self):
        # The badge is computed by running the real queryset with the parameter
        # overridden, so it cannot drift from the list it promises.
        ctx = self._ctx()
        self.assertEqual(ctx['future_count'], len(self._rows('view=future')))
        self.assertEqual(ctx['news_count'], len(self._rows('view=news')))
        self.assertEqual(ctx['future_count'] + ctx['news_count'],
                         len(self._rows()))

    def test_the_active_button_is_known_to_the_template(self):
        self.assertEqual(self._ctx('view=future')['view_mode'], 'future')
        self.assertEqual(self._ctx('view=news')['view_mode'], 'news')
        self.assertEqual(self._ctx()['view_mode'], '')

    def test_counting_does_not_corrupt_the_request(self):
        # _mode_count swaps request.GET to reuse get_queryset; it must put the
        # original back or every later filter on the page reads the wrong value.
        req = RequestFactory().get('/magazine-tenders/?view=future&q=design')
        req.user = self.user
        view = MagazineTenderListView()
        view.request = req
        view.kwargs = {}
        view.object_list = view.get_queryset()
        view.get_context_data()
        self.assertEqual(view.request.GET.get('view'), 'future')
        self.assertEqual(view.request.GET.get('q'), 'design')
