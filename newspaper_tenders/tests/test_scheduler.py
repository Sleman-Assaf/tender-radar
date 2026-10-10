# newspaper_tenders/tests/test_scheduler.py
"""
The 08:00 automatic run.

Two things are worth protecting here.

That it starts in exactly the processes that serve the site and nowhere else —
a scheduler that also started under `manage.py test`, `migrate` or
`collectstatic` would download and AI-process a newspaper during a deploy, or
during this very suite.

And that the job claims the same run slot the buttons claim. That is not about
tidiness: the Reset button asks "is a run in progress?" before deleting an
edition's tenders and photos, so a scheduled run it could not see would be
writing rows while Reset wiped them.
"""
import os
from datetime import date
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from newspaper_tenders import apps as np_apps
from newspaper_tenders import runlock


class SchedulerPolicyTests(TestCase):
    def test_nothing_starts_under_manage_py_test(self):
        # The suite is running right now, so the real policy function must say
        # no. If this ever passes, the test run itself would start scraping.
        from tenders.apps import _should_start_scheduler
        self.assertFalse(_should_start_scheduler())

    def test_ready_schedules_nothing_in_the_test_process(self):
        with mock.patch.object(np_apps, '_start_scheduler') as m_start:
            np_apps.NewspaperTendersConfig.ready(
                np_apps.NewspaperTendersConfig.__new__(
                    np_apps.NewspaperTendersConfig))
            m_start.assert_not_called()

    @override_settings(NEWSPAPER_ENABLED=False)
    def test_disabled_feature_starts_no_scheduler(self):
        with mock.patch('tenders.apps._should_start_scheduler', return_value=True), \
             mock.patch.object(np_apps, '_start_scheduler') as m_start:
            np_apps._maybe_start_scheduler()
            m_start.assert_not_called()

    @override_settings(NEWSPAPER_ENABLED=True,
                       NEWSPAPER_INPROCESS_SCHEDULER=False)
    def test_inprocess_scheduler_can_be_turned_off(self):
        # What a second web worker against the same database sets, so that only
        # one process schedules.
        with mock.patch('tenders.apps._should_start_scheduler', return_value=True), \
             mock.patch.object(np_apps, '_start_scheduler') as m_start:
            np_apps._maybe_start_scheduler()
            m_start.assert_not_called()

    @override_settings(NEWSPAPER_ENABLED=True,
                       NEWSPAPER_INPROCESS_SCHEDULER=True)
    def test_starts_when_the_process_policy_allows_it(self):
        with mock.patch('tenders.apps._should_start_scheduler', return_value=True), \
             mock.patch.object(np_apps, '_start_scheduler') as m_start:
            np_apps._maybe_start_scheduler()
            m_start.assert_called_once()

    def test_next_scheduled_run_is_none_without_a_scheduler(self):
        # This process runs no newspaper scheduler, so the list page must show
        # no "next run" rather than raising.
        self.assertIsNone(np_apps.next_scheduled_run())


class ScheduleTimeTests(TestCase):
    """The requested time, and a bad value that must not break startup."""

    def _start_and_capture(self):
        with mock.patch('apscheduler.schedulers.background.BackgroundScheduler') as m_sched:
            instance = m_sched.return_value
            instance.add_job.return_value = mock.Mock(next_run_time=None)
            np_apps._start_scheduler()
            return instance.add_job.call_args

    def test_the_shipped_default_is_eight_in_the_morning(self):
        # The requirement: the newspaper runs by itself at 08:00 Amman. The
        # environment can still override it, so skip rather than fail there.
        if 'NEWSPAPER_SCHEDULE_TIME' in os.environ:
            self.skipTest('NEWSPAPER_SCHEDULE_TIME is set in the environment')
        self.assertEqual(settings.NEWSPAPER_SCHEDULE_TIME, '08:00')
        self.assertEqual(settings.NEWSPAPER_TIMEZONE, 'Asia/Amman')

    @override_settings(NEWSPAPER_SCHEDULE_TIME='08:00',
                       NEWSPAPER_TIMEZONE='Asia/Amman')
    def test_eight_am_reaches_the_trigger(self):
        call = self._start_and_capture()
        # CronTrigger renders unpadded, e.g. cron[hour='8', minute='0'].
        trigger = str(call.kwargs['trigger'])
        self.assertIn("hour='8'", trigger)
        self.assertIn("minute='0'", trigger)

    @override_settings(NEWSPAPER_SCHEDULE_TIME='06:30')
    def test_another_valid_time_is_honoured(self):
        trigger = str(self._start_and_capture().kwargs['trigger'])
        self.assertIn("hour='6'", trigger)
        self.assertIn("minute='30'", trigger)

    @override_settings(NEWSPAPER_SCHEDULE_TIME='not-a-time')
    def test_invalid_time_falls_back_to_eight(self):
        trigger = str(self._start_and_capture().kwargs['trigger'])
        self.assertIn("hour='8'", trigger)
        self.assertIn("minute='0'", trigger)

    @override_settings(NEWSPAPER_SCHEDULE_TIME='99:99')
    def test_out_of_range_time_falls_back_to_eight(self):
        trigger = str(self._start_and_capture().kwargs['trigger'])
        self.assertIn("hour='8'", trigger)

    def test_a_missed_fire_still_runs(self):
        # A newspaper edition is only available on its own day, so a fire
        # dropped because the machine was asleep loses that day for good.
        call = self._start_and_capture()
        self.assertEqual(call.kwargs['misfire_grace_time'], 3600)
        self.assertEqual(call.kwargs['max_instances'], 1)

    def tearDown(self):
        # _start_scheduler keeps a module-level reference; clear it so it does
        # not leak into other tests.
        np_apps._scheduler = None


class ScheduledRunTests(TestCase):
    """What the job itself does when it fires."""

    def tearDown(self):
        runlock.release()

    def test_it_runs_todays_edition_in_the_newspaper_timezone(self):
        # Not the server's zone: the edition on sale at 08:00 in Amman is
        # Amman's date, and near midnight the two differ.
        with mock.patch('newspaper_tenders.pipeline.run_all') as m_run:
            m_run.return_value = {'ALRAI': 'done'}
            np_apps._run_todays_edition()
        m_run.assert_called_once()
        target = m_run.call_args.args[0]
        self.assertIsInstance(target, date)

        import zoneinfo
        from django.utils import timezone
        expected = timezone.now().astimezone(
            zoneinfo.ZoneInfo(settings.NEWSPAPER_TIMEZONE)).date()
        self.assertEqual(target, expected)

    def test_it_releases_the_slot_afterwards(self):
        with mock.patch('newspaper_tenders.pipeline.run_all', return_value={}):
            np_apps._run_todays_edition()
        self.assertFalse(runlock.is_running())

    def test_it_is_skipped_while_a_run_is_already_in_progress(self):
        # Somebody pressed the button at 07:58 and it is still reading pages.
        self.assertTrue(runlock.claim())
        with mock.patch('newspaper_tenders.pipeline.run_all') as m_run:
            np_apps._run_todays_edition()
        m_run.assert_not_called()
        # The slot still belongs to the run that held it.
        self.assertTrue(runlock.is_running())

    def test_a_crash_does_not_escape_or_keep_the_slot(self):
        # An exception out of the job would kill the APScheduler job, and a
        # slot never released would block every later run and every button.
        with mock.patch('newspaper_tenders.pipeline.run_all',
                        side_effect=RuntimeError('newspaper site down')):
            np_apps._run_todays_edition()      # must not raise
        self.assertFalse(runlock.is_running())


class RunSlotTests(TestCase):
    """The slot itself — one holder at a time, whoever asks."""

    def tearDown(self):
        runlock.release()

    def test_a_second_claim_is_refused(self):
        self.assertTrue(runlock.claim())
        self.assertFalse(runlock.claim())

    def test_release_frees_it(self):
        runlock.claim()
        runlock.release()
        self.assertTrue(runlock.claim())

    def test_release_without_holding_it_is_harmless(self):
        runlock.release()
        runlock.release()
        self.assertFalse(runlock.is_running())
