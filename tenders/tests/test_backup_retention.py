"""
Tests for where backups are written and how many are kept.

Retention used to be applied only by the scheduled task, so manual backups
accumulated without limit. It now lives in create_backup(), which means every
caller is held to the same ceiling. These tests pin that down, along with the
two exemptions that must survive: a pre-restore safety copy is never deleted,
and it never triggers a prune of its own.

BACKUP_ROOT is redirected to a temporary directory throughout — a test must not
write into, or prune, the real backups folder.
"""
import tempfile
from pathlib import Path

from django.test import Client, TestCase, override_settings

from tenders import backup as backup_engine
from tenders.models import BackupSchedule, SystemBackup, SystemRole
from tenders.tests.test_system_dashboard import make_user


class BackupRetentionTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.override = override_settings(BACKUP_ROOT=self.root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        schedule = BackupSchedule.get()
        schedule.retention_count = 10
        schedule.save(update_fields=['retention_count'])

    def _make(self, **kwargs):
        """A cheap archive: config only, so no database dump is taken."""
        kwargs.setdefault('include_database', False)
        kwargs.setdefault('include_media', False)
        kwargs.setdefault('include_config', True)
        return backup_engine.create_backup(None, **kwargs)

    # ── Destination ───────────────────────────────────────────────────────────

    def test_archive_is_written_inside_backup_root(self):
        record = self._make()
        self.assertEqual(record.status, SystemBackup.Status.SUCCESS)
        self.assertEqual(Path(record.file_path).parent, self.root)
        self.assertTrue(Path(record.file_path).is_file())

    # ── Retention ─────────────────────────────────────────────────────────────

    def test_manual_backups_are_capped_at_the_retention_limit(self):
        for _ in range(13):
            self._make()

        kept = SystemBackup.objects.filter(status=SystemBackup.Status.SUCCESS)
        self.assertEqual(kept.count(), 10)
        self.assertEqual(len(list(self.root.glob('*.zip'))), 10,
                         'pruned rows must take their files with them')

    def test_the_newest_backups_are_the_ones_kept(self):
        # Identify rows by pk, not filename. Stamps are second-granularity, so
        # a run this fast reuses the name of an archive that pruning has fully
        # removed — harmless, but it makes filenames useless as identities.
        ids = [self._make().pk for _ in range(12)]

        self.assertEqual(set(SystemBackup.objects.values_list('pk', flat=True)),
                         set(ids[-10:]))
        self.assertEqual(len(list(self.root.glob('*.zip'))), 10)
        for row in SystemBackup.objects.all():
            self.assertTrue(Path(row.file_path).is_file(),
                            f'{row.filename} is catalogued but missing on disk')

    def test_a_name_is_not_reused_while_the_catalogue_still_holds_it(self):
        """
        A row whose archive has vanished from disk (deleted by hand, say) still
        owns its name. Handing that name to a new backup would point two rows
        at one path, and pruning the older row would delete the newer archive.
        """
        orphan = self._make()
        Path(orphan.file_path).unlink()

        replacement = self._make()

        self.assertNotEqual(replacement.filename, orphan.filename)
        self.assertNotEqual(replacement.file_path, orphan.file_path)

    def test_zero_means_keep_everything(self):
        schedule = BackupSchedule.get()
        schedule.retention_count = 0
        schedule.save(update_fields=['retention_count'])

        for _ in range(12):
            self._make()
        self.assertEqual(SystemBackup.objects.count(), 12)

    # ── Exemptions ────────────────────────────────────────────────────────────

    def test_pre_restore_safety_copies_are_never_pruned(self):
        safety = self._make(trigger=SystemBackup.Trigger.PRE_RESTORE, prune=False)
        for _ in range(12):
            self._make()

        safety.refresh_from_db()
        self.assertTrue(Path(safety.file_path).is_file())
        self.assertEqual(
            SystemBackup.objects.filter(
                trigger=SystemBackup.Trigger.PRE_RESTORE).count(), 1)

    def test_a_safety_copy_does_not_itself_trigger_a_prune(self):
        for _ in range(10):
            self._make()
        before = set(SystemBackup.objects.values_list('filename', flat=True))

        self._make(trigger=SystemBackup.Trigger.PRE_RESTORE, prune=False)

        still_there = set(SystemBackup.objects.values_list('filename', flat=True))
        self.assertTrue(before <= still_there,
                        'taking a safety copy must not delete existing archives')

    def test_the_page_names_the_folder_the_archives_live_in(self):
        """
        The page reported a count and a size but never the location, so on an
        unfamiliar machine there was no way to find the files.
        """
        admin  = make_user('backup-dir-admin', SystemRole.SUPER_ADMIN, superuser=True)
        client = Client()
        client.force_login(admin)

        html = client.get('/it/backups/').content.decode()
        self.assertIn(str(self.root), html)

    def test_a_failed_backup_does_not_prune_a_good_one(self):
        keeper = self._make()

        # Force the archive step to blow up after the row exists.
        original = backup_engine._add_tree

        def boom(*args, **kwargs):
            raise OSError('disk full')

        backup_engine._add_tree = boom
        self.addCleanup(setattr, backup_engine, '_add_tree', original)

        schedule = BackupSchedule.get()
        schedule.retention_count = 1
        schedule.save(update_fields=['retention_count'])

        failed = self._make(include_media=True)
        self.assertEqual(failed.status, SystemBackup.Status.FAILED)

        keeper.refresh_from_db()
        self.assertEqual(keeper.status, SystemBackup.Status.SUCCESS)
        self.assertTrue(Path(keeper.file_path).is_file())
