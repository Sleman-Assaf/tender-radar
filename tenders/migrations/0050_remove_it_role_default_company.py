# tenders/migrations/0050_remove_it_role_default_company.py
"""
Data migration for the admin restructure ported into this project.

1. The System Administrator (IT) role was removed — the Super Admin now does
   everything. Any profile still holding role='IT' is promoted to SUPER_ADMIN,
   and the Django auth flags are re-mirrored (is_superuser=True, is_staff=True).

2. Departments now belong to a Company. Any department without one is attached
   to a default "AJi Group" company (created only if needed).

Both steps are no-ops on databases with nothing to convert; reverse is a no-op.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    UserProfile = apps.get_model('tenders', 'UserProfile')
    User        = apps.get_model('auth', 'User')
    Company     = apps.get_model('tenders', 'Company')
    Department  = apps.get_model('tenders', 'Department')

    it_profiles = UserProfile.objects.filter(role='IT')
    user_ids = list(it_profiles.values_list('user_id', flat=True))
    if user_ids:
        it_profiles.update(role='SUPER_ADMIN')
        User.objects.filter(pk__in=user_ids).update(is_superuser=True, is_staff=True)

    orphans = Department.objects.filter(company__isnull=True)
    if orphans.exists():
        company, _ = Company.objects.get_or_create(
            name='AJi Group',
            defaults={'code': 'AJI', 'description': 'Default company created '
                      'by the company/department restructure migration.'},
        )
        orphans.update(company=company)


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0049_company_alter_department_options_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
