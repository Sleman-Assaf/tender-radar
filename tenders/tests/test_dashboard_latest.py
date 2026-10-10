"""
The dashboard shows tenders — portal AND newspaper — but never News.

The distinction is the point, and it is not obvious from the data: a News row
qualifies on exactly the same fields as a tender (is_relevant, is_aec, OPEN,
future deadline), so nothing at the query level separates them except the
source's scraper_class prefix. Newspaper rows ARE tenders, read out of a
newspaper instead of a portal, so they belong here. News is forthcoming
projects and trade press, which cannot be bid on.

Worth testing because of HOW it broke. News arrives in bulk — one Ashghal
sweep added 97 forthcoming projects — and every dashboard list sorts by
scraped_at, so a single import silently took all ten slots and pushed every
genuine tender off the page. Nothing errored; the dashboard just stopped being
about tenders.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from tenders.models import CountryConfig, ScraperSource, Tender
from tenders.views import DashboardView

FUTURE = date.today() + timedelta(days=30)


def make_source(scraper_class, name, country):
    return ScraperSource.objects.create(
        name=name, scraper_class=scraper_class, country=country,
        base_url='https://example.com', is_active=True)


def make_tender(source, title, **extra):
    return Tender.objects.create(
        source=source, country=source.country,
        external_id=f'ext-{title[:40]}', source_url='https://example.com/t',
        title=title, is_aec=True, is_aec_checked=True, is_relevant=True,
        status='OPEN', submission_deadline=FUTURE, **extra)


class DashboardLatestTendersTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('dash-boss', 'd@x.jo', 'pw12345!')
        cls.country = CountryConfig.objects.create(
            code='QA', name_en='Qatar', name_ar='قطر', is_active=True)

        real = make_source('AshghalScraper', 'Ashghal e-Tenders', cls.country)
        news = make_source('MAGAZINE_ASHGHALPROSPECTED', 'Ashghal Prospected',
                           cls.country)
        paper = make_source('NEWSPAPER_ALRAI', 'Al-Rai', cls.country)

        cls.real_tender = make_tender(real, 'Supervision of deep injection wells')
        # Created after, so they sort FIRST by scraped_at — the exact ordering
        # that let a bulk import take every slot.
        for n in range(12):
            make_tender(news, f'Forthcoming Ashghal project {n}')
        for n in range(3):
            make_tender(paper, f'Newspaper advertisement {n}')

    def _rows(self):
        req = RequestFactory().get('/')
        req.user = self.admin
        view = DashboardView()
        view.request = req
        return list(view.get_queryset())

    def test_news_rows_are_not_listed(self):
        for t in self._rows():
            self.assertFalse(
                (t.source.scraper_class or '').startswith('MAGAZINE_'),
                f'News row on the dashboard: {t.title}')

    def test_newspaper_rows_ARE_listed(self):
        # Deliberately the opposite of the News rule. A newspaper tender is a
        # tender; only its origin differs. This assertion exists because the
        # first version of this exclusion took newspaper rows out too.
        papers = [t for t in self._rows()
                  if (t.source.scraper_class or '').startswith('NEWSPAPER_')]
        self.assertTrue(papers, 'newspaper tenders must stay on the dashboard')

    def test_the_real_tender_survives_a_bulk_import(self):
        # 15 feature rows were created after it; without the exclusion they
        # would fill all ten slots and this one would vanish.
        titles = [t.title for t in self._rows()]
        self.assertIn(self.real_tender.title, titles)

    def test_ordinary_tenders_are_still_shown(self):
        # 1 portal tender + 3 newspaper tenders; the 12 News rows are gone.
        rows = self._rows()
        self.assertEqual(len(rows), 4, [t.title for t in rows])

class DashboardCounterTests(DashboardLatestTendersTests):
    """
    The counters must agree with the list.

    Inherits the same fixture: one real tender, 12 News rows, 3 Newspaper rows.
    Before this, the dashboard reported 43 open AEC tenders against a list of
    7 — a headline figure six times the truth, sitting directly above the list
    that contradicted it.
    """

    def _ctx(self):
        from django.test import RequestFactory
        req = RequestFactory().get('/')
        req.user = self.admin
        view = DashboardView()
        view.request = req
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def test_the_counters_count_tenders_but_not_news(self):
        # 1 portal + 3 newspaper = 4. The 12 News rows must not be counted.
        ctx = self._ctx()
        for key in ('total_tenders', 'open_tenders', 'aec_tenders'):
            with self.subTest(counter=key):
                self.assertEqual(ctx[key], 4, f'{key} is wrong')

    def test_the_headline_matches_the_list(self):
        # The specific incoherence that prompted this: a count that disagrees
        # with the list printed underneath it.
        ctx = self._ctx()
        self.assertEqual(ctx['open_tenders'], len(list(ctx['object_list'])))

    def test_per_country_figures_agree_too(self):
        # by_country feeds the Top Countries bars; if it still counted feature
        # rows, the bars would not add up to the total above them.
        ctx = self._ctx()
        self.assertEqual(sum(r['n'] for r in ctx['by_country']), 4)

    def test_admin_stats_still_count_everything(self):
        # Deliberately NOT filtered: this block is documented as stats over ALL
        # tenders, and an administrator asking for the system total wants the
        # real one, Newspaper and News included.
        ctx = self._ctx()
        self.assertEqual(ctx['admin_stats']['all_total'], 16)

class DashboardEverySurfaceTests(DashboardLatestTendersTests):
    """
    EVERY tender list on the dashboard, not just the one that was reported.

    Written after fixing this twice. The first fix caught only
    DashboardView.get_queryset(); "Latest AEC Tenders" (aec_latest), the
    ticker (today_tickers) and the AEC map each build their own queryset from
    scratch instead of going through the shared helpers, so they kept showing
    News. Rather than name the three that were wrong, this walks whatever
    tender lists the context exposes — a fourth one added later is covered
    without anyone remembering to come back here.
    """

    def _ctx(self):
        from django.test import RequestFactory
        req = RequestFactory().get('/')
        req.user = self.admin
        view = DashboardView()
        view.request = req
        view.object_list = view.get_queryset()
        return view.get_context_data()

    @staticmethod
    def _is_feature(tender):
        # News only. Newspaper rows are tenders and are expected here.
        return (tender.source.scraper_class or '').startswith('MAGAZINE_')

    def test_no_dashboard_list_contains_a_feature_row(self):
        ctx = self._ctx()
        checked = 0
        for key, value in ctx.items():
            # QuerySet as well as list/tuple: object_list is a QuerySet, and
            # checking only for lists silently skipped the very surface that
            # was reported. Strings are excluded explicitly or every text
            # value would be walked character by character.
            if isinstance(value, (str, bytes, dict)) or not hasattr(value, '__iter__'):
                continue
            try:
                rows = [x for x in value if isinstance(x, Tender)]
            except TypeError:
                continue
            if not rows:
                continue
            checked += 1
            with self.subTest(context_key=key):
                offenders = [t.title for t in rows if self._is_feature(t)]
                self.assertEqual(offenders, [],
                                 f'{key} shows News rows')
        # object_list, aec_latest and today_tickers at minimum.
        self.assertGreaterEqual(checked, 3,
                                'expected to find several tender lists to check')

    def test_the_named_surfaces_are_all_present_and_clean(self):
        # Explicit as well as generic: if one of these is renamed away, the
        # loop above would silently stop covering it.
        ctx = self._ctx()
        for key in ('object_list', 'aec_latest', 'today_tickers'):
            with self.subTest(surface=key):
                self.assertIn(key, ctx)
                rows = [x for x in (ctx[key] or []) if isinstance(x, Tender)]
                self.assertTrue(rows, f'{key} is empty; test proves nothing')
                self.assertFalse([t for t in rows if self._is_feature(t)])

    def test_the_map_counts_only_real_tenders(self):
        ctx = self._ctx()
        data = ctx.get('aec_map_data') or {}
        total = sum(int((row or {}).get('n', 0) or 0)
                    for row in data.values())
        if total:
            self.assertEqual(total, 4, 'the AEC map counted News rows')


class NewsTickerShowsTodaysPublicationsTests(TestCase):
    """
    The dashboard news bar: PUBLISHED today, not scraped today.

    Worth its own test because the two look identical on a fresh database and
    diverge completely in use. Every morning's sweep re-reads the same
    articles, so "scraped today" showed the entire backlog again each day — on
    10 Sep it was 53 items, none of which had been published that day. The bar
    said "News · 53" and meant "News · 0".
    """

    @classmethod
    def setUpTestData(cls):
        from magazine_tenders.models import MagazineTenderMeta

        cls.country = CountryConfig.objects.create(
            code='AE', name_en='United Arab Emirates', name_ar='الإمارات',
            is_active=True)
        src = make_source('MAGAZINE_CBNME', 'CBNME', cls.country)

        def news(title, pub):
            t = make_tender(src, title, publication_date=pub)
            MagazineTenderMeta.objects.create(tender=t, magazine='CBNME')
            return t

        # All three are scraped now, by definition — that is the point.
        cls.today_row     = news('Design contract awarded today', date.today())
        cls.yesterday_row = news('Design contract awarded yesterday',
                                 date.today() - timedelta(days=1))
        cls.old_row       = news('Design contract from April',
                                 date.today() - timedelta(days=140))
        cls.undated_row   = news('Ashghal forthcoming project', None)

    def _ticker(self):
        from tenders.views import _today_news_tickers
        return list(_today_news_tickers(date.today()))

    def test_only_todays_publications_appear(self):
        self.assertEqual([t.title for t in self._ticker()],
                         ['Design contract awarded today'])

    def test_yesterdays_news_is_gone(self):
        self.assertNotIn(self.yesterday_row, self._ticker())

    def test_an_undated_row_is_not_todays_news(self):
        # Ashghal prospected work is a standing pipeline, not a publication.
        self.assertNotIn(self.undated_row, self._ticker())

    def test_the_bar_is_empty_on_a_quiet_day(self):
        # Then dashboard.html's {% if %} hides the bar entirely, which is the
        # intended behaviour — most days carry no AEC news at all.
        Tender.objects.filter(pk=self.today_row.pk).update(
            publication_date=date.today() - timedelta(days=2))
        self.assertEqual(self._ticker(), [])
