"""
Tests for the System Dashboard consolidation.

Covers the three things the migration is supposed to guarantee:

  1. every administrative page is reachable by the Super Admin and by nobody
     else — including the pages that used to be open or public;
  2. the duplicate scraper-log page is gone;
  3. system administration has exactly one entry point in the navigation.

Plus the role rule the dashboard depends on: only the Super Admin administers
anything (the separate System Administrator / IT role was removed 2026-08-24).
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tenders.it_forms import BackupScheduleForm
from tenders.models import BackupSchedule, SystemRole, UserProfile
from tenders.permissions import assignable_roles, can_edit_user

# (url, human name). Every one of these is administrative.
ADMIN_PAGES = [
    ('/it/',                      'System Dashboard'),
    ('/it/users/',                'Users'),
    ('/it/roles/',                'Roles'),
    ('/it/departments/',          'Departments'),
    ('/it/backups/',              'Backup & Restore'),
    ('/it/logs/',                 'System Logs'),
    ('/it/settings/',             'Settings'),
    ('/manage/',                  'Tender System hub'),
    ('/manage/countries/',        'Countries'),
    ('/manage/sources/',          'Source management'),
    ('/manage/keywords/relevance/', 'Relevance keywords'),
    ('/manage/keywords/category/',  'Category keywords'),
    ('/manage/integrations/',     'Integrations (e-mail / Graph / LDAP)'),
    ('/manage/digest/',           'Notification settings'),
    ('/manage/ai-settings/',      'AI settings'),
    ('/manage/feedback/',         'User feedback'),
    ('/manage/users/',            'Tender access control'),
    ('/analytics/',               'Analytics'),
    ('/analytics/logins/',        'Sign-in report'),
    ('/analytics/scraper-health/', 'Scraper health'),
    ('/sources/',                 'Sources'),
    ('/aec-feedback/report/',     'AEC feedback report'),
    ('/it/runs/',                 'Newspaper & Magazine runs'),
    ('/it/documentation/',        'Developer documentation'),
    ('/it/documentation/add-source/', 'Adding a new source guide'),
]


def make_user(username, role=SystemRole.USER, superuser=False):
    """
    A user with `role` applied the way the application applies it.

    The profile is created by a post_save signal and cached on the user, so it
    is mutated in place rather than re-fetched — updating a second UserProfile
    instance would leave `user.profile` stale and the user would still look like
    a regular user to get_role(). sync_auth_flags() then mirrors the role onto
    is_superuser / is_staff exactly as the admin views do.
    """
    user = User.objects.create_user(username=username, password='pw-for-tests')
    profile = user.profile
    profile.role = SystemRole.SUPER_ADMIN if superuser else role
    profile.save(update_fields=['role'])
    profile.sync_auth_flags()
    user.refresh_from_db()
    return user


class AdminPageAccessTests(TestCase):
    """Anonymous → login, regular user → 403, Super Admin → 200."""

    @classmethod
    def setUpTestData(cls):
        cls.super_admin = make_user('t_super', superuser=True)
        cls.regular = make_user('t_regular', role=SystemRole.USER)

    def client_for(self, user=None):
        c = Client()
        if user:
            c.force_login(user)
        return c

    def test_anonymous_is_sent_to_login(self):
        c = self.client_for()
        for url, name in ADMIN_PAGES:
            with self.subTest(page=name):
                r = c.get(url)
                self.assertIn(r.status_code, (301, 302),
                              f'{url} did not redirect an anonymous visitor')
                self.assertIn('login', r['Location'],
                              f'{url} redirected somewhere other than login')

    def test_regular_user_is_refused(self):
        c = self.client_for(self.regular)
        for url, name in ADMIN_PAGES:
            with self.subTest(page=name):
                self.assertEqual(c.get(url).status_code, 403,
                                 f'{url} is reachable by a regular user')

    def test_super_admin_gets_in(self):
        c = self.client_for(self.super_admin)
        for url, name in ADMIN_PAGES:
            with self.subTest(page=name):
                self.assertEqual(c.get(url).status_code, 200,
                                 f'{url} refused the Super Admin')


class RemovedDuplicateTests(TestCase):
    """The standalone scrape-log page was a weaker copy of System Logs."""

    @classmethod
    def setUpTestData(cls):
        cls.sysadmin = make_user('t_sysadmin2', superuser=True)

    def test_old_scrape_logs_url_is_gone(self):
        c = Client()
        c.force_login(self.sysadmin)
        self.assertEqual(c.get('/scrape-logs/').status_code, 404)

    def test_scrape_log_data_is_still_available_in_the_dashboard(self):
        c = Client()
        c.force_login(self.sysadmin)
        r = c.get('/it/logs/?tab=scrapers')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Scraper Runs')


class SingleEntryPointTests(TestCase):
    """System administration must not be reachable from the main navigation."""

    @classmethod
    def setUpTestData(cls):
        cls.sysadmin = make_user('t_sysadmin3', superuser=True)
        cls.regular = make_user('t_regular3', role=SystemRole.USER)

    def test_main_nav_has_no_admin_links_for_regular_users(self):
        c = Client()
        c.force_login(self.regular)
        html = c.get(reverse('tenders:dashboard')).content.decode()
        for fragment in ('/manage/', '/analytics/',
                         '/scrape-logs/', '/admin/', '/it/'):
            self.assertNotIn(fragment, html,
                             f'regular user is offered {fragment} in the navigation')

    def test_admin_sees_exactly_one_way_in(self):
        c = Client()
        c.force_login(self.sysadmin)
        html = c.get(reverse('tenders:dashboard')).content.decode()
        # the dashboard link appears twice by design: desktop nav + mobile nav
        self.assertEqual(html.count(reverse('tenders:it_dashboard')), 2)
        for gone in ('/manage/', '/analytics/', '/scrape-logs/', '/admin/'):
            self.assertNotIn(gone, html,
                             f'{gone} is still linked from the main navigation')

    def test_django_admin_panel_is_not_mounted(self):
        """It duplicated the dashboard with raw model access; the URL is gone."""
        c = Client()
        c.force_login(self.sysadmin)
        for url in ('/admin/', '/admin/login/', '/admin/auth/user/'):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 404)

    def test_dashboard_does_not_link_the_django_admin(self):
        c = Client()
        c.force_login(self.sysadmin)
        for page in ('/it/', '/it/settings/'):
            with self.subTest(page=page):
                self.assertNotIn('/admin/', c.get(page).content.decode())

    def test_dashboard_nav_offers_every_module(self):
        c = Client()
        c.force_login(self.sysadmin)
        html = c.get('/it/').content.decode()
        for url in ('/it/users/', '/it/roles/', '/it/departments/',
                    '/it/backups/', '/it/settings/', '/it/logs/',
                    '/manage/', '/sources/', '/manage/integrations/',
                    '/manage/digest/', '/analytics/'):
            self.assertIn(url, html, f'{url} is missing from the dashboard nav')


class DocumentationDownloadTests(TestCase):
    """The developer documentation is confidential — gated and audited."""

    @classmethod
    def setUpTestData(cls):
        cls.sysadmin = make_user('t_doc_admin', superuser=True)
        cls.regular = make_user('t_doc_user', role=SystemRole.USER)

    def test_admin_downloads_the_pdf(self):
        c = Client()
        c.force_login(self.sysadmin)
        r = c.get('/it/documentation/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('attachment', r['Content-Disposition'])
        body = b''.join(r.streaming_content)
        self.assertTrue(body.startswith(b'%PDF'), 'not a PDF')
        self.assertGreater(len(body), 1_000_000)

    def test_regular_user_is_refused(self):
        c = Client()
        c.force_login(self.regular)
        self.assertEqual(c.get('/it/documentation/').status_code, 403)

    def test_download_is_written_to_the_audit_trail(self):
        from tenders.models import AdminAuditLog
        c = Client()
        c.force_login(self.sysadmin)
        before = AdminAuditLog.objects.filter(action='DOC_DOWNLOAD').count()
        b''.join(c.get('/it/documentation/').streaming_content)
        self.assertEqual(
            AdminAuditLog.objects.filter(action='DOC_DOWNLOAD').count(),
            before + 1)

    def test_not_served_as_a_static_or_media_file(self):
        """static/ and media/ are handed out with no authentication."""
        c = Client()
        for url in ('/static/docs/TenderRadar_Developer_Documentation.pdf',
                    '/media/TenderRadar_Developer_Documentation.pdf',
                    '/docs/TenderRadar_Developer_Documentation.pdf'):
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 404)

    def test_add_source_guide_downloads(self):
        c = Client()
        c.force_login(self.sysadmin)
        r = c.get('/it/documentation/add-source/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('TenderRadar_Adding_A_New_Source.pdf',
                      r['Content-Disposition'])
        body = b''.join(r.streaming_content)
        self.assertTrue(body.startswith(b'%PDF'))

    def test_add_source_guide_refused_to_regular_users(self):
        c = Client()
        c.force_login(self.regular)
        self.assertEqual(
            c.get('/it/documentation/add-source/').status_code, 403)

    def test_user_guide_downloads_for_an_admin(self):
        c = Client()
        c.force_login(self.sysadmin)
        r = c.get('/documentation/user-guide/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertIn('TenderRadar_User_Guide.pdf', r['Content-Disposition'])
        self.assertTrue(b''.join(r.streaming_content).startswith(b'%PDF'))

    def test_user_guide_is_for_every_signed_in_user(self):
        """Its stated audience is all staff, not just administrators."""
        c = Client()
        c.force_login(self.regular)
        r = c.get('/documentation/user-guide/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(b''.join(r.streaming_content).startswith(b'%PDF'))

    def test_user_guide_still_requires_signing_in(self):
        """It is confidential — open to staff, not to the public."""
        r = Client().get('/documentation/user-guide/')
        self.assertIn(r.status_code, (301, 302))
        self.assertIn('login', r['Location'])

    def test_user_guide_is_offered_in_the_account_menu(self):
        c = Client()
        c.force_login(self.regular)
        html = c.get(reverse('tenders:dashboard')).content.decode()
        self.assertIn('/documentation/user-guide/', html)
        self.assertIn('User Guide', html)

    def test_regular_user_still_gets_no_code_documentation(self):
        c = Client()
        c.force_login(self.regular)
        self.assertEqual(c.get('/it/documentation/').status_code, 403)
        html = c.get(reverse('tenders:dashboard')).content.decode()
        self.assertNotIn('/it/documentation/', html)

    def test_documentation_menu_offers_both_documents(self):
        """Hovering Documentation reveals the code doc and the user guide."""
        c = Client()
        c.force_login(self.sysadmin)
        html = c.get('/it/').content.decode()
        self.assertIn('doc-menu-panel', html)
        panel = html[html.index('doc-menu-panel'):][:900]
        self.assertIn('Code Documentation', panel)
        self.assertIn('User Guide', panel)
        self.assertIn('/it/documentation/"', panel)
        self.assertIn('/documentation/user-guide/', panel)

    def test_both_download_buttons_are_in_the_top_action_bar(self):
        """Beside Create Backup and Add User, not duplicated elsewhere."""
        c = Client()
        c.force_login(self.sysadmin)
        html = c.get('/it/').content.decode()
        # the RENDERED div, not the .page-head-actions CSS rule that appears
        # earlier in the inline stylesheet
        marker = '<div class="page-head-actions">'
        self.assertIn(marker, html)
        bar_at = html.index(marker)
        # The action bar has no unique closing marker to slice on, and a fixed
        # character window silently truncated it (the icons are verbose SVG).
        # Assert position instead: every control sits after the bar opens and
        # before the first section of the page body.
        body_at = html.index('Quick Actions')
        positions = {}
        for label in ('Create Backup', 'Add New Source', 'Documentation',
                      'Add User'):
            with self.subTest(button=label):
                i = html.find(label, bar_at)
                self.assertNotEqual(i, -1, f'{label} is missing')
                self.assertLess(i, body_at,
                                f'{label} is not in the header action bar')
                positions[label] = i
        self.assertEqual(
            sorted(positions, key=positions.get),
            ['Create Backup', 'Add New Source', 'Documentation', 'Add User'],
            'the action bar is not in the expected order')
        # exactly one link to each document in the action bar
        header = html[bar_at:body_at]
        self.assertEqual(header.count('"/it/documentation/"'), 1)
        self.assertEqual(header.count('/it/documentation/add-source/'), 1)
        self.assertEqual(header.count('/documentation/user-guide/'), 1)

    def test_the_dashboard_offers_the_button(self):
        c = Client()
        c.force_login(self.sysadmin)
        html = c.get('/it/').content.decode()
        self.assertIn('/it/documentation/', html)
        self.assertIn('Documentation', html)


class RoleBoundaryTests(TestCase):
    """Only the Super Admin administers anything; the IT role is gone."""

    @classmethod
    def setUpTestData(cls):
        cls.super_admin = make_user('t_super4', superuser=True)
        cls.peer = make_user('t_peer4', superuser=True)
        cls.regular = make_user('t_regular4', role=SystemRole.USER)

    def test_the_it_role_no_longer_exists(self):
        self.assertNotIn('IT', SystemRole.values)
        self.assertEqual(set(SystemRole.values), {'SUPER_ADMIN', 'USER'})

    def test_super_admin_may_administer_everyone(self):
        for target in (self.peer, self.regular, self.super_admin):
            self.assertTrue(can_edit_user(self.super_admin, target))

    def test_super_admin_may_assign_every_role(self):
        self.assertEqual([c for c, _ in assignable_roles(self.super_admin)],
                         list(SystemRole.values))

    def test_regular_user_administers_nobody(self):
        self.assertFalse(can_edit_user(self.regular, self.regular))
        self.assertFalse(can_edit_user(self.regular, self.super_admin))
        self.assertEqual(assignable_roles(self.regular), [])

    def test_regular_user_cannot_reach_role_change_endpoint(self):
        c = Client()
        c.force_login(self.regular)
        r = c.post(f'/it/roles/{self.regular.pk}/change/',
                   {'role': SystemRole.SUPER_ADMIN})
        self.assertEqual(r.status_code, 403)
        self.regular.refresh_from_db()
        self.assertFalse(self.regular.is_superuser)

    def test_super_admin_can_edit_a_peer_super_admin(self):
        c = Client()
        c.force_login(self.super_admin)
        self.assertEqual(c.get(f'/it/users/{self.peer.pk}/edit/').status_code, 200)


class BackupScheduleFormTests(TestCase):
    """
    The schedule form owns `includes_database` / `includes_media` /
    `includes_config`. They were absent from the page, so every save posted
    them as unchecked and cleared them — a scheduled backup would then archive
    nothing. The page must render them, and a save must preserve them.
    """

    def setUp(self):
        self.admin  = make_user('sched-admin', SystemRole.SUPER_ADMIN, superuser=True)
        self.client = Client()
        self.client.force_login(self.admin)

    def test_page_renders_every_field_the_form_saves(self):
        html = self.client.get('/it/backups/').content.decode()
        for name in BackupScheduleForm().fields:
            with self.subTest(field=name):
                self.assertIn(f'name="{name}"', html)

    def test_saving_the_schedule_keeps_the_contents_selected(self):
        schedule = BackupSchedule.get()
        schedule.includes_database = True
        schedule.includes_media    = True
        schedule.includes_config   = True
        schedule.save()

        # Exactly what the page posts once the checkboxes are ticked.
        self.client.post(reverse('tenders:it_backup_schedule'), {
            'enabled': 'on', 'frequency': 'WEEKLY', 'hour': '2',
            'weekday': '3', 'day_of_month': '1', 'retention_count': '10',
            'includes_database': 'on', 'includes_media': 'on', 'includes_config': 'on',
        }, follow=True)

        schedule.refresh_from_db()
        self.assertTrue(schedule.includes_database)
        self.assertTrue(schedule.includes_media)
        self.assertTrue(schedule.includes_config)

    def test_hidden_frequency_fields_still_post_their_value(self):
        # Weekday is hidden when the frequency is Monthly, but it is hidden —
        # not disabled — so switching back to Weekly must find it unchanged.
        self.client.post(reverse('tenders:it_backup_schedule'), {
            'enabled': 'on', 'frequency': 'MONTHLY', 'hour': '3',
            'weekday': '5', 'day_of_month': '12', 'retention_count': '4',
            'includes_database': 'on',
        }, follow=True)
        schedule = BackupSchedule.get()
        self.assertEqual(schedule.weekday, 5)
        self.assertEqual(schedule.day_of_month, 12)

    def test_describe_says_what_will_happen_in_one_sentence(self):
        schedule = BackupSchedule.get()
        schedule.enabled = False
        self.assertEqual(schedule.describe(),
                         'Automatic backups are off — nothing will run.')

        schedule.enabled           = True
        schedule.frequency         = BackupSchedule.Frequency.WEEKLY
        schedule.weekday           = 2
        schedule.hour              = 2
        schedule.retention_count   = 10
        schedule.includes_database = True
        schedule.includes_media    = True
        schedule.includes_config   = False
        self.assertEqual(
            schedule.describe(),
            'Backs up the database and uploaded files every Wednesday at 02:00, '
            'keeping the last 10.')

        # The state that used to be saved silently: an archive of nothing.
        schedule.includes_database = False
        schedule.includes_media    = False
        self.assertIn('the archive would be empty', schedule.describe())
