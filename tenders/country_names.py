# tenders/country_names.py
"""
Names and flags for a country the system meets for the first time.

Countries are created on demand: a scraper reports an ISO code, and a
`CountryConfig` row is made for it if none exists. The fallback used to be the
CODE ITSELF as the name, so a new country arrived on screen as "ZZ" or "CI"
with no flag. Forty-four rows had been created that way before anyone noticed,
and the filter menu made it obvious - a dropdown of two-letter codes.

The flag needs no table. A flag emoji is just the two letters of the code as
regional-indicator characters, so it is derived and can never disagree with
the code it sits beside.

The names do need one, and there is no country library in this project's
dependencies. The table covers what these feeds actually produce - every Arab
League member, every African state, and the rest of the places the newswires
name - which is far more than the seven it replaced. Anything outside it still
falls back to the code, but it now also gets a flag, and the caller logs it so
a missing name is visible rather than silent.
"""
import logging

logger = logging.getLogger('tenders')

#: code -> (English name, Arabic name). Arabic matters because the pages are
#: bilingual and a code in an Arabic column is worse than an English name.
NAMES = {
    # ── Arab League ──────────────────────────────────────────────────────
    'AE': ('United Arab Emirates', 'الإمارات'), 'BH': ('Bahrain', 'البحرين'),
    'DZ': ('Algeria', 'الجزائر'), 'DJ': ('Djibouti', 'جيبوتي'),
    'EG': ('Egypt', 'مصر'), 'IQ': ('Iraq', 'العراق'),
    'JO': ('Jordan', 'الأردن'), 'KM': ('Comoros', 'جزر القمر'),
    'KW': ('Kuwait', 'الكويت'), 'LB': ('Lebanon', 'لبنان'),
    'LY': ('Libya', 'ليبيا'), 'MA': ('Morocco', 'المغرب'),
    'MR': ('Mauritania', 'موريتانيا'), 'OM': ('Oman', 'عُمان'),
    'PS': ('Palestine', 'فلسطين'), 'QA': ('Qatar', 'قطر'),
    'SA': ('Saudi Arabia', 'السعودية'), 'SD': ('Sudan', 'السودان'),
    'SO': ('Somalia', 'الصومال'), 'SY': ('Syria', 'سوريا'),
    'TN': ('Tunisia', 'تونس'), 'YE': ('Yemen', 'اليمن'),
    # ── the rest of Africa ───────────────────────────────────────────────
    'AO': ('Angola', 'أنغولا'), 'BF': ('Burkina Faso', 'بوركينا فاسو'),
    'BI': ('Burundi', 'بوروندي'), 'BJ': ('Benin', 'بنين'),
    'BW': ('Botswana', 'بوتسوانا'), 'CD': ('DR Congo', 'جمهورية الكونغو الديمقراطية'),
    'CF': ('Central African Republic', 'جمهورية أفريقيا الوسطى'),
    'CG': ('Congo', 'الكونغو'), 'CI': ("Côte d'Ivoire", 'ساحل العاج'),
    'CM': ('Cameroon', 'الكاميرون'), 'CV': ('Cabo Verde', 'الرأس الأخضر'),
    'ER': ('Eritrea', 'إريتريا'), 'ET': ('Ethiopia', 'إثيوبيا'),
    'GA': ('Gabon', 'الغابون'), 'GH': ('Ghana', 'غانا'),
    'GM': ('Gambia', 'غامبيا'), 'GN': ('Guinea', 'غينيا'),
    'GQ': ('Equatorial Guinea', 'غينيا الاستوائية'),
    'GW': ('Guinea-Bissau', 'غينيا بيساو'), 'KE': ('Kenya', 'كينيا'),
    'LR': ('Liberia', 'ليبيريا'), 'LS': ('Lesotho', 'ليسوتو'),
    'MG': ('Madagascar', 'مدغشقر'), 'ML': ('Mali', 'مالي'),
    'MU': ('Mauritius', 'موريشيوس'), 'MW': ('Malawi', 'مالاوي'),
    'MZ': ('Mozambique', 'موزمبيق'), 'NA': ('Namibia', 'ناميبيا'),
    'NE': ('Niger', 'النيجر'), 'NG': ('Nigeria', 'نيجيريا'),
    'RW': ('Rwanda', 'رواندا'), 'SC': ('Seychelles', 'سيشل'),
    'SL': ('Sierra Leone', 'سيراليون'), 'SN': ('Senegal', 'السنغال'),
    'SS': ('South Sudan', 'جنوب السودان'),
    'ST': ('São Tomé and Príncipe', 'ساو تومي وبرينسيبي'),
    'SZ': ('Eswatini', 'إسواتيني'), 'TD': ('Chad', 'تشاد'),
    'TG': ('Togo', 'توغو'), 'TZ': ('Tanzania', 'تنزانيا'),
    'UG': ('Uganda', 'أوغندا'), 'ZA': ('South Africa', 'جنوب أفريقيا'),
    'ZM': ('Zambia', 'زامبيا'), 'ZW': ('Zimbabwe', 'زيمبابوي'),
    # ── elsewhere these feeds reach ──────────────────────────────────────
    'AF': ('Afghanistan', 'أفغانستان'), 'AZ': ('Azerbaijan', 'أذربيجان'),
    'BD': ('Bangladesh', 'بنغلاديش'), 'GE': ('Georgia', 'جورجيا'),
    'HT': ('Haiti', 'هايتي'), 'IN': ('India', 'الهند'),
    'KH': ('Cambodia', 'كمبوديا'), 'MD': ('Moldova', 'مولدوفا'),
    'MM': ('Myanmar', 'ميانمار'), 'NP': ('Nepal', 'نيبال'),
    'PA': ('Panama', 'بنما'), 'PE': ('Peru', 'بيرو'),
    'PG': ('Papua New Guinea', 'بابوا غينيا الجديدة'),
    'PK': ('Pakistan', 'باكستان'), 'TR': ('Türkiye', 'تركيا'),
    'UA': ('Ukraine', 'أوكرانيا'), 'UZ': ('Uzbekistan', 'أوزبكستان'),
    'GB': ('United Kingdom', 'المملكة المتحدة'),
    'US': ('United States', 'الولايات المتحدة'),
}

#: Not a country. The bucket a multi-country notice lands in, so it needs a
#: name a reader understands rather than the placeholder code.
INTERNATIONAL = ('International', 'دولي', '🌐')


def flag_for(code: str) -> str:
    """
    The flag emoji for an ISO 3166-1 alpha-2 code.

    Two regional-indicator letters, derived rather than stored, so it cannot
    disagree with the code beside it. '' for anything that is not two letters.
    """
    code = (code or '').strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ''
    return ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in code)


def defaults_for(code: str) -> dict:
    """
    The fields to create a `CountryConfig` with, for `code`.

    Always returns a usable row. An unknown code keeps the code as its name -
    there is nothing better to say - but still gets a flag, and the caller is
    told so a gap in the table can be filled rather than sitting on screen.
    """
    code = (code or '').strip().upper()
    if code == 'ZZ':
        en, ar, flag = INTERNATIONAL
        return {'name_en': en, 'name_ar': ar, 'flag_emoji': flag,
                'currency': ''}

    known = NAMES.get(code)
    if known is None:
        logger.info('[countries] no name known for %s - showing the code. '
                    'Add it to tenders/country_names.py.', code)
        en = ar = code
    else:
        en, ar = known
    return {'name_en': en, 'name_ar': ar, 'flag_emoji': flag_for(code),
            'currency': ''}
