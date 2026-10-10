# tenders/migrations/0007_remove_userprofile_aec_only.py
from django.db import migrations


def drop_aec_only_if_exists(apps, schema_editor):
    """
    Drop the legacy `aec_only` column if a database still carries it.

    On a fresh database of any vendor the column was never created, so this is
    a no-op — which is the case for every MySQL install. The SQLite table
    rebuild is kept for legacy SQLite databases that predate this migration.
    """
    db = schema_editor.connection
    cursor = db.cursor()

    columns = [row[1] for row in db.introspection.get_table_description(
        cursor, 'tenders_userprofile'
    )]
    if 'aec_only' not in columns:
        return  # nothing to do — the normal path on a fresh database

    if db.vendor != 'sqlite':
        # Every other supported backend can drop a column in place.
        cursor.execute('ALTER TABLE tenders_userprofile DROP COLUMN aec_only')
        return

    keep = [c for c in columns if c != 'aec_only']
    cols = ', '.join(f'"{c}"' for c in keep)

    # SQLite full table-rebuild (the only safe way on older SQLite)
    cursor.execute('PRAGMA foreign_keys=OFF')
    cursor.execute(f'CREATE TABLE "tenders_userprofile_backup" AS SELECT {cols} FROM "tenders_userprofile"')
    cursor.execute('DROP TABLE "tenders_userprofile"')
    cursor.execute('ALTER TABLE "tenders_userprofile_backup" RENAME TO "tenders_userprofile"')
    cursor.execute('PRAGMA foreign_keys=ON')


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0006_userprofile'),
    ]

    operations = [
        migrations.RunPython(drop_aec_only_if_exists, migrations.RunPython.noop),
    ]
