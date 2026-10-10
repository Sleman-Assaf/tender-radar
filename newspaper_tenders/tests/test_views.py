# newspaper_tenders/tests/test_views.py
# Dashboard rules: /newspaper-tenders/ shows ONLY newspaper tenders with
# is_aec=True; ordinary tenders never appear there; non-AEC newspaper
# extractions are stored for audit but never rendered.

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenders.models import CountryConfig, ScraperSource, Tender

from newspaper_tenders.models import (NewspaperEdition, NewspaperEditionFile,
                                      NewspaperTenderMeta)
from newspaper_tenders.pipeline import get_newspaper_source

FUTURE = date.today() + timedelta(days=30)


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
        submission_deadline=FUTURE,
        **extra,
    )


class NewspaperListViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('reader', password='pw12345!')
        cls.np_source = get_newspaper_source()
        jo = CountryConfig.objects.get(code='JO')
        cls.web_source = ScraperSource.objects.create(
            name='Some Web Portal', country=jo,
            base_url='https://portal.example.com', scraper_class='JordanGTDScraper')

        cls.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 19),
            article_url='https://alrai.com/article/10972360/x/y')
        cls.file = NewspaperEditionFile.objects.create(
            edition=cls.edition, pdf_url='https://alrai.com/x/763671.pdf')

        # 1) AEC newspaper tender — MUST be listed
        cls.shown = make_tender(cls.np_source, title='عطاء إشراف هندسي',
                                title_ar='عطاء إشراف هندسي',
                                external_id='alrai-shown')
        NewspaperTenderMeta.objects.create(
            tender=cls.shown, edition=cls.edition, edition_file=cls.file,
            page_number=3, ai_confidence=0.9,
            original_pdf_url=cls.file.pdf_url)

        # 2) non-AEC newspaper tender — audited, MUST NOT be listed
        cls.hidden = make_tender(cls.np_source, title='توريد مركبات',
                                 is_aec=False, external_id='alrai-hidden')
        NewspaperTenderMeta.objects.create(
            tender=cls.hidden, edition=cls.edition, edition_file=cls.file,
            page_number=5, original_pdf_url=cls.file.pdf_url)

        # 3) ordinary web tender (AEC) — MUST NOT appear in the newspaper page
        cls.web_tender = make_tender(cls.web_source, title='Web portal tender')

    def setUp(self):
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        resp = self.client.get(reverse('newspaper_tenders:list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp['Location'])

    def test_lists_only_aec_newspaper_tenders(self):
        resp = self.client.get(reverse('newspaper_tenders:list'))
        self.assertEqual(resp.status_code, 200)
        tenders = list(resp.context['tenders'])
        self.assertEqual(tenders, [self.shown])

    def test_provenance_rendered(self):
        resp = self.client.get(reverse('newspaper_tenders:list'))
        html = resp.content.decode()
        self.assertIn('Newspaper Tenders', html)           # renamed page title
        self.assertIn('Page 3', html)
        self.assertIn(self.file.pdf_url, html)             # "Open PDF" link
        self.assertIn('adphoto-overlay', html)             # photo popup present
        self.assertIn('listAECFeedback', html)             # like/dislike wired

    def test_search_filter(self):
        resp = self.client.get(reverse('newspaper_tenders:list'), {'q': 'إشراف'})
        self.assertEqual(list(resp.context['tenders']), [self.shown])
        resp = self.client.get(reverse('newspaper_tenders:list'), {'q': 'zzz-none'})
        self.assertEqual(list(resp.context['tenders']), [])

    def test_edition_filter(self):
        resp = self.client.get(reverse('newspaper_tenders:list'),
                               {'edition': '2026-08-19'})
        self.assertEqual(list(resp.context['tenders']), [self.shown])
        resp = self.client.get(reverse('newspaper_tenders:list'),
                               {'edition': '2026-08-13'})
        self.assertEqual(list(resp.context['tenders']), [])

    def test_newspaper_tenders_not_in_page_for_web_tender(self):
        resp = self.client.get(reverse('newspaper_tenders:list'))
        self.assertNotContains(resp, 'Web portal tender')
        self.assertNotContains(resp, 'توريد مركبات')       # non-AEC hidden

    def test_engineering_tenders_search_includes_newspaper(self):
        # Browsing /tenders/ hides newspaper tenders…
        admin = User.objects.create_superuser('super-s', 's@x.jo', 'pw12345!')
        self.client.force_login(admin)
        resp = self.client.get(reverse('tenders:list'))
        self.assertNotIn(self.shown, resp.context['tenders'])
        # …but SEARCHING includes them.
        resp = self.client.get(reverse('tenders:list'), {'q': 'إشراف هندسي'})
        self.assertIn(self.shown, list(resp.context['tenders']))

    def test_detail_page_shows_provenance(self):
        resp = self.client.get(reverse('tenders:detail', args=[self.shown.pk]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Al-Rai', html)              # dynamic per-newspaper name
        self.assertIn('Open PDF', html)

    def test_detail_page_of_web_tender_has_no_provenance(self):
        resp = self.client.get(reverse('tenders:detail', args=[self.web_tender.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'صحيفة الرأي')

    def test_badges_aec_vs_non_aec(self):
        # In the admin non-AEC view the Non-AEC badge renders
        admin = User.objects.create_superuser('it-admin', 'a@x.jo', 'pw12345!')
        self.client.force_login(admin)
        resp = self.client.get(reverse('newspaper_tenders:non_aec'))
        self.assertContains(resp, 'Non-AEC')


class NonAECAdminViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('boss2', 'b2@x.jo', 'pw12345!')
        cls.user = User.objects.create_user('normal', password='pw12345!')
        src = get_newspaper_source()
        cls.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 19))
        f = NewspaperEditionFile.objects.create(
            edition=cls.edition, pdf_url='https://alrai.com/x/1.pdf')
        cls.aec = make_tender(src, title='عطاء إشراف', external_id='alrai-a')
        NewspaperTenderMeta.objects.create(tender=cls.aec, edition=cls.edition,
                                           edition_file=f, page_number=1)
        cls.non_aec = make_tender(src, title='توريد قرطاسية', is_aec=False,
                                  external_id='alrai-n')
        NewspaperTenderMeta.objects.create(tender=cls.non_aec, edition=cls.edition,
                                           edition_file=f, page_number=2)

    def test_admin_sees_only_non_aec(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('newspaper_tenders:non_aec'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['tenders']), [self.non_aec])
        self.assertContains(resp, 'توريد قرطاسية')
        self.assertNotContains(resp, 'عطاء إشراف')

    def test_regular_user_blocked(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('newspaper_tenders:non_aec'))
        self.assertIn(resp.status_code, (302, 403))   # denied by admin_required

    def test_main_page_shows_only_aec_by_default(self):
        # NEWSPAPER_AEC_ONLY defaults to True — the main page hides non-AEC
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('newspaper_tenders:list'))
        self.assertEqual(list(resp.context['tenders']), [self.aec])


class RetryEditionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('boss3', 'b3@x.jo', 'pw12345!')
        cls.user = User.objects.create_user('plain', password='pw12345!')
        get_newspaper_source()
        cls.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 20),
            status=NewspaperEdition.Status.PROCESSING)

    def tearDown(self):
        # The thread target is mocked in these tests, so its `finally` that
        # normally clears the busy flag never runs — clear it by hand.
        from newspaper_tenders import runlock
        runlock.release()

    def test_admin_can_retry(self):
        from newspaper_tenders import views
        self.client.force_login(self.admin)
        with mock.patch.object(views, '_run_edition_retry') as m_run:
            resp = self.client.post(
                reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'started')

    def test_busy_lock_blocks_second_retry(self):
        from newspaper_tenders import views
        self.client.force_login(self.admin)
        with mock.patch.object(views, '_run_edition_retry'):
            r1 = self.client.post(
                reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]))
            r2 = self.client.post(
                reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]))
        self.assertEqual(r1.json()['status'], 'started')
        self.assertEqual(r2.json()['status'], 'busy')

    def test_unknown_edition_404(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('newspaper_tenders:retry_edition', args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_regular_user_blocked(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]))
        self.assertIn(resp.status_code, (302, 403))


class RescanEditionTests(TestCase):
    """
    force=1 — the full rescan behind the Rescan button on /it/runs/.

    A plain retry deliberately skips pages already marked DONE, which is why a
    separate action exists at all: an edition processed before an AI-service
    improvement needs those finished pages read again to pick up what was
    missed the first time (the advertisement photos, in practice). The
    difference is entirely in whether the file rows are reset, so that is what
    these tests assert.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('boss4', 'b4@x.jo', 'pw12345!')
        get_newspaper_source()
        cls.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 22),
            status=NewspaperEdition.Status.DONE)

    def setUp(self):
        # Fresh files per test: the force branch mutates them.
        self.edition.files.all().delete()
        self.done = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url='https://alrai.com/x/done.pdf',
            status=NewspaperEditionFile.Status.DONE)
        self.failed = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url='https://alrai.com/x/failed.pdf',
            status=NewspaperEditionFile.Status.FAILED,
            error_message='timed out')
        NewspaperEdition.objects.filter(pk=self.edition.pk).update(
            status=NewspaperEdition.Status.DONE)

    def tearDown(self):
        from newspaper_tenders import runlock
        runlock.release()

    def _post(self, **data):
        from newspaper_tenders import views
        self.client.force_login(self.admin)
        with mock.patch.object(views, '_run_edition_retry'):
            return self.client.post(
                reverse('newspaper_tenders:retry_edition',
                        args=[self.edition.pk]), data)

    def test_rescan_resets_every_page_including_finished_ones(self):
        resp = self._post(force='1')
        self.assertEqual(resp.json()['status'], 'started')
        statuses = set(self.edition.files.values_list('status', flat=True))
        self.assertEqual(statuses, {NewspaperEditionFile.Status.PENDING})

    def test_rescan_clears_the_stale_error_message(self):
        self._post(force='1')
        self.failed.refresh_from_db()
        self.assertEqual(self.failed.error_message, '')

    def test_rescan_puts_the_edition_back_into_processing(self):
        self._post(force='1')
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.status,
                         NewspaperEdition.Status.PROCESSING)

    def test_plain_retry_leaves_finished_pages_alone(self):
        # The whole reason both buttons exist. A retry must NOT undo work.
        resp = self._post()
        self.assertEqual(resp.json()['status'], 'started')
        self.done.refresh_from_db()
        self.assertEqual(self.done.status, NewspaperEditionFile.Status.DONE)

    def test_the_two_answers_say_which_one_ran(self):
        # The page shows this message verbatim in a toast, so an administrator
        # can see whether they started a retry or a full rescan.
        rescan = self._post(force='1').json()['message']
        from newspaper_tenders import runlock
        runlock.release()
        retry = self._post().json()['message']
        self.assertIn('ALL pages', rescan)
        self.assertNotIn('ALL pages', retry)

    def test_a_falsy_force_value_is_not_a_rescan(self):
        # 'force=0' arrives as a string and must not read as truthy.
        self._post(force='0')
        self.done.refresh_from_db()
        self.assertEqual(self.done.status, NewspaperEditionFile.Status.DONE)

    def test_get_not_allowed(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_force_reprocess_resets_done_files(self):
        from newspaper_tenders import views
        f1 = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url='https://x/1.pdf',
            status=NewspaperEditionFile.Status.DONE)
        f2 = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url='https://x/2.pdf',
            status=NewspaperEditionFile.Status.FAILED, error_message='old err')
        self.client.force_login(self.admin)
        with mock.patch.object(views, '_run_edition_retry'):
            resp = self.client.post(
                reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]),
                {'force': '1'})
        self.assertEqual(resp.json()['status'], 'started')
        f1.refresh_from_db(); f2.refresh_from_db()
        self.assertEqual(f1.status, NewspaperEditionFile.Status.PENDING)
        self.assertEqual(f2.status, NewspaperEditionFile.Status.PENDING)
        self.assertEqual(f2.error_message, '')

    def test_plain_retry_keeps_done_files(self):
        from newspaper_tenders import views
        f1 = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url='https://x/3.pdf',
            status=NewspaperEditionFile.Status.DONE)
        self.client.force_login(self.admin)
        with mock.patch.object(views, '_run_edition_retry'):
            self.client.post(
                reverse('newspaper_tenders:retry_edition', args=[self.edition.pk]))
        f1.refresh_from_db()
        self.assertEqual(f1.status, NewspaperEditionFile.Status.DONE)
