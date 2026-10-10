# tenders/tests/test_admin_restructure.py
"""
Tests for the 2026-08-24 administration restructure:

  1. the System Administrator (IT) role is gone — Super Admin does everything
     (covered mainly in test_system_dashboard.RoleBoundaryTests);
  2. every outgoing e-mail / WhatsApp message is written to NotificationLog at
     the integrations chokepoints;
  3. the Company → Department hierarchy and its guards;
  4. backups can include the application code and documentation;
  5. an uploaded .zip can be imported for restoring — after validation.

Network is never touched: the e-mail tests exercise the disabled-integration
path and a patched transport, which is exactly where the logging chokepoint
must fire regardless of outcome.
"""
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tenders import backup as backup_engine
from tenders.integrations import MSGraphMailer, WhatsAppClient
from tenders.it_forms import BackupRestoreForm, DepartmentForm, ITUserForm
from tenders.models import (
    AdminAuditLog, Company, Department, NotificationLog, SystemBackup,
    SystemRole, UserProfile,
)
from tenders.tests.test_system_dashboard import make_user


# ── 2. Notification log ───────────────────────────────────────────────────────

class NotificationLogTests(TestCase):

    def test_failed_email_is_logged_with_its_reason(self):
        # Integration unconfigured → the send is refused before any network I/O,
        # and even that refusal must be recorded.
        mailer = MSGraphMailer()
        ok = mailer.send_mail('Subject line', '<p>body</p>',
                              to=['a@example.com', 'b@example.com'],
                              category='DIGEST')
        self.assertFalse(ok)

        row = NotificationLog.objects.get()
        self.assertEqual(row.channel, NotificationLog.Channel.EMAIL)
        self.assertEqual(row.category, NotificationLog.Category.DIGEST)
        self.assertEqual(row.status, NotificationLog.Status.FAILED)
        self.assertEqual(row.recipient, 'a@example.com, b@example.com')
        self.assertEqual(row.subject, 'Subject line')
        self.assertTrue(row.error_message)

    def test_successful_email_is_logged_as_sent(self):
        user = make_user('t_notify_admin', superuser=True)
        with mock.patch.object(MSGraphMailer, '_send_mail', return_value=True):
            ok = MSGraphMailer().send_mail('Hello', '<p>hi</p>', to=['x@example.com'],
                                           category='TEST', triggered_by=user)
        self.assertTrue(ok)
        row = NotificationLog.objects.get()
        self.assertEqual(row.status, NotificationLog.Status.SENT)
        self.assertEqual(row.category, NotificationLog.Category.TEST)
        self.assertEqual(row.triggered_by, user)
        self.assertEqual(row.error_message, '')

    def test_whatsapp_send_is_logged_per_recipient(self):
        client = WhatsAppClient()          # disabled → fails fast, still logged
        ok, detail = client.send_message('+962790000000', 'hello there',
                                         category='ALERT')
        self.assertFalse(ok)
        row = NotificationLog.objects.get()
        self.assertEqual(row.channel, NotificationLog.Channel.WHATSAPP)
        self.assertEqual(row.recipient, '+962790000000')
        self.assertEqual(row.status, NotificationLog.Status.FAILED)

    def test_a_logging_failure_never_breaks_the_send(self):
        with mock.patch.object(MSGraphMailer, '_send_mail', return_value=True), \
             mock.patch('tenders.models.NotificationLog.objects') as objects:
            objects.create.side_effect = RuntimeError('disk full')
            ok = MSGraphMailer().send_mail('S', 'b', to=['x@example.com'])
        self.assertTrue(ok, 'a log-write failure must not fail the send')

    def test_the_message_body_is_stored_and_viewable(self):
        admin = make_user('t_body_admin', superuser=True)
        with mock.patch.object(MSGraphMailer, '_send_mail', return_value=True):
            MSGraphMailer().send_mail('Digest', '<h1>Hello</h1><p>3 tenders</p>',
                                      to=['x@example.com'], category='DIGEST')
        row = NotificationLog.objects.get()
        self.assertEqual(row.body, '<h1>Hello</h1><p>3 tenders</p>')
        self.assertTrue(row.is_html)

        c = Client()
        c.force_login(admin)
        r = c.get(reverse('tenders:it_notification_detail', args=[row.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'x@example.com')
        self.assertContains(r, 'srcdoc')          # rendered via sandboxed iframe

    def test_notification_detail_is_admin_only(self):
        row = NotificationLog.objects.create(
            channel='EMAIL', category='TEST', recipient='x@example.com',
            subject='S', body='<p>b</p>', is_html=True, status='SENT')
        regular = make_user('t_body_user')
        c = Client()
        c.force_login(regular)
        self.assertEqual(
            c.get(reverse('tenders:it_notification_detail', args=[row.pk])).status_code,
            403)

    def test_notifications_tab_renders_for_the_admin(self):
        admin = make_user('t_logs_admin', superuser=True)
        NotificationLog.objects.create(
            channel='EMAIL', category='DIGEST', recipient='x@example.com',
            subject='Daily digest', status='SENT',
        )
        c = Client()
        c.force_login(admin)
        r = c.get('/it/logs/?tab=notifications')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Outgoing Notifications')
        self.assertContains(r, 'x@example.com')


# ── 3. Company → Department hierarchy ─────────────────────────────────────────

class CompanyHierarchyTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user('t_company_admin', superuser=True)
        cls.aji   = Company.objects.create(name='AJi Group', code='AJI')
        cls.other = Company.objects.create(name='Sister Co', code='SIS')

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_department_names_are_unique_per_company_not_globally(self):
        Department.objects.create(company=self.aji, name='Engineering')
        form = DepartmentForm(data={'company': self.other.pk, 'name': 'Engineering',
                                    'is_active': 'on'})
        self.assertTrue(form.is_valid(), form.errors)

        dupe = DepartmentForm(data={'company': self.aji.pk, 'name': 'engineering',
                                    'is_active': 'on'})
        self.assertFalse(dupe.is_valid())
        self.assertIn('name', dupe.errors)

    def test_department_form_requires_a_company(self):
        form = DepartmentForm(data={'name': 'Orphans', 'is_active': 'on'})
        self.assertFalse(form.is_valid())
        self.assertIn('company', form.errors)

    def test_user_company_is_derived_from_their_department(self):
        dept = Department.objects.create(company=self.aji, name='Design')
        user = make_user('t_designer')
        profile = user.profile
        profile.department = dept
        profile.save(update_fields=['department'])
        self.assertEqual(user.profile.department.company, self.aji)
        self.assertEqual(self.aji.employee_count, 1)

    def test_company_with_departments_cannot_be_deleted(self):
        Department.objects.create(company=self.aji, name='Roads')
        c = self.client_for(self.admin)
        c.post(reverse('tenders:it_company_delete', args=[self.aji.pk]), follow=True)
        self.assertTrue(Company.objects.filter(pk=self.aji.pk).exists())

    def test_empty_company_deletes_and_is_audited(self):
        empty = Company.objects.create(name='Shell Co')
        c = self.client_for(self.admin)
        c.post(reverse('tenders:it_company_delete', args=[empty.pk]), follow=True)
        self.assertFalse(Company.objects.filter(pk=empty.pk).exists())
        self.assertTrue(AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.COMPANY_DELETE, target='Shell Co').exists())

    def test_creating_a_company_also_creates_its_first_department(self):
        c = self.client_for(self.admin)
        c.post(reverse('tenders:it_company_add'), {
            'name': 'New Venture', 'code': 'NV', 'is_active': 'on',
            'first_department': 'Operations',
        }, follow=True)
        company = Company.objects.get(name='New Venture')
        self.assertEqual([d.name for d in company.departments.all()], ['Operations'])

    def test_company_cannot_be_created_without_a_first_department(self):
        c = self.client_for(self.admin)
        c.post(reverse('tenders:it_company_add'), {
            'name': 'Deptless Co', 'is_active': 'on',
        }, follow=True)
        self.assertFalse(Company.objects.filter(name='Deptless Co').exists())

    def test_user_form_rejects_department_of_a_different_company(self):
        eng = Department.objects.create(company=self.aji, name='Engineering')
        form = ITUserForm(data={
            'username': 'new.user', 'email': 'new.user@example.com',
            'first_name': 'N', 'last_name': 'U', 'is_active': 'on',
            'role': SystemRole.USER, 'password1': 'Str0ng-pass-123',
            'password2': 'Str0ng-pass-123',
            'company': self.other.pk, 'department': eng.pk,
        }, actor=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('department', form.errors)

    def test_user_form_requires_a_department_when_a_company_is_picked(self):
        form = ITUserForm(data={
            'username': 'new.user2', 'email': 'new.user2@example.com',
            'first_name': 'N', 'last_name': 'U', 'is_active': 'on',
            'role': SystemRole.USER, 'password1': 'Str0ng-pass-123',
            'password2': 'Str0ng-pass-123',
            'company': self.aji.pk,
        }, actor=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('department', form.errors)

    def test_user_form_saves_a_matching_company_and_department(self):
        eng = Department.objects.create(company=self.aji, name='Engineering')
        form = ITUserForm(data={
            'username': 'new.user3', 'email': 'new.user3@example.com',
            'first_name': 'N', 'last_name': 'U', 'is_active': 'on',
            'role': SystemRole.USER, 'password1': 'Str0ng-pass-123',
            'password2': 'Str0ng-pass-123',
            'company': self.aji.pk, 'department': eng.pk,
        }, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.profile.department, eng)
        self.assertEqual(user.profile.company, self.aji)

    def test_profile_page_shows_company_and_department(self):
        eng  = Department.objects.create(company=self.aji, name='Engineering')
        user = make_user('t_profile_user')
        profile = user.profile
        profile.department = eng
        profile.save(update_fields=['department'])
        c = self.client_for(user)
        r = c.get(reverse('tenders:profile'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'AJi Group')
        self.assertContains(r, 'Engineering')

    def test_company_pages_are_admin_only(self):
        regular = make_user('t_company_user')
        c = self.client_for(regular)
        self.assertEqual(c.get(reverse('tenders:it_companies')).status_code, 403)
        c = self.client_for(self.admin)
        r = c.get(reverse('tenders:it_companies'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'AJi Group')


# ── 4 & 5. Backup contents and uploaded restore ───────────────────────────────

class BackupPartsAndUploadTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.override = override_settings(BACKUP_ROOT=self.root)
        self.override.enable()
        self.addCleanup(self.override.disable)

    # -- creating ---------------------------------------------------------------

    def test_docs_part_archives_the_documentation(self):
        record = backup_engine.create_backup(
            None, include_database=False, include_media=False,
            include_config=False, include_docs=True,
        )
        self.assertEqual(record.status, SystemBackup.Status.SUCCESS)
        self.assertTrue(record.includes_docs)
        with zipfile.ZipFile(record.file_path) as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read('manifest.json'))
        self.assertIn('docs', manifest['contents'])
        self.assertTrue(any(n.startswith('docs/') and n.endswith('.pdf')
                            for n in names),
                        'the user guide / documentation PDFs are missing')

    def test_code_part_archives_source_but_never_venv_db_or_backups(self):
        record = backup_engine.create_backup(
            None, include_database=False, include_media=False,
            include_config=False, include_code=True,
        )
        self.assertEqual(record.status, SystemBackup.Status.SUCCESS)
        with zipfile.ZipFile(record.file_path) as zf:
            names = zf.namelist()
        self.assertIn('code/manage.py', names)
        self.assertIn('code/tenders/models.py', names)
        self.assertIn('code/templates/base.html', names)
        for forbidden in ('code/.venv/', 'code/db.sqlite3', 'code/backups/',
                          'code/logs/', 'code/media/', 'code/staticfiles/'):
            self.assertFalse(any(n.startswith(forbidden) for n in names),
                             f'{forbidden} leaked into the code backup')
        self.assertFalse(any(n.endswith('.pyc') for n in names))

    def test_archive_carries_a_version_txt_with_project_and_date(self):
        record = backup_engine.create_backup(
            None, include_database=False, include_media=False,
            include_config=True)
        self.assertEqual(record.status, SystemBackup.Status.SUCCESS)
        with zipfile.ZipFile(record.file_path) as zf:
            self.assertIn('version.txt', zf.namelist())
            text = zf.read('version.txt').decode('utf-8')
        self.assertIn('Project : TenderRadar', text)
        self.assertIn('Date    :', text)

    def test_version_label_shows_project_and_date(self):
        record = backup_engine.create_backup(
            None, include_database=False, include_media=False,
            include_config=True)
        self.assertTrue(record.version_label.startswith('TenderRadar · '))

    # -- uploading --------------------------------------------------------------

    def _valid_archive_bytes(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('manifest.json', json.dumps({
                'app': 'TenderRadar', 'created_at': '2026-08-20T10:00:00',
                'created_by': 'admin', 'contents': ['docs'],
            }))
            zf.writestr('docs/TenderRadar_User_Guide.pdf', b'%PDF-1.4 fake')
        return buf.getvalue()

    def test_valid_upload_is_imported_into_the_catalogue(self):
        upload = SimpleUploadedFile('server_backup.zip', self._valid_archive_bytes())
        record, error = backup_engine.import_backup(upload)
        self.assertEqual(error, '')
        self.assertEqual(record.trigger, SystemBackup.Trigger.UPLOADED)
        self.assertTrue(record.includes_docs)
        self.assertFalse(record.includes_database)
        self.assertTrue(Path(record.file_path).is_file())
        self.assertEqual(Path(record.file_path).parent, self.root)

    def test_non_zip_upload_is_rejected(self):
        upload = SimpleUploadedFile('evil.zip', b'this is not a zip at all')
        record, error = backup_engine.import_backup(upload)
        self.assertIsNone(record)
        self.assertIn('zip', error)
        self.assertEqual(list(self.root.glob('*.zip')), [],
                         'a rejected upload must leave nothing on disk')

    def test_zip_without_tenderradar_manifest_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('random.txt', 'hello')
        record, error = backup_engine.import_backup(
            SimpleUploadedFile('other.zip', buf.getvalue()))
        self.assertIsNone(record)
        self.assertIn('manifest', error)

    def test_zip_slip_paths_are_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('manifest.json', json.dumps({'app': 'TenderRadar'}))
            zf.writestr('../../evil.py', 'print("owned")')
        record, error = backup_engine.import_backup(
            SimpleUploadedFile('slip.zip', buf.getvalue()))
        self.assertIsNone(record)
        self.assertIn('unsafe path', error)

    def test_upload_view_imports_and_audits(self):
        admin = make_user('t_upload_admin', superuser=True)
        c = Client()
        c.force_login(admin)
        r = c.post(reverse('tenders:it_backup_upload'), {
            'archive': SimpleUploadedFile('server_backup.zip',
                                          self._valid_archive_bytes()),
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(SystemBackup.objects.filter(
            trigger=SystemBackup.Trigger.UPLOADED).exists())
        self.assertTrue(AdminAuditLog.objects.filter(
            action=AdminAuditLog.Action.BACKUP_UPLOAD).exists())

    def test_upload_is_admin_only(self):
        regular = make_user('t_upload_user')
        c = Client()
        c.force_login(regular)
        r = c.post(reverse('tenders:it_backup_upload'), {
            'archive': SimpleUploadedFile('x.zip', self._valid_archive_bytes()),
        })
        self.assertEqual(r.status_code, 403)

    # -- restoring --------------------------------------------------------------

    def test_restore_form_offers_only_the_parts_the_archive_contains(self):
        upload = SimpleUploadedFile('server_backup.zip', self._valid_archive_bytes())
        record, _ = backup_engine.import_backup(upload)
        form = BackupRestoreForm(backup=record)
        self.assertIn('restore_docs', form.fields)
        self.assertNotIn('restore_database', form.fields)
        self.assertNotIn('restore_code', form.fields)
