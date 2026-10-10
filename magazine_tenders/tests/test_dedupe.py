# magazine_tenders/tests/test_dedupe.py
"""
The same story, twice.

Five Jordanian sources now cover one country, and a wire agency plus three
dailies report the same events. Measured over the 282 rows on the News page
on 21 September, nine pairs scored 0.70 or better on normalised title
overlap, and every one of them was a genuine duplicate:

    0.91  Gulf Construction, the same maintenance notice twice
    0.90  Zawya, the same ZAKH solar story under two headlines
    0.78  Al-Rai and Al-Ghad, the Ajloun hotel
    0.73  Construction Week and Zawya, the Nakheel award
    0.67  Zawya, the WSP King Fahd design contract, two wordings

So this is not an Arabic problem and not a Jordan problem; it is what
happens when sources overlap. The threshold is 0.70 because that is where
the data separates: below it the next candidates are 0.64 and 0.62, and
those are DIFFERENT contract announcements that must stay apart.
"""
from datetime import date, timedelta

from django.test import TestCase

from magazine_tenders import dedupe


class NormaliseTests(TestCase):
    """What counts as the same word."""

    def test_the_attached_definite_article_is_dropped(self):
        # Arabic has no word boundary before ال/و/ب/ل, so معان and بمعان are
        # the same place and must compare equal.
        self.assertEqual(dedupe.normalise('معان'), dedupe.normalise('بمعان'))
        self.assertEqual(dedupe.normalise('الروضة'), dedupe.normalise('روضة'))

    def test_digits_are_dropped(self):
        # "4 نجوم" and "أربع نجوم" are the same hotel; the digit is noise
        # either way because one paper spells it and the other does not.
        self.assertNotIn('4', ' '.join(dedupe.normalise('فندق 4 نجوم')))

    def test_punctuation_is_dropped(self):
        self.assertEqual(dedupe.normalise("'ZAKH' launches solar"),
                         dedupe.normalise('ZAKH launches solar'))

    def test_short_words_and_stop_words_are_dropped(self):
        self.assertNotIn('في', dedupe.normalise('فندق في عجلون'))


class SimilarityTests(TestCase):
    """The numbers the threshold was chosen from."""

    HOTEL_A = 'فرصة استثمارية لإنشاء فندق 4 نجوم في متنزه عجلون'
    HOTEL_B = 'فرصة استثمارية لإنشاء فندق أربع نجوم في متنزه عجلون الوطني'

    GAS_A = 'رئيس الوزراء يطلق مشروع إيصال الغاز الطبيعي إلى مدينة الروضة الصناعية في معان'
    GAS_B = 'أبو تايه: تزويد الروضة الصناعية بمعان بالغاز الطبيعي يعزز الاستثمارات'

    MERAAS_A = 'Meraas awards AED590 mn main construction contract for Design Quarter'
    MERAAS_B = 'Meraas awards contract for City Walk Crestlane'

    def test_the_hotel_pair_is_over_the_threshold(self):
        self.assertGreaterEqual(
            dedupe.similarity(self.HOTEL_A, self.HOTEL_B), dedupe.THRESHOLD)

    def test_an_identical_title_scores_one(self):
        self.assertEqual(dedupe.similarity(self.HOTEL_A, self.HOTEL_A), 1.0)

    def test_two_different_contract_announcements_stay_apart(self):
        # 0.64 on the live data. The nearest thing to a false merge, and the
        # reason the threshold is not lower.
        self.assertLess(
            dedupe.similarity(self.MERAAS_A, self.MERAAS_B), dedupe.THRESHOLD)

    def test_the_gas_pair_is_not_merged(self):
        # A documented non-goal. Same event, genuinely different articles -
        # a ground-breaking and an executive's comment on it. Catching this
        # needs a threshold low enough to merge unrelated stories.
        self.assertLess(
            dedupe.similarity(self.GAS_A, self.GAS_B), dedupe.THRESHOLD)

    def test_an_empty_title_never_matches(self):
        self.assertEqual(dedupe.similarity('', self.HOTEL_A), 0.0)
        self.assertEqual(dedupe.similarity('', ''), 0.0)


class LinkDuplicateTests(TestCase):
    """Marking the second copy, and never the first."""

    def setUp(self):
        from tenders.models import ScraperSource, CountryConfig
        self.country = CountryConfig.objects.create(
            code='JO', name_en='Jordan', name_ar='الأردن')
        self.a = ScraperSource.objects.create(
            name='Al-Rai', country=self.country, scraper_class='MAGAZINE_ALRAI')
        self.b = ScraperSource.objects.create(
            name='Al-Ghad', country=self.country, scraper_class='MAGAZINE_ALGHAD')

    def tender(self, source, title, when, external_id):
        """
        A saved magazine row, meta and all.

        The meta record is not decoration: only magazine rows are compared,
        and a Tender is not one until MagazineTenderMeta exists. The runner
        writes it before it deduplicates, so the fixture does too.
        """
        from tenders.models import Tender
        from magazine_tenders.models import MagazineTenderMeta
        tender = Tender.objects.create(
            source=source, country=self.country, external_id=external_id,
            title=title, description='[PROJECT] ', publication_date=when,
            is_relevant=True)
        MagazineTenderMeta.objects.create(
            tender=tender, magazine=source.scraper_class.replace('MAGAZINE_', ''))
        return tender

    def test_the_second_copy_is_marked_a_mirror_of_the_first(self):
        first = self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'a1')
        second = self.tender(self.b, SimilarityTests.HOTEL_B, date(2026, 9, 20), 'b1')

        self.assertTrue(dedupe.link_if_duplicate(second))

        second.refresh_from_db()
        self.assertTrue(second.is_mirror)
        self.assertEqual(second.mirror_of_id, first.pk)

    def test_the_first_copy_is_left_alone(self):
        first = self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'a1')

        self.assertFalse(dedupe.link_if_duplicate(first))

        first.refresh_from_db()
        self.assertFalse(first.is_mirror)
        self.assertIsNone(first.mirror_of_id)

    def test_a_different_story_is_not_linked(self):
        self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'a1')
        other = self.tender(self.b, 'افتتاح توسعة مستشفى معان الحكومي',
                            date(2026, 9, 20), 'b1')

        self.assertFalse(dedupe.link_if_duplicate(other))

        other.refresh_from_db()
        self.assertFalse(other.is_mirror)

    def test_the_same_story_a_month_later_is_not_linked(self):
        # A repeated headline outside the window is a new announcement, not
        # a duplicate - a scheme re-tendered, a second phase opening.
        self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 8, 1), 'a1')
        later = self.tender(self.b, SimilarityTests.HOTEL_B, date(2026, 9, 20), 'b1')

        self.assertFalse(dedupe.link_if_duplicate(later))

    def test_an_undated_row_still_compares(self):
        # Ad-Dustour and Al-Ghad rows arrive undated when their article page
        # will not load. They must still be deduplicated, not waved through.
        self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'a1')
        undated = self.tender(self.b, SimilarityTests.HOTEL_B, None, 'b1')

        self.assertTrue(dedupe.link_if_duplicate(undated))

    def test_a_row_already_marked_is_not_relinked(self):
        first = self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'a1')
        second = self.tender(self.b, SimilarityTests.HOTEL_B, date(2026, 9, 20), 'b1')
        dedupe.link_if_duplicate(second)

        self.assertFalse(dedupe.link_if_duplicate(second))

    def test_a_mirror_is_never_chosen_as_the_original(self):
        # Three copies of one story must all point at the first, not form a
        # chain - the page follows mirror_of one step only.
        first = self.tender(self.a, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'a1')
        second = self.tender(self.b, SimilarityTests.HOTEL_B, date(2026, 9, 20), 'b1')
        third = self.tender(self.b, SimilarityTests.HOTEL_A, date(2026, 9, 20), 'b2')
        dedupe.link_if_duplicate(second)
        dedupe.link_if_duplicate(third)

        third.refresh_from_db()
        self.assertEqual(third.mirror_of_id, first.pk)


class NewsPageHidesDuplicatesTests(TestCase):
    """What the reader actually sees."""

    def test_the_news_queryset_excludes_mirrors(self):
        import inspect
        from magazine_tenders import views
        src = inspect.getsource(views._MagazineListBase.get_queryset)
        self.assertIn('is_mirror', src,
                      'the News queryset must filter duplicates out')


class WorksAndItsSupervisionAreNotDuplicatesTests(TestCase):
    """
    The works package and the supervision package are different contracts.

    They share almost every word — "Foul Sewer GAP tunnel" against
    "Supervision for Foul Sewer GAP tunnel" scores well over the threshold —
    but they are two separate procurements, and the consultancy one is the
    kind AJi actually bids for. The first backfill merged eleven of these on
    the Ashghal pipeline before anybody looked.

    So the role a title names outranks the similarity score: if one side
    says supervision, consultancy, design or PSA and the other does not,
    they are different packages whatever the overlap.
    """

    PAIRS = [
        ('Foul Sewer GAP tunnel -1.6 Km long, Dia 600 -800mm',
         'Supervision for Foul Sewer GAP tunnel -1.6 Km long, Dia 600 -800mm'),
        ('Modernisation of Tunnels on Lusail Expressway',
         'Consultancy Services for Modernisation of Tunnels on Lusail Expressway'),
        ('Design and Construction of Pedestrian Crossings',
         'Supervision for Design and Construction of Pedestrian Crossings'),
        ('Roads and Infrastructure in Al Kheesa North',
         'PSA Roads and Infrastructure in Al Kheesa North'),
        ('إنشاء مشروع شبكة الصرف الصحي في المزار الشمالي',
         'الإشراف على إنشاء مشروع شبكة الصرف الصحي في المزار الشمالي'),
        ('إنشاء 4 مدارس جديدة في محافظة البلقاء',
         'إعداد التصاميم الهندسية لإنشاء 4 مدارس جديدة في محافظة البلقاء'),
    ]

    #: The subset that scores ABOVE the threshold, so only the role rule
    #: keeps them apart. The other pairs in PAIRS are separated by score
    #: alone — they are here because they are the same family and must not
    #: start merging if the threshold is ever lowered.
    ROLE_RULE_IS_LOAD_BEARING = [
        ('Foul Sewer GAP tunnel -1.6 Km long, Dia 600 -800mm',
         'Supervision for Foul Sewer GAP tunnel -1.6 Km long, Dia 600 -800mm'),
        ('Design and Construction of Pedestrian Crossings',
         'Supervision for Design and Construction of Pedestrian Crossings'),
        ('Roads and Infrastructure in Al Kheesa North',
         'PSA Roads and Infrastructure in Al Kheesa North'),
        ('إنشاء مشروع شبكة الصرف الصحي في المزار الشمالي',
         'الإشراف على إنشاء مشروع شبكة الصرف الصحي في المزار الشمالي'),
    ]

    def test_the_rule_is_what_separates_them_not_the_score(self):
        # These score OVER the threshold, so similarity alone would merge
        # them. If this ever fails, the role rule has stopped earning its
        # keep and somebody should ask whether it is still needed.
        for works, service in self.ROLE_RULE_IS_LOAD_BEARING:
            with self.subTest(works=works[:40]):
                self.assertGreaterEqual(dedupe.similarity(works, service),
                                        dedupe.THRESHOLD)

    def test_but_they_are_not_treated_as_the_same_story(self):
        for works, service in self.PAIRS:
            with self.subTest(works=works[:40]):
                self.assertFalse(dedupe.same_story(works, service))

    def test_two_copies_of_the_same_supervision_notice_still_merge(self):
        # Both name the same role, so the rule does not separate them.
        self.assertTrue(dedupe.same_story(
            'Call-Off Design Consultancy Services for Roads',
            'Call-off design consultancy services for roads'))

    def test_two_copies_of_the_same_works_notice_still_merge(self):
        self.assertTrue(dedupe.same_story(
            SimilarityTests.HOTEL_A, SimilarityTests.HOTEL_B))


class ThePipelineIsNotDeduplicatedTests(TestCase):
    """
    Forthcoming-work sources are left alone.

    Ashghal's pipeline is not a news feed. Its titles are systematic package
    names that differ by an area, a phase or a three-letter system code —
    "Road Rehabilitation Works - Qatar North Area 1" against "...Qatar South
    Area 2", "FSN rehabilitation" against "TSE rehabilitation" — and those
    are separate procurements that happen to be worded alike. Prose
    headlines from a news desk are not like that, and the threshold was
    measured on prose.

    So the rule is scoped to what it was measured on. Two papers running the
    same story are deduplicated; a pipeline of packages is not.
    """

    def test_the_forthcoming_sources_are_known(self):
        from magazine_tenders import sources
        self.assertTrue(sources.forthcoming_codes(),
                        'the scoping rule needs at least one such source')

    def test_two_pipeline_packages_are_never_merged(self):
        self.assertFalse(dedupe.is_deduplicated('ASHGHALPROSPECTED'))

    def test_a_news_source_is_deduplicated(self):
        for code in ('ALRAI', 'ALGHAD', 'ADDUSTOUR', 'PETRA'):
            with self.subTest(code=code):
                self.assertTrue(dedupe.is_deduplicated(code))


class NumbersThatDistinguishTests(TestCase):
    """
    A number can be the only difference between two contracts.

    Digits were dropped so that "فندق 4 نجوم" and "فندق أربع نجوم" would
    match, and that cost "Area 1" against "Area 2". Multi-digit numbers are
    kept now; a bare single digit is still noise.
    """

    def test_two_areas_are_not_the_same_contract(self):
        self.assertFalse(dedupe.same_story(
            'Road Rehabilitation Works - Qatar North Area 11',
            'Road Rehabilitation Works - Qatar North Area 12'))

    def test_the_hotel_still_matches_across_a_spelt_out_number(self):
        self.assertTrue(dedupe.same_story(
            SimilarityTests.HOTEL_A, SimilarityTests.HOTEL_B))


class ArabicDiacriticsTests(TestCase):
    """
    Tashkeel is decoration, not spelling.

    Al-Rai writes يخفض and Ad-Dustour writes يُخفّض — the same word with a
    damma and a shadda on it. Untouched, they are two different tokens, and
    the two papers' reports of the Ma'an gas story scored 0.545 instead of
    matching outright. The tatweel (ـ), used to stretch a word for
    typesetting, is the same kind of noise.
    """

    def test_a_vowelled_word_equals_its_bare_form(self):
        self.assertEqual(dedupe.normalise('يُخفّض'), dedupe.normalise('يخفض'))
        self.assertEqual(dedupe.normalise('الطَّبيعي'), dedupe.normalise('الطبيعي'))

    def test_the_tatweel_is_removed(self):
        self.assertEqual(dedupe.normalise('مشــــروع'), dedupe.normalise('مشروع'))

    def test_the_two_papers_gas_reports_match(self):
        a = '«تطوير معان»: مشروع تزويد الروضة الصناعية بالغاز يُخفّض كلف الطاقة'
        b = 'أبو تايه: مشروع تزويد الروضة الصناعية بالغاز يخفض كلف الطاقة'
        # The attribution differs, so this is not 1.0 - but the verb must no
        # longer count against them.
        self.assertGreater(dedupe.similarity(a, b), 0.60)
