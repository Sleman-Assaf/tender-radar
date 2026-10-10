# tenders/migrations/0002_tender_mirror_fields.py

from django.db import migrations, models, connection
import django.db.models.deletion


def _existing_columns(conn, table):
    """Column names of `table`, via Django introspection (works on any vendor)."""
    with conn.cursor() as cursor:
        return {c.name for c in conn.introspection.get_table_description(cursor, table)}


def _add_columns_if_missing(apps, schema_editor):
    """
    Add the two mirror columns only if they are not already present.

    Neither SQLite nor MySQL supports IF NOT EXISTS on ALTER TABLE ADD COLUMN,
    so the presence check is done through Django's introspection API rather than
    a SQLite-only PRAGMA. This keeps the migration correct on:
      - fresh databases of any vendor (columns created here)
      - legacy SQLite databases where the columns already exist (no-op)
    """
    conn = schema_editor.connection
    existing = _existing_columns(conn, 'tenders_tender')

    with conn.cursor() as cursor:
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
                # MySQL/InnoDB: the column, its index and the self-referencing
                # FK are three separate statements. Names match what Django's
                # schema editor would have generated for this field.
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


def _noop_reverse(apps, schema_editor):
    pass  # columns are left in place on reverse


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0001_initial'),
    ]

    operations = [
        # Step 1 — add raw columns safely (handles existing DB)
        migrations.RunPython(_add_columns_if_missing, _noop_reverse),

        # Step 2 — sync Django migration state (no DB ops; columns already added)
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name='tender',
                    name='is_mirror',
                    field=models.BooleanField(
                        default=False,
                        help_text='True when this row is a country-mirror copy of a WB/UN/DE/IB tender'
                    ),
                ),
                migrations.AddField(
                    model_name='tender',
                    name='mirror_of',
                    field=models.ForeignKey(
                        blank=True,
                        help_text='Points to the original tender this row was copied from',
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='mirrors',
                        to='tenders.tender',
                    ),
                ),
            ],
        ),
    ]
