# magazine_tenders/tests/test_arabic_rules.py
"""
The Arabic side of the keep-filter.

Every headline below is a real one, taken from 504 articles read off Al-Rai's
اقتصاد and محليات sections on 21 September 2026. They are here because Arabic
carries a set of false friends that a word list cannot survive: the same word
means the thing we want and a thing we do not.

    تصميم   design            AND  resolve, determination
    مشروع   project           AND  draft (مشروع قانون - a bill)
    تأهيل   rehabilitation    AND  training people
    محطة    plant, station    AND  stage, leg of a journey
    تنفيذ   execution of work AND  حيز التنفيذ - "comes into effect"

So the rule is the same one the English side already uses: an EVENT happening
to a piece of BUILT WORK, in the same sentence, with a veto list. A bare word
match is not enough and these tests are what say so.
"""
from django.test import TestCase

from magazine_tenders.scrapers._newsfeed import why_keep


class ArabicFalseFriendTests(TestCase):
    """The words that mean two things. Each of these must be refused."""

    def test_tasmim_meaning_resolve_is_not_a_design_commission(self):
        # The Prime Minister saying "our RESOLVE is stronger".
        self.assertEqual(why_keep(
            'حسّان من معان: أزمات المنطقة قد تطول لأشهر أو سنوات.. وتصميمنا أقوى'
        ), '')

    def test_mashrou_qanoun_is_a_bill_not_a_project(self):
        self.assertEqual(why_keep(
            'مجلس النواب يقر مشروع قانون الموازنة العامة لسنة 2027'
        ), '')

    def test_hayyiz_al_tanfith_is_coming_into_effect(self):
        self.assertEqual(why_keep(
            'رفع أسعار الفائدة 25 نقطة أساس يدخل حيز التنفيذ اليوم'
        ), '')

    def test_mahatta_meaning_stage_is_not_a_plant(self):
        self.assertEqual(why_keep(
            'إطلاق المحطة الرابعة من مشروع "أردننا في عيوننا.. من الهواية إلى الاحتراف"'
        ), '')

    def test_tahil_meaning_training_is_not_rehabilitation_works(self):
        self.assertEqual(why_keep(
            '"السياحة": 49 برنامجا تدريبيا لتأهيل الكفاءات في القطاع'
        ), '')

    def test_ishraf_meaning_regulatory_oversight_is_not_site_supervision(self):
        # "under the SUPERVISION of the Central Bank" - a regulator, not a
        # resident engineer. This one reached the News page as DESIGN.
        self.assertEqual(why_keep(
            'كوين مينا تصبح أول منصة للعملات الرقمية تنطلق ضمن المختبر '
            'التنظيمي التابع للبنك المركزي. تتيح المنصة للمستخدمين في الأردن '
            'إمكانية الإيداع بالدينار الأردني ضمن إطار تنظيمي خاضع لإشراف '
            'البنك المركزي الأردني.'
        ), '')

    def test_ishraf_by_a_ministry_over_a_sector_is_not_site_supervision(self):
        self.assertEqual(why_keep(
            'هيئة تنظيم قطاع الاتصالات تعمل تحت إشراف وزارة الاقتصاد الرقمي'
        ), '')


class ArabicMoneyDeskTests(TestCase):
    """What the economy section actually publishes. None of it is engineering."""

    NOISE = [
        'انخفاض أسعار الذهب محليا الاثنين',
        'الذهب يهبط مع استمرار التركيز على الشرق الأوسط وأسعار الفائدة',
        'عضو بالمركزي الأمريكي: التضخم لا يزال مرتفعا بجميع "جوانب" الاقتصاد',
        'روسيا: ارتفاع عجز الموازنة إلى 70 مليار دولار خلال 8 أشهر',
        'ارتفاع صادرات الغاز الطبيعي الروسي إلى الصين 28%',
        'جمعية البنوك: رفع أسعار الفائدة لن ينعكس على أقساط قروض الأفراد',
    ]

    def test_money_desk_stories_are_refused(self):
        for headline in self.NOISE:
            with self.subTest(headline=headline):
                self.assertEqual(why_keep(headline), '')


class ArabicNotEngineeringTests(TestCase):
    """
    Stories that contain the word "project" but are not engineering work.

    All eight reached the News page on the first Al-Rai run, because bare
    مشروع was in the built-work list and "project" is what a minister, an
    economist and a progress report all say. The genuine schemes never needed
    it - مشروع الصرف الصحي matches on صرف صحي, the hospital on مستشفى - so
    the word came out and these are what say it must stay out.
    """

    def test_economic_impact_of_a_port_is_not_a_project(self):
        self.assertEqual(why_keep(
            '"الصناعة": الميناء البري في معان يخفف كلف الشحن والتخزين'), '')

    def test_a_minister_inspecting_and_signing_agreements(self):
        self.assertEqual(why_keep(
            'وزير الاستثمار يتفقد مشاريع قائمة، ويشهد توقيع اتفاقيات لثلاثة '
            'مشاريع استثمارية جديدة'), '')

    def test_a_programme_creating_jobs(self):
        self.assertEqual(why_keep(
            'حجازي: برامج "جيدكو" توفر 108 فرص عمل في الكرك'), '')

    def test_a_macro_announcement_of_project_value(self):
        self.assertEqual(why_keep(
            'الحكومة: مشاريع كبرى بقيمة 10 مليارات دولار بالشراكة مع القطاع الخاص'
        ), '')

    def test_a_bank_opening_an_office(self):
        self.assertEqual(why_keep(
            'البنك الأوروبي للتنمية يفتتح مكتبه الجديد في الأردن ويتطلع '
            'لمضاعفة استثماراته'), '')

    def test_a_count_of_development_projects(self):
        self.assertEqual(why_keep(
            'المومني: 120 مشروعاً تنموياً في محافظة معان بكلفة ربع مليار دينار'
        ), '')

    def test_a_progress_percentage(self):
        self.assertEqual(why_keep(
            'رئيس الوزراء: إنجاز أكثر من 81% من مشاريع وبرامج رؤية معان التنموية'
        ), '')

    def test_digital_infrastructure_is_not_civil_infrastructure(self):
        # البنية التحتية الرقمية - data centres and connectivity, not works.
        # This one reached the page on "infrastructure" + "investments".
        self.assertEqual(why_keep(
            'البنك الدولي: الأردن أمام فرص واعدة لتوسيع استخدام الذكاء '
            'الاصطناعي. يدخل الأردن عصر الذكاء الاصطناعي مدعوما باستثمارات '
            'في البنية التحتية الرقمية والحكومة والخدمات الرقمية.'
        ), '')


class ArabicCommodityTenderTests(TestCase):
    """A tender is only ours when it is for built work."""

    def test_a_wheat_purchase_tender_is_refused(self):
        self.assertEqual(why_keep(
            'الصناعة والتجارة تعيد طرح عطاء لشراء كميات من القمح'), '')

    def test_a_waste_collection_contract_is_refused(self):
        self.assertEqual(why_keep(
            'أمانة عمّان: 3 شركات تتولى جمع ونقل النفايات في عمّان ضمن مرحلة تجريبية'
        ), '')


class ArabicProjectNewsTests(TestCase):
    """An event happening to a piece of built work. These must be kept."""

    def test_an_opening_is_too_late_to_be_useful(self):
        # This asserted PROJECT until 22 September. It is real construction
        # news and it is worth nothing to a design and supervision practice:
        # by the ribbon-cutting the designer was appointed years ago. The
        # test the business actually wants is "is procurement still open",
        # not "is this about a building".
        self.assertEqual(why_keep(
            'رئيس الوزراء يفتتح مشروع التوسعة الجديد لمستشفى معان الحكومي'
        ), '')

    def test_starting_a_sewerage_scheme(self):
        self.assertEqual(why_keep(
            'النائب الجراح: البدء بالخطوة الأولى لمشروع الصرف الصحي في المزار الشمالي'
        ), 'PROJECT')

    def test_launching_a_gas_connection_to_an_industrial_city(self):
        self.assertEqual(why_keep(
            'رئيس الوزراء يطلق مشروع إيصال الغاز الطبيعي إلى مدينة الروضة الصناعية في معان'
        ), 'PROJECT')

    def test_a_road_scheme_actually_put_out_to_tender(self):
        self.assertEqual(why_keep(
            'طرح عطاء لإنشاء جسر جديد على طريق وادي جرون بالمزار الشمالي'
        ), 'TENDER')


class ArabicEngineeringInvestmentTests(TestCase):
    """Investment in the engineering sector - the reader asked for these."""

    def test_investment_opportunity_to_build_a_hotel(self):
        self.assertEqual(why_keep(
            'فرصة استثمارية لإنشاء فندق 4 نجوم في متنزه عجلون'
        ), 'PROJECT')

    def test_a_power_plant_is_kept(self):
        self.assertEqual(why_keep(
            'تمويل دولي مرتقب لمحطة كهرباء جديدة بقدرة 700 ميغاواط في المفرق'
        ), 'PROJECT')

    def test_building_permits_reported_by_the_engineers_association(self):
        self.assertEqual(why_keep(
            '"المهندسين": 1.45 مليون متر مربع حجم تراخيص الأبنية في إربد'
        ), 'DESIGN')


class ArabicTenderTests(TestCase):
    """The existing Arabic tender route still wins where it applies."""

    def test_a_tender_for_four_schools_is_a_tender_not_a_project(self):
        self.assertEqual(why_keep(
            'طرح عطاءات لإنشاء 4 مدارس جديدة بقيمة نحو 8.7 مليون دينار'
        ), 'TENDER')


class EnglishRulesAreUnaffectedTests(TestCase):
    """
    The Arabic vocabulary must not change any English verdict. Arabic patterns
    cannot match Latin text, and this is the test that keeps it that way when
    somebody adds a rule in the wrong alternation.
    """

    def test_english_tender_still_kept(self):
        self.assertEqual(why_keep(
            'Ashghal issues tender for road upgrade in Doha'), 'TENDER')

    def test_english_project_news_still_kept(self):
        self.assertEqual(why_keep(
            'Dubai Healthcare City breaks ground on AED3 bn Dubai Creek Gardens'
        ), 'PROJECT')

    def test_english_industry_noise_still_refused(self):
        self.assertEqual(why_keep('Cement prices rise 4% across the GCC'), '')


class ArabicMeetingTests(TestCase):
    """Two bodies meeting to discuss cooperation is a diary item."""

    def test_engineers_association_discussing_cooperation(self):
        self.assertEqual(why_keep(
            '«المهندسين» وبلدية بني عبيد تبحثان تعزيز التعاون'), '')


class ArabicAcademicFundingTests(TestCase):
    """
    A university taking a research grant is not a building site.

    جامعة is legitimately built work — "توسعة جامعة اليرموك" is a real
    scheme — and تمويل is legitimately an event, so the two together kept a
    story about two universities winning money from the British Council.
    The veto is on the funder, which is why the expansion still passes.
    """

    def test_universities_winning_a_research_grant(self):
        self.assertEqual(why_keep(
            'عمان الأهلية وجامعة كوينز بلفاست تحصلان على تمويل '
            'من المجلس الثقافي البريطاني'), '')

    def test_building_work_at_a_university_is_still_kept(self):
        self.assertEqual(why_keep(
            'إحالة عطاء توسعة مباني جامعة اليرموك بقيمة 12 مليون دينار'
        ), 'TENDER')


class ArabicNotCommissionedTests(TestCase):
    """
    Nothing has been commissioned, so it is not our field.

    Four shapes reached the News page on 21 September and the reader
    rejected all four. Each one names real built work and carries a real
    verb, which is exactly why the event+object rule kept them; what none
    of them has is a contract, a tender or a scheme under way.

    One of these reverses an earlier judgement of mine: the Wadi Jurun
    road item used to have a test asserting it MUST be kept, on the reading
    that "completing the median island" is work. It is not — it is
    residents asking for work. That test is now the tender above it.
    """

    def test_residents_calling_for_road_works(self):
        self.assertEqual(why_keep(
            'دعوات لاستكمال الجزيرة الوسطية ومعالجة الحوادث '
            'على طريق وادي جرون بالمزار الشمالي'), '')

    def test_operating_a_traffic_signal_is_not_building_one(self):
        self.assertEqual(why_keep(
            'بلدية إربد: بدء تشغيل الإشارة الضوئية تجريبيا '
            'على تقاطع الثقافة اليوم'), '')

    def test_a_body_meeting_to_discuss_land_procedures(self):
        self.assertEqual(why_keep(
            '«التطوير الحضري» تبحث إجراءات تطوير أراضي مشروعي القطرانة ومحي. '
            'عقدت مدير عام المؤسسة العامة للإسكان والتطوير الحضري اجتماعاً '
            'في مبنى محافظة الكرك'), '')

    def test_an_intention_to_build_and_a_land_purchase(self):
        self.assertEqual(why_keep(
            'توجه لإنشاء مدرسة تقنية في إيدون لدعم التعليم المهني. '
            'طرحت المديرية إعلاناً لشراء واستملاك قطعة أرض '
            'بمساحة لا تقل عن 10 دونمات'), '')


class ArabicCommissionedWorkStillPassesTests(TestCase):
    """
    The guard on the four vetoes above.

    Each one is narrow on purpose. A veto that also refused a tender, an
    award or a ground-breaking would cost far more than the noise it saves,
    so these are the cases that must survive it.
    """

    def test_an_invitation_to_bid_is_not_a_public_demand(self):
        # دعوة (singular, an invitation to bid) is not دعوات (calls for).
        self.assertEqual(why_keep(
            'دعوة لتقديم العروض لإنشاء مدرسة جديدة في إربد'), 'TENDER')

    def test_a_school_tender_still_passes(self):
        self.assertEqual(why_keep(
            'طرح عطاءات لإنشاء 4 مدارس جديدة بقيمة نحو 8.7 مليون دينار'
        ), 'TENDER')

    def test_a_design_tender_for_a_hospital_still_passes(self):
        # The opening was the old guard here. An opening is now refused, so
        # what has to survive the vetoes is the procurement, not the ribbon.
        self.assertEqual(why_keep(
            'طرح عطاء التصاميم الهندسية لتوسعة مستشفى معان الحكومي'
        ), 'TENDER')

    def test_a_ground_breaking_is_too_late_as_well(self):
        # Asserted PROJECT until 22 September, and dropped by the reader's
        # own instruction. By the foundation stone the design is finished
        # and the supervision usually appointed with it.
        self.assertEqual(why_keep(
            'رئيس الوزراء يضع حجر الأساس لمشروع إيصال الغاز الطبيعي '
            'لمدينة الروضة الصناعية'), '')

    def test_financing_a_power_plant_still_passes(self):
        self.assertEqual(why_keep(
            'تمويل دولي مرتقب لمحطة كهرباء جديدة بقدرة 700 ميغاواط في المفرق'
        ), 'PROJECT')

    def test_a_design_tender_still_passes(self):
        self.assertEqual(why_keep(
            'طرح عطاء إعداد الدراسات والتصاميم الهندسية الشاملة '
            'لإنشاء مشروع شبكة الصرف الصحي'), 'TENDER')


class ArabicActivityAtAPlaceTests(TestCase):
    """
    An activity held AT a building is not work ON it.

    A school, a college, a university and a hospital are all genuine built
    work, and إطلاق / انطلاق are genuine events, so "launching a training
    scheme at a college" and "a health week at Aqaba's schools" both read as
    construction. Both reached the News page from Petra on 22 September.

    The veto is on the ACTIVITY, never on the building and never on the
    verb, so a training centre being built still passes.
    """

    def test_a_training_scheme_at_a_college(self):
        self.assertEqual(why_keep(
            'إطلاق منظومة تدريب ميداني في كلية الملكة رانيا للطفولة بالهاشمية'
        ), '')

    def test_a_health_week_at_schools(self):
        self.assertEqual(why_keep(
            'انطلاق فعاليات أسبوع مدارس العقبة الصحي'), '')

    def test_a_festival_and_a_seminar_and_a_workshop(self):
        for headline in ('مهرجان الأردن الثقافي في جرش',
                         'ندوة حول مستقبل قطاع الإسكان في عمان',
                         'ورشة عمل حول إدارة المستشفيات الحكومية'):
            with self.subTest(headline=headline):
                self.assertEqual(why_keep(headline), '')

    def test_building_a_training_centre_still_passes(self):
        # The veto must not reach the building. "مركز تدريب" is real work.
        self.assertEqual(why_keep(
            'إحالة عطاء إنشاء مركز تدريب مهني في الزرقاء بقيمة 3 ملايين دينار'
        ), 'TENDER')

    def test_the_hospital_agreement_still_passes(self):
        self.assertEqual(why_keep(
            'اتفاقية لإنشاء مستشفى ميراس بسعة 60 سريرا في الزرقاء'), 'PROJECT')


class ArabicGeneralNewsTests(TestCase):
    """
    What a pan-Arab general news site throws at the filter.

    Al Arabiya covers politics, sport, cars and crime as well as business,
    so its false positives are a different shape from a Jordanian economy
    desk's. All three below reached the page on the first run.
    """

    def test_treasure_found_in_a_factory_wall(self):
        # جدار and مصنع are both built work and العثور is an event. It is a
        # crime story.
        self.assertEqual(why_keep(
            'طالب يعثر على كنز ذهبي بقيمة 10 ملايين دولار مخبأً في جدار مصنع'
        ), '')

    def test_an_airline_launching_a_route_is_not_building_an_airport(self):
        self.assertEqual(why_keep(
            '"تحالف العربية للطيران" يطلق أولى رحلاته من مطار الملك فهد بالدمام'
        ), '')

    def test_the_engineers_syndicate_commenting_on_admissions(self):
        # نقابة المهندسين alone has now kept three non-engineering stories:
        # a cooperation meeting, a research grant and this.
        self.assertEqual(why_keep(
            'نقابة المهندسين في مصر: قبول طلاب بمجاميع منخفضة يهدد المهنة'
        ), '')

    def test_a_school_complex_opening_is_refused(self):
        # Al Arabiya's only keep on 22 September, and an opening: the work
        # is done and there is nothing left to bid for.
        self.assertEqual(why_keep(
            'افتتاح مجمع مدارس شروق المملكة العالمية بالطائف على مساحة 8300 متر مربع'
        ), '')

    def test_an_airport_contract_is_still_kept(self):
        self.assertEqual(why_keep(
            'إحالة عطاء توسعة مطار الملك فهد الدولي بقيمة 400 مليون ريال'
        ), 'TENDER')

    def test_the_engineers_association_on_building_permits_is_still_kept(self):
        self.assertEqual(why_keep(
            '"المهندسين": 1.45 مليون متر مربع حجم تراخيص الأبنية في إربد'
        ), 'DESIGN')


class ProcurementMustStillBeOpenTests(TestCase):
    """
    The test the business actually needs.

    AJi is a DESIGN AND SUPERVISION practice. What matters is not whether a
    story is about construction — it is whether there is anything left to
    bid for. A ribbon-cutting is construction news worth nothing: the
    designer was appointed years earlier.

    So the filter now asks about the STAGE. Adopted 22 September 2026 on the
    reader's instruction, after scoring a day's output and finding half of
    it was announcements of work already finished or under way.
    """

    # Nothing left to win.
    TOO_LATE = [
        'رئيس الوزراء يفتتح مشروع التوسعة الجديد لمستشفى معان الحكومي',
        'افتتاح مجمع مدارس شروق المملكة العالمية بالطائف على مساحة 8300 متر مربع',
        'رئيس الوزراء يضع حجر الأساس لمشروع إيصال الغاز الطبيعي للروضة الصناعية',
        'بلدية إربد: بدء تشغيل الإشارة الضوئية على تقاطع الثقافة',
        'اكتمال أعمال توسعة مستشفى الزرقاء الحكومي',
    ]

    # Commentary on a scheme already under way: no procurement event at all.
    JUST_COMMENTARY = [
        '«تطوير معان»: مشروع تزويد الروضة الصناعية بالغاز يُخفّض كلف الطاقة',
        'أبو تايه: تزويد الروضة الصناعية بمعان بالغاز الطبيعي يعزز الاستثمارات',
        'وزير المياه: مشروع الناقل الوطني يساهم في تأمين حصة الفرد من المياه',
    ]

    # Procurement still ahead. These are the page.
    STILL_OPEN = [
        ('طرح عطاء إعداد الدراسات والتصاميم الهندسية الشاملة '
         'لإنشاء مشروع شبكة الصرف الصحي', 'TENDER'),
        ('طرح عطاءات لإنشاء 4 مدارس جديدة بقيمة نحو 8.7 مليون دينار', 'TENDER'),
        ('إحالة عطاء الإشراف على تنفيذ أعمال توسعة مطار الملك فهد', 'TENDER'),
        ('فرصة استثمارية لإنشاء فندق 4 نجوم في متنزه عجلون', 'PROJECT'),
        ('اتفاقية لإنشاء مستشفى ميراس بسعة 60 سريرا في الزرقاء', 'PROJECT'),
    ]

    def test_finished_work_is_refused(self):
        for headline in self.TOO_LATE:
            with self.subTest(headline=headline[:44]):
                self.assertEqual(why_keep(headline), '')

    def test_commentary_on_existing_work_is_refused(self):
        for headline in self.JUST_COMMENTARY:
            with self.subTest(headline=headline[:44]):
                self.assertEqual(why_keep(headline), '')

    def test_open_procurement_is_kept(self):
        for headline, verdict in self.STILL_OPEN:
            with self.subTest(headline=headline[:44]):
                self.assertEqual(why_keep(headline), verdict)

    def test_our_own_scope_named_directly_is_kept(self):
        # Design and supervision tenders are the top of the list. If the
        # stage rule ever refuses one of these it has gone wrong.
        for headline in ('طرح عطاء الخدمات الاستشارية الهندسية لمشروع الصرف الصحي',
                         'إحالة عطاء التصاميم التفصيلية لمبنى المحكمة الجديد',
                         'طرح عطاء الإشراف على إنشاء 4 مدارس في البلقاء'):
            with self.subTest(headline=headline[:44]):
                self.assertEqual(why_keep(headline), 'TENDER')

    def test_english_openings_are_unaffected(self):
        # The stage rule is Arabic-only for now; the English sources keep
        # the behaviour they had. Worth knowing before anybody assumes the
        # page is uniform.
        self.assertEqual(why_keep(
            'Ashghal issues tender for road upgrade in Doha'), 'TENDER')


class TashkeelDoesNotDodgeTheRulesTests(TestCase):
    """
    Vowel marks must not be a way round the filter.

    Every Arabic pattern in _newsfeed.py is written bare, and Ad-Dustour
    writes يُخفّض where Al-Rai writes يخفض. Untouched, a vowelled headline
    walked straight past a veto — which means a vowelled headline could walk
    past any of the thirty-odd rules. Stripped once, at the top of why_keep.
    """

    def test_a_vowelled_veto_word_is_still_vetoed(self):
        self.assertEqual(why_keep(
            '«تطوير معان»: مشروع تزويد الروضة الصناعية بالغاز يُخفّض كلف الطاقة'
        ), '')

    def test_a_vowelled_opening_is_still_refused(self):
        self.assertEqual(why_keep(
            'رئيس الوزراء يفتتح مشروع التوسعة الجديد لمستشفى مَعان الحكوميّ'), '')

    def test_a_vowelled_tender_is_still_kept(self):
        self.assertEqual(why_keep(
            'طرح عطاءات لإنشاء 4 مدارس جديدة بقيمة نحو 8.7 مليون دينارٍ'
        ), 'TENDER')


class BuildAndOperateIsStillWorkTests(TestCase):
    """
    «إنشاء وتشغيل» is a build-and-operate opportunity, not an opening.

    The first version of the stage rule vetoed تشغيل outright, to catch
    "bringing a traffic signal into service". It also caught the Ajloun
    hotel — «فرصة استثمارية لإنشاء وتشغيل فندق» — which is a concession
    being offered, with no designer appointed. Operating is only a
    disqualifier when it is the START of operation of finished work.
    """

    def test_a_build_and_operate_concession_is_kept(self):
        self.assertEqual(why_keep(
            'طرحت المجموعة الأردنية للمناطق الحرة والمناطق التنموية فرصة '
            'استثمارية لإنشاء وتشغيل فندق من فئة 4 نجوم في متنزه عجلون الوطني'
        ), 'PROJECT')

    def test_bringing_finished_work_into_service_is_still_refused(self):
        self.assertEqual(why_keep(
            'بلدية إربد: بدء تشغيل الإشارة الضوئية تجريبيا على تقاطع الثقافة'
        ), '')

    def test_a_trial_operation_of_a_plant_is_still_refused(self):
        self.assertEqual(why_keep(
            'بدء التشغيل التجريبي لمحطة تنقية مياه الزرقاء'), '')


class ViolenceIsNotConstructionTests(TestCase):
    """
    إطلاق نار is gunfire, not a project launch.

    Petra's wire carries crime and conflict alongside everything else, and
    «8 إصابات بإطلاق نار أمام مدرسة في تركيا» was kept as a PROJECT: إطلاق
    is a launch verb and مدرسة is built work, so the event+object rule fired
    on a school shooting. The building is only the place it happened.

    This is the same family as the treasure found in a factory wall — a
    general news desk puts real buildings in stories that have nothing to do
    with building them.
    """

    def test_a_school_shooting_is_not_a_project(self):
        self.assertEqual(why_keep(
            '8 إصابات بإطلاق نار أمام مدرسة في تركيا'), '')

    def test_casualties_at_a_hospital_are_not_a_project(self):
        self.assertEqual(why_keep(
            'مقتل 3 وإصابة 12 في انفجار قرب مستشفى ميداني'), '')

    def test_an_assault_at_a_station_is_not_a_project(self):
        self.assertEqual(why_keep(
            'اعتداء على موظفين في محطة الحافلات المركزية'), '')

    def test_a_school_tender_is_still_kept(self):
        self.assertEqual(why_keep(
            'طرح عطاءات لإنشاء 4 مدارس جديدة بقيمة نحو 8.7 مليون دينار'
        ), 'TENDER')

    def test_a_hospital_agreement_is_still_kept(self):
        self.assertEqual(why_keep(
            'اتفاقية لإنشاء مستشفى ميراس بسعة 60 سريرا في الزرقاء'), 'PROJECT')
