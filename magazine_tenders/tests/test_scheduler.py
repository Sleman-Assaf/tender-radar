# magazine_tenders/tests/test_scheduler.py
"""
Scheduler policy.

The thing worth protecting here is that the daily sweep starts in exactly the
processes that serve the site and nowhere else. A scheduler that also started
under `manage.py test`, `migrate` or `collectstatic` would scrape during a
deploy — or, worse, during the test suite.
"""
import os
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from magazine_tenders import apps as mag_apps
from magazine_tenders.models import MagazineRun


class SchedulerPolicyTests(TestCase):
    def test_nothing_starts_under_manage_py_test(self):
        # The suite is running right now, so the real policy function must say
        # no. If this ever passes, the test run itself would start scraping.
        from tenders.apps import _should_start_scheduler
        self.assertFalse(_should_start_scheduler())

    @override_settings(MAGAZINE_ENABLED=False)
    def test_disabled_feature_starts_no_scheduler(self):
        with mock.patch.object(mag_apps, '_start_scheduler') as m_start:
            mag_apps.MagazineTendersConfig.ready(
                mag_apps.MagazineTendersConfig.__new__(mag_apps.MagazineTendersConfig))
            m_start.assert_not_called()

    @override_settings(MAGAZINE_ENABLED=True, MAGAZINE_INPROCESS_SCHEDULER=False)
    def test_inprocess_scheduler_can_be_turned_off(self):
        with mock.patch.object(mag_apps, '_start_scheduler') as m_start:
            mag_apps.MagazineTendersConfig.ready(
                mag_apps.MagazineTendersConfig.__new__(mag_apps.MagazineTendersConfig))
            m_start.assert_not_called()

    @override_settings(MAGAZINE_ENABLED=True, MAGAZINE_INPROCESS_SCHEDULER=True)
    def test_starts_when_the_process_policy_allows_it(self):
        with mock.patch('tenders.apps._should_start_scheduler', return_value=True), \
             mock.patch.object(mag_apps, '_start_scheduler') as m_start, \
             mock.patch.object(mag_apps, '_close_orphaned_runs') as m_close:
            mag_apps.MagazineTendersConfig.ready(
                mag_apps.MagazineTendersConfig.__new__(mag_apps.MagazineTendersConfig))
            m_start.assert_called_once()
            m_close.assert_called_once()

    def test_next_scheduled_run_is_none_without_a_scheduler(self):
        # This process runs no magazine scheduler, so the list page must show
        # "no next run" rather than raising.
        self.assertIsNone(mag_apps.next_scheduled_run())


class OrphanedRunTests(TestCase):
    def test_runs_left_running_are_closed_on_start(self):
        stale = MagazineRun.objects.create(magazine='TESTMAG',
                                           status=MagazineRun.Status.RUNNING)
        done = MagazineRun.objects.create(magazine='TESTMAG',
                                          status=MagazineRun.Status.SUCCESS,
                                          finished_at=timezone.now())

        mag_apps._close_orphaned_runs()

        stale.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual(stale.status, MagazineRun.Status.FAILED)
        self.assertIn('Interrupted', stale.error_message)
        self.assertIsNotNone(stale.finished_at)
        # A finished run is untouched.
        self.assertEqual(done.status, MagazineRun.Status.SUCCESS)


class ScheduleTimeParsingTests(TestCase):
    """A bad MAGAZINE_SCHEDULE_TIME must fall back, never crash startup."""

    def _start_and_capture(self):
        with mock.patch('apscheduler.schedulers.background.BackgroundScheduler') as m_sched:
            instance = m_sched.return_value
            instance.add_job.return_value = mock.Mock(next_run_time=None)
            mag_apps._start_scheduler()
            return instance.add_job.call_args

    @override_settings(MAGAZINE_SCHEDULE_TIME='09:30')
    def test_valid_time_is_used(self):
        # CronTrigger renders unpadded, e.g. cron[hour='9', minute='30'].
        trigger = str(self._start_and_capture().kwargs['trigger'])
        self.assertIn("hour='9'", trigger)
        self.assertIn("minute='30'", trigger)

    @override_settings(MAGAZINE_SCHEDULE_TIME='not-a-time')
    def test_invalid_time_falls_back_to_seven(self):
        trigger = str(self._start_and_capture().kwargs['trigger'])
        self.assertIn("hour='7'", trigger)
        self.assertIn("minute='0'", trigger)

    @override_settings(MAGAZINE_SCHEDULE_TIME='99:99')
    def test_out_of_range_time_falls_back_to_seven(self):
        trigger = str(self._start_and_capture().kwargs['trigger'])
        self.assertIn("hour='7'", trigger)

    def tearDown(self):
        # _start_scheduler stores a module-level reference; clear it so it does
        # not leak into other tests.
        mag_apps._scheduler = None
