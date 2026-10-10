# tenders/migrations/0005_sitesettings_ai.py
"""
Adds the SiteSettings AI columns.

Why this migration is not a plain AddField list
-----------------------------------------------
The migration history has two parallel branches that both add these seven
columns:

    0004 → 0005 (this file) → 0010 → 0011 (removes them again)
    0013 → 0014 → 0015 (adds the same seven) → 0016

Neither is an ancestor of the other, and they are only joined by a later merge.
Django's replay order puts 0015 first, so by the time this migration runs the
columns already exist and a plain AddField fails on MySQL with

    OperationalError (1060, "Duplicate column name 'ai_service'")

which is why `manage.py test` — and any deployment that builds the schema with
`migrate` rather than by restoring the shipped SQL dump — could not work. The
net result of the branch is unchanged: 0011 drops these columns again and 0027
re-creates them, so the final schema is exactly what the models declare.

The state operations are still ordinary AddFields, so Django's model state stays
correct for every later migration. Only the *database* step is made conditional,
by looking at the table rather than by assuming a replay order — if the two
branches are ever ordered the other way round, this still does the right thing.
"""
from django.db import migrations, models

# name -> field, exactly as this migration originally declared them. Used only
# when the column is genuinely absent; when the other branch created it first,
# 0010/0016 reconcile the definition with AlterField.
AI_FIELDS = {
    'ai_service': models.CharField(
        choices=[('NONE', 'None — disable AI features'),
                 ('AJIBRAIN', 'AJiBrain — Local LLM (Ollama on port 8001)'),
                 ('OPENAI', 'OpenAI API')],
        default='NONE', max_length=10, verbose_name='AI Service',
    ),
    'ai_classify': models.BooleanField(default=False, verbose_name='AI Classification'),
    'ai_translate': models.BooleanField(default=False, verbose_name='AI Translation'),
    'openai_api_key': models.CharField(
        blank=True, default='', max_length=200, verbose_name='OpenAI API Key'),
    'openai_model': models.CharField(
        blank=True, default='gpt-4o-mini', max_length=100, verbose_name='OpenAI Model'),
    'ajibrain_url': models.CharField(
        blank=True, default='http://localhost:8001', max_length=200,
        verbose_name='AJiBrain URL'),
    'ajibrain_model': models.CharField(
        blank=True, default='qwen2.5:14b', max_length=100,
        verbose_name='AJiBrain Model'),
}


def add_missing_columns(apps, schema_editor):
    """Create each column only if the table does not already have it."""
    model = apps.get_model('tenders', 'SiteSettings')
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, model._meta.db_table)
        }
    for name, template in AI_FIELDS.items():
        if name in existing:
            continue
        field = template.clone()
        field.set_attributes_from_name(name)
        schema_editor.add_field(model, field)


def drop_columns(apps, schema_editor):
    """Reverse: drop whichever of the columns are present."""
    model = apps.get_model('tenders', 'SiteSettings')
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, model._meta.db_table)
        }
    for name, template in AI_FIELDS.items():
        if name not in existing:
            continue
        field = template.clone()
        field.set_attributes_from_name(name)
        schema_editor.remove_field(model, field)


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0004_sitesettings'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='sitesettings', name=name, field=field.clone(),
                )
                for name, field in AI_FIELDS.items()
            ],
            database_operations=[
                migrations.RunPython(add_missing_columns, drop_columns),
            ],
        ),
    ]
