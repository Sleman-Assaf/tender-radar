# tenders/tests/test_aec_scope.py
"""
The AEC *display* rule: which tenders a listing page shows.

AJi sells الاشراف والتصميم — supervision, design and engineering consultancy —
and does not build. So a tender is relevant only when that scope could be
awarded to AJi ON ITS OWN, which makes this a two-part test:

  * POSITIVE — the title describes design / supervision / consultancy work;
  * VETO — unless that scope is welded into a construction contract
    (design-build, EPC, turnkey), where one bidder does both and a consultancy
    cannot take the design half.

Three properties are protected here, each easy to break by accident:

1. "Construction Supervision Consultancy" must MATCH — supervising somebody
   else's build is the core service, so "construction" there is not a veto.
2. "Design and construction of ..." must NOT match, and neither must a headline
   that merely contains the word "design".
3. `aec_display_q()` and `non_aec_display_q()` must partition the rows exactly.
   Overlap puts a tender on both the user page and the audit page; a gap makes
   it vanish from both.

Nothing here asserts anything about `is_aec` itself — that value belongs to the
classification service and this module never writes it.
"""
from datetime import date, timedelta

from django.test import TestCase, override_settings

from tenders.aec_scope import (aec_display_q, design_supervision_q,
                               non_aec_display_q)
from tenders.models import CountryConfig, ScraperSource, Tender

FUTURE = date.today() + timedelta(days=30)


def make(source, title, *, is_aec=False, title_ar='', ext=None):
    return Tender.objects.create(
        source=source, country=source.country,
        external_id=ext or f'ext-{abs(hash(title)) % 10**9}',
        source_url='https://example.com/t',
        title=title, title_ar=title_ar,
        is_aec=is_aec, is_aec_checked=True, is_relevant=True,
        status='OPEN', submission_deadline=FUTURE,
    )


class DesignSupervisionMatchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '', 'currency': 'JOD'})
        cls.src = ScraperSource.objects.create(
            name='T', country=jo, base_url='https://e.com',
            scraper_class='TestScraper')

    # ── should be caught: a design/supervision scope AJi could win alone ────
    AJI_WORK = [
        'Consultancy supervision services (15B) for roads and infrastructure',
        'Engineering services for the preparation of studies and designs',
        # "Construction Supervision" is supervising a contractor — AJi's core
        # service — so the word "construction" here must NOT trigger the veto.
        'PIU Support and Construction Supervision Consultancy Services',
        'Supervision of the rehabilitation of the airport road',
        'Detailed design for a new water network',
        'Design of a 200-bed hospital',
        'Architectural design services for a polyclinic',
        'Feasibility study for a district cooling plant',
    ]

    # ── should NOT be caught: design welded into a construction contract ────
    # A design-build / turnkey / EPC package is one award covering both, so a
    # consultancy cannot bid the design half. This is the distinction the
    # business drew: relevant only if the design scope is separable.
    BUNDLED_WITH_CONSTRUCTION = [
        'Design and construction of orthopaedic Clinic at Khoula Hospital',
        'Design and construction of a secondary school for girls',
        'Design & build of a wastewater treatment plant',
        'Design, supply and installation of fire alarm systems',
        'EPC contract for a 150MW solar plant',
        'Turnkey construction of a logistics hub',
        'Engineering, procurement and construction of a substation',
    ]

    # ── should NOT be caught: no design/supervision scope, or a stray word ──
    NOT_AJI_WORK = [
        'Emaar to relaunch Dubai Creek Tower with new design in three months',
        'Dubai Municipality invites bids for $8.2bn sewage tunnels project',
        'Supply of office furniture',
        'Riyadh Metro launches naming rights tender for five stations',
        'Maintenance services for the local marketing department',
        'Construction of access roads and asphalt reinstatement works',
    ]

    def test_aji_work_is_matched(self):
        for title in self.AJI_WORK:
            with self.subTest(title=title):
                make(self.src, title)
                self.assertTrue(
                    Tender.objects.filter(design_supervision_q(),
                                          title=title).exists(),
                    f'should match: {title}')

    def test_other_work_is_not_matched(self):
        for title in self.NOT_AJI_WORK:
            with self.subTest(title=title):
                make(self.src, title)
                self.assertFalse(
                    Tender.objects.filter(design_supervision_q(),
                                          title=title).exists(),
                    f'should NOT match: {title}')

    def test_design_bundled_with_construction_is_not_matched(self):
        for title in self.BUNDLED_WITH_CONSTRUCTION:
            with self.subTest(title=title):
                make(self.src, title)
                self.assertFalse(
                    Tender.objects.filter(design_supervision_q(),
                                          title=title).exists(),
                    f'bundled into construction, should NOT match: {title}')

    def test_arabic_design_bundled_with_execution_is_not_matched(self):
        for ar in ['تصميم وتنفيذ مبنى إداري',
                   'تصميم وإنشاء مدرسة',
                   'مشروع تسليم مفتاح لمحطة']:
            with self.subTest(ar=ar):
                t = make(self.src, 'placeholder ' + str(abs(hash(ar)))[:6],
                         title_ar=ar)
                self.assertFalse(
                    Tender.objects.filter(design_supervision_q(), pk=t.pk).exists(),
                    f'bundled into execution, should NOT match: {ar}')

    def test_arabic_supervision_and_design_are_matched(self):
        for ar in ['أعمال الإشراف على إعادة تأهيل الطريق',
                   'خدمات التصميم لمبنى إداري',
                   'تقديم خدمات استشارية لمشروع',
                   'الدراسات الهندسية الأساسية لمجمع']:
            with self.subTest(ar=ar):
                t = make(self.src, 'placeholder ' + ar[:8], title_ar=ar)
                self.assertTrue(
                    Tender.objects.filter(design_supervision_q(), pk=t.pk).exists(),
                    f'should match Arabic: {ar}')


class PartitionTests(TestCase):
    """The shown set and the audit set must tile the data exactly."""

    @classmethod
    def setUpTestData(cls):
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '', 'currency': 'JOD'})
        src = ScraperSource.objects.create(
            name='T2', country=jo, base_url='https://e.com',
            scraper_class='TestScraper2')
        # One of each interesting combination.
        make(src, 'Supervision of the ring road', is_aec=True,  ext='a')
        make(src, 'Design of a clinic', is_aec=False, ext='b')
        make(src, 'Supply of office furniture', is_aec=False, ext='c')
        make(src, 'Bulk earthworks package', is_aec=True,  ext='d')
        cls.total = Tender.objects.count()

    def test_shown_and_audit_partition_the_rows(self):
        shown = Tender.objects.filter(aec_display_q()).count()
        audit = Tender.objects.filter(non_aec_display_q()).count()
        self.assertEqual(shown + audit, self.total,
                         'the two sets must tile the data — no gap, no overlap')

    def test_no_row_appears_in_both(self):
        shown = set(Tender.objects.filter(aec_display_q()).values_list('pk', flat=True))
        audit = set(Tender.objects.filter(non_aec_display_q()).values_list('pk', flat=True))
        self.assertEqual(shown & audit, set())

    def test_service_flagged_rows_are_always_shown(self):
        # Widening must never REMOVE something the service called AEC.
        for t in Tender.objects.filter(is_aec=True):
            self.assertIn(t.pk,
                          set(Tender.objects.filter(aec_display_q())
                              .values_list('pk', flat=True)),
                          f'service said AEC but the page hides it: {t.title}')

    def test_design_tender_is_shown_despite_non_aec_flag(self):
        t = Tender.objects.get(external_id='b')
        self.assertFalse(t.is_aec)                     # service's answer is kept
        self.assertTrue(Tender.objects.filter(aec_display_q(), pk=t.pk).exists())

    def test_bundled_design_build_stays_hidden(self):
        # The case the business called out: one award covering design AND
        # construction is not a design commission.
        src = ScraperSource.objects.get(scraper_class='TestScraper2')
        t = make(src, 'Design and construction of a clinic', is_aec=False, ext='e')
        self.assertFalse(Tender.objects.filter(aec_display_q(), pk=t.pk).exists())
        self.assertTrue(Tender.objects.filter(non_aec_display_q(), pk=t.pk).exists())

    @override_settings(AEC_INCLUDE_DESIGN_SUPERVISION=False)
    def test_setting_off_reverts_to_the_service_answer(self):
        shown = set(Tender.objects.filter(aec_display_q())
                    .values_list('external_id', flat=True))
        self.assertEqual(shown, {'a', 'd'})            # exactly is_aec=True


class AfricaScopeTests(TestCase):
    """
    Africa gets a narrower rule: design and desk consultancy, but NOT
    supervision — it needs people on site and AJi has no African presence.

    The deciding case is a notice worded "Consultancy Services FOR Supervision
    of ...". The word consultancy is present but the work is supervision, so it
    must lose — unless design is also in scope, which makes it a design
    commission worth having.
    """

    @classmethod
    def setUpTestData(cls):
        ke, _ = CountryConfig.objects.get_or_create(
            code='KE', defaults={'name_en': 'Kenya', 'name_ar': 'كينيا',
                                 'flag_emoji': '', 'currency': 'KES'})
        jo, _ = CountryConfig.objects.get_or_create(
            code='JO', defaults={'name_en': 'Jordan', 'name_ar': 'الأردن',
                                 'flag_emoji': '', 'currency': 'JOD'})
        cls.af_src = ScraperSource.objects.create(
            name='AF', country=ke, base_url='https://e.com',
            scraper_class='MAGAZINE_AFRICA_T')
        cls.me_src = ScraperSource.objects.create(
            name='ME', country=jo, base_url='https://e.com',
            scraper_class='MAGAZINE_ME_T')

    def _shown(self, source, title, is_aec=False):
        t = make(source, title, is_aec=is_aec,
                 ext=f'x-{abs(hash(title)) % 10**9}')
        return Tender.objects.filter(aec_display_q(), pk=t.pk).exists()

    # ── Africa: design is welcome, with or without supervision alongside ────
    def test_african_design_is_shown(self):
        for title in [
            'EOI - Kenya - Consultancy Firm Services for Design, Compliance and Supervision',
            'EOI - Kenya - Design Review, Preparation of Tender Document and Supervision',
            'EOI - Kenya - Detailed design of a water network',
        ]:
            with self.subTest(title=title):
                self.assertTrue(self._shown(self.af_src, title), title)

    # ── Africa: desk consultancy with no supervision is welcome ─────────────
    def test_african_desk_consultancy_is_shown(self):
        for title in [
            'EOI - Kenya - Feasibility study for a water supply scheme',
            'EOI - Kenya - Technical audit of the road maintenance programme',
        ]:
            with self.subTest(title=title):
                self.assertTrue(self._shown(self.af_src, title), title)

    # ── Africa: supervision without design loses, however it is worded ─────
    def test_african_supervision_is_hidden(self):
        for title in [
            'EOI - Kenya - Construction Supervision of Busega Systems Interchange',
            'EOI - Kenya - Consultancy Supervision Services for Rural Electricity',
            'EOI - Kenya - Construction Supervision & Contract Management Consultancy',
            'EOI - Kenya - Supervision of Kapichira Phase I Rehabilitation',
            'AMI - Kenya - consultant charge du controle et la surveillance des travaux',
        ]:
            with self.subTest(title=title):
                self.assertFalse(self._shown(self.af_src, title), title)

    def test_african_supervision_hidden_even_when_service_says_aec(self):
        # The classification service knows nothing about the site-presence
        # constraint, so its is_aec flag must not be a blanket pass in Africa —
        # otherwise supervision returns through the side door.
        self.assertFalse(
            self._shown(self.af_src,
                        'EOI - Kenya - Construction Supervision of a road',
                        is_aec=True))

    def test_african_construction_and_supply_are_hidden(self):
        for title in ['EOI - Kenya - Construction of a rural water network',
                      'EOI - Kenya - Supply of laboratory equipment']:
            with self.subTest(title=title):
                self.assertFalse(self._shown(self.af_src, title), title)

    # ── The Middle East rule is unchanged ──────────────────────────────────
    def test_middle_east_supervision_is_still_shown(self):
        self.assertTrue(self._shown(
            self.me_src,
            'Consultancy supervision services for roads and infrastructure'))

    def test_middle_east_still_honours_the_service_flag(self):
        self.assertTrue(self._shown(self.me_src, 'Bulk earthworks package',
                                    is_aec=True))

    # ── The country list is configurable ───────────────────────────────────
    @override_settings(AEC_AFRICA_COUNTRIES=[])
    def test_empty_override_falls_back_to_the_built_in_list(self):
        from tenders.aec_scope import AFRICA_COUNTRY_CODES, africa_codes
        self.assertEqual(africa_codes(), AFRICA_COUNTRY_CODES)

    @override_settings(AEC_AFRICA_COUNTRIES=['ke'])
    def test_override_narrows_the_africa_rule(self):
        from tenders.aec_scope import africa_codes
        self.assertEqual(africa_codes(), frozenset({'KE'}))
        # Egypt is no longer "Africa", so Egyptian supervision is shown again.
        eg, _ = CountryConfig.objects.get_or_create(
            code='EG', defaults={'name_en': 'Egypt', 'name_ar': 'مصر',
                                 'flag_emoji': '', 'currency': 'EGP'})
        src = ScraperSource.objects.create(
            name='EG', country=eg, base_url='https://e.com',
            scraper_class='MAGAZINE_EG_T')
        self.assertTrue(self._shown(src, 'Construction supervision services for a road'))

    # ── The partition still holds with two regional rules in play ──────────
    def test_shown_and_audit_still_partition_exactly(self):
        for src in (self.af_src, self.me_src):
            make(src, 'Construction Supervision of a road',
                 ext=f'p1-{src.pk}')
            make(src, 'Design of a clinic', ext=f'p2-{src.pk}')
            make(src, 'Supply of furniture', ext=f'p3-{src.pk}')
        total = Tender.objects.count()
        shown = Tender.objects.filter(aec_display_q()).count()
        audit = Tender.objects.filter(non_aec_display_q()).count()
        self.assertEqual(shown + audit, total,
                         'no tender may fall in both sets or neither')


class DisplayRuleGapTests(TestCase):
    """
    Three gaps found by auditing the Non-AEC page row by row.

    All three hid real design or supervision commissions. None of them was a
    classification problem — aec_scope.py decides only what a page SHOWS and
    never writes is_aec — which is why they were invisible: the classifier had
    already said its piece and the display rule quietly overrode it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sa = CountryConfig.objects.get_or_create(
            code='SA', defaults={'name_en': 'Saudi Arabia', 'name_ar': 'السعودية'})[0]
        cls.qa = CountryConfig.objects.get_or_create(
            code='QA', defaults={'name_en': 'Qatar', 'name_ar': 'قطر'})[0]
        cls.eg = CountryConfig.objects.get_or_create(
            code='EG', defaults={'name_en': 'Egypt', 'name_ar': 'مصر'})[0]
        cls.src = ScraperSource.objects.create(
            name='t', scraper_class='TestScraper', country=cls.sa,
            base_url='https://e.com', is_active=False)

    def _shown(self, title, country=None, is_aec=False):
        t = Tender.objects.create(
            source=self.src, country=country or self.sa,
            external_id=f'g-{title[:40]}', source_url='https://e.com/t',
            title=title, is_aec=is_aec, is_aec_checked=True, is_relevant=True,
            status='OPEN', submission_deadline=date.today() + timedelta(days=30))
        return Tender.objects.filter(pk=t.pk).filter(aec_display_q()).exists()

    # ── 1. a supervision commission names its instrument ────────────────────
    def test_a_supervision_agreement_is_shown(self):
        # AJi's own contract was on the Non-AEC page: the rule accepted
        # supervision "of/for/services" but not the instrument it arrives as.
        self.assertTrue(self._shown(
            'AJi Signs Supervision Agreement For Miraf District With Refad'))

    def test_supervision_contract_and_role_too(self):
        for title in ('Dorsch Wins Construction Supervision Role for Expo 2030',
                      'Firm awarded supervision contract for the metro'):
            with self.subTest(title=title[:40]):
                self.assertTrue(self._shown(title))

    # ── 2. a separable service beats the design-build veto ──────────────────
    def test_consultancy_services_for_a_design_build_are_shown(self):
        # The scope being BOUGHT is the consultancy; the design-build is what
        # it covers. The veto used to fire on "design and build" and hide it.
        self.assertTrue(self._shown(
            'Post-contract professional consultancy services for the design '
            'and build of a water treatment plant', country=self.qa))

    def test_a_bare_design_build_is_still_hidden(self):
        # The business's own example, and the reason the veto exists at all.
        for title in ('Design and construction of orthopaedic Clinic at Khoula',
                      'Design and Build of Dhow Section Project',
                      'Design and Construction of Pedestrian Crossings Phase 4'):
            with self.subTest(title=title[:40]):
                self.assertFalse(self._shown(title, country=self.qa))

    def test_supervision_of_a_design_build_is_shown(self):
        self.assertTrue(self._shown(
            'Supervision for the Design and Build of Dhow Section Project',
            country=self.qa))

    # ── 3. masterplanning is design ─────────────────────────────────────────
    def test_a_masterplan_counts_as_design(self):
        self.assertTrue(self._shown('SOM unveil Alam Al Roum masterplan'))

    def test_a_masterplan_counts_in_africa_too(self):
        # Africa is design-only, so a missing masterplan rule excluded real
        # design commissions in Egypt and Kenya even with is_aec=True.
        self.assertTrue(self._shown(
            'Qatari Diar and SOM unveil Alam Al Roum masterplan',
            country=self.eg, is_aec=True))
        self.assertTrue(self._shown(
            'JOOTRH unveils Medicity Master Plan to expand healthcare',
            country=self.eg, is_aec=True))

    def test_africa_still_excludes_supervision_only(self):
        # Unchanged and deliberate: AJi has no site presence in Africa.
        self.assertFalse(self._shown(
            'Egis Awarded Contract by National Authority for Tunnels to '
            'Supervise Highway Works', country=self.eg, is_aec=True))
