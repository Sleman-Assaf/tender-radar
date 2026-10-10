# tenders/management/commands/seed_data.py
"""
Seeds the database with:
  - 9 Middle East countries
  - 7 scraper sources (one per country/portal)
  - 14+ tendering URLs
  - 30 relevance keywords (EN + AR)
  - 55 category keywords (EN + AR)

Run: python manage.py seed_data
Run: python manage.py seed_data --reset   (clears and re-seeds keywords)
"""

from django.core.management.base import BaseCommand
from django.db import transaction


COUNTRIES = [
    ('JO', 'Jordan',        'الأردن',       '🇯🇴', 'JOD', 1),
    ('SA', 'Saudi Arabia',  'المملكة العربية السعودية', '🇸🇦', 'SAR', 2),
    ('AE', 'UAE',           'الإمارات العربية المتحدة', '🇦🇪', 'AED', 3),
    ('OM', 'Oman',          'سلطنة عُمان',  '🇴🇲', 'OMR', 4),
    ('QA', 'Qatar',         'دولة قطر',     '🇶🇦', 'QAR', 5),
    ('KW', 'Kuwait',        'الكويت',       '🇰🇼', 'KWD', 6),
    ('BH', 'Bahrain',       'مملكة البحرين','🇧🇭', 'BHD', 7),
    ('EG', 'Egypt',         'جمهورية مصر',  '🇪🇬', 'EGP', 8),
    ('IQ', 'Iraq',          'جمهورية العراق','🇮🇶', 'IQD', 9),
    ('SY', 'Syria',         'الجمهورية العربية السورية', '🇸🇾', 'SYP', 10),
    ('BI', 'Burundi',       'بوروندي',                   '🇧🇮', 'BIF', 11),
    ('TZ', 'Tanzania',      'تنزانيا',                   '🇹🇿', 'TZS', 12),
    ('UG', 'Uganda',        'أوغندا',                    '🇺🇬', 'UGX', 13),
    ('KM', 'Comoros',       'جزر القمر',                 '🇰🇲', 'KMF', 14),
    ('DJ', 'Djibouti',      'جيبوتي',                    '🇩🇯', 'DJF', 15),
    ('ER', 'Eritrea',       'إريتريا',                   '🇪🇷', 'ERN', 16),
    ('ET', 'Ethiopia',      'إثيوبيا',                   '🇪🇹', 'ETB', 17),
    ('KE', 'Kenya',         'كينيا',                     '🇰🇪', 'KES', 18),
    ('MG', 'Madagascar',    'مدغشقر',                    '🇲🇬', 'MGA', 19),
    ('MW', 'Malawi',        'مالاوي',                    '🇲🇼', 'MWK', 20),
    ('MU', 'Mauritius',     'موريشيوس',                  '🇲🇺', 'MUR', 21),
    ('MZ', 'Mozambique',    'موزمبيق',                   '🇲🇿', 'MZN', 22),
    ('RW', 'Rwanda',        'رواندا',                    '🇷🇼', 'RWF', 23),
    ('SO', 'Somalia',       'الصومال',                   '🇸🇴', 'SOS', 24),
    ('ZW', 'Zimbabwe',      'زيمبابوي',                  '🇿🇼', 'ZWL', 25),
    ('MA', 'Morocco',       'المملكة المغربية',          '🇲🇦', 'MAD', 26),
    ('WB', 'World Bank',    'البنك الدولي',              '🌍', 'USD', 27),
    ('DE', 'Germany',       'ألمانيا',                   '🇩🇪', 'EUR', 28),
    ('UN', 'United Nations','الأمم المتحدة',             '🇺🇳', 'USD', 29),
    ('IB', 'IsDB',          'البنك الإسلامي للتنمية',   '🕌', 'USD', 30),
    ('EU', 'EBRD',          'البنك الأوروبي لإعادة الإعمار والتنمية', '🏦', 'EUR', 31),
    ('AB', 'AfDB',          'بنك التنمية الأفريقي',                   '🌍', 'USD', 32),
    ('AD', 'ADB',           'بنك التنمية الآسيوي',                    '🌏', 'USD', 33),
    ('BA', 'BADEA',         'البنك العربي للتنمية الاقتصادية في أفريقيا', '🌍', 'USD', 34),
    ('ZZ', 'International',  'دولي / غير محدد',                           '🌐', 'USD', 35),
]

SOURCES = [
    {
        'name': 'Jordan — Government Tenders Directorate (GTD)',
        'name_ar': 'الأردن — ديوان المناقصات الحكومية',
        'country': 'JO',
        'base_url': 'https://www.gtd.gov.jo',
        'scraper_class': 'JordanGTDScraper',
        'description': 'Official portal for all Jordanian government tenders.',
        'description_ar': 'البوابة الرسمية لمناقصات الحكومة الأردنية.',
        'urls': [
            {
                'label': 'Tenders Under Offering — مناقصات قيد الطرح',
                'label_ar': 'عطاءات قيد الطرح',
                'url': 'https://www.gtd.gov.jo/AR/modules/tendersunderoffering',
                'pagination_style': 'QUERY',
                'page_param': 'Page',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
    {
        'name':          'Jordan — JONEPS (Government E-Procurement)',
        'name_ar':       'الأردن — نظام جونبس للمشتريات الحكومية',
        'country':       'JO',
        'base_url':      'https://www.joneps.gov.jo',
        'scraper_class': 'JONEPSScraper',
        'description':   'Jordan Online E-Procurement System — all active tender invitations',
        'urls': [
            {
                'url':              'https://www.joneps.gov.jo/ep/invt/selectListTendInvitAL.do',
                'label':            'Jordan — JONEPS Tender Invitations',
                'label_ar':         'الأردن — دعوات العطاءات (جونبس)',
                'pagination_style': 'QUERY',
                'page_param':       'currentPageNo',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    {
        'name': 'Jordan — TenderJO (Consultancy & Studies)',
        'name_ar': 'الأردن — تندرجو (الدراسات والاستشارات)',
        'country': 'JO',
        'base_url': 'https://www.tenderjo.com',
        'scraper_class': 'TenderJOScraper',
        'description': 'Private/NGO Jordanian tenders portal — consultancy & studies category (API-based).',
        'description_ar': 'بوابة عطاءات خاصة ودولية للأردن — فئة الدراسات والاستشارات.',
        'urls': [],   # API-based scraper, no TenderingURL needed
    },
    {
        'name':          'Jordan — ASEZA (Aqaba Special Economic Zone Authority)',
        'name_ar':       'الأردن — سلطة منطقة العقبة الاقتصادية الخاصة',
        'country':       'JO',
        'base_url':      'https://aseza.jo',
        'scraper_class': 'ASEZAScraper',
        'description':   'ASEZA official tenders portal — engineering, consultancy and supplies.',
        'description_ar': 'بوابة العطاءات الرسمية لسلطة منطقة العقبة الاقتصادية الخاصة.',
        'urls': [
            {
                'label':            'ASEZA Tenders — العطاءات المطروحة',
                'label_ar':         'العطاءات المطروحة حاليا',
                'url':              'https://aseza.jo/AR/Modules/Tenders',
                'pagination_style': 'SINGLE',
                'page_param':       'page',
                'max_pages':        1,
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'UAE -- ADFD (Abu Dhabi Fund for Development)',
        'name_ar':       'الإمارات -- صندوق أبوظبي للتنمية',
        'country':       'AE',
        'base_url':      'https://www.adfd.ae',
        'scraper_class': 'ADFDScraper',
        'description':   'Abu Dhabi Fund for Development -- official tenders portal.',
        'description_ar': 'صندوق أبوظبي للتنمية -- بوابة العطاءات الرسمية.',
        'urls': [
            {
                'label':            'ADFD Tenders',
                'label_ar':         'عطاءات صندوق أبوظبي للتنمية',
                'url':              'https://www.adfd.ae/en/what-we-do/tenders',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'UAE Business Gate -- UAE Tenders & Business Gate',
        'name_ar':       'بوابة الأعمال الإماراتية -- منصة الإمارات للمناقصات',
        'country':       'AE',
        'base_url':      'https://uaebusinessgate.ae',
        'scraper_class': 'UAEBusinessGateScraper',
        'description':   'UAE Business Gate -- government, semi-govt, and private sector tenders from the UAE. Uses Playwright.',
        'description_ar': 'منصة الإمارات للمناقصات والأعمال -- مناقصات حكومية وخاصة.',
        'urls': [
            {
                'label':            'Tenders Listing',
                'label_ar':         'قائمة المناقصات',
                'url':              'https://uaebusinessgate.ae/tenders/',
                'pagination_style': 'PAGE_N',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'BADEA -- Arab Bank for Economic Development in Africa',
        'name_ar':       'بادية -- البنك العربي للتنمية الاقتصادية في أفريقيا',
        'country':       'BA',
        'base_url':      'https://www.badea.org',
        'scraper_class': 'BADEAScraper',
        'description':   'BADEA Procurement Notices -- Arab-African development bank. WordPress/Elementor.',
        'description_ar': 'إشعارات المشتريات لبنك بادية.',
        'urls': [
            {
                'label':            'Procurement Notices',
                'label_ar':         'إشعارات المشتريات',
                'url':              'https://www.badea.org/procurement-notices-2/',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'Asian Development Bank (ADB) -- Projects & Tenders',
        'name_ar':       'بنك التنمية الآسيوي -- المشاريع والمناقصات',
        'country':       'AD',
        'base_url':      'https://www.adb.org',
        'scraper_class': 'ADBScraper',
        'description':   'ADB projects for 50+ Asia-Pacific DMCs. First 10 pages. Requires Playwright.',
        'description_ar': 'مشاريع البنك الآسيوي للتنمية لأكثر من 50 دولة. أول 10 صفحات.',
        'urls': [
            {
                'label':            'Projects & Tenders',
                'label_ar':         'المشاريع والمناقصات',
                'url':              'https://www.adb.org/projects',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'Qatar -- Ashghal (Public Works Authority e-Tenders)',
        'name_ar':       'قطر -- أشغال (مناقصات هيئة الأشغال العامة)',
        'country':       'QA',
        'base_url':      'https://www.ashghal.gov.qa',
        'scraper_class': 'AshghalScraper',
        'description':   'Qatar Public Works Authority (Ashghal) -- e-Tenders (GTC/STC). First 10 page fetches.',
        'description_ar': 'هيئة الأشغال العامة القطرية -- المناقصات الإلكترونية.',
        'urls': [
            {
                'label':            'e-Tenders (Open)',
                'label_ar':         'المناقصات الإلكترونية (المفتوحة)',
                'url':              'https://www.ashghal.gov.qa/en/tenders/pages/erptenders.aspx',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'Bahrain -- Tender Board (Published Tenders)',
        'name_ar':       'البحرين -- لجنة المناقصات (المناقصات المنشورة)',
        'country':       'BH',
        'base_url':      'https://www.tenderboard.gov.bh',
        'scraper_class': 'BahrainTBScraper',
        'description':   'Bahrain Tender Board -- all government published tenders.',
        'description_ar': 'لجنة المناقصات البحرينية -- جميع المناقصات الحكومية المنشورة.',
        'urls': [
            {
                'label':            'Published Tenders',
                'label_ar':         'المناقصات المنشورة',
                'url':              'https://www.tenderboard.gov.bh/Tenders/PublicTenders/',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'Kenya -- KeNHA (Consultancy Tenders)',
        'name_ar':       'كينيا -- هيئة طرق كينيا الوطنية (عطاءات الاستشارات)',
        'country':       'KE',
        'base_url':      'https://kenha.co.ke',
        'scraper_class': 'KeNHAScraper',
        'description':   'Kenya National Highways Authority -- Consultancy Services tenders.',
        'description_ar': 'هيئة طرق كينيا الوطنية -- عطاءات خدمات الاستشارات.',
        'urls': [
            {
                'label':            'KeNHA Consultancy Tenders',
                'label_ar':         'عطاءات الاستشارات',
                'url':              'https://kenha.co.ke/tenders-consultancy-services/',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'Uganda — EGPU (Electronic Government Procurement)',
        'name_ar':       'أوغندا — بوابة المشتريات الحكومية الإلكترونية',
        'country':       'UG',
        'base_url':      'https://egpuganda.go.ug',
        'scraper_class': 'EGPUUgandaScraper',
        'description':   'Uganda EGPU — all government bid notices and procurement opportunities.',
        'description_ar': 'بوابة المشتريات الحكومية الإلكترونية في أوغندا — جميع إشعارات العطاءات الحكومية.',
        'urls': [
            {
                'label':            'EGPU Bid Notices',
                'label_ar':         'إشعارات العطاءات',
                'url':              'https://egpuganda.go.ug/bid-notices',
                'pagination_style': 'PAGE_PARAM',
                'sort_order':       1,
            },
        ],
    },
    {
        'name': 'Iraq — ITP.IQ (Unified Tenders Window)',
        'name_ar': 'العراق — نافذة العراق الموحدة للإعلانات والمناقصات',
        'country': 'IQ',
        'base_url': 'https://itp.iq',
        'scraper_class': 'ITPIQScraper',
        'description': 'Iraq Ministry of Planning — unified portal for all government tenders.',
        'description_ar': 'وزارة التخطيط العراقية — نافذة المناقصات الحكومية الموحدة.',
        'urls': [
            {
                'label': 'All Tenders — JSON API',
                'label_ar': 'جميع المناقصات',
                'url': 'https://itp.iq/BUSINESS/tenders',
                'pagination_style': 'QUERY',
                'page_param': 'page',
                'max_pages': 10,
                'extra_params': {'count': 10},
                'sort_order': 1,
            },
        ],
    },
    {
        'name':          'Iraq — IQTenders (All Categories — Today)',
        'name_ar':       'العراق — آي كيو تندرز (جميع المجالات — اليوم)',
        'country':       'IQ',
        'base_url':      'https://www.iqtenders.com',
        'scraper_class': 'IQTendersScraper',
        'description': (
            'Iraq tenders portal iqtenders.com — fetches ALL categories published TODAY '
            'via the internal JSON API (/api/HomePage/GetValue). '
            'Covers 27 categories including construction, consultancy, IT, health, '
            'supplies, logistics and more from 2,078 Iraqi publishers.'
        ),
        'description_ar': (
            'بوابة مناقصات العراق — تجلب جميع المناقصات المنشورة اليوم عبر واجهة JSON '
            'وتشمل 27 مجالاً من أكثر من 2,078 جهة ناشرة.'
        ),
        'urls': [
            {
                'url':              'https://www.iqtenders.com/TendersSearch.aspx?country=iq&lang=ar',
                'label':            'IQTenders — All Categories Today',
                'label_ar':         'آي كيو تندرز — جميع المجالات اليوم',
                'pagination_style': 'QUERY',
                'page_param':       'page',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'UAE — TenderUAE (Consultancy & Studies)',
        'name_ar':       'الإمارات — تندر الإمارات (الدراسات والاستشارات)',
        'country':       'AE',
        'base_url':      'https://www.tenderuae.com',
        'scraper_class': 'TenderUAEScraper',
        'description':   'UAE tenders via TenderUAE — consultancy & studies (CategoryID=35)',
        'urls': [
            {
                'url':              'https://www.tenderuae.com/TendersSearch.aspx?CategoryID=35&lang=ar',
                'label':            'UAE — Consultancy & Studies',
                'label_ar':         'الإمارات — الدراسات والاستشارات',
                'pagination_style': 'QUERY',
                'page_param':       'page',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    {
        'name': 'Saudi Arabia — TenderSA (Consultancy & Studies)',
        'name_ar': 'السعودية — تندر السعودية (الدراسات والاستشارات)',
        'country': 'SA',
        'base_url': 'https://www.tendersa.com',
        'scraper_class': 'TenderSAScraper',
        'description': 'Saudi Arabia tenders portal — consultancy & studies (API-based).',
        'description_ar': 'بوابة مناقصات المملكة العربية السعودية — فئة الدراسات والاستشارات.',
        'urls': [],
    },
    {
        'name': 'Syria — SyTenders (All Categories)',
        'name_ar': 'سوريا — تندر سوريا (جميع الفئات)',
        'country': 'SY',
        'base_url': 'https://www.sytenders.com',
        'scraper_class': 'SyTendersScraper',
        'description': 'Syria tenders portal — all categories, relevance-filtered (API-based).',
        'description_ar': 'بوابة مناقصات سوريا — جميع الفئات مع تصفية حسب الأهمية.',
        'urls': [],
    },
    {
        'name': 'Syria — Elan (Official Government Tenders)',
        'name_ar': 'سوريا — إعلان (بوابة العطاءات الحكومية الرسمية)',
        'country': 'SY',
        'base_url': 'http://elan.gov.sy',
        'scraper_class': 'ElanSYScraper',
        'description': 'Official Syrian government tenders portal — elan.gov.sy. Covers all ministries and public entities.',
        'description_ar': 'البوابة الرسمية للعطاءات الحكومية السورية — تشمل جميع الوزارات والجهات العامة.',
        'urls': [
            {
                'url':              'http://elan.gov.sy/2017/site/arabic/index.php?node=343',
                'label':            'Elan Syria — All Tenders',
                'label_ar':         'إعلان سوريا — جميع العطاءات',
                'pagination_style': 'SINGLE',
                'page_param':       'CurrentPage',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    {
        'name': 'Saudi Arabia — Etimad / Monafasat',
        'name_ar': 'السعودية — منصة اعتماد للمنافسات',
        'country': 'SA',
        'base_url': 'https://tenders.etimad.sa',
        'scraper_class': 'SaudiEtimadScraper',
        'description': 'Main Saudi government procurement platform used by all ministries.',
        'description_ar': 'المنصة الرئيسية للمشتريات الحكومية السعودية.',
        'urls': [
            {
                'label': 'Open Tenders — JSON API',
                'label_ar': 'المناقصات المفتوحة — API',
                'url': 'https://tenders.etimad.sa/Tender/AllTendersForVisitor',
                'pagination_style': 'API',
                'page_param': 'PageIndex',
                'max_pages': 10,
                'extra_params': {'TenderStatusId': 1},
                'sort_order': 1,
            },
        ],
    },
    {
        'name': 'Saudi Arabia — Etimad Qualifications',
        'name_ar': 'السعودية — اعتماد: التأهيل المسبق',
        'country': 'SA',
        'base_url': 'https://tenders.etimad.sa',
        'scraper_class': 'SaudiEtimadQualScraper',
        'description': 'Pre-qualification and qualification notices on the Etimad platform.',
        'description_ar': 'إشعارات التأهيل المسبق على منصة اعتماد.',
        'urls': [
            {
                'label': 'Qualifications for Visitor',
                'label_ar': 'التأهيل المسبق للزوار',
                'url': 'https://tenders.etimad.sa/Qualification/AllPreQualificationsAsync',
                'pagination_style': 'API',
                'page_param': 'PageNumber',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
    {
        'name': 'Saudi Arabia — Etimad Supplier Templates (قوائم الموردين)',
        'name_ar': 'السعودية — اعتماد: إعلانات قوائم الموردين',
        'country': 'SA',
        'base_url': 'https://tenders.etimad.sa',
        'scraper_class': 'SaudiEtimadSupplierTemplatesScraper',
        'description': (
            'Supplier list announcements (Approved Vendor Lists) published by Saudi '
            'government agencies inviting suppliers to register for specific activity sectors.'
        ),
        'description_ar': (
            'إعلانات قوائم الموردين المعتمدين التي تنشرها الجهات الحكومية السعودية '
            'لدعوة الموردين للتسجيل في قوائم الموردين المعتمدين لقطاعات نشاط محددة.'
        ),
        'urls': [
            {
                'label': 'Supplier Templates — JSON API',
                'label_ar': 'قوائم الموردين — API',
                'url': (
                    'https://tenders.etimad.sa/AnnouncementSuppliersTemplate'
                    '/GetAllAnnouncementSupplierTemplatesForSupplier'
                ),
                'pagination_style': 'API',
                'page_param': 'PageNumber',
                'max_pages': 30,
                'sort_order': 1,
            },
        ],
    },
    {
        'name': 'Saudi Arabia — Etimad Pre-Planning (مشاريع المستقبلية)',
        'name_ar': 'السعودية — اعتماد: مشاريع المنافسات المستقبلية',
        'country': 'SA',
        'base_url': 'https://tenders.etimad.sa',
        'scraper_class': 'SaudiEtimadPrePlanningScraper',
        'description': (
            'Future procurement plans (Pre-Planning) published by Saudi government '
            'agencies on the Etimad platform, indicating upcoming projects before '
            'formal tendering begins.'
        ),
        'description_ar': (
            'مشاريع المنافسات المستقبلية التي تنشرها الجهات الحكومية السعودية '
            'على منصة اعتماد قبل بدء الطرح الرسمي للمناقصات.'
        ),
        'urls': [
            {
                'label': 'Pre-Planning — JSON API',
                'label_ar': 'مشاريع مستقبلية — API',
                'url': (
                    'https://tenders.etimad.sa/PrePlanning'
                    '/PreplaningPagingForVisitorAsync'
                ),
                'pagination_style': 'API',
                'page_param': 'PageNumber',
                'max_pages': 50,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'TenderWell — MENA & Africa Tenders',
        'name_ar': 'تيندرويل — مناقصات منطقة الشرق الأوسط وأفريقيا',
        'country': 'ZZ',
        'base_url': 'https://tenderwell.com',
        'scraper_class': 'TenderWellScraper',
        'description': (
            'Jordan tenders aggregated from TenderWell global tender platform '
            '(37M+ tenders, 190+ countries). Filtered by placeOfOrigin=JO and '
            'status=OPEN. Non-subscription fields only: title, deadline, type, procedure.'
        ),
        'description_ar': (
            'مناقصات الأردن المجمّعة من منصة TenderWell العالمية '
            '(أكثر من 37 مليون مناقصة، 190+ دولة). '
            'مصفّاة حسب الأردن والحالة المفتوحة فقط.'
        ),
        'urls': [
            {
                'label': 'TenderWell Search API — MENA & Africa Open Tenders',
                'label_ar': 'بحث TenderWell — مناقصات الشرق الأوسط وأفريقيا المفتوحة',
                'url': 'https://tenderwell.com/api/frontend/tenders/search',
                'pagination_style': 'API',
                'page_param': 'page',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'DTVP — GIZ International Tenders',
        'name_ar': 'DTVP — مناقصات GIZ الدولية عبر البوابة الألمانية',
        'country': 'ZZ',
        'base_url': 'https://www.dtvp.de',
        'scraper_class': 'DTVPGIZScraper',
        'is_active': False,  # WAF blocks API POST — GIZTendersScraper covers same tenders
        'description': (
            'GIZ international development cooperation tenders published on DTVP '
            '(Deutsches Vergabeportal), Germany central e-procurement portal. '
            'Filtered to GIZ authority only. Country detected from tender title. '
            'Complements the GIZ Vergabemarktplatz scraper (some notices appear on both).'
        ),
        'description_ar': (
            'مناقصات التعاون الدولي الصادرة عن GIZ عبر البوابة الألمانية المركزية للمشتريات DTVP. '
            'مصفّاة لعروض GIZ فقط، مع تحديد الدولة من عنوان المناقصة تلقائياً.'
        ),
        'urls': [
            {
                'label': 'DTVP — Latest GIZ International Notices',
                'label_ar': 'DTVP — آخر إعلانات GIZ الدولية',
                'url': 'https://www.dtvp.de/Center/api/v2/project/search',
                'pagination_style': 'GET',
                'page_param': 'selectedTablePagePROJECT_RESULT',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'GIZ — Vergabemarktplatz (GIZ Procurement Portal)',
        'name_ar': 'GIZ — بوابة المشتريات الألمانية للتعاون الدولي',
        'country': 'DE',
        'base_url': 'https://ausschreibungen.giz.de',
        'scraper_class': 'GIZTendersScraper',
        'description': (
            'International procurement notices published by GIZ (Deutsche Gesellschaft '
            'für Internationale Zusammenarbeit), the German development cooperation agency. '
            'Covers consulting, services, and supply contracts for GIZ projects worldwide '
            'including MENA, Africa, Asia, and Europe. ~113 notices across 6 pages. '
            'Award/modification notices are automatically filtered out.'
        ),
        'description_ar': (
            'إشعارات المشتريات الدولية الصادرة عن GIZ، وكالة التعاون الدولي الألمانية. '
            'تشمل عقود الاستشارات والخدمات والتوريد لمشاريع GIZ حول العالم '
            'بما في ذلك منطقة الشرق الأوسط وأفريقيا وآسيا وأوروبا.'
        ),
        'urls': [
            {
                'label': 'GIZ Vergabemarktplatz — Latest Notices',
                'label_ar': 'بوابة GIZ للمشتريات — آخر الإعلانات',
                'url': 'https://ausschreibungen.giz.de/Satellite/company/welcome.do',
                'pagination_style': 'GET',
                'page_param': 'selectedTablePagePROJECT_RESULT',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'EIB — European Investment Bank Procurement',
        'name_ar': 'البنك الأوروبي للاستثمار — المشتريات',
        'country': 'ZZ',
        'base_url': 'https://www.eib.org',
        'scraper_class': 'EIBProcurementScraper',
        'description': (
            'Procurement notices published by the European Investment Bank (EIB), '
            'covering calls for tenders, technical assistance, project procurement '
            'for EIB-financed projects worldwide including MENA, Africa, and Asia. '
            'Uses AJAX JSON API. TODO: confirm API_URL from browser Network tab.'
        ),
        'description_ar': (
            'إعلانات المشتريات الصادرة عن البنك الأوروبي للاستثمار، '
            'وتشمل طلبات العطاءات والمساعدة الفنية ومشتريات المشاريع '
            'الممولة من البنك حول العالم بما في ذلك منطقة الشرق الأوسط وأفريقيا.'
        ),
        'urls': [
            {
                'label': 'EIB — All Procurement (ongoing)',
                'label_ar': 'البنك الأوروبي للاستثمار — جميع المشتريات الجارية',
                'url': 'https://www.eib.org/provider-eib/app/list/medias/procurements',
                'pagination_style': 'API',
                'page_param': 'pageNumber',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'DGMarket — Global Procurement Notices (MENA)',
        'name_ar': 'DGMarket — إعلانات المشتريات العالمية (الشرق الأوسط وشمال أفريقيا)',
        'country': 'ZZ',
        'base_url': 'https://www2.dgmarket.com',
        'scraper_class': 'DGMarketScraper',
        'description': (
            'DGMarket aggregates procurement notices from governments, international '
            'organizations, and development banks (World Bank, UNDP, AfDB, IsDB, GIZ, EU). '
            'Filtered to MENA and extended region countries: Jordan, Saudi Arabia, UAE, Iraq, '
            'Syria, Egypt, Morocco, Tunisia, Algeria, Libya, Lebanon, Kuwait, Bahrain, Oman, '
            'Yemen, Palestine, Turkey. Server-rendered HTML with page=N pagination.'
        ),
        'description_ar': (
            'يجمع DGMarket إعلانات المشتريات من الحكومات والمنظمات الدولية وبنوك التنمية '
            '(البنك الدولي، UNDP، AfDB، IsDB، GIZ). '
            'مفلتر لدول الشرق الأوسط وشمال أفريقيا: الأردن، السعودية، الإمارات، العراق، وسوريا وغيرها.'
        ),
        'urls': [
            {
                'label': 'DGMarket — MENA Region Notices',
                'label_ar': 'DGMarket — إعلانات منطقة الشرق الأوسط وشمال أفريقيا',
                'url': 'https://www2.dgmarket.com/NoticeList',
                'pagination_style': 'PAGE_PARAM',
                'page_param': 'page',
                'page_size': 25,
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'UNGM — United Nations Global Marketplace (MENA)',
        'name_ar': 'UNGM — السوق العالمي للأمم المتحدة (الشرق الأوسط وشمال أفريقيا)',
        'country': 'ZZ',
        'base_url': 'https://www.ungm.org',
        'scraper_class': 'UNGMScraper',
        'description': (
            'Procurement notices from the UN Global Marketplace (UNGM), aggregating '
            'tenders from 40+ UN agencies: UNDP, UNICEF, WFP, WHO, UNIDO, UNHCR, FAO, '
            'ILO, UNOPS, IAEA, IOM, ITC and more. '
            'Filtered to MENA countries: Jordan, Saudi Arabia, UAE, Iraq, Syria, Egypt, '
            'Morocco, Tunisia, Libya, Lebanon, Kuwait, Bahrain, Oman, Yemen, Palestine. '
            'Uses POST JSON API with HTML response. Requires corporate proxy (172.10.0.90:3128).'
        ),
        'description_ar': (
            'إعلانات المشتريات من السوق العالمي للأمم المتحدة (UNGM)، '
            'يجمع مناقصات من أكثر من 40 وكالة أممية. '
            'مفلتر لدول الشرق الأوسط وشمال أفريقيا.'
        ),
        'urls': [
            {
                'label': 'UNGM — MENA Procurement Opportunities',
                'label_ar': 'UNGM — فرص المشتريات في الشرق الأوسط وشمال أفريقيا',
                'url': 'https://www.ungm.org/Public/Notice/Search',
                'pagination_style': 'POST_JSON',
                'page_param': 'PageIndex',
                'page_size': 25,
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'UNIDO — United Nations Industrial Development Organization',
        'name_ar': 'اليونيدو — منظمة الأمم المتحدة للتنمية الصناعية',
        'country': 'ZZ',
        'base_url': 'https://www.unido.org',
        'scraper_class': 'UNIDOProcurementScraper',
        'description': (
            'Procurement opportunities published by UNIDO (United Nations Industrial '
            'Development Organization), covering goods, services, and works for '
            'industrial development projects globally. Includes engineering assessments, '
            'equipment supply, capacity building, consultancy, and construction services '
            'in MENA, Africa, and Asia. Single page, no pagination (Drupal 10).'
        ),
        'description_ar': (
            'فرص المشتريات الصادرة عن منظمة الأمم المتحدة للتنمية الصناعية (يونيدو) '
            'وتشمل السلع والخدمات والأشغال المتعلقة بمشاريع التنمية الصناعية عالمياً '
            'في منطقة الشرق الأوسط وأفريقيا وآسيا.'
        ),
        'urls': [
            {
                'label': 'UNIDO — Procurement Opportunities',
                'label_ar': 'اليونيدو — فرص المشتريات',
                'url': 'https://www.unido.org/get-involved/procurement/procurement-opportunities',
                'pagination_style': 'NONE',
                'max_pages': 1,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'MOLA — Jordan Ministry of Local Administration Tenders',
        'name_ar': 'وزارة الإدارة المحلية الأردنية — العطاءات',
        'country': 'JO',
        'base_url': 'https://www.mola.gov.jo',
        'scraper_class': 'MOLATendersScraper',
        'description': (
            'Tenders published by Jordan\'s Ministry of Local Administration (MOLA), '
            'covering municipal infrastructure, solid waste management, donor-funded '
            'programs (EU/AFD/World Bank co-financed), and engineering supervision '
            'for transfer stations and municipal facilities across Jordan. '
            'ASP.NET WebForms with DataPager pagination (Arabic content).'
        ),
        'description_ar': (
            'مناقصات وزارة الإدارة المحلية الأردنية، تشمل البنية التحتية البلدية '
            'وإدارة النفايات الصلبة والبرامج الممولة من جهات مانحة دولية '
            'والإشراف الهندسي على محطات النقل والمرافق البلدية في الأردن.'
        ),
        'urls': [
            {
                'label': 'MOLA — Current Tenders',
                'label_ar': 'وزارة الإدارة المحلية — العطاءات المطروحة حالياً',
                'url': 'https://www.mola.gov.jo/Ar/Modules/Tenders',
                'pagination_style': 'ASPNET_DATAPAGER',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'MEMR — Jordan Ministry of Energy and Mineral Resources Tenders',
        'name_ar': 'وزارة الطاقة والثروة المعدنية الأردنية — العطاءات',
        'country': 'JO',
        'base_url': 'https://www.memr.gov.jo',
        'scraper_class': 'MEMRTendersScraper',
        'description': (
            'Tenders published by Jordan\'s Ministry of Energy and Mineral Resources '
            '(MEMR), covering power/energy infrastructure works, technical/engineering '
            'services, renewable energy projects, and mining sector contracts. '
            'Same ASP.NET platform/template as MOLA — DataPager pagination, '
            'mixed Arabic/English content (international tenders often in English).'
        ),
        'description_ar': (
            'مناقصات وزارة الطاقة والثروة المعدنية الأردنية، تشمل أعمال البنية '
            'التحتية للطاقة والخدمات الفنية والهندسية ومشاريع الطاقة المتجددة '
            'وعقود قطاع التعدين.'
        ),
        'urls': [
            {
                'label': 'MEMR — Current Tenders',
                'label_ar': 'وزارة الطاقة والثروة المعدنية — العطاءات',
                'url': 'https://www.memr.gov.jo/ar/modules/tenders',
                'pagination_style': 'ASPNET_DATAPAGER',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'PDTRA — Petra Development and Tourism Region Authority Tenders',
        'name_ar': 'سلطة إقليم البترا التنموي السياحي — العطاءات',
        'country': 'JO',
        'base_url': 'https://pdtra.gov.jo',
        'scraper_class': 'PDTRATendersScraper',
        'description': (
            'Tenders published by the Petra Development and Tourism Region Authority '
            '(PDTRA), covering tourism infrastructure, transport systems, real estate '
            'development, urban/land planning, and construction projects in the Petra '
            'archaeological and tourism region. Same ASP.NET platform as MOLA/MEMR but '
            'NEWER template variant — note: dates are YYYY/MM/DD (not DD/MM/YYYY).'
        ),
        'description_ar': (
            'مناقصات سلطة إقليم البترا التنموي السياحي، تشمل البنية التحتية السياحية '
            'وأنظمة النقل والتطوير العقاري والتخطيط العمراني ومشاريع البناء '
            'في منطقة البترا الأثرية والسياحية.'
        ),
        'urls': [
            {
                'label': 'PDTRA — Current Tenders',
                'label_ar': 'سلطة إقليم البترا — العطاءات المطروحة حالياً',
                'url': 'https://pdtra.gov.jo/AR/Modules/Tenders',
                'pagination_style': 'ASPNET_DATAPAGER',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'JPMC — Jordan Phosphate Mines Company Tenders',
        'name_ar': 'شركة مناجم الفوسفات الأردنية — العطاءات',
        'country': 'JO',
        'base_url': 'https://www.jpmc.com.jo',
        'scraper_class': 'JPMCTendersScraper',
        'description': (
            'Tenders published by the Jordan Phosphate Mines Company (JPMC), '
            'covering mine engineering services, EPC/EPCM contracts for major '
            'industrial projects (incl. the JTFC 300,000 MTPY P2O5 fertilizer plant), '
            'civil/structural works, maintenance, warehouse construction, and housing '
            'rehabilitation at Al-Hassa, Al-Abyad, and Al-Shidiya mine sites. '
            'Yii2 framework, clean GET-based pagination.'
        ),
        'description_ar': (
            'مناقصات شركة مناجم الفوسفات الأردنية، تشمل خدمات الهندسة التعدينية '
            'وعقود EPC للمشاريع الصناعية الكبرى، والأعمال المدنية والإنشائية '
            'والصيانة وبناء المستودعات وإعادة تأهيل الإسكان في مناجم الحسا '
            'والأبيض والشيدية.'
        ),
        'urls': [
            {
                'label': 'JPMC — Currently Offered Tenders',
                'label_ar': 'شركة مناجم الفوسفات — العطاءات المطروحة حالياً',
                'url': 'https://www.jpmc.com.jo/en/tender',
                'pagination_style': 'PAGE_PARAM',
                'page_param': 'page',
                'max_pages': 15,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'APC — Arab Potash Company Tenders',
        'name_ar': 'شركة البوتاس العربية — العطاءات',
        'country': 'JO',
        'base_url': 'https://www.arabpotash.com',
        'scraper_class': 'APCTendersScraper',
        'description': (
            'Tenders published by the Arab Potash Company (APC), covering EPC '
            'contracts for bridges, warehouses, plant construction (CCP3), gas '
            'pipeline re-alignment, engineering consultancy/supervision services, '
            'and civil/structural works at Dead Sea mining and Numira/Aqaba '
            'facilities. Captures bid bond value and vendor qualifications. '
            'ASP.NET DataPager pagination, ~47 pages of active tenders.'
        ),
        'description_ar': (
            'مناقصات شركة البوتاس العربية، تشمل عقود EPC للجسور والمستودعات '
            'وإنشاء المصانع (CCP3) وإعادة محاذاة خطوط الغاز وخدمات الإشراف '
            'الهندسي والأعمال المدنية والإنشائية في مرافق التعدين على البحر الميت.'
        ),
        'urls': [
            {
                'label': 'APC — Active Tenders',
                'label_ar': 'شركة البوتاس العربية — العطاءات النشطة',
                'url': 'https://www.arabpotash.com/En/Modules/Tenders',
                'pagination_style': 'ASPNET_DATAPAGER',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'AFD DGMarket — Agence Française de Développement',
        'name_ar': 'AFD DGMarket — الوكالة الفرنسية للتنمية',
        'country': 'ZZ',
        'base_url': 'https://tenders-afd.dgmarket.com',
        'scraper_class': 'AFDDGMarketScraper',
        'description': (
            'Procurement notices for AFD (Agence Française de Développement)-funded '
            'projects worldwide, covering infrastructure, water, energy, urban '
            'development, and consultancy services across Africa, MENA, and Asia. '
            'Separate white-label DGMarket instance from the general dgmarket.com — '
            'simple GET pagination, no JS challenge. ~131 active notices.'
        ),
        'description_ar': (
            'إعلانات مشتريات المشاريع الممولة من الوكالة الفرنسية للتنمية (AFD) '
            'حول العالم، وتشمل البنية التحتية والمياه والطاقة والتنمية الحضرية '
            'والخدمات الاستشارية في أفريقيا والشرق الأوسط وآسيا.'
        ),
        'urls': [
            {
                'label': 'AFD — Tenders sponsored by AFD',
                'label_ar': 'AFD — المناقصات الممولة من الوكالة الفرنسية للتنمية',
                'url': 'https://tenders-afd.dgmarket.com/tenders/brandedNoticeList.do',
                'pagination_style': 'PAGE_PARAM',
                'page_param': 'd-446978-p',
                'page_size': 20,
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'ADC — Aqaba Development Corporation Tenders',
        'name_ar': 'شركة تطوير العقبة — المناقصات',
        'country': 'JO',
        'base_url': 'https://www.adc.jo',
        'scraper_class': 'ADCTendersScraper',
        'description': (
            'Tenders published by the Aqaba Development Corporation (ADC), '
            'the entity responsible for developing the Aqaba Special Economic Zone (ASEZ). '
            'Covers infrastructure, port facilities, construction, engineering supervision, '
            'border crossing development, marina/port projects, and services in Aqaba. '
            'ASP.NET WebForms with DataPager pagination (10 items/page, POST).'
        ),
        'description_ar': (
            'مناقصات شركة تطوير العقبة المسؤولة عن تطوير منطقة العقبة الاقتصادية الخاصة (آسيز). '
            'تشمل البنية التحتية ومنشآت الميناء والإنشاءات والإشراف الهندسي '
            'وتطوير المعابر الحدودية ومشاريع المرافئ والخدمات في العقبة.'
        ),
        'urls': [
            {
                'label': 'ADC — Tender Announcements',
                'label_ar': 'شركة تطوير العقبة — إعلانات المناقصات',
                'url': 'https://www.adc.jo/nwsgroup.aspx?group_key=tan',
                'pagination_style': 'ASPNET_DATAPAGER',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'NEPCO — National Electric Power Company Tenders',
        'name_ar': 'نبكو — مناقصات شركة الكهرباء الوطنية الأردنية',
        'country': 'JO',
        'base_url': 'https://www.nepco.com.jo',
        'scraper_class': 'NEPCOTendersScraper',
        'description': (
            'Tenders published by NEPCO (National Electric Power Company), '
            'owner and operator of Jordan electric transmission grid. '
            'Covers substations, transmission lines, electrical equipment, '
            'fault recorders, insulators, and engineering/civil works. '
            'Single page, all active tenders shown, no pagination needed.'
        ),
        'description_ar': (
            'مناقصات شركة الكهرباء الوطنية الأردنية (نبكو)، '
            'مالكة ومشغّلة شبكة نقل الكهرباء في المملكة الأردنية الهاشمية. '
            'تشمل محطات التحويل وخطوط النقل والمعدات الكهربائية والأعمال المدنية.'
        ),
        'urls': [
            {
                'label': 'NEPCO — Active Tenders',
                'label_ar': 'نبكو — المناقصات الفعّالة',
                'url': 'https://www.nepco.com.jo/en/Tenders.aspx',
                'pagination_style': 'NONE',
                'max_pages': 1,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'GAM — Greater Amman Municipality Tenders',
        'name_ar': 'أمانة عمّان الكبرى — المناقصات',
        'country': 'JO',
        'base_url': 'https://www.gamtenders.gov.jo',
        'scraper_class': 'GAMTendersScraper',
        'description': (
            'Local tenders published by the Greater Amman Municipality (GAM) '
            'covering infrastructure, supply, services, and construction works '
            'across Amman. Scraped from ASP.NET WebForms HTML table with '
            'ViewState-based pagination.'
        ),
        'description_ar': (
            'المناقصات المحلية الصادرة عن أمانة عمّان الكبرى، وتشمل '
            'مشاريع البنية التحتية والتوريد والخدمات والأعمال الإنشائية '
            'في جميع أنحاء عمّان.'
        ),
        'urls': [
            {
                'label': 'GAM — Local Tenders Announcements',
                'label_ar': 'أمانة عمّان — الاعلان عن المناقصات المحلية',
                'url': 'https://www.gamtenders.gov.jo/adlocaltenders.aspx',
                'pagination_style': 'VIEWSTATE',
                'page_param': 'GVtenders',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'TendersInfo — Jordan Projects & Tenders',
        'name_ar': 'تيندرزإنفو — مشاريع ومناقصات الأردن',
        'country': 'JO',
        'base_url': 'https://www.tendersinfo.com',
        'scraper_class': 'TendersInfoJordanScraper',
        'description': (
            'Jordan government and private tender notices aggregated by TendersInfo '
            '(Euclid Infotech Ltd). Covers 460+ active Jordan projects across all '
            'sectors. Data extracted from JSON-LD structured data embedded in page HTML.'
        ),
        'description_ar': (
            'إشعارات المناقصات الحكومية والخاصة في الأردن المجمّعة من منصة '
            'TendersInfo. تشمل أكثر من 460 مشروعاً أردنياً نشطاً عبر جميع القطاعات.'
        ),
        'urls': [
            {
                'label': 'TendersInfo Jordan Projects',
                'label_ar': 'مشاريع الأردن — TendersInfo',
                'url': 'https://www.tendersinfo.com/esearch/tender_sector_test',
                'pagination_style': 'PAGE',
                'page_param': 'page',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'MCA-Jordan — Tenders Announcements',
        'name_ar': 'حساب تحدي الألفية الأردن — إعلانات المناقصات',
        'country': 'JO',
        'base_url': 'http://www.mca-jordan.gov.jo',
        'scraper_class': 'MCAJordanScraper',
        'description': (
            'Tenders published by MCA-Jordan (Millennium Challenge Account - Jordan), '
            'the Jordanian entity managing the $275M US-funded Jordan Compact for '
            'water and wastewater infrastructure. Scrapes static HTML table from '
            'ASP.NET WebForms page. Compact concluded 2017; page retained for archive.'
        ),
        'description_ar': (
            'المناقصات الصادرة عن حساب تحدي الألفية الأردن، الجهة الأردنية '
            'المسؤولة عن تنفيذ اتفاقية الألفية الأمريكية البالغة 275 مليون دولار '
            'لمشاريع المياه والصرف الصحي. اكتملت الاتفاقية عام 2017.'
        ),
        'urls': [
            {
                'label': 'MCA-Jordan Tenders Announcements',
                'label_ar': 'إعلانات مناقصات حساب تحدي الألفية الأردن',
                'url': 'http://www.mca-jordan.gov.jo/page/Default.aspx?page=Tenders_Announcements',
                'pagination_style': 'NONE',
                'page_param': '',
                'max_pages': 1,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'UNDP Jordan — Procurement Notices',
        'name_ar': 'برنامج الأمم المتحدة الإنمائي الأردن — إشعارات المشتريات',
        'country': 'JO',
        'base_url': 'https://www.undp.org/jordan/procurement',
        'scraper_class': 'UNDPJordanScraper',
        'description': (
            'Procurement notices published by UNDP Jordan via the UNDP public '
            'components system (public-components.undp.org). Returns all active '
            'Jordan tenders in a single JS response with embedded JSON.'
        ),
        'description_ar': (
            'إشعارات المشتريات الصادرة عن برنامج الأمم المتحدة الإنمائي في '
            'الأردن عبر نظام المكونات العامة. تعيد جميع العطاءات النشطة '
            'في الأردن في استجابة واحدة.'
        ),
        'urls': [
            {
                'label': 'UNDP Jordan Procurement Notices (JS Component)',
                'label_ar': 'إشعارات مشتريات UNDP الأردن',
                'url': 'https://public-components.undp.org/?comp=proc_notices&cty_id_c=JOR&style_type=table',
                'pagination_style': 'NONE',
                'page_param': '',
                'max_pages': 1,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'EU Funding & Tenders Portal — Open CFTs',
        'name_ar': 'بوابة التمويل والعطاءات الأوروبية — المناقصات المفتوحة',
        'country': 'EU',
        'base_url': 'https://ec.europa.eu/info/funding-tenders/opportunities/portal',
        'scraper_class': 'ECFundingTendersScraper',
        'description': (
            'Open Calls for Tenders published on the EU Funding and Tenders Portal '
            '(European Commission SEDIA system). Covers procurement contracts across '
            'all EU institutions and agencies, filtered to Open status.'
        ),
        'description_ar': (
            'طلبات العروض المفتوحة المنشورة على بوابة التمويل والعطاءات التابعة '
            'للمفوضية الأوروبية (نظام SEDIA). تشمل عقود المشتريات عبر جميع '
            'المؤسسات والوكالات الأوروبية، مصفاة لحالة الفتح فقط.'
        ),
        'urls': [
            {
                'label': 'EC Funding Portal — Calls for Tenders API',
                'label_ar': 'بوابة الاتحاد الأوروبي — API المناقصات',
                'url': 'https://api.tech.ec.europa.eu/search-api/prod/rest/search',
                'pagination_style': 'API',
                'page_param': 'pageNumber',
                'max_pages': 30,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'AfDB — Consultants / EOI / AMI',
        'name_ar': 'بنك التنمية الأفريقي — الاستشاريون / طلبات التعبير عن الاهتمام',
        'country': 'ZZ',
        'base_url': 'https://www.afdb.org',
        'scraper_class': 'AfDBConsultantsScraper',
        'description': (
            'AfDB Consultants vacancy listings: Expressions of Interest (EOI) and '
            "Appels à Manifestation d'Intérêt (AMI) for Individual and Firm "
            'Consultants hired directly by the African Development Bank Group.'
        ),
        'description_ar': (
            'قوائم وظائف المستشارين في البنك الأفريقي للتنمية: طلبات التعبير عن '
            'الاهتمام (EOI/AMI) للمستشارين الأفراد والشركات الاستشارية التي يوظفها '
            'البنك مباشرةً.'
        ),
        'urls': [
            {
                'label': 'AfDB Consultants Vacancies',
                'label_ar': 'وظائف الاستشاريين — بنك التنمية الأفريقي',
                'url': 'https://www.afdb.org/en/about-us/careers/current-vacancies/consultants',
                'pagination_style': 'PAGE',
                'page_param': 'page',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'BADEA — Arab Bank Procurement Notices',
        'name_ar': 'البنك العربي للتنمية الاقتصادية في أفريقيا — إشعارات المشتريات',
        'country': 'MULTI',
        'base_url': 'https://www.badea.org',
        'scraper_class': 'BADEAScraper',
        'description': (
            'Procurement notices published by BADEA (Arab Bank for Economic '
            'Development in Africa) for consulting, construction, and supply '
            'contracts in African development projects financed by BADEA loans.'
        ),
        'description_ar': (
            'إشعارات المشتريات الصادرة عن البنك العربي للتنمية الاقتصادية '
            'في أفريقيا (بادية) لعقود الاستشارات والبناء والتوريد في مشاريع '
            'التنمية الأفريقية الممولة من قروض البنك.'
        ),
        'urls': [
            {
                'label': 'BADEA Procurement Notices',
                'label_ar': 'إشعارات مشتريات البنك العربي',
                'url': 'https://www.badea.org/procurement-notices-2/',
                'pagination_style': 'PAGE',
                'page_param': 'page',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'Germany (GTAI) — KfW Jordan Tenders',
        'name_ar': 'ألمانيا (GTAI) — مناقصات الأردن الممولة من KfW',
        'country': 'JO',
        'base_url': 'https://www.gtai.de',
        'scraper_class': 'GTAIKfwJordanScraper',
        'description': (
            'Tender notices published by GTAI (Germany Trade & Invest) for '
            'KfW-financed development projects in Jordan. Covers consulting, '
            'construction, water, energy, education and vocational training sectors.'
        ),
        'description_ar': (
            'إشعارات المناقصات التي تنشرها GTAI لمشاريع التنمية الممولة من '
            'بنك KfW الألماني في الأردن، تشمل الاستشارات والبناء والمياه '
            'والطاقة والتعليم والتدريب المهني.'
        ),
        'urls': [
            {
                'label': 'GTAI KfW Jordan Tenders Search',
                'label_ar': 'بحث مناقصات GTAI/KfW الأردن',
                'url': (
                    'https://www.gtai.de/en/meta/search/66080!search;'
                    'eNqNksFOwzAMht_F51YaaKu2PgAvADfEwU3dkSl1OjuBddPenXQT'
                    'HAoVuUXW_9n-f-cCHRoKCvUFmqiWSbVEIZwKSo5MoBbqVwiCLcFb'
                    'AXQyLhXSy_ABakAe4VqAxEasmVO7XVU9_kFxdC5BxkcOMs6pzWqz'
                    'XYY06bzoDFqUO_zMlepAxqLLlZPx7PtfppfdRg2-z168s4xsSObpr'
                    'Nfbajmd4R2Vsjfy_ZDu95TO0GKwnrOtfxCHl3HInqQklrKta2wOS'
                    'ZSdlJcew__d7391Cgn39GzPafuHVQHHe6M01kuaCWVLHUYXSiggx'
                    'XLzSNx-qwR5Tz9IwIm5LXL9AljFGBY'
                ),
                'pagination_style': 'PAGE',
                'page_param': 'page',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'Saudi Arabia — NCP Pipeline Projects',
        'name_ar': 'السعودية — مشاريع التخصيص والشراكة (المركز الوطني)',
        'country': 'SA',
        'base_url': 'https://investors.ncp.gov.sa',
        'scraper_class': 'NCPSaudiScraper',
        'description': (
            'PPP and privatisation pipeline projects published by the Saudi '
            'National Centre for Privatization (NCP). Includes Live Transactions '
            '(active procurement) and Upcoming Opportunities. Closed projects are skipped.'
        ),
        'description_ar': (
            'مشاريع خطوط الشراكة بين القطاعين العام والخاص والتخصيص التي ينشرها '
            'المركز الوطني للتخصيص والشراكة (NCP)، تشمل المعاملات الحية والفرص القادمة.'
        ),
        'urls': [
            {
                'label': 'NCP Pipeline Projects — POST API',
                'label_ar': 'مشاريع NCP — API',
                'url': 'https://investors.ncp.gov.sa/api/Project/GetPipelineProjects',
                'pagination_style': 'API',
                'page_param': 'pageNumber',
                'max_pages': 20,
                'sort_order': 1,
            },
        ],
    },
        {
        'name': 'UAE — Abu Dhabi Procurement (ADPS)',
        'name_ar': 'الإمارات — أبوظبي للمشتريات الحكومية',
        'country': 'AE',
        'base_url': 'https://www.procurement.gov.ae',
        'scraper_class': 'UAEAbuDhabiScraper',
        'description': 'Abu Dhabi government procurement and supply chain platform.',
        'description_ar': 'منصة أبوظبي لسلاسل الإمداد والمشتريات.',
        'urls': [
            {
                'label': 'Procurement Opportunities',
                'label_ar': 'فرص المشتريات',
                'url': 'https://www.procurement.gov.ae/en/opportunities',
                'pagination_style': 'QUERY',
                'page_param': 'page',
                'max_pages': 10,
                'sort_order': 1,
            },
            {
                'label': 'Construction & Infrastructure Tenders',
                'label_ar': 'مناقصات الإنشاء والبنية التحتية',
                'url': 'https://www.procurement.gov.ae/en/opportunities?category=construction',
                'pagination_style': 'QUERY',
                'page_param': 'page',
                'max_pages': 10,
                'sort_order': 2,
            },
        ],
    },
    {
        'name': 'UAE — Dubai Tenders Portal',
        'name_ar': 'الإمارات — بوابة مناقصات دبي',
        'country': 'AE',
        'base_url': 'https://tenders.dubai.gov.ae',
        'scraper_class': 'UAEDubaiTendersScraper',
        'description': 'Dubai government tenders managed by the Government of Dubai.',
        'description_ar': 'مناقصات حكومة دبي.',
        'urls': [
            {
                'label': 'Active Tenders',
                'label_ar': 'المناقصات الفعّالة',
                'url': 'https://tenders.dubai.gov.ae/en/tenders',
                'pagination_style': 'QUERY',
                'page_param': 'page',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
    {
        'name':          'Oman — Tender Board (eTendering)',
        'name_ar':       'عُمان — هيئة تنظيم المناقصات',
        'country':       'OM',
        'base_url':      'https://etendering.tenderboard.gov.om',
        'scraper_class': 'OmanTenderBoardScraper',
        'description':   (
            'Official Oman Tender Board eTendering portal. '
            'Scrapes المفتوحة (open/in-process) and المطروحة (new) tenders '
            'from all government ministries and agencies. '
            '50 rows per page, up to 18,000+ open tenders.'
        ),
        'urls': [
            {
                'url':              'https://etendering.tenderboard.gov.om/product/publicDash?viewFlag=NewTenders&switchLangFlag=1&switchLangFlag=1',
                'label':            'New Tenders (English)',
                'label_ar':         'المناقصات الجديدة',
                'pagination_style': 'QUERY',
                'page_param':       'pageNo',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    {
        'name': 'Qatar — Government Tenders Portal',
        'name_ar': 'قطر — بوابة المناقصات الحكومية',
        'country': 'QA',
        'base_url': 'https://www.tender.gov.qa',
        'scraper_class': 'QatarTendersScraper',
        'description': 'Qatar government tenders and procurement portal.',
        'description_ar': 'بوابة المناقصات والمشتريات الحكومية القطرية.',
        'urls': [
            {
                'label': 'All Tenders',
                'label_ar': 'جميع المناقصات',
                'url': 'https://www.tender.gov.qa/tenders',
                'pagination_style': 'QUERY',
                'page_param': 'page',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },
    {
        'name': 'Kuwait — Central Agency for Public Tenders (CAPT)',
        'name_ar': 'الكويت — الجهاز المركزي للمناقصات العامة',
        'country': 'KW',
        'base_url': 'https://www.capt.gov.kw',
        'scraper_class': 'KuwaitCAPTScraper',
        'description': 'Kuwait CAPT — official entity managing all public tenders.',
        'description_ar': 'الجهاز المركزي للمناقصات الحكومية في الكويت.',
        'urls': [
            {
                'label': 'Current Tenders',
                'label_ar': 'المناقصات الحالية',
                'url': 'https://www.capt.gov.kw/ar/tenders',
                'pagination_style': 'QUERY',
                'page_param': 'page',
                'max_pages': 10,
                'sort_order': 1,
            },
        ],
    },

    # ── Syria ──────────────────────────────────────────────────────────────
    {
        'name':          'Syria — SyriaTender (your-tender.com)',
        'name_ar':       'سوريا — منصة مناقصات سوريا',
        'country':       'SY',
        'base_url':      'https://your-tender.com',
        'scraper_class': 'SyriaTenderScraper',
        'description':   'Syria & Turkey procurement platform — all active tenders',
        'urls': [
            {
                'url':              'https://your-tender.com/ar/tenders',
                'label':            'Syria — All Tenders (AR)',
                'label_ar':         'سوريا — جميع المناقصات',
                'pagination_style': 'QUERY',
                'page_param':       'page',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── AfricaGateway — Africa Multi-Country (single aggregated source) ────────
    {
        'name':          'AfricaGateway — Africa Multi-Country',
        'name_ar':       'أفريكا غيتواي — مناقصات أفريقيا متعددة الدول',
        'country':       'ZA',   # generic Africa; each tender gets its real country_code
        'base_url':      'https://www.africagateway.info',
        'scraper_class': 'AfricaGatewayScraper',
        'description':   (
            'AfricaGateway multi-country scraper. Fetches tenders for 15 African '
            'countries in one run: Burundi, Tanzania, Uganda, Comoros, Djibouti, '
            'Eritrea, Ethiopia, Kenya, Madagascar, Malawi, Mauritius, Mozambique, '
            'Rwanda, Somalia, Zimbabwe. Each tender is tagged to its real country '
            '(no mirrors needed — already country-specific).'
        ),
        'description_ar': (
            'مجمع مناقصات أفريكا غيتواي متعدد الدول. يجمع مناقصات 15 دولة أفريقية '
            'في جلسة واحدة، كل مناقصة مرتبطة بدولتها الفعلية.'
        ),
        'urls': [
            {
                'url':              'https://www.africagateway.info/showtenderlist',
                'label':            'AfricaGateway — All Africa Countries',
                'label_ar':         'أفريكا غيتواي — جميع الدول الأفريقية',
                'pagination_style': 'SINGLE',
                'page_param':       'page',
                'max_pages':        1,   # 1 page × 30 rows × 15 countries = 450 tenders
                'sort_order':       1,
            },
        ],
    },

    # ── GlobalTenders — Engineering & Construction (ME + Africa) ────────────
    {
        'name':          'GlobalTenders — Engineering & Construction',
        'name_ar':       'غلوبال تندرز — هندسة وإنشاء (الشرق الأوسط وأفريقيا)',
        'country':       'JO',
        'base_url':      'https://www.globaltenders.com',
        'scraper_class': 'GlobalTendersScraper',
        'description':   (
            'GlobalTenders.com — Engineering (sector 32) + Architecture/Construction (sector 11) '
            'in Middle East (REG0102), Africa (REG0103), East Africa (REG0308). '
            'Live tenders only. Pages 1-10.'
        ),
        'urls': [
            {
                'url': (
                    'https://www.globaltenders.com/gtsearch'
                    '?status=menu&limit=0&sector%5B%5D=32&sector%5B%5D=11'
                    '&region_name%5B%5D=REG0102&region_name%5B%5D=REG0103'
                    '&region_name%5B%5D=REG0308'
                    '&notice_type=gpn%2Cpp%2Cspn%2Crei%2Cppn%2Cacn%2Crfc'
                    '&tender_type=live&est_cost_condition=greater_equal'
                ),
                'label':            'GlobalTenders — Engineering & Construction',
                'label_ar':         'غلوبال تندرز — هندسة وإنشاء',
                'pagination_style': 'QUERY',
                'page_param':       'limit',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── UAE Ministry of Finance — Current Business Opportunities ────────────
    {
        'name':          'UAE Ministry of Finance — Current Business Opportunities',
        'name_ar':       'وزارة المالية الإماراتية — الفرص التجارية الحالية',
        'country':       'AE',
        'base_url':      'https://mof.gov.ae',
        'scraper_class': 'MOFUAEScraper',
        'description':   'Federal procurement tenders published by UAE MoF and other federal entities.',
        'description_ar': 'مناقصات الجهات الاتحادية المنشورة عبر وزارة المالية الإماراتية.',
        'urls': [
            {
                'label':            'MoF Current Business Opportunities',
                'label_ar':         'الفرص التجارية الحالية',
                'url':              'https://mof.gov.ae/en/public-finance/government-procurement/current-business-opportunities/',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    # ── DEWA — Dubai Electricity & Water Authority ──────────────────────────
    {
        'name':          'DEWA -- Dubai Electricity & Water Authority',
        'name_ar':       'هيئة كهرباء ومياه دبي',
        'country':       'AE',
        'base_url':      'https://www.dewa.gov.ae',
        'scraper_class': 'DEWAScraper',
        'description':   'DEWA open tenders — Dubai Electricity & Water Authority.',
        'description_ar': 'مناقصات هيئة كهرباء ومياه دبي.',
        'urls': [
            {
                'label':            'DEWA Open Tenders',
                'label_ar':         'مناقصات ديوا',
                'url':              'https://www.dewa.gov.ae/en/supplier/services/list-of-tender-documents',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    # ── eSupply Dubai ────────────────────────────────────────────────────────
    {
        'name':          'eSupply Dubai -- Dubai Government Procurement Portal',
        'name_ar':       'بوابة مشتريات حكومة دبي -- إي سبلاي',
        'country':       'AE',
        'base_url':      'https://esupply.dubai.gov.ae',
        'scraper_class': 'ESupplyDubaiScraper',
        'description':   'eSupply Dubai RFQ/RFPs open to all suppliers.',
        'description_ar': 'طلبات عروض الأسعار المفتوحة لجميع الموردين في بوابة إي سبلاي دبي.',
        'urls': [
            {
                'url':              'https://esupply.dubai.gov.ae/esop/toolkit/negotiation/pubRfq/list.si',
                'label':            'RFQ/RFPs Open to All Suppliers',
                'label_ar':         'طلبات عروض الأسعار المفتوحة لجميع الموردين',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    # ── UMUCYO Rwanda ────────────────────────────────────────────────────────
    {
        'name':          'UMUCYO -- Rwanda Public Procurement Portal',
        'name_ar':       'بوابة المشتريات العامة في رواندا',
        'country':       'RW',
        'base_url':      'https://www.umucyo.gov.rw',
        'scraper_class': 'UmucyoRwandaScraper',
        'description':   'Rwanda official e-procurement portal managed by RPPA.',
        'description_ar': 'البوابة الرسمية للمشتريات الإلكترونية في رواندا.',
        'urls': [
            {
                'url':              'https://www.umucyo.gov.rw/eb/bav/selectListAdvertisingListForGU.do',
                'label':            'List of Advertising (All)',
                'label_ar':         'قائمة الإعلانات',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    # ── DevelopmentAid Rwanda ────────────────────────────────────────────────
    {
        'name':          'DevelopmentAid -- Rwanda Tenders',
        'name_ar':       'دافوف المساعدة التنموية -- رواندا',
        'country':       'RW',
        'base_url':      'https://www.developmentaid.org',
        'scraper_class': 'DevelopmentAidRwandaScraper',
        'description':   'DevelopmentAid.org tenders filtered to Rwanda (locations=51).',
        'description_ar': 'مناقصات رواندا من موقع دافوف.',
        'urls': [
            {
                'label':            'DevelopmentAid Rwanda',
                'label_ar':         'مناقصات رواندا',
                'url':              'https://www.developmentaid.org/tenders/search?locations=51',
                'pagination_style': 'SINGLE',
                'sort_order':       1,
            },
        ],
    },
    # ── DevelopmentAid — International Development Tenders ─────────────────
    {
        'name':          'DevelopmentAid — International Tenders',
        'name_ar':       'ديفلوبمنت إيد — مناقصات التنمية الدولية',
        'country':       'ZZ',
        'base_url':      'https://www.developmentaid.org',
        'scraper_class': 'DevelopmentAidScraper',
        'description':   'DevelopmentAid.org — global procurement notices. Pages 1-20.',
        'urls': [
            {
                'url':              'https://www.developmentaid.org/api/tenders?pageNr=1',
                'label':            'DevelopmentAid — All Tenders',
                'label_ar':         'ديفلوبمنت إيد — جميع المناقصات',
                'pagination_style': 'QUERY',
                'page_param':       'pageNr',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    {
        'name':          'Morocco — MoroccoTenders.com',
        'name_ar':       'المغرب — بوابة مناقصات المغرب',
        'country':       'MA',
        'base_url':      'https://www.moroccotenders.com',
        'scraper_class': 'MoroccoTendersScraper',
        'description':   (
            'MoroccoTenders.com — largest online database of Morocco public, '
            'government and private tenders. Daily updates from official portals, '
            'ministries, local authorities and private entities.'
        ),
        'description_ar': (
            'بوابة مناقصات المغرب — أكبر قاعدة بيانات إلكترونية للمناقصات الحكومية '
            'والخاصة في المملكة المغربية. تحديثات يومية من المصادر الرسمية.'
        ),
        'urls': [
            {
                'url':              'https://www.moroccotenders.com/tenders.php',
                'label':            'Morocco — MoroccoTenders Listing',
                'label_ar':         'المغرب — قائمة المناقصات',
                'pagination_style': 'PATH',
                'page_param':       '',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },
    # ── World Bank — Procurement Notices ─────────────────────────────────────
    {
        'name':          'World Bank — Procurement Notices',
        'name_ar':       'البنك الدولي — إشعارات المشتريات',
        'country':       'WB',
        'base_url':      'https://projects.worldbank.org',
        'scraper_class': 'WorldBankScraper',
        'description':   (
            'World Bank Procurement Notices — global procurement opportunities '
            'from World Bank-financed projects. Sorted by submission deadline desc. '
            'Fetches up to 10 pages × 20 notices = 200 notices per run via JSON API.'
        ),
        'description_ar': (
            'إشعارات مشتريات البنك الدولي — فرص المشتريات العالمية من المشاريع '
            'الممولة من البنك الدولي عبر واجهة برمجية JSON.'
        ),
        'urls': [
            {
                'url':              'https://search.worldbank.org/api/v2/procnotices',
                'label':            'World Bank — Procurement Notices API',
                'label_ar':         'البنك الدولي — واجهة إشعارات المشتريات',
                'pagination_style': 'SINGLE',   # scraper handles offset internally
                'page_param':       'os',
                'max_pages':        10,          # 10 × 20 = 200 notices
                'sort_order':       1,
            },
        ],
    },
    # ── World Bank RFxNow — Active Advertisements ─────────────────────────────
    {
        'name':          'World Bank RFxNow — Active Advertisements',
        'name_ar':       'البنك الدولي — منصة RFxNow للمناقصات',
        'country':       'WB',
        'base_url':      'https://wbgeprocure-rfxnow.worldbank.org',
        'scraper_class': 'WorldBankRFxScraper',
        'description':   (
            'World Bank Group RFxNow eProcurement platform — active RFP/RFQ/EOI '
            'advertisements open to vendors. Single-call fetch of all active notices.'
        ),
        'description_ar': (
            'منصة RFxNow للمشتريات الإلكترونية لمجموعة البنك الدولي — '
            'الإعلانات النشطة (طلبات عروض الأسعار والمؤهلات).'
        ),
        'urls': [
            {
                'url':              'https://wbgeprocure-rfxnow.worldbank.org/rfxnow/public/advertisement/index.html',
                'label':            'WB RFxNow — Public Advertisements (Playwright)',
                'label_ar':         'البنك الدولي — واجهة الإعلانات النشطة',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        1,
                'sort_order':       1,
            },
        ],
    },
    # ── GTAI — KfW Development Bank Tenders ──────────────────────────────────
    {
        'name':          'GTAI — KfW Development Bank Tenders',
        'name_ar':       'بنك التنمية الألماني KfW — مناقصات عالمية',
        'country':       'DE',
        'base_url':      'https://www.gtai.de',
        'scraper_class': 'GTAIKfWScraper',
        'description':   (
            'Germany Trade & Invest (GTAI) — KfW Development Bank-financed tenders '
            'worldwide. Architecture, engineering, construction supervision, consulting, '
            'infrastructure. 1,000+ active notices across Africa, Asia, Europe, Americas.'
        ),
        'description_ar': (
            'وكالة التجارة والاستثمار الألمانية (GTAI) — مناقصات ممولة من بنك التنمية '
            'الألماني KfW حول العالم. هندسة، إنشاء، إشراف، استشارات، بنية تحتية.'
        ),
        'urls': [
            {
                'url': (
                    'https://www.gtai.de/en/meta/search/kfw-tenders/795748!search;'
                    'eNqVkUFOxDAMRe_idSsBGlVMD8AFYIdYuKk7ZJQ6xXaAMurdSZFgUUQVdk70v'
                    '-33fYEBHZlCe4EuqWdSrVEI1w-lQM6oh_YRTLAneKqA3l3IH7lyfIYWkGdYKp'
                    'DUiXdb1_HYNDc7LhcTm8wb2996zZIoWqwP-FbeeyLnMRTryUWO4y_mHdikFsfy'
                    '5QfPyI5kG-rhcNvsZDo9o1L5UnGccnmXD9Gj-cjl_K_E9jBP9I_riadyfk3dO'
                    'avK84oyopX2X5PCE937jwxwfVXBC7ScQshzo-ShUPc0YApWQwU5mi9M4v5bJcgn'
                    '-rEYrp71sSyf-3QUnQ'
                ),
                'label':            'GTAI — KfW Tenders Search',
                'label_ar':         'GTAI — بحث مناقصات KfW',
                'pagination_style': 'QUERY',
                'page_param':       'page',
                'max_pages':        10,   # 20 × 10 = 200; full archive = 105 pages
                'sort_order':       1,
            },
        ],
    },
    # ── EBRD — European Bank for Reconstruction and Development ─────────────────
    {
        'name':          'EBRD — ECEPP Procurement Notices',
        'name_ar':       'البنك الأوروبي لإعادة الإعمار والتنمية — إشعارات المشتريات',
        'country':       'EU',
        'base_url':      'https://ecepp.ebrd.com',
        'scraper_class': 'EBRDScraper',
        'description':   (
            'EBRD ECEPP (e-Procurement Portal) — official portal for all EBRD-funded '
            'project procurement notices across 40+ countries in Eastern Europe, Central '
            'Asia, and the Middle East & Africa. Includes IFT (Invitation for Tenders), '
            'Contract Award Notices, and addenda for Works, Consultancy and Goods.'
        ),
        'description_ar': (
            'بوابة المشتريات الإلكترونية للبنك الأوروبي لإعادة الإعمار والتنمية — '
            'البوابة الرسمية لجميع إشعارات المشتريات الممولة من البنك عبر أكثر من 40 دولة '
            'في أوروبا الشرقية وآسيا الوسطى والشرق الأوسط وأفريقيا.'
        ),
        'urls': [
            {
                'url':              'https://ecepp.ebrd.com/delta/noticeSearchResults.html',
                'label':            'EBRD — All Procurement Notices',
                'label_ar':         'البنك الأوروبي لإعادة الإعمار — جميع إشعارات المشتريات',
                'pagination_style': 'SINGLE',
                'page_param':       'page',
                'max_pages':        1,
                'sort_order':       1,
            },
        ],
    },
    # ── Rwanda UMUCYO / RPPA e-Procurement ──────────────────────────────────────
    {
        'name':          'Rwanda — UMUCYO / RPPA e-Procurement',
        'name_ar':       'رواندا — بوابة المشتريات الإلكترونية',
        'country':       'RW',
        'base_url':      'https://www.umucyo.gov.rw',
        'scraper_class': 'RwandaUMUCYOScraper',
        'description':   (
            'UMUCYO (Rwanda Public Procurement Authority) online e-procurement portal. '
            'Publishes all government bid notices, RFPs, and contract awards across '
            'Rwanda national and district-level entities. KONEPS-based system.'
        ),
        'description_ar': (
            'بوابة المشتريات الإلكترونية لسلطة المشتريات العامة في رواندا. '
            'تنشر جميع إشعارات العطاءات والمناقصات الحكومية على المستوى الوطني والمحلي.'
        ),
        'urls': [
            {
                'url':              'https://www.umucyo.gov.rw/eb/bav/selectListAdvertisingListForGU.do',
                'label':            'Rwanda UMUCYO — Full Advertising List (50/page)',
                'label_ar':         'رواندا — قائمة الإعلانات الكاملة',
                'pagination_style': 'SINGLE',
                'page_param':       'currentPageNo',
                'max_pages':        10,   # 10 × 50 = 500 tenders per run
                'sort_order':       1,
            },
        ],
    },
    # ── AfDB Procurement ─────────────────────────────────────────────────────
    {
        'name':          'AfDB — African Development Bank Procurement',
        'name_ar':       'بنك التنمية الأفريقي — المشتريات',
        'country':       'ZZ',
        'base_url':      'https://www.afdb.org',
        'scraper_class': 'AfDBScraper',
        'description':   'African Development Bank active project portfolio via MapAfrica API.',
        'description_ar': 'محفظة مشاريع بنك التنمية الأفريقي عبر واجهة برمجة MapAfrica.',
        'urls': [
            {
                'url':              'https://www.afdb.org/en/projects-and-operations/procurement',
                'label':            'AfDB Procurement Notices',
                'label_ar':         'محفظة مشاريع بنك التنمية الأفريقي',
                'pagination_style': 'API',
                'sort_order':       1,
            },
        ],
    },

    # ── AFD — Agence Française de Développement (dgMarket portal) ────────────
    {
        'name':          'AFD — Agence Française de Développement',
        'name_ar':       'وكالة التنمية الفرنسية — مناقصات المشاريع',
        'country':       'DE',
        'base_url':      'https://tenders-afd.dgmarket.com',
        'scraper_class': 'AFDScraper',
        'description':   (
            'AFD (Agence Française de Développement) project tenders published via '
            'the dgMarket branded portal. Covers projects financed by AFD across '
            'Africa, Asia, Middle East and Latin America. '
            '~150 active notices spanning works, goods and consulting services.'
        ),
        'description_ar': (
            'مناقصات مشاريع وكالة التنمية الفرنسية (AFD) المنشورة عبر بوابة dgMarket. '
            'تغطي المشاريع الممولة من AFD في أفريقيا وآسيا والشرق الأوسط وأمريكا اللاتينية.'
        ),
        'urls': [
            {
                'url':              'https://tenders-afd.dgmarket.com/tenders/brandedNoticeList.do',
                'label':            'AFD dgMarket — Tender Notices',
                'label_ar':         'إشعارات مناقصات AFD',
                'pagination_style': 'QUERY',
                'page_param':       'selPageNumber',
                'max_pages':        10,    # 3 × 20 = 60 tenders per run
                'sort_order':       1,
            },
        ],
    },
    # ── WASAC Group Rwanda ───────────────────────────────────────────────────────────────────
    {
        'name':          'WASAC Group Rwanda',
        'name_ar':       'مجموعة WASAC رواندا',
        'country':       'RW',
        'base_url':      'https://www.wasac.rw',
        'scraper_class': 'WASACRwandaScraper',
        'description':   (
            'Water and Sanitation Corporation (WASAC) Group Rwanda — national water utility. '
            'Publishes tenders for water infrastructure, sanitation, consultancy, and supply. '
            'TYPO3 CMS — all tenders on single page, no pagination.'
        ),
        'urls': [
            {
                'url':              'https://www.wasac.rw/en/public-information/tenders',
                'label':            'WASAC Rwanda — Tenders',
                'label_ar':         'WASAC رواندا — المناقصات',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        1,
                'sort_order':       1,
            },
        ],
    },

    # ── Ethiopian Roads Administration (ERA) ────────────────────────────────────────────
    {
        'name':          'Ethiopian Roads Administration (ERA)',
        'name_ar':       'إدارة الطرق الإثيوبية (ERA)',
        'country':       'ET',
        'base_url':      'https://www.era.gov.et',
        'scraper_class': 'ERAEthiopiaScraper',
        'description':   (
            'Ethiopian Roads Administration (ERA) official procurement portal. '
            'Publishes road construction, maintenance, consultancy, and supply tenders. '
            'Note: site uses SSL/IP restrictions — works from Ethiopian IPs.'
        ),
        'urls': [
            {
                'url':              'https://www.era.gov.et/en_US/web/guest/cuntry-profile',
                'label':            'ERA Ethiopia — Tenders',
                'label_ar':         'إدارة الطرق الإثيوبية — المناقصات',
                'pagination_style': 'QUERY',
                'page_param':       'p_r_p_cur',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── BidDetail — Western Asia ─────────────────────────────────────────────────────
    {
        'name':          'BidDetail — Western Asia Tenders',
        'name_ar':       'BidDetail — مناقصات غرب آسيا',
        'country':       'ZZ',  # multi-country region — per-tender country resolved from entity
        'base_url':      'https://www.biddetail.com',
        'scraper_class': 'BidDetailWesternAsiaScraper',
        'description':   (
            'BidDetail Western Asia region tenders — covers Saudi Arabia, UAE, Kuwait, '
            'Qatar, Oman, Bahrain, Jordan, Iraq, Yemen, Syria, Lebanon, Israel, Palestine. '
            'Government departments, World Bank, UN, and multilateral funding agencies.'
        ),
        'urls': [
            {
                'url':              'https://www.biddetail.com/region/western-asia-tenders',
                'label':            'BidDetail — Western Asia',
                'label_ar':         'BidDetail — غرب آسيا',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── BidDetail — Eastern Africa ────────────────────────────────────────────────────
    {
        'name':          'BidDetail — Eastern Africa Tenders',
        'name_ar':       'BidDetail — مناقصات شرق أفريقيا',
        'country':       'ZZ',  # multi-country region — per-tender country resolved from entity
        'base_url':      'https://www.biddetail.com',
        'scraper_class': 'BidDetailEasternAfricaScraper',
        'description':   (
            'BidDetail Eastern Africa region tenders — covers Kenya, Tanzania, Uganda, '
            'Ethiopia, Mozambique, Rwanda, Somalia, Sudan, Eritrea, Djibouti, and more. '
            'Government, World Bank, UN, and NGO tenders.'
        ),
        'urls': [
            {
                'url':              'https://www.biddetail.com/region/eastern-africa-tenders',
                'label':            'BidDetail — Eastern Africa',
                'label_ar':         'BidDetail — شرق أفريقيا',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── TenderImpulse — Middle East ────────────────────────────────────────────────
    {
        'name':          'TenderImpulse — Middle East Tenders',
        'name_ar':       'TenderImpulse — مناقصات الشرق الأوسط',
        'country':       'AE',
        'base_url':      'https://tenderimpulse.com',
        'scraper_class': 'TenderImpulseMiddleEastScraper',
        'description':   (
            'TenderImpulse Middle East tenders aggregator — covers Saudi Arabia, UAE, '
            'Kuwait, Qatar, Oman, Bahrain, Jordan, Iraq, Egypt, and other Middle East '
            'countries. Includes government, ministry, and international organization tenders.'
        ),
        'urls': [
            {
                'url':              'https://tenderimpulse.com/middle-east-tenders',
                'label':            'TenderImpulse — Middle East',
                'label_ar':         'TenderImpulse — الشرق الأوسط',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── TenderImpulse — Africa ────────────────────────────────────────────────────
    {
        'name':          'TenderImpulse — Africa Tenders',
        'name_ar':       'TenderImpulse — مناقصات أفريقيا',
        'country':       'ZA',
        'base_url':      'https://tenderimpulse.com',
        'scraper_class': 'TenderImpulseAfricaScraper',
        'description':   (
            'TenderImpulse Africa tenders aggregator — covers South Africa, Kenya, '
            'Nigeria, Egypt, Morocco, Ethiopia, Tanzania, Uganda, Rwanda, and 50+ '
            'African countries. Government, NGO, and international organization tenders.'
        ),
        'urls': [
            {
                'url':              'https://tenderimpulse.com/africa-tenders',
                'label':            'TenderImpulse — Africa',
                'label_ar':         'TenderImpulse — أفريقيا',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── Kuwait PAHW — Public Authority for Housing Welfare ─────────────────────────
    {
        'name':          'Kuwait — PAHW (Public Authority for Housing Welfare)',
        'name_ar':       'الكويت — المؤسسة العامة للرعاية السكنية',
        'country':       'KW',
        'base_url':      'https://www.pahw.gov.kw',
        'scraper_class': 'KuwaitPAHWScraper',
        'description':   (
            'Kuwait Public Authority for Housing Welfare (PAHW) — official procurement '
            'portal for major housing infrastructure projects, construction, utilities, '
            'IT services, and consultancy contracts. Covers large-scale residential '
            'city development projects (Mutlaa, South Sabah Al-Ahmad, etc.).'
        ),
        'description_ar': (
            'المؤسسة العامة للرعاية السكنية بالكويت — بوابة المناقصات الرسمية للمشاريع '
            'الإسكانية الكبرى والبنية التحتية وتكنولوجيا المعلومات والخدمات الاستشارية.'
        ),
        'urls': [
            {
                'url':              'https://www.pahw.gov.kw/Tenders_arabic',
                'label':            'Kuwait PAHW — Tenders & Practices',
                'label_ar':         'المؤسسة العامة للرعاية السكنية — المناقصات والممارسات',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── Kuwait CAPT — Central Agency for Public Tenders ────────────────────────────
    {
        'name':          'Kuwait — CAPT (Central Agency for Public Tenders)',
        'name_ar':       'الكويت — الجهاز المركزي للمناقصات العامة',
        'country':       'KW',
        'base_url':      'https://capt.gov.kw',
        'scraper_class': 'KuwaitCAPTScraper',
        'description':   (
            'Kuwait Central Agency for Public Tenders (CAPT) — official government '
            'procurement portal. Covers all Kuwaiti ministries, agencies, and '
            'state-owned companies including Kuwait Oil Company, Kuwait National '
            'Petroleum Company, Kuwait Municipality, and 100+ other entities.'
        ),
        'description_ar': (
            'الجهاز المركزي للمناقصات العامة في الكويت — البوابة الرسمية للمشتريات '
            'الحكومية. تشمل جميع الوزارات والهيئات والشركات الحكومية الكويتية.'
        ),
        'urls': [
            {
                'url':              'https://capt.gov.kw/ar/tenders/opening-tenders/',
                'label':            'Kuwait CAPT — Open Tenders',
                'label_ar':         'الجهاز المركزي للمناقصات — المناقصات المطروحة',
                'pagination_style': 'QUERY',
                'page_param':       'page',
                'max_pages':        10,
                'sort_order':       1,
            },
        ],
    },

    # ── Iraq Business News — Tenders ─────────────────────────────────────────────
    {
        'name':          'Iraq Business News — Tenders',
        'name_ar':       'أخبار الأعمال العراقية — المناقصات',
        'country':       'IQ',
        'base_url':      'https://www.iraq-businessnews.com',
        'scraper_class': 'IraqBusinessNewsScraper',
        'description':   (
            'Iraq Business News tenders page. Aggregates procurement notices '
            'from international NGOs, UN agencies, embassies and Iraqi companies '
            'operating in Iraq. Single-page listing with ~80-100 active tenders. '
            'Entities include UNICEF, UNDP, DRC, NRC, GIZ, WVI, IRC, IOM, FAO.'
        ),
        'description_ar': (
            'صفحة مناقصات أخبار الأعمال العراقية. تجمع إشعارات المناقصات من '
            'المنظمات الدولية والأمم المتحدة والشركات العاملة في العراق.'
        ),
        'urls': [
            {
                'url':              'https://www.iraq-businessnews.com/tenders/',
                'label':            'Iraq Business News — Tenders',
                'label_ar':         'مناقصات العراق',
                'pagination_style': 'SINGLE',
                'page_param':       '',
                'max_pages':        1,
                'sort_order':       1,
            },
        ],
    },

    # ── IsDB — Islamic Development Bank Project Procurement ───────────────────
    {
        'name':          'IsDB — Project Procurement Tenders',
        'name_ar':       'البنك الإسلامي للتنمية — مناقصات المشتريات',
        'country':       'IB',
        'base_url':      'https://www.isdb.org',
        'scraper_class': 'IsDBScraper',
        'description':   (
            'IsDB Project Procurement Tenders — official portal for all Islamic '
            'Development Bank project tenders across 57 member countries. '
            'Types include SPN, SPN-CW (Civil Works), GPN, EOI, PQN. '
            'Covers infrastructure, consulting, civil works, supply, health & education.'
        ),
        'description_ar': (
            'مناقصات مشتريات مشاريع البنك الإسلامي للتنمية — البوابة الرسمية لجميع '
            'مناقصات المشتريات عبر 57 دولة عضواً. '
            'تشمل: إخطار المشتريات المحددة، الأعمال المدنية، الإخطار العام للمشتريات، '
            'إبداء الاهتمام، إشعار التأهيل المسبق.'
        ),
        'urls': [
            {
                'url':              'https://www.isdb.org/project-procurement/tenders',
                'label':            'IsDB — Active Procurement Tenders',
                'label_ar':         'البنك الإسلامي للتنمية — المناقصات النشطة',
                'pagination_style': 'QUERY',
                'page_param':       'page',
                'max_pages':        10,
                'extra_params':     {'status': 'active'},
                'sort_order':       1,
            },
        ],
    },
    # ── UNDP — United Nations Development Programme Procurement Notices ───────
    {
        'name':          'UNDP — Procurement Notices',
        'name_ar':       'برنامج الأمم المتحدة الإنمائي — إشعارات المشتريات',
        'country':       'UN',
        'base_url':      'https://procurement-notices.undp.org',
        'scraper_class': 'UNDPScraper',
        'description':   (
            'UNDP Procurement Notices — official portal for all UNDP country-office '
            'tenders worldwide (ITB, RFP, RFQ, IC, EOI). Covers 170+ country offices. '
            'All active tenders delivered on a single HTML page; scraper parses them in one pass.'
        ),
        'description_ar': (
            'إشعارات مشتريات برنامج الأمم المتحدة الإنمائي — البوابة الرسمية لجميع '
            'مناقصات المكاتب القُطرية لبرنامج الأمم المتحدة الإنمائي حول العالم '
            '(دعوة للعروض، طلب مقترحات، طلب عروض أسعار، مقاول فردي، طلب إبداء اهتمام). '
            'تغطي أكثر من 170 مكتباً قُطرياً.'
        ),
        'urls': [
            {
                'url':              'https://procurement-notices.undp.org/index.cfm',
                'label':            'UNDP — All Procurement Notices (Active)',
                'label_ar':         'UNDP — جميع إشعارات المشتريات النشطة',
                'pagination_style': 'SINGLE',   # full list on one page
                'page_param':       'page',
                'max_pages':        1,
                'sort_order':       1,
            },
        ],
    },
    # ── UNGM — United Nations Global Marketplace ─────────────────────────────
    {
        'name':          'UNGM — United Nations Global Marketplace',
        'name_ar':       'السوق العالمية للأمم المتحدة (UNGM)',
        'country':       'UN',
        'base_url':      'https://www.ungm.org',
        'scraper_class': 'UNGMScraper',
        'description':   (
            'UNGM — official procurement platform of the UN system. Consolidated '
            'procurement notices across 80+ UN agencies (UNOPS, UNDP, UNICEF, WHO, '
            'UNHCR, WFP, FAO, IAEA, WBG, ILO, etc.) covering all beneficiary '
            'countries. Fetched via /Public/Notice/Search AJAX endpoint '
            '(10 pages × 15 notices = 150 per run).'
        ),
        'description_ar': (
            'UNGM — البوابة الرسمية لمشتريات منظومة الأمم المتحدة، توحّد فرص '
            'المشتريات لأكثر من 80 وكالة (UNOPS, UNDP, UNICEF, WHO, UNHCR, WFP, '
            'FAO, IAEA, البنك الدولي، ILO، إلخ) ولجميع الدول المستفيدة.'
        ),
        'urls': [
            {
                'url':              'https://www.ungm.org/Public/Notice',
                'label':            'UNGM — All Procurement Opportunities',
                'label_ar':         'UNGM — جميع فرص المشتريات',
                'pagination_style': 'SINGLE',   # scraper drives PageIndex internally
                'page_param':       'PageIndex',
                'max_pages':        10,          # 10 × 15 = 150 notices per run
                'sort_order':       1,
            },
        ],
    },
]

RELEVANCE_KEYWORDS = [
    # English
    ('engineer', 'EN', 'Core term'), ('engineering', 'EN', ''),
    ('design', 'EN', ''), ('supervision', 'EN', ''),
    ('consultant', 'EN', ''), ('consultancy', 'EN', ''),
    ('architect', 'EN', ''), ('architecture', 'EN', ''),
    ('structural', 'EN', ''), ('civil', 'EN', ''),
    ('mep', 'EN', 'Mechanical Electrical Plumbing'), ('mechanical', 'EN', ''),
    ('electrical', 'EN', ''), ('plumbing', 'EN', ''),
    ('urban', 'EN', ''), ('planning', 'EN', ''),
    ('infrastructure', 'EN', ''), ('project management', 'EN', ''),
    ('technical assistance', 'EN', 'TA missions'),
    ('laboratory', 'EN', 'Lab services'),
    ('smart city', 'EN', ''), ('smart cities', 'EN', ''),
    ('rehabilitation', 'EN', ''), ('reconstruction', 'EN', ''),
    ('procurement', 'EN', ''), ('assessment', 'EN', ''),
    ('capacity building', 'EN', ''), ('institutional', 'EN', ''),
    ('pmc', 'EN', 'Project Management Consultancy'),
    ('environmental', 'EN', ''), ('healthcare', 'EN', ''),
    ('hospital', 'EN', ''), ('transportation', 'EN', ''),
    ('road', 'EN', ''), ('bridge', 'EN', ''),
    ('feasibility', 'EN', ''), ('master plan', 'EN', ''),
    ('construction management', 'EN', ''), ('geotechnical', 'EN', ''),
    ('quantity surveying', 'EN', ''), ('landscape', 'EN', ''),
    # Arabic
    ('استشارات', 'AR', 'Consultancy'), ('تصميم', 'AR', 'Design'),
    ('إشراف', 'AR', 'Supervision'), ('هندسة', 'AR', 'Engineering'),
    ('معماري', 'AR', 'Architectural'), ('مدني', 'AR', 'Civil'),
    ('بنية تحتية', 'AR', 'Infrastructure'), ('تخطيط', 'AR', 'Planning'),
    ('رقابة', 'AR', 'Oversight/Control'), ('دراسة جدوى', 'AR', 'Feasibility Study'),
    ('مشروع', 'AR', 'Project'), ('إنشاء', 'AR', 'Construction'),
    ('تطوير', 'AR', 'Development'), ('مستشفى', 'AR', 'Hospital'),
    ('صحة', 'AR', 'Health'), ('نقل', 'AR', 'Transport'),
    ('طرق', 'AR', 'Roads'), ('جسور', 'AR', 'Bridges'),
    ('إدارة مشاريع', 'AR', 'Project Management'), ('كهرباء', 'AR', 'Electrical'),
    ('ميكانيكا', 'AR', 'Mechanical'), ('بيئة', 'AR', 'Environment'),
    ('تخطيط عمراني', 'AR', 'Urban Planning'),
    ('مخطط رئيسي', 'AR', 'Master Plan'),
    ('هندسة مدنية', 'AR', 'Civil Engineering'),
    ('هندسة إنشائية', 'AR', 'Structural Engineering'),
    ('خدمات استشارية', 'AR', 'Advisory Services'),
    ('دراسات هندسية', 'AR', 'Engineering Studies'),
    ('مسح كميات', 'AR', 'Quantity Surveying'),
    ('تصريف مياه', 'AR', 'Drainage'),
    # Arabic — Syria/NGO renovation & civil works context
    ('ترميم', 'AR', 'Renovation'),
    ('تأهيل', 'AR', 'Rehabilitation'),
    ('إعادة تأهيل', 'AR', 'Re-Rehabilitation'),
    ('إعادة إعمار', 'AR', 'Reconstruction'),
    ('أعمال مدنية', 'AR', 'Civil Works'),
    ('أعمال إنشائية', 'AR', 'Construction Works'),
    ('مقاول', 'AR', 'Contractor'),
    ('شبكات مياه', 'AR', 'Water Networks'),
    ('صرف صحي', 'AR', 'Sanitation'),
    ('طاقة شمسية', 'AR', 'Solar Energy'),
    ('مولدة', 'AR', 'Generator'),
    ('محطة مياه', 'AR', 'Water Station'),
    ('توريد وتركيب', 'AR', 'Supply & Install'),
    ('خدمات هندسية', 'AR', 'Engineering Services'),
    ('رسومات هندسية', 'AR', 'Engineering Drawings'),
    ('مخطط', 'AR', 'Plan/Drawing'),
    ('مباني', 'AR', 'Buildings'),
    ('منشآت', 'AR', 'Facilities'),
    ('بناء', 'AR', 'Building/Construction'),
    ('الصرف الصحي', 'AR', 'Sanitation (with article)'),
    ('أعمال المياه', 'AR', 'Water Works'),
    ('شبكة المياه', 'AR', 'Water Network'),
    ('توريد وتنفيذ', 'AR', 'Supply & Execute'),
    ('توريد', 'AR', 'Supply/Procurement'),
    ('تنفيذ', 'AR', 'Implementation/Execute'),
]

CATEGORY_KEYWORDS = [
    # BUILDING — buildings of any type (residential, commercial, education, ...)
    ('BUILDING', 'building', 'EN'), ('BUILDING', 'architect', 'EN'),
    ('BUILDING', 'architectural', 'EN'), ('BUILDING', 'structural', 'EN'),
    ('BUILDING', 'mep', 'EN'), ('BUILDING', 'mechanical', 'EN'),
    ('BUILDING', 'electrical', 'EN'), ('BUILDING', 'plumbing', 'EN'),
    ('BUILDING', 'hvac', 'EN'), ('BUILDING', 'interior design', 'EN'),
    ('BUILDING', 'school', 'EN'), ('BUILDING', 'university', 'EN'),
    ('BUILDING', 'hotel', 'EN'), ('BUILDING', 'residential', 'EN'),
    ('BUILDING', 'housing', 'EN'), ('BUILDING', 'office', 'EN'),
    ('BUILDING', 'warehouse', 'EN'),
    ('BUILDING', 'مبنى', 'AR'), ('BUILDING', 'مباني', 'AR'),
    ('BUILDING', 'معماري', 'AR'), ('BUILDING', 'تصميم معماري', 'AR'),
    ('BUILDING', 'هندسة معمارية', 'AR'), ('BUILDING', 'إنشائي', 'AR'),
    ('BUILDING', 'ميكانيكا', 'AR'), ('BUILDING', 'كهرباء', 'AR'),
    ('BUILDING', 'تكييف', 'AR'), ('BUILDING', 'مدرسة', 'AR'),
    ('BUILDING', 'جامعة', 'AR'), ('BUILDING', 'فندق', 'AR'),
    ('BUILDING', 'سكني', 'AR'), ('BUILDING', 'إسكان', 'AR'),
    ('BUILDING', 'مسجد', 'AR'), ('BUILDING', 'مستودع', 'AR'),
    # INFRA — roads, bridges, water, wastewater, transport, utilities
    ('INFRA', 'infrastructure', 'EN'), ('INFRA', 'road', 'EN'),
    ('INFRA', 'highway', 'EN'), ('INFRA', 'bridge', 'EN'),
    ('INFRA', 'tunnel', 'EN'), ('INFRA', 'dam', 'EN'),
    ('INFRA', 'utility', 'EN'), ('INFRA', 'sewer', 'EN'),
    ('INFRA', 'water supply', 'EN'), ('INFRA', 'wastewater', 'EN'),
    ('INFRA', 'drainage', 'EN'), ('INFRA', 'irrigation', 'EN'),
    ('INFRA', 'transport', 'EN'), ('INFRA', 'metro', 'EN'),
    ('INFRA', 'railway', 'EN'), ('INFRA', 'airport', 'EN'),
    ('INFRA', 'port', 'EN'), ('INFRA', 'pavement', 'EN'),
    ('INFRA', 'power', 'EN'),
    ('INFRA', 'بنية تحتية', 'AR'), ('INFRA', 'طرق', 'AR'),
    ('INFRA', 'جسور', 'AR'), ('INFRA', 'أنفاق', 'AR'),
    ('INFRA', 'سد', 'AR'), ('INFRA', 'صرف صحي', 'AR'),
    ('INFRA', 'مياه', 'AR'), ('INFRA', 'شبكات', 'AR'),
    ('INFRA', 'ري', 'AR'), ('INFRA', 'مواصلات', 'AR'),
    ('INFRA', 'نقل', 'AR'), ('INFRA', 'مطار', 'AR'),
    ('INFRA', 'قطار', 'AR'), ('INFRA', 'رصف', 'AR'),
    # HEALTH — hospitals, clinics, medical facilities
    ('HEALTH', 'health', 'EN'), ('HEALTH', 'hospital', 'EN'),
    ('HEALTH', 'clinic', 'EN'), ('HEALTH', 'medical', 'EN'),
    ('HEALTH', 'laboratory', 'EN'), ('HEALTH', 'healthcare', 'EN'),
    ('HEALTH', 'wellness', 'EN'),
    ('HEALTH', 'صحي', 'AR'), ('HEALTH', 'مستشفى', 'AR'),
    ('HEALTH', 'عيادة', 'AR'), ('HEALTH', 'طبي', 'AR'),
    ('HEALTH', 'مختبر', 'AR'), ('HEALTH', 'مرافق صحية', 'AR'),
]


class Command(BaseCommand):
    help = 'Seed database with countries, sources, URLs, and keywords'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Clear all keywords before re-seeding')
        parser.add_argument('--keywords-only', action='store_true',
                            help='Only seed keywords, skip countries and sources')

    @transaction.atomic
    def handle(self, *args, **options):
        from tenders.models import (
            CountryConfig, ScraperSource, TenderingURL,
            RelevanceKeyword, CategoryKeyword
        )

        if options['reset']:
            self.stdout.write('🗑  Clearing keywords...')
            RelevanceKeyword.objects.all().delete()
            CategoryKeyword.objects.all().delete()

        if not options['keywords_only']:
            self._seed_countries(CountryConfig)
            self._seed_sources(CountryConfig, ScraperSource, TenderingURL)

        self._seed_keywords(RelevanceKeyword, CategoryKeyword)

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Seeding complete!\n'
            f'  Countries:           {CountryConfig.objects.count()}\n'
            f'  Sources:             {ScraperSource.objects.count()}\n'
            f'  Tendering URLs:      {TenderingURL.objects.count()}\n'
            f'  Relevance keywords:  {RelevanceKeyword.objects.count()}\n'
            f'  Category keywords:   {CategoryKeyword.objects.count()}\n'
        ))

        # Auto-register the 6-hour Celery Beat schedule
        try:
            from django.core.management import call_command
            call_command('setup_schedule', hours=6)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(
                f'  Note: setup_schedule skipped ({exc}). '
                'Run manually: python manage.py setup_schedule'
            ))

    def _seed_countries(self, Model):
        self.stdout.write('\n🌍 Seeding countries...')
        _ORG_CODES = {'WB', 'UN', 'AB', 'IB', 'EU', 'AD', 'BA', 'ZZ'}
        for code, name_en, name_ar, flag, currency, order in COUNTRIES:
            obj, created = Model.objects.update_or_create(
                code=code,
                defaults={
                    'name_en': name_en, 'name_ar': name_ar,
                    'flag_emoji': flag, 'currency': currency,
                    'sort_order': order, 'is_active': True,
                    'is_organization': code in _ORG_CODES,
                }
            )
            self.stdout.write(f'  {"+" if created else "~"} {flag} {name_en}')

    def _seed_sources(self, CountryModel, SourceModel, URLModel):
        self.stdout.write('\n🏛️  Seeding sources and URLs...')
        for s in SOURCES:
            try:
                country = CountryModel.objects.get(code=s['country'])
            except CountryModel.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'  ✗ Country {s["country"]} not found'))
                continue

            source, created = SourceModel.objects.update_or_create(
                name=s['name'],
                defaults={
                    'name_ar':              s.get('name_ar', ''),
                    'country':              country,
                    'base_url':             s['base_url'],
                    'scraper_class':        s['scraper_class'],
                    'description':          s.get('description', ''),
                    'description_ar':       s.get('description_ar', ''),
                    'is_active':            True,
                }
            )
            self.stdout.write(f'  {"+" if created else "~"} {country.flag_emoji} {s["name"]}')

            for u in s.get('urls', []):
                url_obj, u_created = URLModel.objects.update_or_create(
                    source=source, url=u['url'],
                    defaults={
                        'label':            u['label'],
                        'label_ar':         u.get('label_ar', ''),
                        'pagination_style': u.get('pagination_style', 'QUERY'),
                        'page_param':       u.get('page_param', 'page'),
                        'max_pages':        u.get('max_pages', 20),
                        'extra_params':     u.get('extra_params', {}),
                        'is_active':        True,
                        'sort_order':       u.get('sort_order', 0),
                    }
                )
                self.stdout.write(f'    {"+" if u_created else "~"} 🔗 {u["label"]}')

    def _seed_keywords(self, RelModel, CatModel):
        self.stdout.write('\n🔍 Seeding relevance keywords...')
        rel_new = 0
        for kw, lang, note in RELEVANCE_KEYWORDS:
            _, created = RelModel.objects.get_or_create(
                keyword=kw,
                defaults={'language': lang, 'note': note, 'is_active': True}
            )
            if created:
                rel_new += 1
        self.stdout.write(f'  + {rel_new} new relevance keywords')

        self.stdout.write('📐 Seeding category keywords...')
        cat_new = 0
        for cat, kw, lang in CATEGORY_KEYWORDS:
            _, created = CatModel.objects.get_or_create(
                category=cat, keyword=kw,
                defaults={'language': lang, 'is_active': True}
            )
            if created:
                cat_new += 1
        self.stdout.write(f'  + {cat_new} new category keywords')
