# tenders/migrations/0040_rbac_departments_backups.py
"""
Role Management & IT Administration.

Adds Department, the UserProfile.role / .department fields, the backup
catalogue (SystemBackup, BackupRestore, BackupSchedule) and the administrative
audit trail, then backfills a role for every existing account:

    is_superuser            → SUPER_ADMIN
    member of 'IT' group    → IT
    everyone else           → USER
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_roles(apps, schema_editor):
    User        = apps.get_model('auth', 'User')
    Group       = apps.get_model('auth', 'Group')
    UserProfile = apps.get_model('tenders', 'UserProfile')

    it_user_ids = set()
    try:
        it_group = Group.objects.get(name='IT')
        it_user_ids = set(it_group.user_set.values_list('pk', flat=True))
    except Group.DoesNotExist:
        pass

    for user in User.objects.all():
        if user.is_superuser:
            role = 'SUPER_ADMIN'
        elif user.pk in it_user_ids:
            role = 'IT'
        else:
            role = 'USER'

        profile, created = UserProfile.objects.get_or_create(
            user=user, defaults={'role': role},
        )
        if not created and profile.role != role:
            profile.role = role
            profile.save(update_fields=['role'])

        # Keep the auth flags consistent with the role from day one.
        should_be_staff = role in ('SUPER_ADMIN', 'IT')
        if user.is_staff != should_be_staff:
            user.is_staff = should_be_staff
            user.save(update_fields=['is_staff'])


def unbackfill_roles(apps, schema_editor):
    """Reverse is a no-op — the columns themselves are dropped."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('tenders', '0039_merge_20260805_1144'),
    ]

    operations = [
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True, verbose_name='Department Name')),
                ('name_ar', models.CharField(blank=True, default='', max_length=120, verbose_name='الاسم بالعربي')),
                ('code', models.CharField(blank=True, default='', help_text='Optional short code, e.g. ENG, FIN, IT.', max_length=20)),
                ('description', models.TextField(blank=True, default='')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('head', models.ForeignKey(blank=True, help_text='The user who leads this department. Can be changed at any time.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='headed_departments', to=settings.AUTH_USER_MODEL, verbose_name='Department Head')),
            ],
            options={
                'verbose_name': 'Department',
                'verbose_name_plural': 'Departments — الأقسام',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='SystemBackup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('filename', models.CharField(max_length=200)),
                ('file_path', models.CharField(blank=True, default='', max_length=500)),
                ('size_bytes', models.BigIntegerField(default=0)),
                ('status', models.CharField(choices=[('RUNNING', 'Running'), ('SUCCESS', 'Success'), ('FAILED', 'Failed')], default='RUNNING', max_length=10)),
                ('trigger', models.CharField(choices=[('MANUAL', 'Manual'), ('SCHEDULED', 'Scheduled'), ('PRE_RESTORE', 'Safety copy (pre-restore)')], default='MANUAL', max_length=15)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('includes_database', models.BooleanField(default=True)),
                ('includes_media', models.BooleanField(default=True)),
                ('includes_config', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('error_message', models.TextField(blank=True, default='')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='system_backups', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'System Backup',
                'verbose_name_plural': 'System Backups — النسخ الاحتياطية',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='BackupSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False, verbose_name='Automatic Backups')),
                ('frequency', models.CharField(choices=[('DAILY', 'Daily'), ('WEEKLY', 'Weekly'), ('MONTHLY', 'Monthly')], default='DAILY', max_length=10)),
                ('hour', models.PositiveSmallIntegerField(default=2, help_text='Server local time (Asia/Amman).', verbose_name='Run At (hour, 0–23)')),
                ('weekday', models.PositiveSmallIntegerField(default=0, help_text='0 = Monday … 6 = Sunday. Used when frequency is Weekly.', verbose_name='Weekday')),
                ('day_of_month', models.PositiveSmallIntegerField(default=1, help_text='1–28. Used when frequency is Monthly.', verbose_name='Day of Month')),
                ('includes_database', models.BooleanField(default=True, verbose_name='Include Database')),
                ('includes_media', models.BooleanField(default=True, verbose_name='Include Uploaded Files')),
                ('includes_config', models.BooleanField(default=True, verbose_name='Include Configuration')),
                ('retention_count', models.PositiveSmallIntegerField(default=10, help_text='Older scheduled backups are pruned automatically. 0 = keep everything.', verbose_name='Keep Last N Backups')),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Backup Schedule',
                'verbose_name_plural': 'Backup Schedule',
            },
        ),
        migrations.CreateModel(
            name='BackupRestore',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('restored_at', models.DateTimeField(auto_now_add=True)),
                ('status', models.CharField(choices=[('SUCCESS', 'Success'), ('FAILED', 'Failed')], default='SUCCESS', max_length=10)),
                ('restored_database', models.BooleanField(default=False)),
                ('restored_media', models.BooleanField(default=False)),
                ('restored_config', models.BooleanField(default=False)),
                ('error_message', models.TextField(blank=True, default='')),
                ('backup', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='restores', to='tenders.systembackup')),
                ('restored_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='backup_restores', to=settings.AUTH_USER_MODEL)),
                ('safety_backup', models.ForeignKey(blank=True, help_text='Automatic backup taken immediately before this restore.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='restore_safety_for', to='tenders.systembackup')),
            ],
            options={
                'verbose_name': 'Backup Restore',
                'verbose_name_plural': 'Backup Restores',
                'ordering': ['-restored_at'],
            },
        ),
        migrations.CreateModel(
            name='AdminAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('USER_CREATE', 'User created'), ('USER_UPDATE', 'User updated'), ('USER_DELETE', 'User deleted'), ('USER_ACTIVATE', 'User activated'), ('USER_DEACTIVATE', 'User deactivated'), ('PASSWORD_RESET', 'Password reset'), ('ROLE_CHANGE', 'Role changed'), ('DEPT_CREATE', 'Department created'), ('DEPT_UPDATE', 'Department updated'), ('DEPT_DELETE', 'Department deleted'), ('DEPT_HEAD_CHANGE', 'Department head changed'), ('USER_MOVE', 'User moved between departments'), ('BACKUP_CREATE', 'Backup created'), ('BACKUP_DELETE', 'Backup deleted'), ('BACKUP_DOWNLOAD', 'Backup downloaded'), ('BACKUP_RESTORE', 'Backup restored'), ('SCHEDULE_UPDATE', 'Backup schedule updated'), ('ACCESS_DENIED', 'Access denied')], max_length=20)),
                ('target', models.CharField(blank=True, default='', max_length=200)),
                ('detail', models.TextField(blank=True, default='')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='admin_actions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Admin Audit Log',
                'verbose_name_plural': 'Admin Audit Logs — سجل العمليات',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='adminauditlog',
            index=models.Index(fields=['-created_at'], name='tenders_adm_created_17968f_idx'),
        ),
        migrations.AddIndex(
            model_name='adminauditlog',
            index=models.Index(fields=['action'], name='tenders_adm_action_4b3c5f_idx'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='role',
            field=models.CharField(choices=[('SUPER_ADMIN', 'Super Admin'), ('IT', 'IT Department'), ('USER', 'Regular User')], default='USER', help_text='Super Admin = unrestricted. IT Department = IT Dashboard. Regular User = tender modules only.', max_length=20, verbose_name='System Role'),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='department',
            field=models.ForeignKey(blank=True, help_text='An employee belongs to exactly one department.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='member_profiles', to='tenders.department', verbose_name='Department'),
        ),
        migrations.RunPython(backfill_roles, unbackfill_roles),
    ]
