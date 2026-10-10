# tenders/migrations/0002_seed_sources.py

from django.db import migrations


def seed_sources(apps, schema_editor):
    ScraperSource = apps.get_model('tenders', 'ScraperSource')

    sources = [
        {
            'name': 'Jordan Government Tenders Directorate',
            'country': 'JO',
            'base_url': 'https://www.gtd.gov.jo',
            'scraper_class': 'JordanGTDScraper',
            'is_active': True,
            'description': 'Official government tenders portal for Jordan — all ministries and public entities.',
        },
        {
            'name': 'Saudi Arabia — Etimad / Monafasat',
            'country': 'SA',
            'base_url': 'https://monafasat.etimad.sa',
            'scraper_class': 'SaudiEtimadScraper',
            'is_active': True,
            'description': 'Main Saudi government procurement platform used by all Saudi ministries.',
        },
        {
            'name': 'UAE — Abu Dhabi Procurement (ADPS)',
            'country': 'AE',
            'base_url': 'https://www.procurement.gov.ae',
            'scraper_class': 'UAEAbuDhabiScraper',
            'is_active': True,
            'description': 'Abu Dhabi government procurement and supply chain platform.',
        },
        {
            'name': 'UAE — Dubai Tenders Portal',
            'country': 'AE',
            'base_url': 'https://tenders.dubai.gov.ae',
            'scraper_class': 'UAEDubaiTendersScraper',
            'is_active': True,
            'description': 'Dubai government tenders portal managed by the Dubai Government.',
        },
        {
            'name': 'Oman — Tender Board',
            'country': 'OM',
            'base_url': 'https://www.tenderboard.gov.om',
            'scraper_class': 'OmanTenderBoardScraper',
            'is_active': True,
            'description': 'Oman official tender board — public tenders for all government entities.',
        },
        {
            'name': 'Qatar — Government Tenders Portal',
            'country': 'QA',
            'base_url': 'https://www.tender.gov.qa',
            'scraper_class': 'QatarTendersScraper',
            'is_active': True,
            'description': 'Qatar government tenders and procurement portal.',
        },
        {
            'name': 'Kuwait — Central Agency for Public Tenders',
            'country': 'KW',
            'base_url': 'https://www.capt.gov.kw',
            'scraper_class': 'KuwaitCAPTScraper',
            'is_active': True,
            'description': 'Kuwait CAPT — the official entity managing all public procurement tenders.',
        },
    ]

    for s in sources:
        ScraperSource.objects.get_or_create(
            name=s['name'],
            defaults=s
        )


def unseed_sources(apps, schema_editor):
    ScraperSource = apps.get_model('tenders', 'ScraperSource')
    ScraperSource.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_sources, unseed_sources),
    ]
