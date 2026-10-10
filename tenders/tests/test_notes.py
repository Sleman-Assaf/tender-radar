"""
The notes box on a tender is private to each user.

`Tender.notes` used to be one text field on the tender shared by everybody: you
could read a colleague's note and overwrite it just by pressing Save. These tests
pin down that a note now belongs to one user, and cover the rule the data
migration used to work out who wrote the two pre-existing notes.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tenders.models import (CountryConfig, ScraperSource, SystemRole, Tender,
                            TenderUserNote, TenderView, UserProfile)

MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _user(username):
    u = User.objects.create_user(username, password='pw-for-tests')
    p, _ = UserProfile.objects.get_or_create(user=u)
    p.role = SystemRole.USER
    p.save(update_fields=['role'])
    return u


class PrivateNoteTests(TestCase):

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

    def _save(self, user, text):
        self.client.force_login(user, backend=MODEL_BACKEND)
        return self.client.post(reverse('tenders:detail', args=[self.tender.pk]),
                                {'save_notes': '1', 'notes': text})

    def _body(self, user):
        note = TenderUserNote.objects.filter(tender=self.tender, user=user).first()
        return note.body if note else None

    def test_saving_stores_the_note_against_that_user(self):
        r = self._save(self.alice, 'Call the consultant on Sunday')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._body(self.alice), 'Call the consultant on Sunday')
        self.assertIsNone(self._body(self.bob))

    def test_one_user_cannot_overwrite_anothers_note(self):
        """The whole point: Bob saving must leave Alice's text alone."""
        self._save(self.alice, "Alice's private note")
        self._save(self.bob, "Bob's private note")
        self.assertEqual(self._body(self.alice), "Alice's private note")
        self.assertEqual(self._body(self.bob), "Bob's private note")

    def test_the_box_shows_only_your_own_note(self):
        self._save(self.alice, 'ALICE-ONLY-TEXT')

        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        page = self.client.get(
            reverse('tenders:detail', args=[self.tender.pk])
        ).content.decode()
        self.assertIn('ALICE-ONLY-TEXT', page)

        self.client.force_login(self.bob, backend=MODEL_BACKEND)
        page = self.client.get(
            reverse('tenders:detail', args=[self.tender.pk])
        ).content.decode()
        self.assertNotIn('ALICE-ONLY-TEXT', page,
                         "Bob's page must not contain Alice's note")

    def test_saving_twice_updates_rather_than_duplicates(self):
        self._save(self.alice, 'first')
        self._save(self.alice, 'second')
        self.assertEqual(
            TenderUserNote.objects.filter(tender=self.tender, user=self.alice).count(), 1)
        self.assertEqual(self._body(self.alice), 'second')

    def test_clearing_the_box_removes_your_note_only(self):
        self._save(self.alice, 'keep me')
        self._save(self.bob, 'delete me')
        self._save(self.bob, '   ')
        self.assertEqual(self._body(self.alice), 'keep me')
        self.assertIsNone(self._body(self.bob))

    def test_empty_save_on_an_empty_box_stores_nothing(self):
        self._save(self.alice, '')
        self.assertEqual(TenderUserNote.objects.count(), 0)

    def test_one_note_per_user_per_tender(self):
        from django.db import IntegrityError, transaction
        TenderUserNote.objects.create(tender=self.tender, user=self.alice, body='a')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TenderUserNote.objects.create(tender=self.tender, user=self.alice, body='b')

    def test_note_is_labelled_private_not_group_wide(self):
        """The old label said "AJi group only", which is now the wrong promise."""
        self.client.force_login(self.alice, backend=MODEL_BACKEND)
        page = self.client.get(
            reverse('tenders:detail', args=[self.tender.pk])
        ).content.decode()
        self.assertIn('Private to you', page)
        self.assertNotIn('AJi</span> group only', page)


class NoteAuthorRecoveryTests(TestCase):
    """
    The rule the data migration used to attribute the two pre-existing notes.

    A note could only be written from the detail page, which records a TenderView,
    so the viewers are the candidate authors. Notes carried no timestamp, so a
    sole viewer is the author and otherwise the earliest viewer is used.
    """

    @classmethod
    def setUpTestData(cls):
        cls.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        cls.source = ScraperSource.objects.create(
            name='Test source', country=cls.country,
            base_url='https://example.invalid', scraper_class='JordanGTDScraper')
        cls.alice = _user('alice')
        cls.bob   = _user('bob')

    @staticmethod
    def _rule():
        # Cannot use `from … import`: the module name starts with a digit.
        import importlib
        mod = importlib.import_module('tenders.migrations.0046_per_user_notes')
        return mod._note_author

    def _tender(self):
        return Tender.objects.create(
            source=self.source, country=self.country, external_id='x1',
            source_url='https://example.invalid/x', title='T')

    def test_sole_viewer_becomes_the_author(self):
        v = TenderView.objects.create(tender=self._tender(), user=self.alice)
        self.assertEqual(self._rule()([v]).user, self.alice)

    def test_earliest_viewer_wins_when_several_opened_it(self):
        t = self._tender()
        first  = TenderView.objects.create(tender=t, user=self.alice)
        second = TenderView.objects.create(tender=t, user=self.bob)
        # Passed newest-first to prove the rule sorts rather than trusting order.
        self.assertEqual(self._rule()([second, first]).user, self.alice)

    def test_a_note_nobody_ever_opened_has_no_author(self):
        self.assertIsNone(self._rule()([]))
