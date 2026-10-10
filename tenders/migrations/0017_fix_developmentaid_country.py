# tenders/migrations/0017_fix_developmentaid_country.py
"""
Fixes existing DevelopmentAid tenders that were incorrectly assigned
country='JO' (Jordan) when they should have a different country or 'ZZ'.

Re-resolves country from the tender title/entity using the same logic
as the updated scraper.
"""
from django.db import migrations


ORG_CODES = {'WB', 'UN', 'AB', 'IB', 'EU', 'AD', 'BA', 'ZZ'}

COUNTRY_MAP = {
    'guinea': 'GN', "cote d'ivoire": 'CI', 'ivory coast': 'CI',
    'democratic republic of congo': 'CD', 'dem. rep. congo': 'CD',
    'congo': 'CG', 'kenya': 'KE', 'ethiopia': 'ET',
    'tanzania': 'TZ', 'mozambique': 'MZ', 'uganda': 'UG', 'ghana': 'GH',
    'nigeria': 'NG', 'senegal': 'SN', 'mali': 'ML', 'niger': 'NE',
    'burkina faso': 'BF', 'cameroon': 'CM', 'south sudan': 'SS',
    'sudan': 'SD', 'somalia': 'SO', 'egypt': 'EG', 'morocco': 'MA',
    'algeria': 'DZ', 'tunisia': 'TN', 'libya': 'LY', 'iraq': 'IQ',
    'syria': 'SY', 'lebanon': 'LB', 'palestine': 'PS', 'yemen': 'YE',
    'afghanistan': 'AF', 'pakistan': 'PK', 'bangladesh': 'BD',
    'india': 'IN', 'nepal': 'NP', 'myanmar': 'MM', 'cambodia': 'KH',
    'haiti': 'HT', 'ukraine': 'UA', 'georgia': 'GE', 'moldova': 'MD',
    'zambia': 'ZM', 'zimbabwe': 'ZW', 'malawi': 'MW', 'rwanda': 'RW',
    'burundi': 'BI', 'madagascar': 'MG', 'togo': 'TG', 'benin': 'BJ',
    'liberia': 'LR', 'sierra leone': 'SL', 'gambia': 'GM',
    'mauritania': 'MR', 'chad': 'TD', 'central african': 'CF',
    'jordan': 'JO', 'saudi': 'SA', 'uae': 'AE', 'emirates': 'AE',
    'turkey': 'TR', 'indonesia': 'ID', 'vietnam': 'VN',
    'philippines': 'PH', 'thailand': 'TH', 'laos': 'LA',
    'tajikistan': 'TJ', 'kyrgyzstan': 'KG', 'uzbekistan': 'UZ',
    'kazakhstan': 'KZ', 'armenia': 'AM', 'azerbaijan': 'AZ',
    'colombia': 'CO', 'peru': 'PE', 'ecuador': 'EC', 'bolivia': 'BO',
    'guatemala': 'GT', 'honduras': 'HN', 'nicaragua': 'NI',
    'mozambique': 'MZ', 'angola': 'AO', 'namibia': 'NA',
    'botswana': 'BW', 'lesotho': 'LS', 'eswatini': 'SZ',
}


def resolve_from_text(text: str) -> str:
    if not text:
        return 'ZZ'
    t = text.lower()
    for name, code in COUNTRY_MAP.items():
        if name in t:
            return code
    return 'ZZ'


def fix_countries(apps, schema_editor):
    Tender       = apps.get_model('tenders', 'Tender')
    ScraperSource = apps.get_model('tenders', 'ScraperSource')

    # Get all DevelopmentAid source IDs
    da_source_ids = list(
        ScraperSource.objects.filter(
            scraper_class__icontains='DevelopmentAid'
        ).values_list('id', flat=True)
    )

    if not da_source_ids:
        return

    # Find tenders from DevelopmentAid assigned to Jordan that aren't actually Jordan
    tenders = Tender.objects.filter(
        source_id__in=da_source_ids,
        country__code='JO'
    ).select_related('country')

    updated = 0
    for tender in tenders:
        # Try to resolve from title + entity
        text = f"{tender.title or ''} {tender.title_ar or ''} {tender.entity or ''}"
        code = resolve_from_text(text)
        if code != 'JO':
            try:
                from django.apps import apps as django_apps
                CountryConfig = django_apps.get_model('tenders', 'CountryConfig')
                country = CountryConfig.objects.filter(code=code).first()
                if country:
                    tender.country = country
                    tender.save(update_fields=['country'])
                    updated += 1
                else:
                    # Use ZZ
                    zz = CountryConfig.objects.filter(code='ZZ').first()
                    if zz:
                        tender.country = zz
                        tender.save(update_fields=['country'])
                        updated += 1
            except Exception:
                pass

    print(f'[Migration] Fixed {updated} DevelopmentAid tenders reassigned from JO')


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0016_sitesettings_ollama'),
    ]

    operations = [
        migrations.RunPython(fix_countries, migrations.RunPython.noop),
    ]
