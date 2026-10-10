# Path: tenders/migrations/0033_integrationsettings.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

GROUP_NAME = 'System Admins'
TABLE = 'tenders_integrationsettings'


def drop_stale_table(apps, schema_editor):
    """
    Older builds shipped a side-branch migration that may already have created
    this table. Drop it first (it can only hold un-editable placeholder config,
    since no UI existed) so CreateModel below always succeeds.
    """
    conn = schema_editor.connection
    if TABLE in conn.introspection.table_names():
        schema_editor.execute(f'DROP TABLE {schema_editor.quote_name(TABLE)}')


def seed_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    group, _ = Group.objects.get_or_create(name=GROUP_NAME)
    for user in User.objects.filter(is_superuser=True):
        user.groups.add(group)


def unseed_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('tenders', '0032_merge_integration_branch'),
    ]

    operations = [
        migrations.RunPython(drop_stale_table, migrations.RunPython.noop),
        migrations.CreateModel(
            name='IntegrationSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                # ── Microsoft Graph API ──────────────────────────────────────
                ('graph_enabled', models.BooleanField(default=False, help_text='Send e-mail alerts & reports through Microsoft Graph API.', verbose_name='Enable MS Graph e-mail')),
                ('graph_tenant_id', models.CharField(blank=True, default='', max_length=100, verbose_name='Tenant ID (Directory ID)')),
                ('graph_client_id', models.CharField(blank=True, default='', max_length=100, verbose_name='Client ID (Application ID)')),
                ('graph_client_secret', models.CharField(blank=True, default='', help_text='App registration secret. Requires Mail.Send application permission.', max_length=300, verbose_name='Client Secret')),
                ('graph_sender_email', models.EmailField(blank=True, default='', help_text='Mailbox used as From: (e.g. tenders@company.com).', max_length=254, verbose_name='Sender mailbox')),
                ('graph_report_recipients', models.TextField(blank=True, default='', help_text='Comma-separated e-mail addresses for scheduled alerts & reports.', verbose_name='Default report recipients')),
                # ── LDAP / Active Directory ──────────────────────────────────
                ('ldap_enabled', models.BooleanField(default=False, help_text='Authenticate users against Microsoft LDAP / Active Directory.', verbose_name='Enable LDAP login')),
                ('ldap_server_uri', models.CharField(blank=True, default='', help_text='e.g. ldap://dc1.company.local:389 or ldaps://dc1.company.local:636', max_length=200, verbose_name='LDAP server URI')),
                ('ldap_use_ssl', models.BooleanField(default=False, verbose_name='Use SSL (LDAPS)')),
                ('ldap_domain', models.CharField(blank=True, default='', help_text='e.g. company.local — used to build username@domain for binding.', max_length=120, verbose_name='AD domain / UPN suffix')),
                ('ldap_bind_dn', models.CharField(blank=True, default='', help_text='Optional service account for user lookup, e.g. svc_ldap@company.local', max_length=255, verbose_name='Service bind DN / user')),
                ('ldap_bind_password', models.CharField(blank=True, default='', max_length=255, verbose_name='Service bind password')),
                ('ldap_user_search_base', models.CharField(blank=True, default='', help_text='e.g. OU=Users,DC=company,DC=local', max_length=255, verbose_name='User search base')),
                ('ldap_user_filter', models.CharField(blank=True, default='(sAMAccountName={username})', help_text='{username} is replaced with the login name.', max_length=255, verbose_name='User search filter')),
                ('ldap_auto_create_users', models.BooleanField(default=True, verbose_name='Auto-create users on first LDAP login')),
                # ── WhatsApp ─────────────────────────────────────────────────
                ('wa_enabled', models.BooleanField(default=False, verbose_name='Enable WhatsApp alerts')),
                ('wa_provider', models.CharField(choices=[('WASENDER', 'WaSenderAPI (https://wasenderapi.com/api)'), ('CUSTOM', 'Custom / Other provider (WaSender-compatible)')], default='WASENDER', max_length=12, verbose_name='WhatsApp provider')),
                ('wa_api_url', models.CharField(blank=True, default='https://wasenderapi.com/api', max_length=200, verbose_name='API base URL')),
                ('wa_api_key', models.CharField(blank=True, default='', help_text='WaSenderAPI session API key (sent as Bearer token).', max_length=300, verbose_name='API key / token')),
                ('wa_default_recipients', models.TextField(blank=True, default='', help_text='Comma-separated phone numbers in international format, e.g. +9627xxxxxxxx', verbose_name='Default recipients')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Last changed by')),
            ],
            options={
                'verbose_name': 'Integration Settings',
                'verbose_name_plural': 'Integration Settings',
            },
        ),
        migrations.RunPython(seed_group, unseed_group),
    ]
