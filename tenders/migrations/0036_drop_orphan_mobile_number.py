# Path: tenders/migrations/0036_drop_orphan_mobile_number.py
"""
Drops the orphan `mobile_number` column from tenders_userprofile.

The column was created by an early version of 0030 (later rewritten as a
no-op), so live databases still carry a NOT NULL `mobile_number` column
that the UserProfile model no longer declares. Every INSERT (e.g. the
post_save signal when adding a user in /admin/) then fails with:
    IntegrityError: NOT NULL constraint failed: tenders_userprofile.mobile_number
"""
from django.db import migrations


def drop_orphan_column(apps, schema_editor):
    connection = schema_editor.connection
    table = 'tenders_userprofile'

    with connection.cursor() as cursor:
        if connection.vendor == 'sqlite':
            cursor.execute(f'PRAGMA table_info({table})')
            columns = [row[1] for row in cursor.fetchall()]
            if 'mobile_number' in columns:
                cursor.execute(f'ALTER TABLE {table} DROP COLUMN mobile_number')
        else:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'mobile_number'",
                [table],
            )
            if cursor.fetchone():
                cursor.execute(f'ALTER TABLE {table} DROP COLUMN mobile_number')


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0035_graph_auth_mode'),
    ]

    operations = [
        migrations.RunPython(drop_orphan_column, migrations.RunPython.noop),
    ]
