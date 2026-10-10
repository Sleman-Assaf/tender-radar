# tenders/migrations/0003_add_mirror_columns.py
#
# This migration exists solely to handle the case where 0002 was recorded
# as applied in django_migrations but the ALTER TABLE statements never
# actually ran (e.g. the DB predated the migration or migrate was not run).
#
# It checks whether the columns exist before attempting ALTER TABLE, so it is
# completely safe to run multiple times. The check goes through Django's
# introspection API (not a SQLite-only PRAGMA), so it is vendor-portable;
# once 0002 has run this is normally a no-op.

from django.db import migrations


def _add_columns_if_missing(apps, schema_editor):
    conn = schema_editor.connection

    with conn.cursor() as cursor:
        existing = {c.name for c in conn.introspection.get_table_description(
            cursor, 'tenders_tender')}

        if 'is_mirror' not in existing:
            cursor.execute(
                "ALTER TABLE tenders_tender "
                "ADD COLUMN is_mirror bool NOT NULL DEFAULT 0"
            )

        if 'mirror_of_id' not in existing:
            if conn.vendor == 'sqlite':
                cursor.execute(
                    "ALTER TABLE tenders_tender "
                    "ADD COLUMN mirror_of_id integer NULL "
                    "REFERENCES tenders_tender(id) DEFERRABLE INITIALLY DEFERRED"
                )
            else:
                cursor.execute(
                    "ALTER TABLE tenders_tender ADD COLUMN mirror_of_id bigint NULL"
                )
                cursor.execute(
                    "CREATE INDEX tenders_tender_mirror_of_id_idx "
                    "ON tenders_tender (mirror_of_id)"
                )
                cursor.execute(
                    "ALTER TABLE tenders_tender "
                    "ADD CONSTRAINT tenders_tender_mirror_of_id_fk "
                    "FOREIGN KEY (mirror_of_id) REFERENCES tenders_tender (id)"
                )


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0002_tender_mirror_fields'),
    ]

    operations = [
        # Only touches the DB — no state changes (0002 already declared the fields).
        migrations.RunPython(_add_columns_if_missing, _noop),
    ]
