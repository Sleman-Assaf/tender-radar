# tenders/migrations/0047_three_categories.py
# Path: TenderRadar/tenders/migrations/0047_three_categories.py
#
# Collapse all legacy tender categories into the new three-category system:
#   BUILDING, INFRA, HEALTH  (+ OTHER as internal default)

from django.db import migrations, models


# old code → new code
CATEGORY_REMAP = {
    # → BUILDING
    'ARCH':        'BUILDING',
    'STRUCT':      'BUILDING',
    'MEP':         'BUILDING',
    'SUPV':        'BUILDING',
    'PMC':         'BUILDING',
    'URBAN':       'BUILDING',
    # → INFRA
    'CIVIL':       'INFRA',
    'TRANS':       'INFRA',
    'ENV':         'INFRA',
    'INFRA':       'INFRA',
    # → HEALTH
    'HEALTH':      'HEALTH',
    # everything else (ADV, WORKS, CONSULTANCY, GOODS, SERVICES, ...) → OTHER
}

NEW_CODES = {'BUILDING', 'INFRA', 'HEALTH', 'OTHER'}


def forwards(apps, schema_editor):
    Tender          = apps.get_model('tenders', 'Tender')
    CategoryKeyword = apps.get_model('tenders', 'CategoryKeyword')

    # ── Remap Tender.category ────────────────────────────────────────────
    for old, new in CATEGORY_REMAP.items():
        if old != new:
            Tender.objects.filter(category=old).update(category=new)
    # Anything left that's not a valid new code → OTHER
    Tender.objects.exclude(category__in=NEW_CODES).update(category='OTHER')

    # ── Remap CategoryKeyword rows (dedupe on unique (category, keyword)) ─
    for kw in CategoryKeyword.objects.all():
        new_cat = CATEGORY_REMAP.get(kw.category, 'OTHER' if kw.category not in NEW_CODES else kw.category)
        if new_cat == kw.category:
            continue
        clash = CategoryKeyword.objects.filter(
            category=new_cat, keyword=kw.keyword
        ).exclude(pk=kw.pk).exists()
        if clash:
            kw.delete()
        else:
            kw.category = new_cat
            kw.save(update_fields=['category'])
    # Drop keyword rows that ended up as OTHER — OTHER is not a real category
    CategoryKeyword.objects.filter(category='OTHER').delete()


def backwards(apps, schema_editor):
    # Not reversible — old fine-grained categories cannot be restored.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0046_per_user_notes'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name='tender',
            name='category',
            field=models.CharField(
                choices=[
                    ('BUILDING', 'Building — مباني'),
                    ('INFRA',    'Infrastructure — بنية تحتية'),
                    ('HEALTH',   'Health care — رعاية صحية'),
                    ('OTHER',    'Other — أخرى'),
                ],
                default='OTHER',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='categorykeyword',
            name='category',
            field=models.CharField(
                choices=[
                    ('BUILDING', 'Building — مباني'),
                    ('INFRA',    'Infrastructure — بنية تحتية'),
                    ('HEALTH',   'Health care — رعاية صحية'),
                    ('OTHER',    'Other — أخرى'),
                ],
                max_length=10,
            ),
        ),
    ]
