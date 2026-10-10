# tenders/country_extractor.py
"""
Country extractor for mirror-copy logic.

For tenders from WB / UN / DE / IB sources, the scraper sets
`td.country_code` to the actual beneficiary country (e.g. 'JO', 'EG').
This module collects ALL distinct country codes from that field, plus
any extra countries mentioned by name in the title/description text,
and returns them as a list of ISO-2 codes.

If a country code is not yet in CountryConfig it is auto-created using
the name/flag data embedded in COUNTRY_META below.

Usage:
    from tenders.country_extractor import extract_mirror_countries

    codes = extract_mirror_countries(td)   # returns ['JO', 'EG', ...]
"""

import re
import logging
from typing import Optional

logger = logging.getLogger('tenders')

# ── Global source codes that trigger mirror logic ──────────────────────────────
MIRROR_SOURCE_CODES = {'WB', 'UN', 'DE', 'IB', 'EU'}

# ── Country meta for auto-creation when DB row is missing ─────────────────────
# (code, name_en, name_ar, flag, currency, sort_order)
COUNTRY_META: dict[str, tuple] = {
    'AF': ('Afghanistan',        'أفغانستان',          '🇦🇫', 'AFN', 100),
    'AL': ('Albania',            'ألبانيا',             '🇦🇱', 'ALL', 101),
    'DZ': ('Algeria',            'الجزائر',             '🇩🇿', 'DZD', 102),
    'AZ': ('Azerbaijan',         'أذربيجان',            '🇦🇿', 'AZN', 103),
    'BH': ('Bahrain',            'البحرين',             '🇧🇭', 'BHD', 104),
    'BD': ('Bangladesh',         'بنغلاديش',            '🇧🇩', 'BDT', 105),
    'BJ': ('Benin',              'بنين',                '🇧🇯', 'XOF', 106),
    'BA': ('Bosnia Herzegovina', 'البوسنة والهرسك',     '🇧🇦', 'BAM', 107),
    'BN': ('Brunei',             'بروناي',              '🇧🇳', 'BND', 108),
    'BF': ('Burkina Faso',       'بوركينا فاسو',        '🇧🇫', 'XOF', 109),
    'CM': ('Cameroon',           'الكاميرون',           '🇨🇲', 'XAF', 110),
    'CF': ('Central African Republic', 'جمهورية أفريقيا الوسطى', '🇨🇫', 'XAF', 111),
    'TD': ('Chad',               'تشاد',                '🇹🇩', 'XAF', 112),
    'KM': ('Comoros',            'جزر القمر',           '🇰🇲', 'KMF', 113),
    'CI': ('Cote d\'Ivoire',     'ساحل العاج',          '🇨🇮', 'XOF', 114),
    'DJ': ('Djibouti',           'جيبوتي',              '🇩🇯', 'DJF', 115),
    'EG': ('Egypt',              'مصر',                 '🇪🇬', 'EGP', 116),
    'GA': ('Gabon',              'الغابون',             '🇬🇦', 'XAF', 117),
    'GM': ('Gambia',             'غامبيا',              '🇬🇲', 'GMD', 118),
    'GN': ('Guinea',             'غينيا',               '🇬🇳', 'GNF', 119),
    'GW': ('Guinea-Bissau',      'غينيا بيساو',         '🇬🇼', 'XOF', 120),
    'GY': ('Guyana',             'غيانا',               '🇬🇾', 'GYD', 121),
    'ID': ('Indonesia',          'إندونيسيا',           '🇮🇩', 'IDR', 122),
    'IR': ('Iran',               'إيران',               '🇮🇷', 'IRR', 123),
    'IQ': ('Iraq',               'العراق',              '🇮🇶', 'IQD', 124),
    'JO': ('Jordan',             'الأردن',              '🇯🇴', 'JOD', 125),
    'KZ': ('Kazakhstan',         'كازاخستان',           '🇰🇿', 'KZT', 126),
    'KE': ('Kenya',              'كينيا',               '🇰🇪', 'KES', 127),
    'KG': ('Kyrgyzstan',         'قيرغيزستان',          '🇰🇬', 'KGS', 128),
    'KW': ('Kuwait',             'الكويت',              '🇰🇼', 'KWD', 129),
    'LB': ('Lebanon',            'لبنان',               '🇱🇧', 'LBP', 130),
    'LY': ('Libya',              'ليبيا',               '🇱🇾', 'LYD', 131),
    'MY': ('Malaysia',           'ماليزيا',             '🇲🇾', 'MYR', 132),
    'MV': ('Maldives',           'المالديف',            '🇲🇻', 'MVR', 133),
    'ML': ('Mali',               'مالي',                '🇲🇱', 'XOF', 134),
    'MR': ('Mauritania',         'موريتانيا',           '🇲🇷', 'MRU', 135),
    'MA': ('Morocco',            'المغرب',              '🇲🇦', 'MAD', 136),
    'MZ': ('Mozambique',         'موزمبيق',             '🇲🇿', 'MZN', 137),
    'NE': ('Niger',              'النيجر',              '🇳🇪', 'XOF', 138),
    'NG': ('Nigeria',            'نيجيريا',             '🇳🇬', 'NGN', 139),
    'OM': ('Oman',               'عُمان',               '🇴🇲', 'OMR', 140),
    'PK': ('Pakistan',           'باكستان',             '🇵🇰', 'PKR', 141),
    'PS': ('Palestine',          'فلسطين',              '🇵🇸', 'ILS', 142),
    'QA': ('Qatar',              'قطر',                 '🇶🇦', 'QAR', 143),
    'SA': ('Saudi Arabia',       'المملكة العربية السعودية', '🇸🇦', 'SAR', 2),
    'SN': ('Senegal',            'السنغال',             '🇸🇳', 'XOF', 144),
    'SL': ('Sierra Leone',       'سيراليون',            '🇸🇱', 'SLL', 145),
    'SO': ('Somalia',            'الصومال',             '🇸🇴', 'SOS', 146),
    'SD': ('Sudan',              'السودان',             '🇸🇩', 'SDG', 147),
    'SR': ('Suriname',           'سورينام',             '🇸🇷', 'SRD', 148),
    'SY': ('Syria',              'سوريا',               '🇸🇾', 'SYP', 149),
    'TJ': ('Tajikistan',         'طاجيكستان',           '🇹🇯', 'TJS', 150),
    'TG': ('Togo',               'توغو',                '🇹🇬', 'XOF', 151),
    'TN': ('Tunisia',            'تونس',                '🇹🇳', 'TND', 152),
    'TR': ('Turkey',             'تركيا',               '🇹🇷', 'TRY', 153),
    'TM': ('Turkmenistan',       'تركمانستان',          '🇹🇲', 'TMT', 154),
    'UG': ('Uganda',             'أوغندا',              '🇺🇬', 'UGX', 155),
    'AE': ('United Arab Emirates', 'الإمارات العربية المتحدة', '🇦🇪', 'AED', 156),
    'UZ': ('Uzbekistan',         'أوزبكستان',           '🇺🇿', 'UZS', 157),
    'YE': ('Yemen',              'اليمن',               '🇾🇪', 'YER', 158),
    'GH': ('Ghana',              'غانا',                '🇬🇭', 'GHS', 159),
    'ET': ('Ethiopia',           'إثيوبيا',             '🇪🇹', 'ETB', 160),
    'RW': ('Rwanda',             'رواندا',              '🇷🇼', 'RWF', 161),
    'TZ': ('Tanzania',           'تنزانيا',             '🇹🇿', 'TZS', 162),
    'ZW': ('Zimbabwe',           'زيمبابوي',            '🇿🇼', 'ZWL', 163),
    'MG': ('Madagascar',         'مدغشقر',              '🇲🇬', 'MGA', 164),
    'MW': ('Malawi',             'ملاوي',               '🇲🇼', 'MWK', 165),
    'BI': ('Burundi',            'بوروندي',             '🇧🇮', 'BIF', 166),
    'MU': ('Mauritius',          'موريشيوس',            '🇲🇺', 'MUR', 167),
    'ER': ('Eritrea',            'إريتريا',             '🇪🇷', 'ERN', 168),
    # ── EBRD / Eastern Europe / Central Asia ──────────────────────────────
    'UA': ('Ukraine',            'أوكرانيا',            '🇺🇦', 'UAH', 169),
    'AM': ('Armenia',            'أرمينيا',             '🇦🇲', 'AMD', 170),
    'BY': ('Belarus',            'بيلاروسيا',           '🇧🇾', 'BYR', 171),
    'BG': ('Bulgaria',           'بلغاريا',             '🇧🇬', 'BGN', 172),
    'HR': ('Croatia',            'كرواتيا',             '🇭🇷', 'HRK', 173),
    'CY': ('Cyprus',             'قبرص',                '🇨🇾', 'EUR', 174),
    'CZ': ('Czech Republic',     'التشيك',              '🇨🇿', 'CZK', 175),
    'EE': ('Estonia',            'إستونيا',             '🇪🇪', 'EUR', 176),
    'GE': ('Georgia',            'جورجيا',              '🇬🇪', 'GEL', 177),
    'HU': ('Hungary',            'المجر',               '🇭🇺', 'HUF', 178),
    'XK': ('Kosovo',             'كوسوفو',              '🇽🇰', 'EUR', 179),
    'LV': ('Latvia',             'لاتفيا',              '🇱🇻', 'EUR', 180),
    'LT': ('Lithuania',          'ليتوانيا',            '🇱🇹', 'EUR', 181),
    'MK': ('North Macedonia',    'مقدونيا الشمالية',    '🇲🇰', 'MKD', 182),
    'MD': ('Moldova',            'مولدوفا',             '🇲🇩', 'MDL', 183),
    'MN': ('Mongolia',           'منغوليا',             '🇲🇳', 'MNT', 184),
    'ME': ('Montenegro',         'الجبل الأسود',        '🇲🇪', 'EUR', 185),
    'PL': ('Poland',             'بولندا',              '🇵🇱', 'PLN', 186),
    'RO': ('Romania',            'رومانيا',             '🇷🇴', 'RON', 187),
    'RU': ('Russia',             'روسيا',               '🇷🇺', 'RUB', 188),
    'RS': ('Serbia',             'صربيا',               '🇷🇸', 'RSD', 189),
    'SK': ('Slovakia',           'سلوفاكيا',            '🇸🇰', 'EUR', 190),
    'SI': ('Slovenia',           'سلوفينيا',            '🇸🇮', 'EUR', 191),
    'ZM': ('Zambia',             'زامبيا',              '🇿🇲', 'ZMW', 192),
    'IN': ('India',              'الهند',               '🇮🇳', 'INR', 193),
    'CN': ('China',              'الصين',               '🇨🇳', 'CNY', 194),
    'ZA': ('South Africa',       'جنوب أفريقيا',        '🇿🇦', 'ZAR', 195),
    'PS': ('Palestine',          'فلسطين',              '🇵🇸', 'ILS', 142),
}

# ── Country name → ISO2 for text-mining the description ──────────────────────
# Lower-cased; longer/more-specific names listed first to avoid partial matches.
_NAME_TO_CC: list[tuple[str, str]] = [
    # Multi-word first (longer matches win)
    ('central african republic',   'CF'),
    ('guinea-bissau',              'GW'),
    ('guinea bissau',              'GW'),
    ('sierra leone',               'SL'),
    ('burkina faso',               'BF'),
    ('cote d\'ivoire',             'CI'),
    ("cote d'ivoire",              'CI'),
    ('ivory coast',                'CI'),
    ('saudi arabia',               'SA'),
    ('united arab emirates',       'AE'),
    ('kyrgyz republic',            'KG'),
    ('kyrgyzstan',                 'KG'),
    ('kyrgyz',                     'KG'),
    ('bosnia and herzegovina',     'BA'),
    ('bosnia & herzegovina',       'BA'),
    ('bosnia herzegovina',         'BA'),
    ('south africa',               'ZA'),
    ('south sudan',                'SS'),
    ('trinidad and tobago',        'TT'),
    ('new zealand',                'NZ'),
    ('democratic republic of congo', 'CD'),
    ('dr congo',                   'CD'),
    ('congo',                      'CG'),
    ('sri lanka',                  'LK'),
    ('el salvador',                'SV'),
    ('costa rica',                 'CR'),
    ('cape verde',                 'CV'),
    ('cabo verde',                 'CV'),
    ('burkina',                    'BF'),
    ('turkmenistan',               'TM'),
    ('türkiye',                    'TR'),
    ('tajikistan',                 'TJ'),
    ('azerbaijan',                 'AZ'),
    ('kazakhstan',                 'KZ'),
    ('uzbekistan',                 'UZ'),
    ('afghanistan',                'AF'),
    ('bangladesh',                 'BD'),
    ('indonesia',                  'ID'),
    ('mozambique',                 'MZ'),
    ('mauritania',                 'MR'),
    ('mauritius',                  'MU'),
    ('madagascar',                 'MG'),
    ('zimbabwe',                   'ZW'),
    ('tanzania',                   'TZ'),
    ('djibouti',                   'DJ'),
    ('cameroon',                   'CM'),
    ('suriname',                   'SR'),
    ('maldives',                   'MV'),
    ('malaysia',                   'MY'),
    ('comoros',                    'KM'),
    ('albania',                    'AL'),
    ('algeria',                    'DZ'),
    ('bahrain',                    'BH'),
    ('lebanon',                    'LB'),
    ('morocco',                    'MA'),
    ('nigeria',                    'NG'),
    ('somalia',                    'SO'),
    ('tunisia',                    'TN'),
    ('ukraine',                    'UA'),
    ('armenia',                    'AM'),
    ('belarus',                    'BY'),
    ('bulgaria',                   'BG'),
    ('croatia',                    'HR'),
    ('cyprus',                     'CY'),
    ('czech republic',             'CZ'),
    ('czechia',                    'CZ'),
    ('estonia',                    'EE'),
    ('georgia',                    'GE'),
    ('hungary',                    'HU'),
    ('kosovo',                     'XK'),
    ('latvia',                     'LV'),
    ('lithuania',                  'LT'),
    ('north macedonia',            'MK'),
    ('macedonia',                  'MK'),
    ('moldova',                    'MD'),
    ('mongolia',                   'MN'),
    ('montenegro',                 'ME'),
    ('poland',                     'PL'),
    ('romania',                    'RO'),
    ('russia',                     'RU'),
    ('serbia',                     'RS'),
    ('slovakia',                   'SK'),
    ('slovenia',                   'SI'),
    ('zambia',                     'ZM'),
    ('india',                      'IN'),
    ('china',                      'CN'),
    ('south africa',               'ZA'),
    ('burundi',                    'BI'),
    ('eritrea',                    'ER'),
    ('guyana',                     'GY'),
    ('jordan',                     'JO'),
    ('kuwait',                     'KW'),
    ('malawi',                     'MW'),
    ('panama',                     'PA'),
    ('rwanda',                     'RW'),
    ('senegal',                    'SN'),
    ('turkey',                     'TR'),
    ('uganda',                     'UG'),
    ('brunei',                     'BN'),
    ('gabon',                      'GA'),
    ('ghana',                      'GH'),
    ('india',                      'IN'),
    ('kenya',                      'KE'),
    ('libya',                      'LY'),
    ('niger',                      'NE'),
    ('china',                      'CN'),
    ('oman',                       'OM'),
    ('peru',                       'PE'),
    ('togo',                       'TG'),
    ('benin',                      'BJ'),
    ('chile',                      'CL'),
    ('egypt',                      'EG'),
    ('gambia',                     'GM'),
    ('guinea',                     'GN'),
    ('iran',                       'IR'),
    ('iraq',                       'IQ'),
    ('mali',                       'ML'),
    ('chad',                       'TD'),
    ('qatar',                      'QA'),
    ('sudan',                      'SD'),
    ('syria',                      'SY'),
    ('tonga',                      'TO'),
    ('yemen',                      'YE'),
    ('pakistan',                   'PK'),
    ('ethiopia',                   'ET'),
    ('palestine',                  'PS'),
]

# Pre-compile into sorted regex (longest first to avoid shadowing)
_COUNTRY_RE = re.compile(
    r'\b(' + '|'.join(re.escape(name) for name, _ in _NAME_TO_CC) + r')\b',
    re.IGNORECASE
)
_NAME_LOOKUP: dict[str, str] = {name: cc for name, cc in _NAME_TO_CC}

# Codes we never want as mirrors (the "umbrella" source countries)
_SKIP_AS_MIRROR = {'WB', 'UN', 'DE', 'IB', 'EU', 'ZA', 'AB'}  # ZA=AfricaGateway, AB=AfDB


def extract_mirror_countries(td) -> list[str]:
    """
    Return a deduplicated list of ISO-2 country codes for mirror copies.

    Strategy:
    1. If td.country_code is already a real country (not an umbrella code), use it.
    2. Mine title + description text for country name mentions.
    3. Deduplicate; skip umbrella codes.
    """
    codes: list[str] = []
    seen: set[str] = set()

    def _add(cc: str):
        cc = cc.upper()
        if cc and cc not in _SKIP_AS_MIRROR and cc not in seen:
            seen.add(cc)
            codes.append(cc)

    # 1. Use per-tender country_code if it's a real country
    primary = (td.country_code or '').upper()
    if primary and primary not in _SKIP_AS_MIRROR:
        _add(primary)

    # 2. Text-mine title + description
    text = ' '.join(filter(None, [td.title, getattr(td, 'description', '')])).lower()
    for match in _COUNTRY_RE.finditer(text):
        name = match.group(1).lower()
        cc   = _NAME_LOOKUP.get(name)
        if cc:
            _add(cc)

    return codes


def ensure_country(code: str) -> Optional[object]:
    """
    Get or auto-create a CountryConfig row for the given ISO-2 code.
    Returns the CountryConfig instance, or None on failure.
    """
    from tenders.models import CountryConfig

    code = code.upper()
    try:
        return CountryConfig.objects.get(code=code)
    except CountryConfig.DoesNotExist:
        pass

    meta = COUNTRY_META.get(code)
    if not meta:
        logger.warning(f'[mirror] Unknown country code {code!r} — cannot auto-create')
        return None

    name_en, name_ar, flag, currency, sort_order = meta
    obj, created = CountryConfig.objects.get_or_create(
        code=code,
        defaults={
            'name_en':    name_en,
            'name_ar':    name_ar,
            'flag_emoji': flag,
            'currency':   currency,
            'sort_order': sort_order,
            'is_active':  True,
        }
    )
    if created:
        logger.info(f'[mirror] Auto-created CountryConfig: {flag} {name_en} ({code})')
    return obj
