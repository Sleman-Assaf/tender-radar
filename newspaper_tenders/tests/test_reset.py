"""
Stopping a run, and resetting an edition so it can be read from scratch.

Three mechanisms, tested here because each one fails silently if it breaks:

  * COOPERATIVE CANCEL. Python cannot kill a thread, so run_for_date() checks a
    flag between pages. If that check is ever removed the tests still pass at a
    glance — the run simply never stops — so the assertion below is that the
    SECOND page was not processed.
  * THE WIPE. Deleting an edition's output means deleting the tenders (which do
    not cascade), their photos (FileFields, which survive row deletion), and the
    file hashes (which otherwise trip the SHA256 skip on the next read). Missing
    any one of the three leaves the edition dirty in a way nothing reports.
  * THE ORPHAN SWEEP. A row left PROCESSING by a dead process shows a
    live-looking badge forever.
"""
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from tenders.models import Tender

from newspaper_tenders import pipeline
from newspaper_tenders.models import (NewspaperEdition, NewspaperEditionFile,
                                      NewspaperTenderMeta)
from newspaper_tenders.pipeline import get_newspaper_source

FUTURE = date.today().replace(year=date.today().year + 1)

# A one-pixel JPEG, so ad_image holds a real file that deletion must remove.
JPEG = bytes.fromhex(
    'ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffff'
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
    'ffc00011080001000101011100ffc40014000100000000000000000000000000000'
    '00009ffda0008010100003fff00d9')


class CancelRegistryTests(TestCase):

    def tearDown(self):
        pipeline.clear_cancel(4242)

    def test_a_request_is_visible_to_the_running_loop(self):
        self.assertFalse(pipeline.cancel_requested(4242))
        pipeline.request_cancel(4242)
        self.assertTrue(pipeline.cancel_requested(4242))

    def test_clearing_forgets_the_request(self):
        pipeline.request_cancel(4242)
        pipeline.clear_cancel(4242)
        self.assertFalse(pipeline.cancel_requested(4242))

    def test_a_string_pk_is_the_same_request_as_an_int(self):
        # The pk arrives from a URL, so it can be a string. If the two forms
        # landed in the set separately, a cancel would never be seen.
        pipeline.request_cancel('4242')
        self.assertTrue(pipeline.cancel_requested(4242))


class CancelStopsTheRunTests(TestCase):
    """The checkpoint actually has to stop the loop."""

    @classmethod
    def setUpTestData(cls):
        get_newspaper_source()

    def tearDown(self):
        for e in NewspaperEdition.objects.all():
            pipeline.clear_cancel(e.pk)

    def _edition_with_two_pages(self):
        edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 25))
        for n in (1, 2):
            NewspaperEditionFile.objects.create(
                edition=edition, pdf_url=f'https://alrai.com/x/{n}.pdf')
        return edition

    def _run(self, edition, cancel_before_second=False):
        """run_for_date with discovery mocked; counts pages processed."""
        calls = []

        def fake_process(run_id, ed, file_row, source, counters, cfg=None):
            calls.append(file_row.pk)
            if cancel_before_second:
                pipeline.request_cancel(ed.pk)
            file_row.mark(NewspaperEditionFile.Status.DONE)

        discovered = mock.Mock(
            article=mock.Mock(url='https://alrai.com/article/1'),
            pdf_urls=[f.pdf_url for f in edition.files.all()])

        with mock.patch.object(pipeline.sources, 'discover_for',
                               return_value=discovered), \
             mock.patch.object(pipeline, '_process_file', fake_process):
            status = pipeline.run_for_date(edition.edition_date, 'ALRAI')
        return status, calls

    def test_without_a_cancel_every_page_is_read(self):
        edition = self._edition_with_two_pages()
        status, calls = self._run(edition)
        self.assertEqual(len(calls), 2, 'both pages should have been read')
        self.assertEqual(status, 'done')

    def test_a_cancel_stops_before_the_next_page(self):
        edition = self._edition_with_two_pages()
        status, calls = self._run(edition, cancel_before_second=True)
        self.assertEqual(len(calls), 1, 'the second page must not be read')
        self.assertEqual(status, 'stopped')

    def test_a_stopped_edition_says_why_on_the_row(self):
        edition = self._edition_with_two_pages()
        self._run(edition, cancel_before_second=True)
        edition.refresh_from_db()
        self.assertEqual(edition.status, NewspaperEdition.Status.FAILED)
        self.assertIn('Stopped by an administrator', edition.error_message)

    def test_the_request_does_not_survive_into_the_next_run(self):
        # A cancel arriving after a run finished would otherwise sit in the set
        # and kill the following run before it read anything.
        edition = self._edition_with_two_pages()
        self._run(edition, cancel_before_second=True)
        self.assertFalse(pipeline.cancel_requested(edition.pk))

        edition.files.update(status=NewspaperEditionFile.Status.PENDING)
        status, calls = self._run(edition)
        self.assertEqual(len(calls), 2, 'the next run must not be cancelled')
        self.assertEqual(status, 'done')


class ResetEditionTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('boss5', 'b5@x.jo', 'pw12345!')
        cls.user = User.objects.create_user('plain5', password='pw12345!')
        cls.source = get_newspaper_source()

    def setUp(self):
        self.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 26),
            status=NewspaperEdition.Status.DONE)
        self.page = NewspaperEditionFile.objects.create(
            edition=self.edition, pdf_url='https://alrai.com/x/reset.pdf',
            status=NewspaperEditionFile.Status.DONE,
            file_hash='a' * 64, file_size=1234, tenders_found=2,
            error_message='some old error')
        self.tenders = []
        for n in (1, 2):
            t = Tender.objects.create(
                source=self.source, country=self.source.country,
                external_id=f'reset-{n}', source_url='https://x/t',
                title=f'tender {n}', is_aec=True, is_aec_checked=True,
                is_relevant=True, submission_deadline=FUTURE)
            meta = NewspaperTenderMeta.objects.create(
                tender=t, edition=self.edition, edition_file=self.page,
                page_number=n)
            meta.ad_image.save(f'reset-{n}.jpg', ContentFile(JPEG), save=True)
            self.tenders.append(t)
        self.photo_paths = [
            m.ad_image.path for m in
            NewspaperTenderMeta.objects.filter(edition=self.edition)]

    def tearDown(self):
        from newspaper_tenders import runlock
        runlock.release()

    def _post(self):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse('newspaper_tenders:reset_edition', args=[self.edition.pk]))

    # ── the wipe ─────────────────────────────────────────────────────────────
    def test_the_tenders_are_deleted(self):
        resp = self._post()
        self.assertEqual(resp.json()['status'], 'reset')
        self.assertEqual(
            Tender.objects.filter(pk__in=[t.pk for t in self.tenders]).count(), 0)

    def test_the_meta_rows_go_with_them(self):
        self._post()
        self.assertEqual(
            NewspaperTenderMeta.objects.filter(edition=self.edition).count(), 0)

    def test_the_photos_are_removed_from_disk(self):
        import os
        for path in self.photo_paths:
            self.assertTrue(os.path.exists(path), 'fixture photo missing')
        self._post()
        for path in self.photo_paths:
            self.assertFalse(os.path.exists(path),
                             f'{path} was left behind as untracked media')

    def test_every_page_goes_back_to_pending(self):
        self._post()
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, NewspaperEditionFile.Status.PENDING)

    def test_the_file_hash_is_cleared(self):
        # Left in place it keys the SHA256 skip in _process_file, and a
        # non-empty hash on a reset row can collide with the partial unique
        # index the moment the re-read stores it again.
        self._post()
        self.page.refresh_from_db()
        self.assertEqual(self.page.file_hash, '')

    def test_stale_page_state_is_cleared(self):
        self._post()
        self.page.refresh_from_db()
        self.assertEqual(self.page.error_message, '')
        self.assertEqual(self.page.file_size, 0)
        self.assertEqual(self.page.tenders_found, 0)
        self.assertIsNone(self.page.downloaded_at)

    # ── what must survive ────────────────────────────────────────────────────
    def test_the_edition_row_survives(self):
        # Deleting it would take the Retry button with it.
        self._post()
        self.assertTrue(
            NewspaperEdition.objects.filter(pk=self.edition.pk).exists())

    def test_the_edition_says_it_was_reset(self):
        self._post()
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.status,
                         NewspaperEdition.Status.DISCOVERED)
        self.assertIn('Reset by an administrator', self.edition.error_message)

    def test_retry_still_works_afterwards(self):
        from newspaper_tenders import views
        self._post()
        with mock.patch.object(views, '_run_edition_retry'):
            resp = self.client.post(
                reverse('newspaper_tenders:retry_edition',
                        args=[self.edition.pk]))
        self.assertEqual(resp.json()['status'], 'started')

    def test_other_editions_are_untouched(self):
        other = NewspaperEdition.objects.create(
            source='ALGHAD', edition_date=date(2026, 8, 26),
            status=NewspaperEdition.Status.DONE)
        NewspaperEditionFile.objects.create(
            edition=other, pdf_url='https://alghad.com/y.pdf',
            status=NewspaperEditionFile.Status.DONE, file_hash='b' * 64)
        self._post()
        other.refresh_from_db()
        self.assertEqual(other.status, NewspaperEdition.Status.DONE)
        self.assertEqual(other.files.first().status,
                         NewspaperEditionFile.Status.DONE)

    # ── the running case ─────────────────────────────────────────────────────
    def test_a_live_run_is_asked_to_stop_instead_of_wiping(self):
        # Wiping now would race the thread: it would keep saving tenders for
        # the page it is part-way through, into the rows we just cleared.
        from newspaper_tenders import runlock
        self.assertTrue(runlock.claim())
        resp = self._post()
        self.assertEqual(resp.json()['status'], 'stopping')
        self.assertTrue(pipeline.cancel_requested(self.edition.pk))
        # Nothing may have been deleted yet.
        self.assertEqual(
            Tender.objects.filter(pk__in=[t.pk for t in self.tenders]).count(), 2)
        pipeline.clear_cancel(self.edition.pk)

    # ── access and errors ────────────────────────────────────────────────────
    def test_regular_user_blocked(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('newspaper_tenders:reset_edition', args=[self.edition.pk]))
        self.assertIn(resp.status_code, (302, 403))

    def test_get_is_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse('newspaper_tenders:reset_edition', args=[self.edition.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_unknown_edition_404(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('newspaper_tenders:reset_edition', args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_resetting_twice_is_harmless(self):
        self._post()
        resp = self._post()
        self.assertEqual(resp.json()['status'], 'reset')


class OrphanedEditionSweepTests(TestCase):
    """
    A row left PROCESSING by a dead process must not keep a live-looking badge.
    """

    def test_a_processing_edition_is_closed(self):
        from newspaper_tenders.apps import _close_orphaned_editions
        e = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 27),
            status=NewspaperEdition.Status.PROCESSING)
        _close_orphaned_editions()
        e.refresh_from_db()
        self.assertEqual(e.status, NewspaperEdition.Status.FAILED)
        # Asserts the claim that is always TRUE of a swept row. It must not
        # pin the word 'interrupted': the sweep cannot know that, because a
        # partial run rests at PROCESSING too.
        self.assertIn('did not finish', e.error_message)

    def test_finished_editions_are_left_alone(self):
        from newspaper_tenders.apps import _close_orphaned_editions
        done = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 28),
            status=NewspaperEdition.Status.DONE)
        waiting = NewspaperEdition.objects.create(
            source='ALGHAD', edition_date=date(2026, 8, 28),
            status=NewspaperEdition.Status.PENDING_PDFS)
        _close_orphaned_editions()
        done.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual(done.status, NewspaperEdition.Status.DONE)
        self.assertEqual(waiting.status, NewspaperEdition.Status.PENDING_PDFS)

    def test_the_pages_keep_their_own_state(self):
        # Retry has to resume exactly where the interrupted run stopped, so the
        # sweep must not touch the file rows.
        from newspaper_tenders.apps import _close_orphaned_editions
        e = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 29),
            status=NewspaperEdition.Status.PROCESSING)
        done = NewspaperEditionFile.objects.create(
            edition=e, pdf_url='https://alrai.com/z/1.pdf',
            status=NewspaperEditionFile.Status.DONE)
        pending = NewspaperEditionFile.objects.create(
            edition=e, pdf_url='https://alrai.com/z/2.pdf',
            status=NewspaperEditionFile.Status.PENDING)
        _close_orphaned_editions()
        done.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(done.status, NewspaperEditionFile.Status.DONE)
        self.assertEqual(pending.status, NewspaperEditionFile.Status.PENDING)

    def test_the_sweep_never_runs_under_the_test_runner(self):
        # It would fight the fixtures of whatever test happened to be running.
        from newspaper_tenders.apps import _serves_requests
        import sys
        self.assertIn('test', ' '.join(sys.argv).lower())
        self.assertFalse(_serves_requests())

class ReviewFindingRegressionTests(TestCase):
    """
    Six defects an adversarial review found in the first cut of this work.
    Each test below fails if the corresponding fix is undone.
    """

    @classmethod
    def setUpTestData(cls):
        get_newspaper_source()

    def tearDown(self):
        for e in NewspaperEdition.objects.all():
            pipeline.clear_cancel(e.pk)

    def test_a_clean_finish_clears_a_stale_message(self):
        # models.mark() only ever writes error_message, so a note left by an
        # interrupted or reset run used to sit on a healthy DONE row for ever.
        edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 7, 1),
            status=NewspaperEdition.Status.FAILED,
            error_message='This edition did not finish.')
        NewspaperEditionFile.objects.create(
            edition=edition, pdf_url='https://alrai.com/q/1.pdf')

        def fake_process(run_id, ed, file_row, source, counters, cfg=None):
            file_row.mark(NewspaperEditionFile.Status.DONE)

        discovered = mock.Mock(
            article=mock.Mock(url='https://alrai.com/article/9'),
            pdf_urls=['https://alrai.com/q/1.pdf'])
        with mock.patch.object(pipeline.sources, 'discover_for',
                               return_value=discovered),              mock.patch.object(pipeline, '_process_file', fake_process):
            pipeline.run_for_date(edition.edition_date, 'ALRAI')

        edition.refresh_from_db()
        self.assertEqual(edition.status, NewspaperEdition.Status.DONE)
        self.assertEqual(edition.error_message, '',
                         'a stale note survived a clean run')

    def test_a_cancel_asked_for_before_the_run_starts_still_applies(self):
        # run_all walks all three papers in one thread, so a request can be
        # recorded while an EARLIER paper is running. Clearing the flag as each
        # run started used to discard it.
        edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 7, 2))
        for n in (1, 2):
            NewspaperEditionFile.objects.create(
                edition=edition, pdf_url=f'https://alrai.com/r/{n}.pdf')

        pipeline.request_cancel(edition.pk)          # before run_for_date runs

        calls = []

        def fake_process(run_id, ed, file_row, source, counters, cfg=None):
            calls.append(file_row.pk)
            file_row.mark(NewspaperEditionFile.Status.DONE)

        discovered = mock.Mock(
            article=mock.Mock(url='https://alrai.com/article/9'),
            pdf_urls=[f.pdf_url for f in edition.files.all()])
        with mock.patch.object(pipeline.sources, 'discover_for',
                               return_value=discovered),              mock.patch.object(pipeline, '_process_file', fake_process):
            status = pipeline.run_for_date(edition.edition_date, 'ALRAI')

        self.assertEqual(calls, [], 'the pre-run cancel was discarded')
        self.assertEqual(status, 'stopped')

    def test_the_sweep_does_not_claim_the_edition_was_interrupted(self):
        # PROCESSING is also a RESTING status: run_for_date ends a partial run
        # there on purpose, so "interrupted" is often simply untrue.
        from newspaper_tenders.apps import _close_orphaned_editions
        e = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 7, 3),
            status=NewspaperEdition.Status.PROCESSING)
        _close_orphaned_editions()
        e.refresh_from_db()
        self.assertNotIn('Interrupted — the process', e.error_message)
        self.assertIn('did not finish', e.error_message)
        self.assertIn('either', e.error_message)

    def test_the_sweep_does_not_stamp_processed_at(self):
        # processed_at means "finished successfully" here - models.mark() sets
        # it only for DONE - so a failed row must not carry one.
        from newspaper_tenders.apps import _close_orphaned_editions
        e = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 7, 4),
            status=NewspaperEdition.Status.PROCESSING)
        self.assertIsNone(e.processed_at)
        _close_orphaned_editions()
        e.refresh_from_db()
        self.assertEqual(e.status, NewspaperEdition.Status.FAILED)
        self.assertIsNone(e.processed_at,
                          'the sweep stamped a success timestamp on a failure')

    def test_a_single_file_edition_can_still_be_stopped(self):
        # Al-Ghad and Ad-Dustour publish ONE file per edition. With only the
        # per-file checkpoint, Stop did nothing for them: all the work happens
        # inside that one row. There must be a checkpoint in the page loop too.
        import inspect
        src = inspect.getsource(pipeline._process_file)
        self.assertIn('cancel_requested(edition.pk)', src,
                      '_process_file has no cancellation checkpoint, so a stop '
                      'cannot interrupt a single-file edition')
        # and it must sit in the per-page loop, before the AI call
        loop_at = src.index('for page_bytes, base_page in payloads:')
        check_at = src.index('cancel_requested(edition.pk)')
        ai_at = src.index('ai_client.extract_tenders(')
        self.assertLess(loop_at, check_at, 'checkpoint is outside the page loop')
        self.assertLess(check_at, ai_at, 'checkpoint runs after the AI call')
