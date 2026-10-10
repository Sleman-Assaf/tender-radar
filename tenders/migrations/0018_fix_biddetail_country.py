# tenders/migrations/0018_fix_biddetail_country.py
"""
Re-assigns BidDetail tenders from default country (JO/KE) to the correct
country derived from the entity/title text.
"""
from django.db import migrations

_COUNTRY_MAP = {
    'jordan': 'JO', 'iraq': 'IQ', 'syria': 'SY', 'lebanon': 'LB',
    'palestine': 'PS', 'saudi': 'SA', 'yemen': 'YE', 'oman': 'OM',
    'kuwait': 'KW', 'bahrain': 'BH', 'qatar': 'QA', 'uae': 'AE',
    'emirates': 'AE', 'turkey': 'TR', 'egypt': 'EG', 'iran': 'IR',
    'kenya': 'KE', 'tanzania': 'TZ', 'uganda': 'UG', 'ethiopia': 'ET',
    'rwanda': 'RW', 'somalia': 'SO', 'eritrea': 'ER', 'djibouti': 'DJ',
    'south sudan': 'SS', 'sudan': 'SD', 'burundi': 'BI', 'comoros': 'KM',
    'ghana': 'GH', 'nigeria': 'NG', 'senegal': 'SN', 'mali': 'ML',
    'morocco': 'MA', 'algeria': 'DZ', 'tunisia': 'TN', 'libya': 'LY',
    'mozambique': 'MZ', 'zambia': 'ZM', 'zimbabwe': 'ZW', 'angola': 'AO',
    'india': 'IN', 'pakistan': 'PK', 'bangladesh': 'BD', 'nepal': 'NP',
    'afghanistan': 'AF', 'myanmar': 'MM', 'cambodia': 'KH',
    'indonesia': 'ID', 'philippines': 'PH', 'vietnam': 'VN',
}


def _resolve(text):
    t = text.lower()
    for name, code in sorted(_COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if name in t:
            return code
    return 'ZZ'


def fix_biddetail_countries(apps, schema_editor):
    Tender        = apps.get_model('tenders', 'Tender')
    ScraperSource = apps.get_model('tenders', 'ScraperSource')
    CountryConfig = apps.get_model('tenders', 'CountryConfig')

    bd_ids = list(ScraperSource.objects.filter(
        scraper_class__in=['BidDetailWesternAsiaScraper', 'BidDetailEasternAfricaScraper']
    ).values_list('id', flat=True))

    if not bd_ids:
        return

    country_cache = {c.code: c for c in CountryConfig.objects.all()}
    updated = 0

    for tender in Tender.objects.filter(source_id__in=bd_ids).select_related('country'):
        text = f"{tender.title or ''} {tender.entity or ''}"
        code = _resolve(text)
        country = country_cache.get(code) or country_cache.get('ZZ')
        if country and tender.country != country:
            tender.country = country
            tender.save(update_fields=['country'])
            updated += 1

    print(f'[Migration 0018] Fixed {updated} BidDetail tender countries')


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0017_fix_developmentaid_country'),
    ]

    operations = [
        migrations.RunPython(fix_biddetail_countries, migrations.RunPython.noop),
    ]
