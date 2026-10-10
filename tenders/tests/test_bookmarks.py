"""
Bookmarks are per user.

`Tender.is_bookmarked` used to be one boolean on the tender shared by everybody:
whoever pressed the star put the tender in all 38 users' "Bookmarked" list, the
dashboard counted it for all of them, and anyone could clear it again. These
tests pin down that a bookmark now belongs to exactly one user, plus the rule the
data migration used to work out who owned the three pre-existing bookmarks.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tenders.models import (CountryConfig, ScraperSource, SystemRole, Tender,
                            TenderBookmark, TenderView, UserProfile)

MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _user(username, role=SystemRole.USER):
    u = User.objects.create_user(username, password='pw-for-tests')
    p, _ = UserProfile.objects.get_or_create(user=u)
    p.role = role
    p.save(update_fields=['role'])
    return u


class BookmarkIsolationTests(TestCase):
    """One user's bookmark is invisible to another."""

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.source = ScraperSource.objects.create(
            name='Test source', country=cls.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        cls.tender = Tender.objects.create(
            source=cls.source, country=cls.country, external_id='t1',
            source_url='https://example.invalid/1', title='Design consultancy',
            status='OPEN', is_relevant=True, is_aec=True)
        cls.alice = _user('alice')
        cls.bob   = _user('bob')

    def _toggle(self, user):
        self.client.force_login(user, backend=MODEL_BACKEND)
        return self.client.post(
            reverse('tenders:toggle_bookmark', args=[self.tender.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_toggle_adds_then_removes_for_that_user_only(self):
        r = self._toggle(self.alice)
        self.assertTrue(r.json()['bookmarked'])
        self.assertTrue(TenderBookmark.objects.filter(
            tender=self.tender, user=self.alice).exists())

        r = self._toggle(self.alice)
        self.assertFalse(r.json()['bookmarked'])
        self.assertFalse(TenderBookmark.objects.filter(
            tender=self.tender, user=self.alice).exists())

    def test_one_users_bookmark_does_not_appear_for_another(self):
        self._toggle(self.alice)

        # Alice sees it in her bookmarked list.
        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        alice_list = self.client.get(reverse('tenders:list'), {'bookmarked': '1'})
        self.assertEqual(
            [t.pk for t in alice_list.context['tenders']], [self.tender.pk])

        # Bob does not.
        self.client.force_login(self.bob, backend=MODEL_BACKEND)
        bob_list = self.client.get(reverse('tenders:list'), {'bookmarked': '1'})
        self.assertEqual(list(bob_list.context['tenders']), [])

    def test_row_flag_is_per_user(self):
        self._toggle(self.alice)

        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        row = self.client.get(reverse('tenders:list')).context['tenders'][0]
        self.assertTrue(row.is_bookmarked)

        self.client.force_login(self.bob, backend=MODEL_BACKEND)
        row = self.client.get(reverse('tenders:list')).context['tenders'][0]
        self.assertFalse(row.is_bookmarked, "Bob must not see Alice's bookmark")

    def test_detail_page_flag_is_per_user(self):
        self._toggle(self.alice)

        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        ctx = self.client.get(
            reverse('tenders:detail', args=[self.tender.pk])).context
        self.assertTrue(ctx['is_bookmarked'])
        self.assertIsNotNone(ctx['bookmarked_at'])

        self.client.force_login(self.bob, backend=MODEL_BACKEND)
        ctx = self.client.get(
            reverse('tenders:detail', args=[self.tender.pk])).context
        self.assertFalse(ctx['is_bookmarked'])
        self.assertIsNone(ctx['bookmarked_at'])

    def test_dashboard_count_is_per_user(self):
        self._toggle(self.alice)

        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        self.assertEqual(
            self.client.get(reverse('tenders:dashboard')).context['bookmarked'], 1)

        self.client.force_login(self.bob, backend=MODEL_BACKEND)
        self.assertEqual(
            self.client.get(reverse('tenders:dashboard')).context['bookmarked'], 0)

    def test_two_users_can_bookmark_the_same_tender(self):
        self._toggle(self.alice)
        self._toggle(self.bob)
        self.assertEqual(
            TenderBookmark.objects.filter(tender=self.tender).count(), 2)

        # And one removing theirs leaves the other's intact.
        self._toggle(self.bob)
        self.assertEqual(
            [b.user.username for b in TenderBookmark.objects.filter(tender=self.tender)],
            ['alice'])

    def test_one_bookmark_per_user_per_tender(self):
        from django.db import IntegrityError, transaction
        TenderBookmark.objects.create(tender=self.tender, user=self.alice)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenderBookmark.objects.create(tender=self.tender, user=self.alice)

    def test_bookmarked_filter_does_not_duplicate_rows(self):
        TenderBookmark.objects.create(tender=self.tender, user=self.alice)
        TenderBookmark.objects.create(tender=self.tender, user=self.bob)
        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        rows = self.client.get(reverse('tenders:list'),
                               {'bookmarked': '1'}).context['tenders']
        self.assertEqual(len(rows), 1, 'the join must not repeat the tender')


class BookmarkOwnerRecoveryTests(TestCase):
    """
    The rule the data migration used to attribute the pre-existing bookmarks.

    The old schema stored no owner, so ownership was recovered from TenderView:
    the person who last had the tender open before it was bookmarked.
    """

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.source = ScraperSource.objects.create(
            name='Test source', country=cls.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        cls.tender = Tender.objects.create(
            source=cls.source, country=cls.country, external_id='t1',
            source_url='https://example.invalid/1', title='T')
        cls.alice = _user('alice')
        cls.bob   = _user('bob')

    @staticmethod
    def _rule():
        # The migration module cannot be imported with `from … import` because
        # its name starts with a digit.
        import importlib
        mod = importlib.import_module('tenders.migrations.0045_per_user_bookmarks')
        return mod._best_owner

    def _view(self, user, when):
        v = TenderView.objects.create(tender=self.tender, user=user)
        TenderView.objects.filter(pk=v.pk).update(viewed_at=when)
        v.refresh_from_db()
        return v

    def test_prefers_the_latest_view_before_the_bookmark(self):
        now = timezone.now()
        bookmarked_at = now
        far  = self._view(self.bob,   now - timedelta(days=3))
        near = self._view(self.alice, now - timedelta(seconds=9))
        self.assertEqual(self._rule()([far, near], bookmarked_at), self.alice.pk)

    def test_falls_back_to_the_closest_view_when_all_are_later(self):
        """A bookmark made from the list page records no view of its own."""
        now = timezone.now()
        bookmarked_at = now - timedelta(days=4)
        later   = self._view(self.alice, now)
        latest  = self._view(self.bob,   now + timedelta(days=10))
        self.assertEqual(self._rule()([later, latest], bookmarked_at), self.alice.pk)

    def test_no_views_means_no_owner(self):
        self.assertIsNone(self._rule()([], timezone.now()))

    def test_missing_bookmark_date_uses_the_first_view(self):
        v = self._view(self.alice, timezone.now())
        self.assertEqual(self._rule()([v], None), self.alice.pk)
