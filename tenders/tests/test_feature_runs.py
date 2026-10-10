"""
The consolidated Newspaper & Magazine runs page (/it/runs/).

Written because a capability went missing when the two separate run-history
pages were merged into this one. The newspaper feature has always had TWO
actions per edition — a retry that picks up only the failed or pending PDFs,
and a full rescan that pushes every page back through the AI — but the merged
page offered just the retry. The navigation stopped linking the old newspaper
page at the same time, which left the rescan reachable only by way of the
Tender System hub card (templates/manage/hub.html) — alive, but not on the
page an administrator is sent to.

So the assertions here deliberately pin the distinction that was easy to lose:
a newspaper row offers both actions, a magazine row offers only a retry, and
that is correct rather than an oversight — a magazine sweep re-reads its whole
feed every time, so its retry already IS a rescan.
"""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from magazine_tenders.models import MagazineRun

from newspaper_tenders.models import NewspaperEdition, NewspaperEditionFile
from newspaper_tenders.pipeline import get_newspaper_source

RUNS_URL = '/it/runs/'


class FeatureRunsPageTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('runs-boss', 'rb@x.jo',
                                                  'pw12345!')
        cls.user = User.objects.create_user('runs-reader', password='pw12345!')

        get_newspaper_source()
        cls.edition = NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 21),
            status=NewspaperEdition.Status.DONE)
        NewspaperEditionFile.objects.create(
            edition=cls.edition, pdf_url='https://alrai.com/x/1.pdf',
            status=NewspaperEditionFile.Status.DONE)

        # NOT `cls.run`: that shadows TestCase.run() and unittest then
        # tries to call the model instance to run the test.
        cls.mag_run = MagazineRun.objects.create(
            magazine='GULFCONSTRUCTION',
            status=MagazineRun.Status.SUCCESS,
            trigger=MagazineRun.Trigger.MANUAL)

    # ── access ───────────────────────────────────────────────────────────────
    def test_admin_can_open_the_page(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(RUNS_URL).status_code, 200)

    def test_regular_user_is_refused(self):
        self.client.force_login(self.user)
        self.assertIn(self.client.get(RUNS_URL).status_code, (302, 403))

    def test_signing_in_is_required(self):
        self.assertIn(self.client.get(RUNS_URL).status_code, (302, 403))

    # ── both features on one page ────────────────────────────────────────────
    def test_both_features_appear(self):
        self.client.force_login(self.admin)
        rows = self.client.get(RUNS_URL).context['rows']
        kinds = {r['kind'] for r in rows}
        self.assertEqual(kinds, {'newspaper', 'news'})

    def test_kind_filter_partitions_the_rows(self):
        self.client.force_login(self.admin)
        for kind in ('newspaper', 'news'):
            with self.subTest(kind=kind):
                rows = self.client.get(RUNS_URL, {'kind': kind}).context['rows']
                self.assertTrue(rows, f'no {kind} rows')
                self.assertEqual({r['kind'] for r in rows}, {kind})

    # ── the rescan action ────────────────────────────────────────────────────
    def test_newspaper_row_offers_a_rescan(self):
        self.client.force_login(self.admin)
        rows = self.client.get(RUNS_URL, {'kind': 'newspaper'}).context['rows']
        row = rows[0]
        self.assertTrue(row['rescan_url'], 'newspaper row has no rescan_url')
        self.assertIn(str(self.edition.pk), row['rescan_url'])

    def test_news_row_offers_no_rescan(self):
        # Not an omission: a magazine retry re-reads the entire feed, so there
        # is no separate "do it all again" to offer.
        self.client.force_login(self.admin)
        rows = self.client.get(RUNS_URL, {'kind': 'news'}).context['rows']
        self.assertFalse(rows[0]['rescan_url'])

    def test_the_rescan_button_is_rendered_for_an_edition(self):
        self.client.force_login(self.admin)
        html = self.client.get(RUNS_URL, {'kind': 'newspaper'}).content.decode()
        self.assertIn('Rescan', html)
        # It must post force=1, or it is just a second Retry button.
        self.assertIn("this, true", html)
        self.assertIn("fd.append('force', '1')", html)

    def test_no_rescan_button_on_a_news_only_page(self):
        self.client.force_login(self.admin)
        html = self.client.get(RUNS_URL, {'kind': 'news'}).content.decode()
        self.assertIn('Retry', html)
        self.assertNotIn('> Rescan<', html)

    def test_the_two_actions_are_described_differently(self):
        # The retry hint has to say it skips finished pages, otherwise an
        # administrator cannot tell the buttons apart.
        self.client.force_login(self.admin)
        rows = self.client.get(RUNS_URL, {'kind': 'newspaper'}).context['rows']
        hint = rows[0]['retry_hint'].lower()
        self.assertIn('failed', hint)
        self.assertIn('pending', hint)

    def test_the_rescan_posts_to_the_newspaper_retry_endpoint(self):
        # The rescan is the same endpoint as the retry, distinguished only by
        # force=1 in the body. If that ever stops being true, this test says so.
        self.client.force_login(self.admin)
        row = self.client.get(RUNS_URL, {'kind': 'newspaper'}).context['rows'][0]
        self.assertEqual(row['rescan_url'], row['retry_url'])
        self.assertEqual(row['rescan_url'],
                         f'/newspaper-tenders/runs/{self.edition.pk}/retry/')


class FeatureRunsRemovabilityTests(TestCase):
    """
    Either feature must stay removable — the page is built from guarded
    imports so deleting an app cannot take the runs page down with it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('runs-boss2', 'rb2@x.jo',
                                                  'pw12345!')

    def test_page_renders_with_no_runs_at_all(self):
        self.client.force_login(self.admin)
        resp = self.client.get(RUNS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context['rows']), [])
        self.assertContains(resp, 'No runs yet')

class FeatureRunsTabCountTests(TestCase):
    """
    A tab's number must describe where it LEADS, not where you are.

    The counts used to be computed from the already-filtered rows, so every
    tab reported the tab you were on: clicking NEWSPAPER showed "News (0)" and
    clicking News showed "NEWSPAPER (0)". The page therefore claimed the other
    feature had no runs at all — a wrong statement about the data, not just a
    cosmetic slip, and the same mistake the dashboard headline made.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('runs-boss3', 'rb3@x.jo',
                                                  'pw12345!')
        get_newspaper_source()
        for day in (11, 12, 13):
            NewspaperEdition.objects.create(
                source='ALRAI', edition_date=date(2026, 8, day),
                status=NewspaperEdition.Status.DONE)
        for code in ('GULFCONSTRUCTION', 'CBNME'):
            MagazineRun.objects.create(magazine=code,
                                       status=MagazineRun.Status.SUCCESS,
                                       trigger=MagazineRun.Trigger.MANUAL)

    def _counts(self, kind=''):
        self.client.force_login(self.admin)
        url = RUNS_URL + (f'?kind={kind}' if kind else '')
        return self.client.get(url).context['counts']

    def test_the_counts_are_the_same_on_every_tab(self):
        unfiltered = self._counts()
        self.assertEqual(self._counts('newspaper'), unfiltered)
        self.assertEqual(self._counts('news'), unfiltered)

    def test_the_counts_describe_the_whole_history(self):
        c = self._counts()
        self.assertEqual(c['newspaper'], 3)
        self.assertEqual(c['news'], 2)

    def test_the_other_feature_is_never_reported_as_zero(self):
        # The exact symptom: pick a tab, and the OTHER tab still has its count.
        self.assertEqual(self._counts('newspaper')['news'], 2)
        self.assertEqual(self._counts('news')['newspaper'], 3)

    def test_all_means_all_not_all_of_what_is_shown(self):
        self.client.force_login(self.admin)
        for kind in ('', 'newspaper', 'news'):
            url = RUNS_URL + (f'?kind={kind}' if kind else '')
            with self.subTest(kind=kind or 'all'):
                self.assertEqual(self.client.get(url).context['total'], 5)

    def test_the_rows_are_still_filtered(self):
        # Fixing the counts must not stop the filter filtering.
        self.client.force_login(self.admin)
        for kind in ('newspaper', 'news'):
            with self.subTest(kind=kind):
                rows = self.client.get(f'{RUNS_URL}?kind={kind}').context['rows']
                self.assertEqual({r['kind'] for r in rows}, {kind})

class FeatureRunsLegacyKindTests(TestCase):
    """?kind=magazine was in the address bar before the rename, so it stays."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('runs-boss4', 'rb4@x.jo',
                                                  'pw12345!')
        get_newspaper_source()
        NewspaperEdition.objects.create(
            source='ALRAI', edition_date=date(2026, 8, 14),
            status=NewspaperEdition.Status.DONE)
        MagazineRun.objects.create(magazine='CBNME',
                                   status=MagazineRun.Status.SUCCESS,
                                   trigger=MagazineRun.Trigger.MANUAL)

    def test_the_old_value_still_filters(self):
        self.client.force_login(self.admin)
        legacy = self.client.get(RUNS_URL, {'kind': 'magazine'})
        current = self.client.get(RUNS_URL, {'kind': 'news'})
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual([r['kind'] for r in legacy.context['rows']],
                         [r['kind'] for r in current.context['rows']])

    def test_the_old_value_is_normalised(self):
        # It must not just work — it must report itself as the new value, or
        # the tab highlight would be wrong for anyone arriving on the old URL.
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(RUNS_URL, {'kind': 'magazine'}).context['kind'],
            'news')

    def test_the_tab_links_to_the_new_value(self):
        self.client.force_login(self.admin)
        html = self.client.get(RUNS_URL).content.decode()
        self.assertIn('?kind=news', html)
        self.assertNotIn('?kind=magazine', html)
