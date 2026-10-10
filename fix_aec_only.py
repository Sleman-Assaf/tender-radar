# fix_aec_only.py
# Run with: python manage.py shell < fix_aec_only.py
# OR:       python manage.py shell -c "exec(open('fix_aec_only.py').read())"

from django.db import connection

cursor = connection.cursor()

# Check columns
cols = [r[1] for r in connection.introspection.get_table_description(cursor, 'tenders_userprofile')]
print(f'Current columns: {cols}')

if 'aec_only' not in cols:
    print('aec_only column not found — nothing to do.')
else:
    keep = [c for c in cols if c != 'aec_only']
    cols_sql = ', '.join(f'"{c}"' for c in keep)

    cursor.execute('PRAGMA foreign_keys=OFF')
    cursor.execute(f'CREATE TABLE "tenders_userprofile_new" AS SELECT {cols_sql} FROM "tenders_userprofile"')
    cursor.execute('DROP TABLE "tenders_userprofile"')
    cursor.execute('ALTER TABLE "tenders_userprofile_new" RENAME TO "tenders_userprofile"')
    cursor.execute('PRAGMA foreign_keys=ON')
    connection.commit()

    # Verify
    cols_after = [r[1] for r in connection.introspection.get_table_description(cursor, 'tenders_userprofile')]
    print(f'Columns after fix: {cols_after}')
    print('Done — aec_only column removed.' if 'aec_only' not in cols_after else 'ERROR: column still present!')

# Also mark migration 0007 as applied so migrate doesn't try to run it again
cursor.execute("DELETE FROM django_migrations WHERE app='tenders' AND name='0007_remove_userprofile_aec_only'")
cursor.execute("INSERT INTO django_migrations (app, name, applied) VALUES ('tenders', '0007_remove_userprofile_aec_only', datetime('now'))")
connection.commit()
print('Migration 0007 marked as applied.')
