# magazine_tenders/tests/test_newsfeed.py
"""
The tender-announcement filter, and the five news-feed scrapers built on it.

These tests matter more than most in this app. Four of the six magazines are
trade-news publications, so the filter is the only thing standing between the
Magazines page and a wall of industry news relabelled as tenders. Every
assertion below is about that boundary.

All parsing runs offline against saved API responses.
"""
import json
from datetime import date
from pathlib import Path

from django.test import TestCase

from magazine_tenders.scrapers._newsfeed import (extract_deadline,
                                                 extract_reference,
                                                 reads_as_tender_announcement,
                                                 strip_html)
from magazine_tenders.scrapers.arabianbusiness import ArabianBusinessScraper
from magazine_tenders.scrapers.cbnme import CBNMEScraper
from magazine_tenders.scrapers.constructionweek import ConstructionWeekScraper
from magazine_tenders.scrapers.healthcaremea import HealthcareMEAScraper
from magazine_tenders.scrapers.zawya import ZawyaScraper

FIXTURES = Path(__file__).parent / 'fixtures'


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8', errors='replace'))


class AnnouncementFilterTests(TestCase):
    """The predicate that decides tender vs news."""

    OPEN_TENDERS = [
        'RTA invites bids for Dubai Metro Gold Line project',
        'RTA Invites Bids for Prequalification on Dubai Metro Gold Line',
        'Ashghal issues tender for road upgrade in Doha',
        'Request for proposals: design of a new teaching hospital',
        'PAHW tender no. PAHW/M/1532-2026/2027 for 132/11kV substations',
        'Ministry seeks consultants for water network supervision',
        'Expressions of interest invited for the airport expansion',
        'Call for bids: construction of a secondary school',
        'طرح مناقصة لإنشاء مبنى إداري',
        'دعوة لتقديم عروض لأعمال الإشراف الهندسي',
    ]

    INDUSTRY_NEWS = [
        'Dubai awards $2bn contract for Palm Jebel Ali infrastructure',
        'Contractor wins tender for Riyadh metro extension',
        'ALEC completes handover of Expo pavilion',
        'Saudi Arabia inaugurates new desalination plant',
        'Construction output in the UAE grew 4% last year',
        'Interview: the CEO reshaping Gulf contracting',
        'Five trends shaping healthcare facilities in 2026',
        'Aldar secured the contract for the Yas Island development',
        # An appointment is a decision already taken — intelligence, not a bid.
        'Deloitte, WSP among consultants appointed for Dubai 120 MIGD plant',
        'Consultant appointed for $2bn Jawharat Al-Arous project',
        'HKS Architects named design consultant for RAK resort',
    ]

    def test_open_tender_language_is_accepted(self):
        for text in self.OPEN_TENDERS:
            with self.subTest(text=text):
                self.assertTrue(reads_as_tender_announcement(text),
                                f'should be accepted: {text}')

    def test_industry_news_is_rejected(self):
        for text in self.INDUSTRY_NEWS:
            with self.subTest(text=text):
                self.assertFalse(reads_as_tender_announcement(text),
                                 f'should be rejected: {text}')

    def test_retrospective_beats_a_signal_word(self):
        # Carries "tender" AND an award verb: it reports a result, so it loses.
        self.assertFalse(reads_as_tender_announcement(
            'Contractor wins tender for the new stadium'))

    def test_empty_text_is_rejected(self):
        self.assertFalse(reads_as_tender_announcement(''))
        self.assertFalse(reads_as_tender_announcement(None))


class FieldExtractionTests(TestCase):
    def test_strip_html(self):
        self.assertEqual(
            strip_html('<p>Tender  <b>No</b> 12/2026</p>'), 'Tender No 12/2026')

    def test_reference_extracted(self):
        self.assertEqual(
            extract_reference('Tender No. PAHW/M/1532-2026 for substations'),
            'PAHW/M/1532-2026')

    def test_bare_year_is_not_a_reference(self):
        self.assertEqual(extract_reference('tender no 2026'), '')

    def test_no_reference_returns_empty(self):
        self.assertEqual(extract_reference('bids are invited shortly'), '')

    def test_deadline_day_month_year(self):
        self.assertEqual(
            extract_deadline('Closing date 12 August 2026', date(2026, 8, 1)),
            date(2026, 8, 12))

    def test_deadline_month_day_year(self):
        self.assertEqual(
            extract_deadline('Submission deadline October 5, 2026', date(2026, 9, 1)),
            date(2026, 10, 5))

    def test_deadline_without_year_uses_publication_year(self):
        self.assertEqual(
            extract_deadline('bids are due 20 September', date(2026, 9, 1)),
            date(2026, 9, 20))

    def test_deadline_without_year_rolls_forward_when_past(self):
        # Published in September, closing "5 March" → next March.
        self.assertEqual(
            extract_deadline('closing date 5 March', date(2026, 9, 1)),
            date(2027, 3, 5))

    def test_no_deadline_returns_none(self):
        self.assertIsNone(extract_deadline('no dates at all here', date(2026, 9, 1)))

    def test_impossible_deadline_returns_none(self):
        self.assertIsNone(
            extract_deadline('closing date 31 February 2026', date(2026, 1, 1)))


class _FeedParseMixin:
    """Shared assertions for a scraper parsing a saved feed."""
    scraper_cls = None
    fixture = None

    def _items(self):
        posts = load(self.fixture)
        scraper = self.scraper_cls()
        raw = [scraper._from_wp_post(p) for p in posts]
        return scraper, raw, scraper.build_items(raw)

    def test_rejects_the_majority(self):
        _, raw, kept = self._items()
        self.assertGreater(len(raw), 0, 'fixture should hold feed items')
        # Still a filter, not a pass-through. The bound has moved twice, each
        # time because the rule widened: tenders only (1 in 100), then design
        # and supervision too (1 in 10), and now construction project news as
        # well. On a construction trade feed roughly a third of the posts ARE
        # construction projects, so a third surviving is the source being
        # itself rather than the predicate failing. Past half would mean it had
        # stopped discriminating.
        self.assertLess(len(kept), max(4, len(raw) * 0.5),
                        f'{len(kept)} of {len(raw)} kept — filter is too loose')

    def test_every_kept_item_is_explained(self):
        # The real invariant: nothing is kept without a reason, and the reason
        # is recorded so the page can be read without opening each article.
        from magazine_tenders.scrapers._newsfeed import why_keep
        _, _, kept = self._items()
        for td in kept:
            with self.subTest(title=td.title[:60]):
                self.assertRegex(td.description,
                                 r'^\[(TENDER|DESIGN|PROJECT)\] ')
                self.assertTrue(why_keep(f'{td.title} {td.description}'),
                                f'kept with no rule matching: {td.title}')

    def test_kept_items_are_well_formed(self):
        _, _, kept = self._items()
        for td in kept:
            self.assertTrue(td.title.strip())
            self.assertTrue(td.source_url.startswith('http'))
            self.assertIn(td.status, ('OPEN', 'CLOSED'))
            self.assertRegex(td.country_code, r'^[A-Z]{2}$')

    def test_parsing_writes_nothing_to_the_database(self):
        from tenders.models import Tender
        before = Tender.objects.count()
        self._items()
        self.assertEqual(Tender.objects.count(), before)

    def test_scrape_without_a_source_returns_empty(self):
        self.assertEqual(self.scraper_cls().scrape(), [])


class ConstructionWeekTests(_FeedParseMixin, TestCase):
    scraper_cls = ConstructionWeekScraper
    fixture = 'cw_posts.json'

    def test_finds_the_rta_tender(self):
        # Still found, and still labelled as an invitation rather than as
        # design news - the widened net must not blur the two.
        # Matched on the INVITATION rather than on the letters 'RTA': since
        # project news counts, the authority also appears in stories about
        # work already under way ('RTA completes first phase of tunnel
        # excavation'), and those are not what this test is about.
        _, _, kept = self._items()
        rta = [t for t in kept
               if 'RTA' in t.title and 'invites bids' in t.title.lower()]
        self.assertEqual(len(rta), 1, ' | '.join(t.title for t in kept))
        self.assertTrue(rta[0].description.startswith('[TENDER] '),
                        rta[0].description[:60])

    def test_also_finds_the_design_stories(self):
        # What the widening was asked for: this feed carries design news that
        # the tender-only rule threw away.
        _, _, kept = self._items()
        design = [t for t in kept if t.description.startswith('[DESIGN] ')]
        self.assertGreaterEqual(len(design), 3,
                                ' | '.join(t.title for t in kept))
        blob = ' '.join(t.title.lower() for t in design)
        self.assertTrue(any(w in blob for w in ('design', 'masterplan')),
                        blob[:200])


class CBNMETests(_FeedParseMixin, TestCase):
    scraper_cls = CBNMEScraper
    fixture = 'cbnme_tender_search.json'

    def test_finds_a_genuine_invitation(self):
        # The saved feed contains a real procurement invitation (DEWA inviting
        # developers for MBR Solar Park Phase 7). Asserted on the announcing
        # language rather than one headline, so re-saving the fixture does not
        # break the test for the wrong reason.
        _, _, kept = self._items()
        self.assertGreaterEqual(len(kept), 1)
        invitations = [td for td in kept
                       if reads_as_tender_announcement(f'{td.title} {td.description}')]
        self.assertGreaterEqual(len(invitations), 1,
                                ' | '.join(t.title for t in kept))

    def test_the_rest_are_kept_on_a_named_rule_not_by_accident(self):
        # Items that are NOT invitations must be here on one of the other two
        # rules, and must be labelled with which - otherwise the widened net
        # is indistinguishable from a broken filter.
        from magazine_tenders.scrapers._newsfeed import (
            reads_as_design_or_supervision, reads_as_project_news)
        _, _, kept = self._items()
        for td in kept:
            blob = f'{td.title} {td.description}'
            if reads_as_tender_announcement(blob):
                continue
            with self.subTest(title=td.title[:60]):
                self.assertTrue(
                    reads_as_design_or_supervision(blob)
                    or reads_as_project_news(blob),
                    f'kept for no reason: {td.title}')
                self.assertTrue(
                    td.description.startswith('[DESIGN] ')
                    or td.description.startswith('[PROJECT] '),
                    td.description[:60])


class ArabianBusinessTests(_FeedParseMixin, TestCase):
    scraper_cls = ArabianBusinessScraper
    fixture = 'ab_tender_search.json'

    def test_carries_no_open_tenders(self):
        # The measured reality, unchanged: this feed announces no tenders.
        _, _, kept = self._items()
        invitations = [t for t in kept
                       if t.description.startswith('[TENDER] ')]
        self.assertEqual(len(invitations), 0, [t.title for t in invitations])

    def test_what_it_does_yield_is_design_or_project_news(self):
        # Whatever survives here survives on a named rule, never by default.
        # Recorded because a general business feed is where a loose predicate
        # shows its false positives first - a software or hotel story that
        # happens to say "design" is not construction design news, and a
        # funding round at an airport is not an airport project.
        _, _, kept = self._items()
        for td in kept:
            with self.subTest(title=td.title[:60]):
                self.assertTrue(
                    td.description.startswith('[DESIGN] ')
                    or td.description.startswith('[PROJECT] '),
                    td.description[:60])


class HealthcareMEATests(_FeedParseMixin, TestCase):
    scraper_cls = HealthcareMEAScraper
    fixture = 'hcmea_posts.json'

    def test_hospitals_category_carries_no_tender_announcements(self):
        # The reason healthcare AEC tenders do not come from this site. Still
        # true: what the widened net finds here is project news, not notices.
        _, _, kept = self._items()
        invitations = [t for t in kept
                       if t.description.startswith('[TENDER] ')]
        self.assertEqual(len(invitations), 0, [t.title for t in invitations])

    def test_whole_tender_search_also_carries_no_invitations(self):
        posts = load('hcmea_tender_search.json')
        s = HealthcareMEAScraper()
        kept = s.build_items([s._from_wp_post(p) for p in posts])
        invitations = [t for t in kept
                       if t.description.startswith('[TENDER] ')]
        self.assertEqual(len(invitations), 0, [t.title for t in invitations])

    def test_a_hospital_tender_would_be_accepted_if_published(self):
        # Proof the scraper is not the limitation — the source is.
        s = HealthcareMEAScraper()
        td = s.build_one({
            'title': 'Ministry of Health invites bids for the design and '
                     'supervision of a new 200-bed hospital in Abu Dhabi',
            'summary': 'Closing date 15 October 2026. Tender No MOH/2026/44.',
            'body': '', 'url': 'https://www.healthcaremea.com/x',
            'published': date(2026, 9, 1),
        })
        self.assertIsNotNone(td)
        self.assertEqual(td.country_code, 'AE')
        self.assertEqual(td.submission_deadline, date(2026, 10, 15))
        self.assertEqual(td.reference_no, 'MOH/2026/44')


class ZawyaSitemapTests(TestCase):
    def test_parses_the_news_sitemap(self):
        xml = (FIXTURES / 'zawya_news_sitemap.xml').read_text(
            encoding='utf-8', errors='replace')
        s = ZawyaScraper()
        # Feed the saved XML straight to the sitemap reader.
        s._client_get = lambda url, params=None: xml
        s.SITEMAP_URLS = ['file://saved']
        raw = s._fetch_sitemap()
        self.assertGreater(len(raw), 50, 'sitemap should list many articles')
        kept = s.build_items(raw)
        # A rolling 2-day news window: few or no tenders on any given day, and
        # whatever survives must be well formed.
        self.assertLess(len(kept), len(raw))
        for td in kept:
            self.assertTrue(td.title.strip())
            self.assertTrue(td.source_url.startswith('http'))

    def test_country_is_inferred_from_the_text(self):
        s = ZawyaScraper()
        self.assertEqual(s._country('Kuwait PAHW invites bids'), 'KW')
        self.assertEqual(s._country('Ashghal Doha tender'), 'QA')
        self.assertEqual(s._country('nothing identifiable'), 'AE')


class EntityInferenceTests(TestCase):
    def test_entity_is_the_announcing_body(self):
        s = ConstructionWeekScraper()
        self.assertEqual(s._entity('RTA invites bids for Dubai Metro'), 'RTA')

    def test_entity_blank_when_unclear(self):
        s = ConstructionWeekScraper()
        self.assertEqual(s._entity('bids are invited for works'), '')

class DesignOrSupervisionNewsTests(TestCase):
    """
    The widened net: "all news that is إشراف or تصميم", not only notices open
    for bids.

    Deliberately looser than tenders/aec_scope.py, which decides whether a
    TENDER TITLE describes separable design work and so demands a scope context
    to keep out "relaunched with a new design". Here that headline is a wanted
    result, which is exactly the difference these tests pin.
    """

    def test_a_bare_design_mention_is_NOT_enough(self):
        # This asserted the opposite until the rule was tightened. A bare
        # mention could not be made precise with a noun list, because
        # hospitals and airports appear on both the wanted and unwanted side.
        # Design or supervision now has to be COMMISSIONED or DELIVERED.
        #
        # Measured over the 258 stored trade-press rows: 254 kept before, 174
        # after, and nothing that was visible on the News page was lost.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Emaar to relaunch Dubai Creek Tower with new design'),
            '')

    def test_the_same_story_counts_once_it_is_a_commission(self):
        # The contrast that defines the rule: the subject is identical, only
        # the relationship to the work differs.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Emaar awards design contract for Dubai Creek Tower'),
            'DESIGN')

    def test_the_noise_the_tightening_was_for(self):
        # Every one of these was reaching the section before.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'UAE parents warned over children ordering food online '
                'without supervision',
                'Saudi Arabia to give 27 million people free access to Adobe '
                'AI tools',
                'US researchers use generative AI to design functional '
                'synthetic viruses',
                'Checking In: Francis Hotel, Bath'):
            with self.subTest(text=text[:40]):
                self.assertEqual(why_keep(text), '')

    def test_real_commissions_survive_the_tightening(self):
        # Drawn from what the sites actually published.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'SSH leads Design and Supervision for Dubai Expo Valley Views',
                'Diriyah Company awards Parsons $56mn design and construction '
                'supervision contract',
                'Oman issues tender for the design of 3 upcoming airports',
                'Dorsch Wins Construction Supervision Role for Expo 2030',
                'AECOM wins lead consultant role on phase one of Al Dhaher '
                'development',
                'Mace Consult To Drive Delivery Of Oman Cultural Complex',
                'REVEALED: Here are the top MEP Consultants for 2024'):
            with self.subTest(text=text[:40]):
                self.assertTrue(why_keep(text), f'lost: {text[:50]}')

    def test_a_negated_arabic_mention_is_not_a_commission(self):
        # The English rules refuse a bare mention by demanding the work be
        # commissioned. The Arabic rules accept a genitive construct instead,
        # which is right for "design OF a hospital" and wrong for a negated
        # one - the Arabic twin of the very headline the English tightening
        # was written for was still being kept.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in ('بدون إشراف الوالدين',
                     'دون تصميم',
                     'بلا اشراف'):
            with self.subTest(text=text):
                self.assertEqual(why_keep(text), '')

    def test_a_real_arabic_scope_is_untouched(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in ('تصميم مستشفى',
                     'أعمال الإشراف على الطرق',
                     'اعمال اشراف'):
            with self.subTest(text=text):
                self.assertEqual(why_keep(text), 'DESIGN')

    def test_arabic_genitive_constructs_count(self):
        # Arabic needs no preposition: تصميم مستشفى already means "design OF
        # a hospital". An earlier version demanded an of/for equivalent and
        # dropped real scopes.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in ('اعمال اشراف',
                     'أعمال الإشراف',
                     'تصميم مستشفى',
                     'تصاميم المشروع'):
            with self.subTest(text=text):
                self.assertEqual(why_keep(text), 'DESIGN')

    def test_supervision_is_news(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Consultant appointed for supervision of Doha metro'),
            'DESIGN')

    def test_arabic_with_and_without_the_hamza(self):
        # The feeds are inconsistent, and a missed hamza silently drops items.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in ('اعمال اشراف',
                     'اعمال الإشراف',
                     'تصميم جديد',
                     'تصاميم المشروع'):
            with self.subTest(text=text):
                self.assertEqual(why_keep(text), 'DESIGN')

    def test_unrelated_trade_news_is_still_dropped(self):
        # Widening the net must not turn it into "keep everything".
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in ('Volvo announces truck driver competition winners',
                     'Cement prices rise 4% across the GCC',
                     'Dubai launches Future Week for investors'):
            with self.subTest(text=text):
                self.assertEqual(why_keep(text), '')

    def test_an_announcement_still_wins_and_is_labelled_tender(self):
        # Nothing captured before may be lost: the label distinguishes the two.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Ashghal invites bids for supervision of roads package 7'),
            'TENDER')

    def test_the_modes_can_be_narrowed(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        design_only = 'Zaha Hadid reveals design for Riyadh centre'
        self.assertEqual(why_keep(design_only, 'design'), 'DESIGN')
        self.assertEqual(why_keep(design_only, 'tender'), '',
                         'tender-only mode must not keep design news')

    def test_every_widened_source_reads_all_sections(self):
        # An empty query dict is what makes /wp-json/wp/v2/posts return the
        # whole site rather than one category - "check all news in the nav".
        from magazine_tenders.scrapers.arabianbusiness import ArabianBusinessScraper
        from magazine_tenders.scrapers.cbnme import CBNMEScraper
        from magazine_tenders.scrapers.constructionweek import ConstructionWeekScraper
        from magazine_tenders.scrapers.healthcaremea import HealthcareMEAScraper
        for cls in (ArabianBusinessScraper, CBNMEScraper,
                    ConstructionWeekScraper, HealthcareMEAScraper):
            with self.subTest(scraper=cls.__name__):
                self.assertIn({}, cls.WP_QUERIES,
                              f'{cls.__name__} still only reads a sub-section')
                self.assertEqual(cls.RELEVANCE, 'either')



class PersonProfileVetoTests(TestCase):
    """
    Ranking entries about PEOPLE are not commissions.

    A trade magazine's "top consultants" list reached the News page: the
    entries are individuals, but their standfirsts are company blurb thick
    with consultancy language, so the classifier — once it was given that
    context — read them as AEC. It was not wrong about the words. The item
    just is not a commission.

    Vetoed at fetch time rather than in the display rule: a person profile is
    never a tender, so storing it and hiding it would leave it in the audit
    list pretending to be a rejected opportunity.
    """

    def test_the_four_that_reached_the_page_are_rejected(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        for title in ('8. Mouhammad Takieddin, CEO, JLL',
                      '17. Talal Shair, Chairman and CEO, Dar Al-Handasah',
                      '18. Craig Carter, Managing Partner, CSQ Project '
                      'Development Consultants',
                      '19. Natasha Abbas, Project Director, North 51 Consulting'):
            with self.subTest(title=title[:34]):
                self.assertEqual(why_keep(title), '')

    def test_named_people_columns_are_rejected(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        for title in ('Unsung Heroes: Ruairi Magee',
                      'Power Hour 2022: Ashraf Botros, Managing Director at ASU',
                      '10. Katrina Antonovich, Founder and CEO, Antonovich Group'):
            with self.subTest(title=title[:34]):
                self.assertEqual(why_keep(title), '')

    def test_a_numbered_title_that_is_real_work_survives(self):
        # The veto needs BOTH a numbered opening AND a job title, precisely so
        # a genuine title beginning with a number is not swept up.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(why_keep('1. Design of the new hospital wing'),
                         'DESIGN')

    def test_a_firm_whose_name_starts_with_a_number_survives(self):
        # "10 Design" is a real practice; "North 51 Consulting" appears in the
        # rejected entries only because a PERSON is named in front of it.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertTrue(why_keep('10 Design wins design contract for Riyadh '
                                 'tower'))

    def test_real_commissions_are_untouched(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        for title in ('AJi Signs Supervision Agreement For Miraf District',
                      'BSBG appointed lead consultant for Emaar Dubai Square',
                      'Package 1: Design and Build of Strategic SGW Drainage'):
            with self.subTest(title=title[:34]):
                self.assertTrue(why_keep(title), f'lost: {title[:44]}')

    def test_the_predicate_is_usable_on_its_own(self):
        from magazine_tenders.scrapers._newsfeed import reads_as_person_profile
        self.assertTrue(reads_as_person_profile('5. Jane Doe, CEO, ACME'))
        self.assertFalse(reads_as_person_profile('Design of a school'))
        self.assertFalse(reads_as_person_profile(''))


class PersonProfileVetoGapTests(TestCase):
    """The ranking entry that got through, and why it must not again."""

    def test_general_manager_is_a_job_title(self):
        from magazine_tenders.scrapers._newsfeed import reads_as_person_profile
        self.assertTrue(reads_as_person_profile(
            '13. Simon Baines, General Manager, House of Consulting Office (HCO), KSA'))
        self.assertTrue(reads_as_person_profile(
            '7. Lina Haddad, Vice President, Dar Al-Handasah'))

    def test_a_real_title_with_a_number_is_still_kept(self):
        from magazine_tenders.scrapers._newsfeed import reads_as_person_profile
        self.assertFalse(reads_as_person_profile(
            '1. Design of the new hospital wing, manager appointed'))


class ProjectNewsTests(TestCase):
    """
    The third keep-route: construction PROJECT news.

    Why it exists. Measured against the live feeds on 13 September 2026, the
    two design/tender routes kept 4 of Construction Week's 100 most recent
    posts and 1 of CBNME's — roughly 2%. Both sites publish daily, so the News
    page sat eleven days out of date while ~90 recent articles were refused.

    The refused set was not noise. It included ground-breakings, awarded
    construction contracts, consultants engaged to deliver schemes and new
    master-planned communities — the industry news the page is for.

    The rule is EVENT + BUILT WORK: something must be happening to a named
    piece of built work. That is what separates "Dubai Holding awards AED5
    billion construction contract" from "Cement prices rise 4% across the GCC",
    which is industry news but not a project, and from "Dubai launches Future
    Week for investors", which merely shares a verb.

    Whether a kept item is AEC is still not decided here — that remains the
    shared classification service's job, exactly as for every other tender.
    """

    def test_the_project_news_the_page_was_missing(self):
        # Every one of these was published in the eleven days the News page
        # showed nothing, and every one was refused.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'Dubai Healthcare City breaks ground on AED3 bn Dubai Creek '
                'Gardens',
                'Qiddiya awards $221m MEP contract for Prince Mohammed Bin '
                'Salman Stadium',
                'Dubai Holding awards AED5 billion construction contract to '
                'CSCEC ME',
                'Moon World Resorts appoints RLB to deliver its giant $5bn '
                'resort',
                'Fakhruddin Properties unveils AED700 mn robotics-led '
                'mixed-use development',
                'Azizi Developments introduces AED30 bn master-planned '
                'community in Sharjah',
                'Elegancia Wins SAR 829 Million Mechanical Contract For '
                "Qiddiya's World Cup Stadium"):
            with self.subTest(text=text[:45]):
                self.assertTrue(why_keep(text), f'still lost: {text[:60]}')

    def test_the_noise_stays_out(self):
        # The widening must not become "keep everything". These are the
        # headlines the earlier tightening was for, plus the ones the live
        # feeds ran alongside the project news above.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'Emaar to relaunch Dubai Creek Tower with new design',
                'Cement prices rise 4% across the GCC',
                'Dubai launches Future Week for investors',
                'Volvo announces truck driver competition winners',
                'Checking In: Francis Hotel, Bath',
                'Saudi cement sales stay flat in August as exports fall 18%',
                'Qatar Airways Expands Winter 2026-27 Network to More Than '
                '170 Destinations',
                'Shortlist Revealed: Pillars Of Real Estate Awards 2026',
                'Egnyte launches right-to-left language support for Arabic '
                'web interface',
                'SAL Zones Signs MoU to Support Saudi Arabia Aviation '
                'Logistics Infrastructure'):
            with self.subTest(text=text[:45]):
                self.assertEqual(why_keep(text), '', f'let through: {text[:60]}')

    def test_capability_building_is_not_a_building(self):
        # This one led the live News page: a beverages company launching an
        # employability programme, kept because "capability building" ends in
        # a word a contractor also uses.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'Al Ahram Beverages Company launches "Her Mark in '
                'Hospitality" capability building & employability track',
                'The ministry launches a capacity building programme for '
                'engineers',
                'Team building day announced for the Dubai office'):
            with self.subTest(text=text[:45]):
                self.assertEqual(why_keep(text), '', f'let through: {text[:56]}')

    def test_an_actual_building_still_counts(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in ('Ashghal unveils the new administrative building in Doha',
                     'Contractor wins deal for three buildings at Expo City'):
            with self.subTest(text=text[:45]):
                self.assertTrue(why_keep(text), f'lost: {text[:56]}')

    def test_a_rebrand_or_a_fundraiser_is_not_a_project(self):
        # Both reached the live News page through 'unveils' / 'launches'
        # sitting next to a word from the built-work list.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'RAKBANK unveils new brand identity after expanding its '
                'retail network',
                'Jood launches new donation drive for community housing',
                'Emirates NBD unveils a rebrand across every branch'):
            with self.subTest(text=text[:45]):
                self.assertEqual(why_keep(text), '', f'let through: {text[:60]}')

    def test_a_decimal_in_the_money_figure_does_not_lose_the_story(self):
        # The gap between the event and the built work was written as "any
        # run of characters that is not a full stop". That also refused the
        # point in a decimal, so the SIZE of the contract decided whether the
        # story was kept: "$16bn" came through and "$1.6bn" did not. Money
        # with a decimal is everywhere in this trade press.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'Aldar launches The Canopies at $1.6bn Yas Point development',
                'Emaar awards a $2.4bn design contract for Dubai Creek Tower',
                'Diriyah awards Parsons a $56.5mn supervision contract',
                'RTA unveils AED1.5 billion plans for the Al Shindagha tunnel'):
            with self.subTest(text=text[:45]):
                self.assertTrue(why_keep(text), f'lost on a decimal: {text[:56]}')

    def test_the_gap_still_stops_at_a_real_sentence_end(self):
        # The point of the gap is that both halves belong to ONE sentence.
        # Widening it for decimals must not let a rule reach into the next.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Ripe Market is coming back to Dubai. Ripe Market has '
                     'operated as a residential community since 2011'), '')

    def test_the_word_community_is_not_a_building(self):
        # Two stories reached the live News page on this word alone: a
        # community market returning to Dubai for the season, and a charity
        # launching a digital platform for "Dubai's community". Neither is a
        # project. "Community" names a group of people at least as often as
        # it names a housing development, so it cannot stand as built work by
        # itself - the residential sense always says so another way.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'Ripe Market is coming back to Dubai in October: opening '
                'times, dates and key info revealed. Ripe Market has operated '
                'as a community-led business since 2011',
                'The Dubai Community Contributions Establishment Jood '
                "launches new digital platform to advance Dubai's community "
                'contributions'):
            with self.subTest(text=text[:45]):
                self.assertEqual(why_keep(text), '', f'let through: {text[:60]}')

    def test_a_real_housing_community_still_counts(self):
        # The wanted sense names itself, so removing the bare word costs
        # nothing.
        from magazine_tenders.scrapers._newsfeed import why_keep
        for text in (
                'Azizi Developments introduces AED30 bn master-planned '
                'community in Sharjah',
                'Emaar unveils a new residential community at Dubai Creek',
                'Aldar launches The Canopies at $1.6bn Yas Point development'):
            with self.subTest(text=text[:45]):
                self.assertTrue(why_keep(text), f'lost: {text[:60]}')

    def test_an_event_alone_is_not_enough(self):
        # A verb with no built work behind it is somebody else's news.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(why_keep('Careem unveils a new subscription tier'), '')

    def test_built_work_alone_is_not_enough(self):
        # Naming a hospital is not reporting a project.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('A day in the life of a Dubai hospital porter'), '')

    def test_design_still_wins_and_keeps_its_label(self):
        # The labels are what an operator reads on the audit page, so a design
        # commission must not start arriving labelled PROJECT.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Emaar awards design contract for Dubai Creek Tower'),
            'DESIGN')

    def test_a_tender_still_wins_and_keeps_its_label(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('Ashghal invites bids for supervision of roads package 7'),
            'TENDER')

    def test_a_person_profile_is_still_vetoed(self):
        # A ranking entry whose blurb is full of contract language must not
        # come back in through the new route.
        from magazine_tenders.scrapers._newsfeed import why_keep
        self.assertEqual(
            why_keep('17. Talal Shair, Chairman and CEO, Dar Al-Handasah'), '')

    def test_narrowed_modes_do_not_get_project_news(self):
        from magazine_tenders.scrapers._newsfeed import why_keep
        text = 'Dubai Holding awards AED5 billion construction contract'
        self.assertEqual(why_keep(text, 'tender'), '')
        self.assertEqual(why_keep(text, 'design'), '')

    def test_the_route_can_be_switched_off(self):
        from django.test import override_settings

        from magazine_tenders.scrapers._newsfeed import why_keep
        text = 'Dubai Healthcare City breaks ground on AED3 bn Dubai Creek Gardens'
        self.assertEqual(why_keep(text), 'PROJECT')
        with override_settings(MAGAZINE_KEEP_PROJECT_NEWS=False):
            self.assertEqual(why_keep(text), '')
