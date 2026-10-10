"""
Tests for who may start a scan, and for the automatic 6-hourly run.

Three things this file exists to guarantee:

  1. a regular user can start a scan — the "Run All Scanners" button is on the
     dashboard, which every user sees, and it used to answer 403;
  2. two scans never run at once, however many people press the button;
  3. the scheduler starts in the processes that serve the site and in no others.
     It did not start under `python serve_http.py` — the way this project is
     actually launched — so the automatic run silently never happened.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tenders import apps as tenders_apps
from tenders.models import (CountryConfig, ScrapeLog, ScraperSource, SystemRole,
                            UserProfile)

MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _make_user(username, role=SystemRole.USER, superuser=False):
    user = User.objects.create_user(username, password='pw-for-tests')
    if superuser:
        user.is_superuser = user.is_staff = True
        user.save()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save(update_fields=['role'])
    return user



class ScanStateMixin:
    """
    Reset the scan locks around every test.

    The run guard is module-level state shared by the whole process, so a test
    that leaves a slot claimed silently breaks a later one — and because Django
    rolls each TestCase back, primary keys get reused, so the leaked id belongs to
    a different source by then. Tests must be order-independent.
    """

    def setUp(self):
        super().setUp()
        self._reset_scan_state()
        self.addCleanup(self._reset_scan_state)

    @staticmethod
    def _reset_scan_state():
        import time
        from tenders.scrapers import base as scraper_base

        # Give a worker thread a moment to release the slot itself. Snatching the
        # lock out from under a live thread makes its own release() raise
        # "release unlocked lock", which is noise that can hide a real error.
        deadline = time.monotonic() + 2.0
        while tenders_apps._full_run_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        if tenders_apps._full_run_lock.locked():
            try:
                tenders_apps._full_run_lock.release()   # genuinely leaked
            except RuntimeError:
                pass
        with tenders_apps._active_lock:
            tenders_apps._active_sources.clear()
        with scraper_base._console_lock:
            scraper_base._console_depth = 0
            scraper_base._console_saved = None


class ScanPermissionTests(ScanStateMixin, TestCase):
    """Starting a scan is open to every signed-in user; anonymous is not."""

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.source = ScraperSource.objects.create(
            name='Test source', country=cls.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        cls.user  = _make_user('plain_user')
        cls.admin = _make_user('super_user', SystemRole.SUPER_ADMIN, superuser=True)

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _post(self, user, url):
        if user is not None:
            self.client.force_login(user, backend=MODEL_BACKEND)
        # The scan itself must not run in a test: patch the workers, not the
        # view, so the permission and locking logic is what gets exercised.
        # Both entry points are stubbed so the spawned thread does no real
        # database work — under SQLite a thread querying inside the test's own
        # transaction reports "database table is locked" on stderr.
        with mock.patch.object(tenders_apps, '_do_full_run'), \
             mock.patch.object(tenders_apps, '_run_single_scraper'):
            return self.client.post(url)

    def test_regular_user_can_start_a_full_scan(self):
        r = self._post(self.user, reverse('tenders:trigger_all'))
        self.assertEqual(r.status_code, 200, 'a regular user must be able to scan')
        self.assertEqual(r.json()['status'], 'started')

    def test_regular_user_can_start_one_source(self):
        r = self._post(self.user,
                       reverse('tenders:trigger_scraper', args=[self.source.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'started')

    def test_admin_can_start_a_full_scan(self):
        r = self._post(self.admin, reverse('tenders:trigger_all'))
        self.assertEqual(r.status_code, 200)

    def test_anonymous_is_sent_to_the_login_page(self):
        r = self._post(None, reverse('tenders:trigger_all'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/', r['Location'])

    def test_get_is_not_allowed(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        self.assertEqual(
            self.client.get(reverse('tenders:trigger_all')).status_code, 405)

    def test_status_is_readable_by_a_regular_user(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        r = self.client.get(reverse('tenders:scraper_status'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('running', r.json())


class ScanLockTests(ScanStateMixin, TestCase):
    """One scan at a time, no matter how many callers."""

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.source = ScraperSource.objects.create(
            name='Test source', country=cls.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        cls.user = _make_user('plain_user')

    def test_second_full_scan_is_refused_while_one_runs(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        # Hold the lock the way a run in progress would.
        self.assertTrue(tenders_apps._full_run_lock.acquire(blocking=False))
        try:
            with mock.patch.object(tenders_apps, '_do_full_run') as worker:
                r = self.client.post(reverse('tenders:trigger_all'))
            self.assertEqual(r.json()['status'], 'busy')
            worker.assert_not_called()
        finally:
            tenders_apps._full_run_lock.release()

    def test_a_source_already_scanning_is_refused(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        with tenders_apps._active_lock:
            tenders_apps._active_sources.add(self.source.pk)
        try:
            with mock.patch.object(tenders_apps, '_scrape_one_source') as worker:
                r = self.client.post(
                    reverse('tenders:trigger_scraper', args=[self.source.pk]))
            self.assertEqual(r.json()['status'], 'busy')
            worker.assert_not_called()
        finally:
            with tenders_apps._active_lock:
                tenders_apps._active_sources.discard(self.source.pk)

    def test_full_run_skips_itself_rather_than_doubling_up(self):
        """The scheduled job firing during a manual run must not run twice."""
        self.assertTrue(tenders_apps._full_run_lock.acquire(blocking=False))
        try:
            with mock.patch.object(tenders_apps, '_scrape_one_source') as one:
                tenders_apps._run_all_scrapers()
            one.assert_not_called()
        finally:
            tenders_apps._full_run_lock.release()

    def test_lock_is_released_after_a_run(self):
        with mock.patch.object(tenders_apps, '_scrape_one_source', return_value=True):
            tenders_apps._run_all_scrapers()
        self.assertFalse(tenders_apps.full_run_is_active())


class SchedulerStartupTests(TestCase):
    """
    `_should_start_scheduler()` decides whether this process owns the 6-hourly
    run. Getting it wrong either skips the automatic scan entirely or runs it
    twice, and both failures are silent.
    """

    def _decide(self, argv, env=None):
        with mock.patch('sys.argv', argv), \
             mock.patch.dict('os.environ', env or {}, clear=False):
            for key in ('RUN_MAIN', 'TENDERRADAR_SCHEDULER'):
                if key not in (env or {}):
                    import os
                    os.environ.pop(key, None)
            return tenders_apps._should_start_scheduler()

    def test_starts_under_the_production_http_server(self):
        # The regression: this is how start_http.bat launches the site.
        self.assertTrue(self._decide(['serve_http.py']))

    def test_starts_under_the_https_server(self):
        self.assertTrue(self._decide(['serve_https.py']))

    def test_starts_under_waitress_and_gunicorn(self):
        self.assertTrue(self._decide(['waitress-serve', 'tender_radar.wsgi']))
        self.assertTrue(self._decide(['gunicorn', 'tender_radar.wsgi']))

    def test_starts_in_the_runserver_child_only(self):
        self.assertTrue(self._decide(['manage.py', 'runserver'],
                                     {'RUN_MAIN': 'true'}))
        self.assertFalse(self._decide(['manage.py', 'runserver']))

    def test_never_starts_for_deploy_commands(self):
        for cmd in ('migrate', 'collectstatic', 'shell', 'test',
                    'makemigrations', 'createsuperuser'):
            self.assertFalse(self._decide(['manage.py', cmd]), cmd)

    def test_environment_override_wins_both_ways(self):
        self.assertFalse(self._decide(['serve_http.py'],
                                      {'TENDERRADAR_SCHEDULER': '0'}))
        self.assertTrue(self._decide(['manage.py', 'migrate'],
                                     {'TENDERRADAR_SCHEDULER': '1'}))


class OrphanedScrapeLogTests(TestCase):
    """A run killed mid-flight leaves RUNNING rows that read as 'scanning now'."""

    def test_interrupted_runs_are_closed_at_startup(self):
        country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        source = ScraperSource.objects.create(
            name='Test source', country=country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        stale = ScrapeLog.objects.create(source=source)
        self.assertEqual(stale.status, ScrapeLog.RunStatus.RUNNING)

        tenders_apps._reset_orphaned_scrape_logs()

        stale.refresh_from_db()
        self.assertEqual(stale.status, ScrapeLog.RunStatus.FAILED)
        self.assertIsNotNone(stale.finished_at)
        self.assertIn('Interrupted', stale.error_message)


class MutatingGetTests(TestCase):
    """Bookmarks and favourites must not change on a bare GET."""

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.user = _make_user('plain_user')

    def test_bookmark_and_favourite_reject_get(self):
        from tenders.models import ScraperSource, Tender
        source = ScraperSource.objects.create(
            name='Test source', country=self.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        tender = Tender.objects.create(
            source=source, country=self.country, title='T',
            source_url='https://example.invalid/1', external_id='1')

        self.client.force_login(self.user, backend=MODEL_BACKEND)
        self.assertEqual(
            self.client.get(reverse('tenders:toggle_bookmark',
                                    args=[tender.pk])).status_code, 405)
        self.assertEqual(
            self.client.get(reverse('tenders:toggle_favorite_country',
                                    args=['JO'])).status_code, 405)

        from tenders.models import TenderBookmark
        self.assertFalse(TenderBookmark.objects.filter(tender=tender).exists())
        self.assertEqual(
            UserProfile.objects.get(user=self.user).get_favorite_codes(), [])

    def test_post_still_works(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        r = self.client.post(reverse('tenders:toggle_favorite_country',
                                     args=['JO']))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['favorites'], ['JO'])


class TolerantConsoleTests(TestCase):
    """
    A progress print must never be reported as a scrape failure.

    Reproduces the exact shape of the production fault: colorama's console
    converter raising OSError(EINVAL) from write/flush when the process has no
    real console.
    """

    class _BrokenConsole:
        """Stands in for colorama on a handle that is not a console."""
        def write(self, text):
            raise OSError(22, 'Invalid argument')

        def flush(self):
            raise OSError(22, 'Invalid argument')

    def test_print_does_not_raise_through_the_wrapper(self):
        import sys
        from tenders.scrapers.base import tolerant_console

        original = sys.stdout
        sys.stdout = self._BrokenConsole()
        try:
            # Without the wrapper this is the production failure.
            with self.assertRaises(OSError):
                print('─' * 62)
            # With it, the same print is harmless.
            with tolerant_console():
                print('─' * 62)
                print('progress', flush=True)
        finally:
            sys.stdout = original

    def test_a_scraper_that_only_fails_to_print_still_succeeds(self):
        import sys
        from tenders.scrapers.base import tolerant_console

        def scrape():
            print('─' * 62)          # would raise OSError on a broken console
            return ['tender-a', 'tender-b']

        original = sys.stdout
        sys.stdout = self._BrokenConsole()
        try:
            with tolerant_console():
                result = scrape()
        finally:
            sys.stdout = original
        self.assertEqual(result, ['tender-a', 'tender-b'])

    def test_real_output_still_reaches_a_working_stream(self):
        import io
        import sys
        from tenders.scrapers.base import tolerant_console

        buf = io.StringIO()
        original = sys.stdout
        sys.stdout = buf
        try:
            with tolerant_console():
                print('kept')
        finally:
            sys.stdout = original
        self.assertIn('kept', buf.getvalue())


class StartScanClaimsTheSlotTests(ScanStateMixin, TestCase):
    """
    start_full_run / start_source_run must claim the slot before answering.

    Asking "is a scan running?" and then spawning a thread is two steps: two
    people pressing the button in the same second both saw "no", both were told
    the scan had started, and only one run happened.
    """

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.source = ScraperSource.objects.create(
            name='Test source', country=cls.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')

    def test_only_one_of_two_simultaneous_starts_succeeds(self):
        import threading as th

        started = []
        gate    = th.Event()

        def slow_run():
            gate.wait(timeout=5)        # hold the slot while both calls race

        with mock.patch.object(tenders_apps, '_do_full_run', slow_run):
            try:
                results = [tenders_apps.start_full_run() for _ in range(5)]
                started = [r for r in results if r]
                self.assertEqual(len(started), 1,
                                 'exactly one caller may be told the scan started')
            finally:
                gate.set()

        # And once it finishes the slot frees up again.
        for _ in range(50):
            if not tenders_apps.full_run_is_active():
                break
            import time
            time.sleep(0.02)
        self.assertFalse(tenders_apps.full_run_is_active())

    def test_source_start_is_refused_while_that_source_runs(self):
        import threading as th

        gate = th.Event()

        def slow_one(source, claimed=False):
            gate.wait(timeout=5)
            return True

        with mock.patch.object(tenders_apps, '_scrape_one_source', slow_one):
            try:
                first  = tenders_apps.start_source_run(self.source.pk)
                second = tenders_apps.start_source_run(self.source.pk)
                self.assertTrue(first)
                self.assertFalse(second, 'the same source must not start twice')
            finally:
                gate.set()

    def test_source_start_is_refused_during_a_full_run(self):
        self.assertTrue(tenders_apps._full_run_lock.acquire(blocking=False))
        try:
            with mock.patch.object(tenders_apps, '_scrape_one_source') as worker:
                self.assertFalse(tenders_apps.start_source_run(self.source.pk))
                worker.assert_not_called()
        finally:
            tenders_apps._full_run_lock.release()

    def test_a_missing_source_frees_the_slot_again(self):
        """Otherwise a Run on a deleted source would block it forever."""
        missing = 9_999_999
        tenders_apps._run_single_scraper(missing, claimed=True)
        self.assertFalse(tenders_apps.source_run_is_active(missing))


class ConcurrentConsoleTests(ScanStateMixin, TestCase):
    """Two overlapping scrapes must leave sys.stdout exactly as they found it."""

    def test_nested_use_restores_the_original_stream(self):
        import io
        import sys
        from tenders.scrapers.base import tolerant_console

        original = sys.stdout
        sentinel = io.StringIO()
        sys.stdout = sentinel
        try:
            with tolerant_console():
                inner_a = sys.stdout
                with tolerant_console():          # a second scrape starts
                    print('from the second')
                # the first is still inside, so the wrapper must still be in place
                self.assertIs(sys.stdout, inner_a)
                print('from the first')
            self.assertIs(sys.stdout, sentinel,
                          'the last one out must restore the real stream')
        finally:
            sys.stdout = original
        self.assertIn('from the first', sentinel.getvalue())
        self.assertIn('from the second', sentinel.getvalue())

    def test_interleaved_threads_do_not_strand_a_wrapper(self):
        import io
        import sys
        import threading as th
        from tenders.scrapers.base import tolerant_console

        original = sys.stdout
        sentinel = io.StringIO()
        sys.stdout = sentinel
        a_in, b_in, a_out = th.Event(), th.Event(), th.Event()

        def thread_a():
            with tolerant_console():
                a_in.set()
                b_in.wait(timeout=5)     # B enters while A is inside
            a_out.set()                  # A leaves first

        def thread_b():
            a_in.wait(timeout=5)
            with tolerant_console():
                b_in.set()
                a_out.wait(timeout=5)    # B leaves last

        try:
            ta, tb = th.Thread(target=thread_a), th.Thread(target=thread_b)
            ta.start(); tb.start(); ta.join(6); tb.join(6)
            self.assertIs(sys.stdout, sentinel,
                          'stdout must not be left wrapped after both finish')
        finally:
            sys.stdout = original
