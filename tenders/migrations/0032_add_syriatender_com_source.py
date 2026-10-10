# tenders/migrations/0032_add_syriatender_com_source.py
"""
Data migration — registers the SyriaTender.com scraper source in the admin
(ScraperSource + one TenderingURL) so it appears automatically after
`python manage.py migrate`, without needing seed_data.
"""

from django.db import migrations


def add_source(apps, schema_editor):
    CountryConfig = apps.get_model('tenders', 'CountryConfig')
    ScraperSource = apps.get_model('tenders', 'ScraperSource')
    TenderingURL  = apps.get_model('tenders', 'TenderingURL')

    # Ensure the SY country row exists (it normally does already).
    country, _ = CountryConfig.objects.get_or_create(
        code='SY',
        defaults={'name_en': 'Syria', 'name_ar': 'سوريا', 'flag_emoji': '🇸🇾'},
    )

    source, created = ScraperSource.objects.get_or_create(
        scraper_class='SyriaTenderComScraper',
        defaults={
            'name':        'Syria — SyriaTender.com',
            'name_ar':     'سوريا — مركز المناقصات السوري',
            'country':     country,
            'base_url':    'https://syriatender.com',
            'is_active':   True,
            'description': ('Syrian Tender Center (syriatender.com) — '
                            'open tenders via public Supabase API'),
        },
    )

    if created:
        TenderingURL.objects.create(
            source=source,
            url='https://syriatender.com/tenders',
            label='Syria — Open Tenders',
            label_ar='سوريا — المناقصات المفتوحة',
            is_active=True,
            sort_order=1,
        )


def remove_source(apps, schema_editor):
    ScraperSource = apps.get_model('tenders', 'ScraperSource')
    ScraperSource.objects.filter(scraper_class='SyriaTenderComScraper').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0031_userprofile_phone'),
    ]

    operations = [
        migrations.RunPython(add_source, remove_source),
    ]
