# tenders/migrations/0013_countryconfig_is_organization.py
from django.db import migrations, models

# Organization codes — not real countries
ORG_CODES = ['WB', 'UN', 'AB', 'IB', 'EU', 'AD', 'BA', 'ZZ']


def mark_organizations(apps, schema_editor):
    CountryConfig = apps.get_model('tenders', 'CountryConfig')
    CountryConfig.objects.filter(code__in=ORG_CODES).update(is_organization=True)


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0012_afdb_source_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='countryconfig',
            name='is_organization',
            field=models.BooleanField(
                default=False,
                help_text='If True, this is an international org (World Bank, AfDB…), '
                          'not a country — excluded from country filter dropdown.',
            ),
        ),
        migrations.RunPython(mark_organizations, migrations.RunPython.noop),
    ]
