# magazine_tenders/tests/test_views.py
"""
Page rules: /magazine-tenders/ shows ONLY magazine tenders with is_aec=True;
ordinary web tenders and newspaper tenders never appear there; non-AEC magazine
rows are stored for audit and reachable only by an admin.

Also asserts the two containment promises the feature makes to the rest of the
system: magazine tenders are hidden from All-Tenders BROWSING but still found by
SEARCH, and an AEC magazine tender does reach the Dashboard with no Dashboard
code change.
"""
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender

from magazine_tenders import sources
from magazine_tenders.models import MagazineTenderMeta

FUTURE = date.today() + timedelta(days=30)

TEST_MAGAZINES = {
    'TESTMAG': {
        'code':          'TESTMAG',
        'name':          'Test Magazine — Tender Announcements',
        'name_ar':       'مجلة الاختبار',
        'display':       'الاختبار',
        'display_en':    'Test Magazine',
        'scraper_class': 'MAGAZINE_TESTMAG',
        'base_url':      'https://example-magazine.com',
        'country_code':  'JO',
        'id_prefix':     'mag-testmag',
        'scraper':       'magazine_tenders.tests.test_runner._FakeScraper',
        'listing_urls':  ['https://example-magazine.com/tenders'],
    },
}


def make_tender(source, *, title, is_aec=True, external_id=None, **extra):
    return Tender.objects.create(
        source=source,
        country=source.country,
        external_id=external_id or f'ext-{title[:40]}',
        source_url='https://example.com/t',
        title=title,
        is_aec=is_aec,
        is_aec_checked=True,
        is_relevant=True,
        status='OPEN',
        submission_deadline=FUTURE,
        **extra,
    )


class MagazineListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user  = User.objects.create_user('reader', password='pw12345!')
        cls.admin = User.objects.create_superuser('boss', password='pw12345!')

        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})

        cls.mag_source = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')
        cls.web_source = ScraperSource.objects.create(
            name='Some Web Portal', country=jo,
            base_url='https://portal.example.com',
            scraper_class='JordanGTDScraper')

        cls.aec = make_tender(cls.mag_source, title='Magazine AEC announcement',
                              is_aec=True, external_id='mag-aec')
        MagazineTenderMeta.objects.create(
            tender=cls.aec, magazine='TESTMAG',
            listing_url='https://example-magazine.com/tenders/1')

        cls.non_aec = make_tender(cls.mag_source, title='Magazine non-AEC announcement',
                                  is_aec=False, external_id='mag-non-aec')
        MagazineTenderMeta.objects.create(
            tender=cls.non_aec, magazine='TESTMAG',
            listing_url='https://example-magazine.com/tenders/2')

        cls.web = make_tender(cls.web_source, title='Ordinary portal tender',
                              is_aec=True, external_id='web-1')

    def setUp(self):
        self.client.force_login(self.user)

    # ── access ───────────────────────────────────────────────────────────────
    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('magazine_tenders:list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp['Location'])

    # ── what the page shows ──────────────────────────────────────────────────
    def test_aec_magazine_tender_is_listed(self):
        resp = self.client.get(reverse('magazine_tenders:list'))
        self.assertEqual(resp.status_code, 200)
        titles = [t.title for t in resp.context['tenders']]
        self.assertIn('Magazine AEC announcement', titles)

    def test_non_aec_magazine_tender_is_hidden(self):
        resp = self.client.get(reverse('magazine_tenders:list'))
        titles = [t.title for t in resp.context['tenders']]
        self.assertNotIn('Magazine non-AEC announcement', titles)

    def test_ordinary_web_tender_is_not_listed(self):
        resp = self.client.get(reverse('magazine_tenders:list'))
        titles = [t.title for t in resp.context['tenders']]
        self.assertNotIn('Ordinary portal tender', titles)

    def test_magazine_filter(self):
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True):
            resp = self.client.get(reverse('magazine_tenders:list'),
                                   {'magazine': 'TESTMAG'})
            self.assertEqual(len(resp.context['tenders']), 1)
            resp = self.client.get(reverse('magazine_tenders:list'),
                                   {'magazine': 'OTHER'})
            self.assertEqual(len(resp.context['tenders']), 0)

    def test_search_filter(self):
        resp = self.client.get(reverse('magazine_tenders:list'), {'q': 'zzz-nothing'})
        self.assertEqual(len(resp.context['tenders']), 0)
        resp = self.client.get(reverse('magazine_tenders:list'), {'q': 'announcement'})
        self.assertEqual(len(resp.context['tenders']), 1)

    # ── admin-only pages ─────────────────────────────────────────────────────
    def test_non_aec_page_is_admin_only(self):
        resp = self.client.get(reverse('magazine_tenders:non_aec'))
        self.assertIn(resp.status_code, (302, 403))

        self.client.force_login(self.admin)
        resp = self.client.get(reverse('magazine_tenders:non_aec'))
        self.assertEqual(resp.status_code, 200)
        titles = [t.title for t in resp.context['tenders']]
        self.assertIn('Magazine non-AEC announcement', titles)
        self.assertNotIn('Magazine AEC announcement', titles)

    # The separate News runs page was removed on 2026-09-06: the consolidated
    # /it/runs/ replaced it and is strictly better (both features interleaved
    # by time, with Retry, Rescan and Reset). Its admin gate and its contents
    # are covered by tenders/tests/test_feature_runs.py, so the test that used
    # to live here is not merely deleted — it moved.

    # ── manual trigger ───────────────────────────────────────────────────────
    def test_run_endpoint_requires_post(self):
        resp = self.client.get(reverse('magazine_tenders:run'))
        self.assertEqual(resp.status_code, 405)

    def test_run_endpoint_refuses_when_nothing_configured(self):
        with mock.patch.dict(sources.MAGAZINES, {}, clear=True):
            resp = self.client.post(reverse('magazine_tenders:run'))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['ok'])

    def test_run_endpoint_starts_a_sweep(self):
        with mock.patch.dict(sources.MAGAZINES, TEST_MAGAZINES, clear=True), \
             mock.patch('magazine_tenders.views.threading.Thread') as m_thread:
            resp = self.client.post(reverse('magazine_tenders:run'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        m_thread.assert_called_once()
        # Reset the module flag the view set, so later tests are unaffected.
        import magazine_tenders.views as v
        v._trigger_running = False

    # ── containment promises to the rest of the system ───────────────────────
    def test_hidden_from_all_tenders_browsing_but_found_by_search(self):
        browse = self.client.get(reverse('tenders:list'))
        titles = [t.title for t in browse.context['tenders']]
        self.assertNotIn('Magazine AEC announcement', titles)
        self.assertIn('Ordinary portal tender', titles)

        found = self.client.get(reverse('tenders:list'), {'q': 'Magazine AEC'})
        titles = [t.title for t in found.context['tenders']]
        self.assertIn('Magazine AEC announcement', titles)

    def test_magazine_anchor_row_absent_from_the_sources_list(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('tenders:sources'))
        names = [s.scraper_class for s in resp.context['sources']]
        self.assertNotIn('MAGAZINE_TESTMAG', names)
        self.assertIn('JordanGTDScraper', names)

    def test_aec_news_tender_stays_off_the_dashboard(self):
        # This test asserted the OPPOSITE until 2026-09-06, and the change is
        # a requirement change rather than a fix: News rows were allowed onto
        # the dashboard because they qualify on the same fields as a tender
        # (is_relevant + is_aec + OPEN + future deadline).
        #
        # What made that untenable is volume. News arrives in bulk — one
        # Ashghal sweep added 97 forthcoming projects — and every dashboard
        # list sorts by scraped_at, so a single import took every slot and the
        # headline count read 43 open tenders when 7 were tenders. News now
        # lives only on its own pages.
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('tenders:dashboard'))
        self.assertEqual(resp.status_code, 200)
        latest = [t.title for t in resp.context.get('aec_latest', [])]
        self.assertNotIn('Magazine AEC announcement', latest)
        # Still reachable where it belongs.
        page = self.client.get(reverse('magazine_tenders:list'))
        self.assertContains(page, 'Magazine AEC announcement')


class NewsOrderingTests(TestCase):
    """
    Newest publication first, on every News tab.

    Ordering by scraped_at looked correct for months and was not: a sweep
    re-reads the whole feed in one minute, so every row ends up with the same
    scrape time and the page comes out in whatever order the database
    happens to return — April, then December, then January.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.user = User.objects.create_user('sorter', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        # Created in a deliberately unhelpful order, so a scraped_at sort
        # would produce a different answer from a publication_date one.
        for title, pub in (('Middle one',  _date(2026, 5, 5)),
                           ('Newest one',  _date(2026, 9, 1)),
                           ('Oldest one',  _date(2026, 1, 1))):
            t = make_tender(src, title=title, external_id=f'ord-{title}',
                            publication_date=pub)
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')

    def _titles(self, query=''):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('magazine_tenders:list') + query)
        self.assertEqual(resp.status_code, 200)
        return [t.title for t in resp.context['tenders']]

    def test_newest_publication_comes_first(self):
        self.assertEqual(self._titles(),
                         ['Newest one', 'Middle one', 'Oldest one'])

    def test_the_news_tab_is_ordered_too(self):
        self.assertEqual(self._titles('?view=news'),
                         ['Newest one', 'Middle one', 'Oldest one'])

    def test_an_undated_row_sinks_to_the_bottom(self):
        from magazine_tenders.models import MagazineTenderMeta
        src = ScraperSource.objects.get(scraper_class='MAGAZINE_TESTMAG')
        t = make_tender(src, title='Undated one', external_id='ord-undated',
                        publication_date=None)
        MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
        self.assertEqual(self._titles()[-1], 'Undated one')


class FirstPageLargerPaginatorTests(TestCase):
    """
    The paginator itself: a wider first page, everything else unchanged.

    Worth testing apart from the view because the failure mode is silent - a
    slice that is off by one loses a row between pages, or shows it twice, and
    nothing raises.
    """

    def setUp(self):
        from magazine_tenders.pagination import FirstPageLargerPaginator
        self.cls = FirstPageLargerPaginator

    def test_the_first_page_is_wider_and_the_rest_are_not(self):
        p = self.cls(list(range(100)), 24, first_page_extra=1)
        self.assertEqual(len(p.page(1).object_list), 25)
        self.assertEqual(len(p.page(2).object_list), 24)
        self.assertEqual(len(p.page(3).object_list), 24)

    def test_no_row_is_lost_or_shown_twice(self):
        # The invariant that matters: the pages, concatenated, are the list.
        rows = list(range(100))
        p = self.cls(rows, 24, first_page_extra=1)
        seen = []
        for n in p.page_range:
            seen.extend(p.page(n).object_list)
        self.assertEqual(seen, rows)

    def test_the_page_count_accounts_for_the_wider_first_page(self):
        # 100 rows: 25, then 24 + 24 + 24 is 97, leaving 3 for a fifth page.
        p = self.cls(list(range(100)), 24, first_page_extra=1)
        self.assertEqual(p.num_pages, 5)
        self.assertEqual(len(p.page(5).object_list), 3)
        # Django, given the same list and no wider first page, needs five too
        # but splits them differently - the count is not simply inherited.
        from django.core.paginator import Paginator
        self.assertEqual(len(Paginator(list(range(100)), 24).page(5)
                             .object_list), 4)

    def test_a_list_that_fits_on_one_page(self):
        p = self.cls(list(range(25)), 24, first_page_extra=1)
        self.assertEqual(p.num_pages, 1)
        self.assertEqual(len(p.page(1).object_list), 25)

    def test_one_row_over_the_first_page_starts_a_second(self):
        p = self.cls(list(range(26)), 24, first_page_extra=1)
        self.assertEqual(p.num_pages, 2)
        self.assertEqual(len(p.page(2).object_list), 1)

    def test_no_extra_behaves_exactly_like_django(self):
        from django.core.paginator import Paginator
        rows = list(range(100))
        mine = self.cls(rows, 24, first_page_extra=0)
        theirs = Paginator(rows, 24)
        self.assertEqual(mine.num_pages, theirs.num_pages)
        for n in theirs.page_range:
            self.assertEqual(list(mine.page(n).object_list),
                             list(theirs.page(n).object_list))

    def test_orphans_do_not_duplicate_rows(self):
        # num_pages counted the tail that page() folds away, so the last page
        # re-served rows the page before it had already shown - 27 cards for
        # 26 announcements. Latent while paginate_orphans is 0, silent the
        # moment anyone sets it.
        for count, orphans in ((26, 3), (27, 3), (52, 3), (49, 2)):
            with self.subTest(count=count, orphans=orphans):
                p = self.cls(list(range(count)), 24, first_page_extra=1,
                             orphans=orphans)
                served = []
                for n in p.page_range:
                    served.extend(p.page(n).object_list)
                self.assertEqual(served, sorted(set(served)),
                                 'a row was served twice')
                self.assertEqual(len(served), count, 'a row was lost')

    def test_it_still_matches_django_with_orphans_and_no_extra(self):
        from django.core.paginator import Paginator
        for count, orphans in ((26, 3), (49, 2), (100, 0), (1, 0), (0, 0)):
            with self.subTest(count=count, orphans=orphans):
                rows = list(range(count))
                mine = self.cls(rows, 24, first_page_extra=0, orphans=orphans)
                theirs = Paginator(rows, 24, orphans=orphans)
                self.assertEqual(mine.num_pages, theirs.num_pages)
                for n in theirs.page_range:
                    self.assertEqual(list(mine.page(n).object_list),
                                     list(theirs.page(n).object_list))

    def test_each_page_reports_the_rows_it_actually_holds(self):
        # Django derives these from a uniform per_page, so every page after
        # the first was one row early. Nothing renders them today; a
        # "Showing X-Y of N" line added later would have been wrong.
        p = self.cls(list(range(175)), 24, first_page_extra=1)
        for n in p.page_range:
            page = p.page(n)
            with self.subTest(page=n):
                self.assertEqual(page.end_index() - page.start_index() + 1,
                                 len(page.object_list))
        self.assertEqual((p.page(1).start_index(), p.page(1).end_index()),
                         (1, 25))
        self.assertEqual((p.page(2).start_index(), p.page(2).end_index()),
                         (26, 49))
        self.assertEqual(p.page(p.num_pages).end_index(), 175)

    def test_an_empty_list_is_still_one_page(self):
        p = self.cls([], 24, first_page_extra=1)
        self.assertEqual(p.num_pages, 1)
        self.assertEqual(len(p.page(1).object_list), 0)


class PageOneFillsEveryGridRowTests(TestCase):
    """
    Every page is a whole number of grid rows, page one included.

    Page one used to carry one extra card to make up for the featured strip.
    The strip is gone - every item is a card of the same size - so page one
    is an ordinary page of 24, which is six rows of four.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('page1-grid',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')
        # Comfortably more than two pages, every one with a photo so the strip
        # always finds a lead.
        for i in range(60):
            t = make_tender(src, title=f'Story {i:02}', is_aec=True,
                            external_id=f'p1-{i}',
                            publication_date=_date(2026, 9, 1))
            MagazineTenderMeta.objects.create(
                tender=t, magazine='TESTMAG',
                image_url='https://cdn.example.com/p.jpg')

    def _page(self, n):
        self.client.force_login(self.reader)
        resp = self.client.get(
            reverse('magazine_tenders:list') + f'?view=news&page={n}')
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_page_one_leaves_no_empty_cell(self):
        from magazine_tenders.views import MagazineTenderListView
        rows = list(self._page(1).context['tenders'])
        self.assertEqual(len(rows) % MagazineTenderListView.GRID_COLUMNS, 0,
                         f'page one holds {len(rows)} cards')

    def test_a_later_page_leaves_no_empty_cell_either(self):
        from magazine_tenders.views import MagazineTenderListView
        rows = list(self._page(2).context['tenders'])
        self.assertEqual(len(rows) % MagazineTenderListView.GRID_COLUMNS, 0,
                         f'page two holds {len(rows)} cards')

    def test_page_one_is_an_ordinary_page(self):
        from magazine_tenders.views import MagazineTenderListView
        self.assertEqual(len(list(self._page(1).context['tenders'])),
                         MagazineTenderListView.paginate_by)

    def test_nothing_is_skipped_between_page_one_and_two(self):
        # The card that moved up a page must not also appear on page two.
        one = [t.pk for t in self._page(1).context['tenders']]
        two = [t.pk for t in self._page(2).context['tenders']]
        self.assertEqual(set(one) & set(two), set())
        self.assertEqual(len(one) + len(two), len(set(one + two)))

    def test_the_audit_page_has_the_same_plain_first_page(self):
        from magazine_tenders.views import MagazineTenderListView
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:non_aec'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['paginator'].first_page_extra, 0)
        self.assertEqual(resp.context['paginator'].first_page_size,
                         MagazineTenderListView.paginate_by)

    def test_a_list_that_went_back_to_the_table_would_not_get_it(self):
        # The pairing stated directly: no cards, no strip, no extra.
        from magazine_tenders.views import MagazineTenderListView
        from magazine_tenders.pagination import FirstPageLargerPaginator

        class _Table(MagazineTenderListView):
            photo_layout = False

        v = _Table()
        p = v.get_paginator(list(range(50)), MagazineTenderListView.paginate_by)
        self.assertIsInstance(p, FirstPageLargerPaginator)
        self.assertEqual(p.first_page_extra, 0)


class BookmarksComeBackTests(TestCase):
    """
    Whatever the user saves comes back, whatever the site would otherwise hide.

    The Bookmarks button opens the Tenders list with ?bookmarked=1. That view
    already lifts the rule that keeps news out of the tenders list - but not
    the site-wide "AEC only" rule. Once News began carrying construction
    PROJECT news the classifier had marked Non-AEC, that emptied the page:
    the user bookmarked a story, the row was written, and the list refused to
    show it back with nothing on screen to say why.

    A bookmark is an explicit act. None of the POLICY filters - AEC, mirrors,
    the reader's industry and country preferences - may override it. The
    filters the user can see and set on the page still apply.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('bookmarker',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        news_src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, aec):
            t = make_tender(news_src, title=title, is_aec=aec,
                            external_id=f'bm-{title}',
                            publication_date=_date.today())
            t.description = '[PROJECT] Work has started on site.'
            t.save(update_fields=['description'])
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
            return t

        cls.non_aec = row('A Non-AEC project story', False)
        cls.aec = row('An AEC supervision story', True)
        cls.unsaved = row('A story nobody bookmarked', False)

        from tenders.models import TenderBookmark
        for t in (cls.non_aec, cls.aec):
            TenderBookmark.objects.create(tender=t, user=cls.reader)

    def _titles(self, url):
        self.client.force_login(self.reader)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return {t.title for t in resp.context['tenders']}

    def test_a_non_aec_news_bookmark_comes_back(self):
        self.assertIn('A Non-AEC project story',
                      self._titles(reverse('tenders:list') + '?bookmarked=1'))

    def test_an_aec_news_bookmark_comes_back(self):
        self.assertIn('An AEC supervision story',
                      self._titles(reverse('tenders:list') + '?bookmarked=1'))

    def test_only_what_was_bookmarked_comes_back(self):
        # Lifting the policy filters must not turn the page into "everything".
        titles = self._titles(reverse('tenders:list') + '?bookmarked=1')
        self.assertNotIn('A story nobody bookmarked', titles)
        self.assertEqual(len(titles), 2)

    def test_browsing_the_tenders_list_is_unchanged(self):
        # The news rows stay out of the ordinary tenders list, bookmarked or
        # not. Only the bookmarks view lifts that.
        titles = self._titles(reverse('tenders:list'))
        for t in ('A Non-AEC project story', 'An AEC supervision story',
                  'A story nobody bookmarked'):
            self.assertNotIn(t, titles)

    def test_a_filter_the_user_can_see_still_applies(self):
        # Policy filters are lifted; the ones with a control on the page are
        # not, or the page would stop answering what the user asked.
        titles = self._titles(reverse('tenders:list')
                              + '?bookmarked=1&q=supervision')
        self.assertIn('An AEC supervision story', titles)
        self.assertNotIn('A Non-AEC project story', titles)


class BookmarkingFromTheAuditPageTests(TestCase):
    """
    The whole round trip from the Non-AEC page: save, reload, find, remove.

    That page is where the bug bit hardest. Every row on it is Non-AEC by
    definition, so the site-wide "AEC only" rule on the bookmarks view hid
    100% of what could be saved from there - not most of it, all of it.

    Driven through the real URLs the buttons use, not the queryset, because
    the failure was in the round trip rather than in any one query.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('audit-bm',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')
        cls.rejected = make_tender(src, title='A rejected announcement',
                                   is_aec=False, external_id='abm-1',
                                   publication_date=_date.today())
        cls.rejected.description = '[DESIGN] A consultancy was appointed.'
        cls.rejected.save(update_fields=['description'])
        MagazineTenderMeta.objects.create(tender=cls.rejected,
                                          magazine='TESTMAG')

    def setUp(self):
        self.client.force_login(self.reader)

    def _audit(self):
        resp = self.client.get(reverse('magazine_tenders:non_aec'))
        self.assertEqual(resp.status_code, 200)
        return resp

    def _toggle(self):
        resp = self.client.post(
            reverse('tenders:toggle_bookmark', args=[self.rejected.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        return resp

    def _bookmarks(self):
        resp = self.client.get(reverse('tenders:list') + '?bookmarked=1')
        self.assertEqual(resp.status_code, 200)
        return [t.pk for t in resp.context['tenders']]

    def test_the_card_offers_a_bookmark_button(self):
        self.assertContains(self._audit(),
                            f'toggleBookmark({self.rejected.pk},')

    def test_no_button_is_nested_inside_a_card_link(self):
        # A <button> inside an <a> is invalid and the anchor eats the click.
        # The card's links are the photo, the headline and "Read more"; the
        # actions sit beside them in the footer, never inside one.
        import re
        body = self._audit().content.decode()
        anchors = re.findall(r'<a\b[^>]*>.*?</a>', body, re.S)
        self.assertTrue(anchors)
        for a in anchors:
            self.assertNotIn('toggleBookmark', a)

    def test_saving_then_finding_it_again(self):
        from tenders.models import TenderBookmark
        self._toggle()
        self.assertTrue(TenderBookmark.objects.filter(
            tender=self.rejected, user=self.reader).exists())
        self.assertIn(self.rejected.pk, self._bookmarks())

    def test_the_audit_page_shows_it_as_saved_on_reload(self):
        # Without this the button resets to empty on every page load and the
        # reader cannot tell what they already saved.
        self._toggle()
        resp = self._audit()
        row = [t for t in resp.context['tenders']
               if t.pk == self.rejected.pk][0]
        self.assertTrue(row.is_bookmarked)
        self.assertContains(resp, 'on-book')

    def test_clicking_again_removes_it(self):
        from tenders.models import TenderBookmark
        self._toggle()
        self._toggle()
        self.assertFalse(TenderBookmark.objects.filter(
            tender=self.rejected, user=self.reader).exists())
        self.assertNotIn(self.rejected.pk, self._bookmarks())

    def test_another_reader_does_not_see_it(self):
        # Bookmarks are per user; the audit page is shared by admins.
        from django.contrib.auth.models import User
        self._toggle()
        other = User.objects.create_superuser('audit-bm-2', password='pw12345!')
        self.client.force_login(other)
        self.assertNotIn(self.rejected.pk, self._bookmarks())
        row = [t for t in self._audit().context['tenders']
               if t.pk == self.rejected.pk][0]
        self.assertFalse(row.is_bookmarked)


class TheTwoListsAreComplementsTests(TestCase):
    """
    Every announcement is listed on News or on the audit page, never both.

    This broke once and was visible: letting construction PROJECT news past
    the classifier on News, without subtracting it from the audit page, put
    121 announcements on both at the same time - a reader saw "Dubai Creek
    Gardens breaks ground" filed as news and, a click away, as rejected.

    The invariant is asserted as a partition rather than case by case, because
    that is what actually has to hold: the halves must cover everything and
    overlap in nothing.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('complement',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, desc, aec):
            t = make_tender(src, title=title, is_aec=aec,
                            external_id=f'cx-{title}',
                            publication_date=_date.today())
            t.description = desc
            t.save(update_fields=['description'])
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG')
            return t

        cls.promoted = row('Ground broken at the new hospital',
                           '[PROJECT] Work has started on site.', False)
        cls.rejected = row('A design story the classifier rejected',
                           '[DESIGN] A consultancy was appointed.', False)
        cls.accepted = row('An AEC supervision contract',
                           '[DESIGN] Supervision of the works.', True)
        cls.untagged = row('An older row with no keep label',
                           'No tag at all.', False)

    def _ids(self, url):
        self.client.force_login(self.reader)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return {t.pk for t in resp.context['tenders']}

    def _news(self):
        return self._ids(reverse('magazine_tenders:list'))

    def _audit(self):
        return self._ids(reverse('magazine_tenders:non_aec'))

    def test_nothing_is_listed_on_both(self):
        self.assertEqual(self._news() & self._audit(), set())

    def test_nothing_falls_between_them(self):
        from magazine_tenders.models import MagazineTenderMeta
        every = set(MagazineTenderMeta.objects.values_list('tender_id',
                                                           flat=True))
        self.assertEqual(self._news() | self._audit(), every)

    def test_a_promoted_story_is_news_and_not_a_reject(self):
        self.assertIn(self.promoted.pk, self._news())
        self.assertNotIn(self.promoted.pk, self._audit())

    def test_a_rejected_story_is_only_on_the_audit_page(self):
        self.assertNotIn(self.rejected.pk, self._news())
        self.assertIn(self.rejected.pk, self._audit())

    def test_an_aec_story_is_only_on_news(self):
        self.assertIn(self.accepted.pk, self._news())
        self.assertNotIn(self.accepted.pk, self._audit())

    def test_switching_the_exemption_off_moves_it_back_whole(self):
        # With no exemption the split is the classifier's alone - and it is
        # still a partition, which is the part that matters.
        from django.test import override_settings
        with override_settings(MAGAZINE_NEWS_INCLUDES_PROJECTS=False):
            news, audit = self._news(), self._audit()
            self.assertNotIn(self.promoted.pk, news)
            self.assertIn(self.promoted.pk, audit)
            self.assertEqual(news & audit, set())


class ThePageSizeFitsTheGridTests(TestCase):
    """
    The page size must divide by the grid's column count.

    The cards lay out as `repeat(auto-fill, minmax(300px, 1fr))`, which is
    four columns on a desktop and reflows to three and two as the window
    narrows. At 25 a page held six full rows of four and one orphan, leaving
    three empty cells - the visible hole that prompted this.

    Asserted as arithmetic rather than by rendering, because the column count
    is a CSS breakpoint and cannot be measured from a test client. This is the
    rule that keeps the two in step.
    """

    def test_a_full_page_fills_every_row_at_every_column_count(self):
        from magazine_tenders.views import MagazineTenderListView
        size = MagazineTenderListView.paginate_by
        for columns in (2, 3, 4):
            with self.subTest(columns=columns):
                self.assertEqual(
                    size % columns, 0,
                    f'{size} cards leave {columns - size % columns} empty '
                    f'cell(s) in a {columns}-column grid')

    def test_the_size_is_still_a_sensible_page(self):
        # A number that divides cleanly is easy to find (4 does); it also has
        # to be a page someone wants to read.
        from magazine_tenders.views import MagazineTenderListView
        self.assertGreaterEqual(MagazineTenderListView.paginate_by, 12)
        self.assertLessEqual(MagazineTenderListView.paginate_by, 48)


class PhotosFirstOrderingTests(TestCase):
    """
    Cards with a photo come first, photo-less ones sit at the bottom.

    In the QUERY, not per page: a photo-less item newer than every photo item
    must still land after the last photo item, even if that means page two.
    Within each group the order stays newest-first. The audit page is untouched.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.admin = User.objects.create_superuser('photo-order', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com', scraper_class='MAGAZINE_TESTMAG')
        img = 'https://cdn.example.com/p.jpg'
        for title, pub, image, aec in (
            ('Newest, no photo',   _date(2026, 9, 9), '',  True),
            ('Older, with photo',  _date(2026, 9, 1), img, True),
            ('Oldest, with photo', _date(2026, 8, 1), img, True),
            ('Middle, no photo',   _date(2026, 8, 15), '', True),
            ('Rejected, no photo', _date(2026, 9, 10), '', False),
            ('Rejected, photo',    _date(2026, 9, 5), img, False),
        ):
            t = make_tender(src, title=title, is_aec=aec, external_id=f'pf-{title}',
                            publication_date=pub)
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG', image_url=image)

    def _titles(self, url):
        self.client.force_login(self.admin)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return [t.title for t in resp.context['tenders']]

    def test_photo_cards_precede_photo_less_ones_newest_first_within_each(self):
        self.assertEqual(self._titles(reverse('magazine_tenders:list') + '?view=news'), [
            'Older, with photo', 'Oldest, with photo',
            'Newest, no photo', 'Middle, no photo',
        ])

    def test_the_audit_page_follows_the_same_order(self):
        # It kept plain date order while it was a table. Now that it draws
        # the same cards it takes the same rule, photos first - a card grid
        # that mixes photographs with blank blocks at random reads as broken
        # whichever page it is on.
        self.assertEqual(self._titles(reverse('magazine_tenders:non_aec')),
                         ['Rejected, photo', 'Rejected, no photo'])


class UnreadPhotolessLeadsItsGroupTests(TestCase):
    """
    Among the photo-less cards, one the reader has not opened comes first.

    The photo-less block is where a new arrival is easiest to miss: it sits
    below every photo card, and its items carry no picture to catch the eye.
    So within THAT group — and only there — unread leads, and the newest-first
    order applies underneath it. The photo group is deliberately untouched: it
    is the front page, and it stays in date order.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('unread-order',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')
        img = 'https://cdn.example.com/p.jpg'
        cls.rows = {}
        for title, pub, image in (
            ('Photo card',        _date(2026, 9, 8), img),
            ('Seen, newer',       _date(2026, 9, 7), ''),
            ('Unread, older',     _date(2026, 9, 2), ''),
            ('Seen, oldest',      _date(2026, 8, 20), ''),
        ):
            t = make_tender(src, title=title, is_aec=True,
                            external_id=f'uo-{title}', publication_date=pub)
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG',
                                              image_url=image)
            cls.rows[title] = t

        # Two of the three photo-less rows have been opened before.
        from tenders.models import TenderView
        for title in ('Seen, newer', 'Seen, oldest'):
            TenderView.objects.create(tender=cls.rows[title], user=cls.reader)

    def _titles(self):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list') + '?view=news')
        self.assertEqual(resp.status_code, 200)
        return [t.title for t in resp.context['tenders']]

    def test_the_unread_photoless_card_leads_its_group(self):
        self.assertEqual(self._titles(), [
            'Photo card',
            'Unread, older',      # unread, so first among the photo-less
            'Seen, newer',        # then the seen ones, newest first
            'Seen, oldest',
        ])

    def test_it_never_climbs_above_a_photo_card(self):
        # The rule reorders inside the photo-less block only. An unread item
        # must not jump the wall of photos - that ordering was asked for first
        # and still holds.
        self.assertEqual(self._titles()[0], 'Photo card')


class TheDateLivesOnTheArticlePageTests(TestCase):
    """
    Every card shows its publication date, and so does the article page.

    The cards hid the date for a while - a date on every card invites the
    reader to treat the page as a timeline, which these feeds are not. It was
    asked back on 1 October: with the summary on the card, the reader wants
    to know how old the story is without opening it.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('date-vis',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')
        cls.tender = make_tender(src, title='A dated design commission',
                                 is_aec=True, external_id='dv-1',
                                 publication_date=_date(2026, 7, 4))
        MagazineTenderMeta.objects.create(tender=cls.tender, magazine='TESTMAG',
                                          image_url='https://cdn.example.com/p.jpg')

    def _html(self, url):
        self.client.force_login(self.reader)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_the_list_prints_the_date_on_the_card(self):
        html = self._html(reverse('magazine_tenders:list') + '?view=news')
        self.assertIn('A dated design commission', html)
        self.assertIn('04 Jul 2026', html)
        self.assertIn('class="nf-date"', html)

    def test_the_article_page_still_prints_it(self):
        html = self._html(reverse('tenders:detail', args=[self.tender.pk]))
        self.assertIn('04 Jul 2026', html)


class ProjectNewsReachesTheNewsPageTests(TestCase):
    """
    Construction project news appears on the News page even when the
    classifier marks it Non-AEC.

    The two gates answer different questions. The classifier asks "is this
    separable design or supervision work AJi could bid for?" - the right
    question for a tender, and far too narrow for a news page. Measured on the
    live data, it was holding back 122 of the kept items, among them the day's
    biggest stories: a ground-breaking at Dubai Healthcare City, a $221m MEP
    contract at Qiddiya.

    Only items the scraper positively labelled PROJECT come through. That
    label already required an event and a named piece of built work, so it is
    a construction test in its own right - which is why it can stand in for
    the classifier here and nowhere else. TENDER and DESIGN items still face
    the classifier, and the audit page is unchanged.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('proj-gate',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, desc, aec):
            t = make_tender(src, title=title, is_aec=aec,
                            external_id=f'pg-{title}',
                            publication_date=_date.today())
            t.description = desc
            t.save(update_fields=['description'])
            MagazineTenderMeta.objects.create(tender=t, magazine='TESTMAG',
                                              image_url='')
            return t

        row('Ground broken at the new hospital',
            '[PROJECT] Work has started on site.', False)
        row('A design commission the classifier rejected',
            '[DESIGN] A consultancy was appointed.', False)
        row('An AEC supervision contract', '[DESIGN] Supervision.', True)
        row('An old row with no keep label', 'No tag at all.', False)

    def _titles(self):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list') + '?view=news')
        self.assertEqual(resp.status_code, 200)
        return {x.title for x in resp.context['tenders']}

    def test_non_aec_project_news_is_shown(self):
        self.assertIn('Ground broken at the new hospital', self._titles())

    def test_non_aec_design_news_is_still_held_back(self):
        # The label is what earns the exemption, not simply being Non-AEC.
        self.assertNotIn('A design commission the classifier rejected',
                         self._titles())

    def test_an_untagged_row_is_still_held_back(self):
        self.assertNotIn('An old row with no keep label', self._titles())

    def test_aec_items_are_unaffected(self):
        self.assertIn('An AEC supervision contract', self._titles())

    def test_the_exemption_can_be_switched_off(self):
        from django.test import override_settings
        with override_settings(MAGAZINE_NEWS_INCLUDES_PROJECTS=False):
            self.assertNotIn('Ground broken at the new hospital',
                             self._titles())


class ArabicSourcesFaceTheClassifierTests(TestCase):
    """
    The PROJECT exemption does not apply to the Arabic dailies and wires.

    On 29 September the News page carried, from Al-Rai, Petra and Ad-Dustour:
    a princess launching a school programme, a municipality asking people to
    look after a newly paved street, a two-year government progress report
    and a courthouse whose works were finished. All of them non-AEC by the
    classifier; all of them on the page because the Arabic PROJECT rule had
    matched "developing the infrastructure" or "launch ... schools". For those
    sources the classifier decides alone; the rejected items move to the
    Non-AEC page, so the two lists stay complements.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('ar-gate', password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        def row(title, desc, aec, magazine):
            t = make_tender(src, title=title, is_aec=aec,
                            external_id=f'ar-{title}',
                            publication_date=_date.today())
            t.description = desc
            t.save(update_fields=['description'])
            MagazineTenderMeta.objects.create(tender=t, magazine=magazine)
            return t

        cls.alrai_rejected = row('برنامج البكالوريا في المدارس',
                                 '[PROJECT] إطلاق برنامج في مدارس', False, 'ALRAI')
        cls.petra_rejected = row('تطوير الطرق والمطارات',
                                 '[PROJECT] عامان من العمل', False, 'PETRA')
        cls.alrai_accepted = row('عطاء تصميم مستشفى',
                                 '[PROJECT] إحالة عطاء تصميم مستشفى', True, 'ALRAI')
        cls.english_rejected = row('Ground broken at the new hospital',
                                   '[PROJECT] Work has started on site.', False, 'TESTMAG')

    def _ids(self, url):
        self.client.force_login(self.reader)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return {t.pk for t in resp.context['tenders']}

    def _news(self):
        return self._ids(reverse('magazine_tenders:list') + '?view=news')

    def _audit(self):
        return self._ids(reverse('magazine_tenders:non_aec'))

    def test_the_registry_names_the_five_arabic_sources(self):
        self.assertEqual(sources.news_exemption_off_codes(),
                         ['ADDUSTOUR', 'ALARABIYA', 'ALGHAD', 'ALRAI', 'PETRA'])

    def test_rejected_arabic_project_news_is_not_on_news(self):
        news = self._news()
        self.assertNotIn(self.alrai_rejected.pk, news)
        self.assertNotIn(self.petra_rejected.pk, news)

    def test_it_is_on_the_non_aec_page_instead(self):
        audit = self._audit()
        self.assertIn(self.alrai_rejected.pk, audit)
        self.assertIn(self.petra_rejected.pk, audit)

    def test_an_arabic_item_the_classifier_accepts_is_still_news(self):
        self.assertIn(self.alrai_accepted.pk, self._news())
        self.assertNotIn(self.alrai_accepted.pk, self._audit())

    def test_the_english_exemption_is_unchanged(self):
        self.assertIn(self.english_rejected.pk, self._news())
        self.assertNotIn(self.english_rejected.pk, self._audit())

    def test_the_two_lists_stay_complements(self):
        self.assertEqual(self._news() & self._audit(), set())


class FutureProjectsRunByQuarterTests(TestCase):
    """
    Future Projects is ordered by the quarter the work is expected in.

    It is a pipeline, not a news feed. Nothing in it has happened, so the
    publication date orders it by when somebody typed the notice - which is
    noise - while the quarter is the one fact a reader scanning a pipeline
    is actually after. Soonest first, and projects with no stated quarter go
    last: "not known yet" is a real answer and must not pose as Q1.

    News is untouched: it IS a news feed and stays newest-first.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('q-order',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        cls.src = ScraperSource.objects.create(
            name='Ashghal Prospected', country=jo, is_active=False,
            base_url='https://example-ashghal.com',
            scraper_class='MAGAZINE_ASHGHALPROSPECTED')

        # Quarters relative to TODAY, so the test means the same thing in
        # any month: the current quarter, the next, one just passed, one long
        # passed, and one not stated. Created in an order that is neither the
        # wanted one nor publication order, so passing cannot be an accident
        # of insertion.
        from magazine_tenders import quarters
        cls.cur = quarters.current_key()
        for title, key, pub in (
            ('Just passed',       cls.shift(cls.cur, -1), _date(2026, 9, 1)),
            ('Not stated yet',    '',                     _date(2026, 9, 2)),
            ('Long passed',       cls.shift(cls.cur, -3), _date(2026, 8, 1)),
            ('Next quarter',      cls.shift(cls.cur, +1), _date(2026, 7, 1)),
            ('This quarter',      cls.cur,                _date(2026, 6, 1)),
        ):
            t = make_tender(cls.src, title=title, is_aec=True,
                            external_id=f'q-{title}', publication_date=pub)
            MagazineTenderMeta.objects.create(
                tender=t, magazine='ASHGHALPROSPECTED', image_url='',
                expected_quarter=key)

    @staticmethod
    def shift(key, n):
        """'2026Q4' shifted by n quarters: +1 -> '2027Q1', -1 -> '2026Q3'."""
        y, q = int(key[:4]), int(key[5])
        idx = y * 4 + (q - 1) + n
        return f'{idx // 4}Q{idx % 4 + 1}'

    def _titles(self, query):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list') + query)
        self.assertEqual(resp.status_code, 200)
        return [x.title for x in resp.context['tenders']]

    def test_this_quarter_leads_then_the_coming_then_the_passed_then_unknown(self):
        self.assertEqual(self._titles('?view=future'), [
            'This quarter',        # the current quarter opens the page
            'Next quarter',        # then what is coming, soonest first
            'Just passed',         # then quarters already behind us, most recent first
            'Long passed',
            'Not stated yet',      # no quarter: last
        ])

    def test_the_turn_of_the_year_is_handled(self):
        from magazine_tenders import quarters
        self.assertEqual(self.shift('2026Q4', 1), '2027Q1')
        self.assertEqual(self.shift('2027Q1', -1), '2026Q4')
        self.assertEqual(quarters.key_number('2026Q4'), 20264)
        self.assertLess(quarters.key_number('2026Q4'), quarters.key_number('2027Q1'))
        self.assertEqual(quarters.key_number(''), 0)

    def test_a_photo_does_not_let_a_later_quarter_jump_the_queue(self):
        # Photos lead on News. Here they must not, or the sequence restarts:
        # Q1, Q3, Q4, then Q1 again for the rows that happen to have a
        # picture. The pipeline order is the point of this page.
        from magazine_tenders.models import MagazineTenderMeta
        late = make_tender(self.src, title='Late but photogenic', is_aec=True,
                           external_id='q-photo')
        MagazineTenderMeta.objects.create(
            tender=late, magazine='ASHGHALPROSPECTED',
            image_url='https://cdn.example.com/p.jpg',
            expected_quarter=self.shift(self.cur, +4))
        titles = self._titles('?view=future')
        # a year out: after this quarter and the next, before the passed ones
        self.assertEqual(titles[:3], ['This quarter', 'Next quarter', 'Late but photogenic'])
        self.assertEqual(titles[-1], 'Not stated yet')

    def test_the_quarter_is_offered_to_the_template(self):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('magazine_tenders:list') + '?view=future')
        first = resp.context['tenders'][0]
        self.assertEqual(first.magazine_meta.expected_quarter, self.cur)
        self.assertEqual(first.fallback_label, f'Q{self.cur[5]} {self.cur[:4]}')


class QuarterKeyTests(TestCase):
    """The key itself: one sortable string from two different feeds."""

    def test_the_wording_ashghal_uses(self):
        from magazine_tenders.quarters import quarter_key
        self.assertEqual(
            quarter_key('Construction is expected Third Quarter 2026'),
            '2026Q3')

    def test_the_short_form(self):
        from magazine_tenders.quarters import quarter_key
        self.assertEqual(quarter_key('Tendering in Q2 2027'), '2027Q2')

    def test_a_date_when_no_quarter_is_written(self):
        from datetime import date

        from magazine_tenders.quarters import quarter_key
        self.assertEqual(quarter_key('', date(2026, 11, 4)), '2026Q4')
        self.assertEqual(quarter_key('', date(2026, 1, 1)), '2026Q1')
        self.assertEqual(quarter_key('', date(2026, 3, 31)), '2026Q1')
        self.assertEqual(quarter_key('', date(2026, 4, 1)), '2026Q2')

    def test_the_description_beats_the_date(self):
        from datetime import date

        from magazine_tenders.quarters import quarter_key
        # The publisher saying "expected Q1" outranks a date we inferred.
        self.assertEqual(
            quarter_key('expected First Quarter 2027', date(2026, 11, 4)),
            '2027Q1')

    def test_nothing_stated_is_empty_not_a_guess(self):
        from magazine_tenders.quarters import quarter_key
        self.assertEqual(quarter_key('', None), '')
        self.assertEqual(quarter_key('', 'not a date'), '')

    def test_the_key_sorts_chronologically_as_text(self):
        from magazine_tenders.quarters import quarter_key
        keys = [quarter_key('expected Fourth Quarter 2026'),
                quarter_key('expected First Quarter 2027'),
                quarter_key('expected Second Quarter 2026')]
        self.assertEqual(sorted(keys), ['2026Q2', '2026Q4', '2027Q1'])

    def test_the_short_form_is_case_insensitive_like_its_sibling(self):
        # The worded pattern took any casing; this one did not, so 'q3 2026'
        # parsed as nothing while 'Q3 2026' parsed fine.
        from magazine_tenders.quarters import quarter_key
        self.assertEqual(quarter_key('delivery in q3 2026'), '2026Q3')
        self.assertEqual(quarter_key('delivery in Q3 2026'), '2026Q3')

    def test_a_corrupt_key_produces_no_label(self):
        from magazine_tenders.quarters import quarter_label
        for bad in ('abcdQz', 'xxxxQ3', '2026X3', '2026Q', ''):
            with self.subTest(bad=bad):
                self.assertEqual(quarter_label(bad), '')

    def test_the_label_is_how_a_reader_writes_it(self):
        from magazine_tenders.quarters import quarter_label
        self.assertEqual(quarter_label('2026Q3'), 'Q3 2026')
        self.assertEqual(quarter_label(''), '')


class AForecastHasNoPublicationDateTests(TestCase):
    """
    A forthcoming project shows the quarter it is expected in, never a
    publication date.

    Dating a forecast reads as though the project has happened. The date it
    carries belongs to the notice, not the work, and on a pipeline page that
    is worse than showing nothing - so the quarter takes its place on the
    article, and the list shows no date at all.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date as _date

        from django.contrib.auth.models import User

        from magazine_tenders.models import MagazineTenderMeta

        cls.reader = User.objects.create_superuser('fc-date',
                                                   password='pw12345!')
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '🇯🇴', 'currency': 'JOD'})
        fut = ScraperSource.objects.create(
            name='Ashghal Prospected', country=jo, is_active=False,
            base_url='https://example-ashghal.com',
            scraper_class='MAGAZINE_ASHGHALPROSPECTED')
        news = ScraperSource.objects.create(
            name='Test Magazine', country=jo, is_active=False,
            base_url='https://example-magazine.com',
            scraper_class='MAGAZINE_TESTMAG')

        cls.forecast = make_tender(fut, title='A forthcoming roads package',
                                   is_aec=True, external_id='fc-1',
                                   publication_date=_date(2026, 7, 4))
        MagazineTenderMeta.objects.create(tender=cls.forecast,
                                          magazine='ASHGHALPROSPECTED',
                                          expected_quarter='2026Q3')
        cls.article = make_tender(news, title='A published design commission',
                                  is_aec=True, external_id='fc-2',
                                  publication_date=_date(2026, 7, 4))
        MagazineTenderMeta.objects.create(tender=cls.article,
                                          magazine='TESTMAG')

    def _html(self, tender):
        self.client.force_login(self.reader)
        resp = self.client.get(reverse('tenders:detail', args=[tender.pk]))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_the_forecast_shows_its_quarter_not_a_date(self):
        html = self._html(self.forecast)
        self.assertIn('Q3 2026', html)
        self.assertNotIn('04 Jul 2026', html)

    def test_an_ordinary_article_still_shows_its_date(self):
        html = self._html(self.article)
        self.assertIn('04 Jul 2026', html)

    def test_the_forthcoming_flag_matches_the_registry(self):
        self.assertTrue(self.forecast.magazine_meta.is_forthcoming)
        self.assertFalse(self.article.magazine_meta.is_forthcoming)
