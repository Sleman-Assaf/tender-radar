# tenders/management/commands/fix_migrations.py
"""
Run this once on any machine where 'migrate' fails with table-already-exists errors.

Usage:
    python manage.py fix_migrations
    python manage.py migrate          # then run this normally
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone


ALL_MIGRATIONS = [
    ('tenders', '0001_initial'),
    ('tenders', '0002_seed_sources'),
    ('tenders', '0003_keywords_and_arabic_fields'),
    ('tenders', '0004_country_config_and_tendering_urls'),
    ('tenders', '0005_scrapersource_filter_by_relevance'),
    ('tenders', '0006_add_morocco_country'),
    ('tenders', '0007_add_worldbank_country'),
    ('tenders', '0008_add_germany_country'),
]

# Tables that must exist before we can proceed
REQUIRED_TABLES = [
    'tenders_tender',
    'tenders_scrapersource',
    'tenders_countryconfig',
    'tenders_relevancekeyword',
]


class Command(BaseCommand):
    help = 'Fix migration state when tables already exist but django_migrations is out of sync'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:

            vendor = connection.vendor

            # 1. Ensure django_migrations table exists (vendor-specific DDL:
            #    AUTOINCREMENT is SQLite syntax, AUTO_INCREMENT is MySQL's).
            if vendor == 'sqlite':
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS django_migrations "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, app VARCHAR(255) NOT NULL, "
                    "name VARCHAR(255) NOT NULL, applied DATETIME NOT NULL)"
                )
            elif vendor == 'mysql':
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS django_migrations "
                    "(id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, "
                    "app VARCHAR(255) NOT NULL, name VARCHAR(255) NOT NULL, "
                    "applied DATETIME(6) NOT NULL) "
                    "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                )
            else:
                # Any other backend: let Django's own recorder create it.
                from django.db.migrations.recorder import MigrationRecorder
                MigrationRecorder(connection).ensure_schema()
            self.stdout.write('✔ django_migrations table ready')

            # 2. Check which tables actually exist in the DB (vendor-portable)
            existing_tables = set(connection.introspection.table_names(cursor))

            missing = [t for t in REQUIRED_TABLES if t not in existing_tables]
            if missing:
                self.stdout.write(self.style.ERROR(
                    f'✘ Missing tables: {missing}\n'
                    'The database does not have the base tables. Recreate it and run: python manage.py migrate'
                ))
                return

            self.stdout.write('✔ All required tables exist in DB')

            # 3. Fake-insert all baseline migrations that aren't already recorded
            cursor.execute("SELECT app, name FROM django_migrations")
            already_applied = {(row[0], row[1]) for row in cursor.fetchall()}

            inserted = 0
            for app, name in ALL_MIGRATIONS:
                if (app, name) not in already_applied:
                    cursor.execute(
                        "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, %s)",
                        [app, name, timezone.now()]
                    )
                    self.stdout.write(f'  + Faked: {app}.{name}')
                    inserted += 1
                else:
                    self.stdout.write(f'  · Already applied: {app}.{name}')

            # 4. Also ensure contenttypes and auth are faked (needed for 0009)
            for app, name in [
                ('contenttypes', '0001_initial'),
                ('contenttypes', '0002_remove_content_type_name'),
                ('auth', '0001_initial'),
                ('auth', '0002_alter_permission_name_max_length'),
                ('auth', '0003_alter_user_email_max_length'),
                ('auth', '0004_alter_user_username_opts'),
                ('auth', '0005_alter_user_last_login_null'),
                ('auth', '0006_require_contenttypes_0002'),
                ('auth', '0007_alter_validators_add_error_messages'),
                ('auth', '0008_alter_user_username_max_length'),
                ('auth', '0009_alter_user_last_name_max_length'),
                ('auth', '0010_alter_group_name_max_length'),
                ('auth', '0011_update_proxy_permissions'),
                ('auth', '0012_alter_user_first_name_max_length'),
                ('admin', '0001_initial'),
                ('admin', '0002_logentry_remove_auto_add'),
                ('admin', '0003_logentry_add_action_flag_choices'),
                ('sessions', '0001_initial'),
            ]:
                if (app, name) not in already_applied:
                    # No INSERT OR IGNORE on MySQL; the membership test above
                    # already guarantees the row is absent.
                    cursor.execute(
                        "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, %s)",
                        [app, name, timezone.now()]
                    )
                    inserted += 1

        self.stdout.write(self.style.SUCCESS(
            f'\n✔ Done. Faked {inserted} migration(s).\n'
            'Now run:  python manage.py migrate\n'
            'This will only apply 0009_tenderview (the new TenderView table).'
        ))
