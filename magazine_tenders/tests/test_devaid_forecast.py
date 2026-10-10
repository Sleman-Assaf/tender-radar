"""
DevelopmentAid forecast notices, reusing the existing tenders-app scraper.

The point of these tests is the REUSE contract. The forecast scraper is a
nine-line subclass, so what can break is not its parsing — it has none — but
the wiring: the status filter it asks for, the payload the parent builds from
it, and the fact that it is flagged as forthcoming so it lands under Future
Projects rather than in the general news list.

Offline: the payload builder is pure, and the one test that would otherwise
reach the network mocks the session.
"""
from unittest import mock

from django.test import TestCase

from tenders.scrapers.developmentaid import (DEFAULT_LOCATIONS,
                                             DEFAULT_STATUSES,
                                             DevelopmentAidScraper,
                                             _build_payload)

from magazine_tenders import registry, sources
from magazine_tenders.scrapers.developmentaid_forecast import (
    FORECAST_STATUS, DevelopmentAidForecastScraper, names_a_project)


class ReuseContractTests(TestCase):

    def test_it_subclasses_the_existing_scraper(self):
        # If this ever stops being true, the parsing, paging and country
        # resolution have been duplicated instead of reused.
        self.assertTrue(issubclass(DevelopmentAidForecastScraper,
                                   DevelopmentAidScraper))

    def test_it_inherits_the_parsing_rather_than_copying_it(self):
        # The subclass may add FILTERS — it does, for this feed's boilerplate
        # and plan revisions — but it must never redefine the parsing, paging,
        # country resolution or date handling. Redefining any of those means
        # logic was copied and the two will drift apart.
        for name in ('_parse_items', '_extract_items', '_resolve_country',
                     '_map_category', '_parse_date'):
            with self.subTest(method=name):
                self.assertNotIn(name, vars(DevelopmentAidForecastScraper),
                                 f'{name} was copied instead of inherited')

    def test_scrape_delegates_to_the_parent(self):
        # It wraps the parent rather than reimplementing the fetch.
        with mock.patch.object(DevelopmentAidScraper, 'scrape',
                               return_value=[]) as parent:
            DevelopmentAidForecastScraper().scrape()
        self.assertTrue(parent.called)

    def test_it_asks_for_the_forecast_status(self):
        self.assertEqual(DevelopmentAidForecastScraper.STATUSES,
                         [FORECAST_STATUS])
        self.assertEqual(FORECAST_STATUS, 2)

    def test_the_open_source_is_unchanged(self):
        # The Tenders section keeps getting OPEN notices from the same site.
        self.assertEqual(DevelopmentAidScraper.STATUSES, DEFAULT_STATUSES)
        self.assertEqual(DEFAULT_STATUSES, [3])


class PayloadTests(TestCase):

    def test_statuses_reach_the_payload(self):
        p = _build_payload(1, 50, None, [FORECAST_STATUS])
        self.assertEqual(p['filter']['statuses'], [2])

    def test_omitting_statuses_keeps_the_old_default(self):
        # Backwards compatibility: every existing caller passes three args.
        self.assertEqual(_build_payload(1, 50)['filter']['statuses'],
                         DEFAULT_STATUSES)
        self.assertEqual(_build_payload(1, 50, None)['filter']['statuses'],
                         DEFAULT_STATUSES)

    def test_the_forecast_filter_keeps_the_tracked_locations(self):
        # Changing the status must not narrow the geography.
        p = _build_payload(1, 50, None, [FORECAST_STATUS])
        self.assertEqual(p['filter']['locations'], DEFAULT_LOCATIONS)

    def test_a_location_subclass_still_works(self):
        p = _build_payload(1, 50, 51, [FORECAST_STATUS])
        self.assertEqual(p['filter']['locations'], [51])


class StatusParsingTests(TestCase):
    """The API sends a dict, not an int."""

    def _status(self, raw):
        s = DevelopmentAidForecastScraper()
        items = s._parse_items([{
            'id': 1, 'name': 'A forecast notice for a design study',
            'slug': 'x', 'status': raw, 'locationNames': ['Jordan'],
        }])
        return items[0].status if items else None

    def test_a_forecast_dict_is_open(self):
        # Nothing has closed yet — the notice has not opened.
        self.assertEqual(
            self._status({'id': 2, 'name': 'forecast',
                          'stage': 'procurement'}), 'OPEN')

    def test_an_open_dict_is_open(self):
        self.assertEqual(self._status({'id': 3, 'name': 'open'}), 'OPEN')

    def test_a_closed_dict_is_closed(self):
        # This is what str(dict) used to get wrong by accident.
        for name in ('closed', 'expired', 'awarded', 'cancelled'):
            with self.subTest(name=name):
                self.assertEqual(self._status({'id': 9, 'name': name}),
                                 'CLOSED')

    def test_an_int_status_still_works(self):
        self.assertEqual(self._status(3), 'OPEN')
        self.assertEqual(self._status(9), 'CLOSED')


class RegistrationTests(TestCase):

    def test_it_is_a_news_source(self):
        self.assertIn('DEVAIDFORECAST', sources.all_codes())
        self.assertTrue(registry.scraper_exists('DEVAIDFORECAST'))

    def test_it_appears_under_future_projects(self):
        # The whole reason the business asked for it.
        self.assertIn('DEVAIDFORECAST', sources.forthcoming_codes())

    def test_the_registered_url_is_the_forecast_filter(self):
        cfg = sources.MAGAZINES['DEVAIDFORECAST']
        self.assertIn('statuses=2', cfg['listing_urls'][0])
        self.assertEqual(cfg['scraper_class'], 'MAGAZINE_DEVAIDFORECAST')

    def test_ashghal_is_still_forthcoming_too(self):
        self.assertIn('ASHGHALPROSPECTED', sources.forthcoming_codes())


class SessionTests(TestCase):
    """Plain requests gets 403 from this API; impersonation does not."""

    def test_the_session_warms_up_cookies(self):
        # The API only answers after the search PAGE has been fetched, so a
        # missing warm-up GET is a silent 403 for every later request.
        s = DevelopmentAidForecastScraper()
        fake = mock.MagicMock()
        with mock.patch('curl_cffi.requests.Session', return_value=fake):
            s._make_session()
        self.assertTrue(fake.get.called, 'no warm-up request was made')
        url = fake.get.call_args[0][0]
        self.assertIn('/tenders/search', url)

    def test_a_warm_up_failure_is_not_fatal(self):
        s = DevelopmentAidForecastScraper()
        fake = mock.MagicMock()
        fake.get.side_effect = RuntimeError('network down')
        with mock.patch('curl_cffi.requests.Session', return_value=fake):
            self.assertIsNotNone(s._make_session())

class BoilerplateAndRevisionTests(TestCase):
    """
    Two filters specific to this feed, both measured against the live data.

    DevelopmentAid publishes a fresh notice for each revision of a World Bank
    procurement plan, so one project arrived up to four times — 11 duplicate
    rows in 100. And a few notices are pure boilerplate naming no project.
    """

    def _titles(self, titles):
        from tenders.scrapers.base import TenderData
        return [TenderData(title=t, source_url='https://e.com',
                           external_id=f'x{i}', country_code='ZZ')
                for i, t in enumerate(titles)]

    def _kept(self, titles):
        s = DevelopmentAidForecastScraper()
        return [td.title for td in
                s._drop_boilerplate_and_revisions(self._titles(titles))]

    # ── boilerplate ─────────────────────────────────────────────────────────
    def test_pure_boilerplate_is_dropped(self):
        self.assertEqual(
            self._kept(['General Procurement Notice - Procurement Plan n°30']),
            [])

    def test_a_named_project_survives_the_same_boilerplate(self):
        title = ('P176623- Dodoma Integrated and Sustainable Transport '
                 'Project - Procurement Plan (Ministry)')
        self.assertEqual(self._kept([title]), [title])

    def test_a_very_short_project_name_survives(self):
        # "Dzap" is Digital Zambia Acceleration Project. A word-count rule
        # would have thrown it away, which is why the threshold is letters.
        title = 'Dzap - Procurement Plan (Smart Zambia)'
        self.assertEqual(self._kept([title]), [title])

    def test_names_a_project_is_usable_on_its_own(self):
        self.assertFalse(names_a_project('PPM - Procurement Plan'))
        self.assertTrue(names_a_project('Kandadji Project - Procurement Plan'))
        self.assertFalse(names_a_project(''))

    # ── plan revisions ──────────────────────────────────────────────────────
    def test_only_the_newest_revision_of_a_project_is_kept(self):
        titles = [
            'P160865 - Livestock Productivity Project - Procurement Plan (v4)',
            'P160865- Livestock Productivity Project - Procurement Plan (v3)',
            'P160865 - Livestock Productivity Project - Procurement Plan (v2)',
        ]
        kept = self._kept(titles)
        self.assertEqual(len(kept), 1)
        # The API sorts newest first, so the first one seen is the one to keep.
        self.assertEqual(kept[0], titles[0])

    def test_different_projects_are_both_kept(self):
        titles = ['P111111 - Alpha Project - Procurement Plan',
                  'P222222 - Beta Project - Procurement Plan']
        self.assertEqual(len(self._kept(titles)), 2)

    def test_notices_without_a_project_code_are_not_deduplicated(self):
        # Only the World Bank code identifies a project; two unrelated
        # notices without one must not collapse into a single row.
        titles = ['Estudo tecnico de viabilidade Projeto Skola Azul',
                  'Riparazione della copiatrice CANON IRC 3226i']
        self.assertEqual(len(self._kept(titles)), 2)


class AfricaOfficeNameTests(TestCase):
    """
    "bureau d" matched an office NAME, not a consultancy.

    In Africa the display rule does not require is_aec=True, so a keyword
    match alone can surface a row the classifier rejected — which is how a
    social-protection programme reached the News page.
    """

    def test_an_office_name_is_not_a_consultancy(self):
        import re
        from tenders.aec_scope import CONSULTANCY_AFRICA
        title = ("P175594 - Cote d'Ivoire Social Safety Nets System "
                 "Strengthening Program - Procurement Plan "
                 "(Bureau de Coordination des Projets Filets Sociaux)")
        self.assertIsNone(re.search(CONSULTANCY_AFRICA, title, re.I))

    def test_a_design_practice_still_matches(self):
        import re
        from tenders.aec_scope import CONSULTANCY_AFRICA
        for title in ("AMI - Senegal - Cabinet d'etudes pour la conception",
                      "Recrutement d'un bureau d'études techniques",
                      'Bureau detudes pour la surveillance des travaux'):
            with self.subTest(title=title[:34]):
                self.assertIsNotNone(
                    re.search(CONSULTANCY_AFRICA, title, re.I))


class CountryNamesWithAccentsTests(TestCase):
    """
    An accented country name must resolve to its code.

    Found on a live row: "Côte d'Ivoire" fell through to ZZ and the card read
    "DevelopmentAid Forecast · ZZ". The lookup table is keyed on unaccented
    names, and the feed sends the accented spelling, so every match failed on
    a character the reader cannot even see.
    """

    def setUp(self):
        from tenders.scrapers.developmentaid import DevelopmentAidScraper
        self.resolve = DevelopmentAidScraper.__new__(
            DevelopmentAidScraper)._resolve_country

    def test_the_accented_spelling_resolves(self):
        self.assertEqual(self.resolve("Côte d'Ivoire"), 'CI')

    def test_the_plain_spelling_still_resolves(self):
        self.assertEqual(self.resolve("Cote d'Ivoire"), 'CI')

    def test_other_accented_names_the_feed_sends(self):
        for name, code in (('Türkiye', 'TR'),
                           ('São Tomé and Príncipe', 'ST'),
                           ('Perú', 'PE')):
            with self.subTest(name=name):
                self.assertEqual(self.resolve(name), code)

    def test_the_longest_name_wins_not_the_first(self):
        # 'Congo, Dem. Rep.' resolved to CG - the Republic of the Congo -
        # because the short key matched first. Two different countries.
        self.assertEqual(self.resolve('Congo, Dem. Rep.'), 'CD')
        self.assertEqual(self.resolve('Democratic Republic of Congo'), 'CD')
        self.assertEqual(self.resolve('Congo'), 'CG')

    def test_an_unknown_place_is_still_ZZ(self):
        self.assertEqual(self.resolve('Atlantis'), 'ZZ')
        self.assertEqual(self.resolve(''), 'ZZ')


class ReferenceGluedToTheTitleTests(TestCase):
    """
    Some notices arrive with the procurement reference welded to the title.

    The live row read
    'MCA-CI-Regional/CONSULTANT/QCBS/CFPCRCI-26-4016Services juridiques
    externes' - one string, no separator, because that is what the source's
    `name` field contains. The reference is worth keeping; it just does not
    belong in the headline.

    Conservative on purpose: the leading token must contain a slash, which is
    what a procurement reference looks like and what an ordinary title does
    not. 'P513144- Cote d'Ivoire Public Resource Management' must survive
    untouched.
    """

    def setUp(self):
        from tenders.scrapers.developmentaid import split_glued_reference
        self.split = split_glued_reference

    def test_the_live_row_is_separated(self):
        ref, title = self.split(
            'MCA-CI-Regional/CONSULTANT/QCBS/CFPCRCI-26-4016'
            'Services juridiques externes')
        self.assertEqual(ref, 'MCA-CI-Regional/CONSULTANT/QCBS/CFPCRCI-26-4016')
        self.assertEqual(title, 'Services juridiques externes')

    def test_an_ordinary_title_is_untouched(self):
        for title in (
                "P513144- Cote d'Ivoire Public Resource Management for Growth",
                '59447-001 - Baku Metro Signaling Modernization',
                'AGPM - Madagascar - Projet d amenagement de corridors',
                'Cameroon Water Security Project: General Procurement Notice'):
            with self.subTest(title=title[:40]):
                ref, out = self.split(title)
                self.assertEqual(ref, '')
                self.assertEqual(out, title)

    def test_a_reference_with_nothing_after_it_is_left_alone(self):
        # Splitting here would leave an empty headline, which is worse than
        # an ugly one.
        ref, title = self.split('MCA-CI/CONSULTANT/QCBS/CFPCRCI-26-4016')
        self.assertEqual(ref, '')
        self.assertEqual(title, 'MCA-CI/CONSULTANT/QCBS/CFPCRCI-26-4016')

    def test_empty_input_is_safe(self):
        self.assertEqual(self.split(''), ('', ''))
        self.assertEqual(self.split(None), ('', ''))


class CountryFromTheOrganisationTests(TestCase):
    """
    When the notice carries no usable location, the organisation often names
    the country.

    Six live rows sat in the international bucket. Two of them said exactly
    where they were - "Millennium Challenge Account Côte d'Ivoire" and "South
    African National Parks" - in a field the resolver never looked at. The
    rest are genuinely regional and must stay international, which is why this
    is a fallback and not a second guess over the top of a good answer.
    """

    def setUp(self):
        from tenders.scrapers.developmentaid import DevelopmentAidScraper
        self.s = DevelopmentAidScraper.__new__(DevelopmentAidScraper)

    def test_the_organisation_names_the_country(self):
        for org, code in (
                ("Millennium Challenge Account Côte d'Ivoire", 'CI'),
                ('South African National Parks (SANParks)', 'ZA'),
                ('Ministry of Water and Irrigation, Jordan', 'JO')):
            with self.subTest(org=org[:36]):
                self.assertEqual(self.s._resolve_country('', org), code)

    def test_a_real_location_is_never_overridden(self):
        # The location is the authority. The organisation is only consulted
        # when it produced nothing.
        self.assertEqual(
            self.s._resolve_country('Kenya', 'South African National Parks'),
            'KE')

    def test_a_regional_notice_stays_international(self):
        for org in ('African Union Development Agency',
                    'Ministry of Budget',
                    'National Agency for Persons with Disabilities'):
            with self.subTest(org=org[:36]):
                self.assertEqual(self.s._resolve_country('', org), 'ZZ')

    def test_the_fallback_is_optional(self):
        # Every existing caller passes one argument; it must keep working.
        self.assertEqual(self.s._resolve_country('Kenya'), 'KE')
        self.assertEqual(self.s._resolve_country(''), 'ZZ')

    def test_the_longer_country_name_still_wins_in_the_fallback(self):
        # 'Somalia' contains 'mali'. Sorting by length is what stops the
        # fallback filing a Somali agency under Mali.
        self.assertEqual(
            self.s._resolve_country('', 'Federal Republic of Somalia'), 'SO')


class CountryResolutionEdgesTests(TestCase):
    """The ways the lookup filed a notice under the wrong country."""

    def setUp(self):
        from tenders.scrapers.developmentaid import DevelopmentAidScraper
        self.s = DevelopmentAidScraper.__new__(DevelopmentAidScraper)

    def test_the_typographic_apostrophe(self):
        # NFKD strips diacritics but leaves U+2019, so the one name the
        # folding was written for still missed. Feeds carry the curly one.
        curly = 'C\u00f4te d\u2019Ivoire'
        self.assertEqual(self.s._resolve_country(curly), 'CI')
        self.assertEqual(
            self.s._resolve_country('', 'Millennium Challenge Account ' + curly),
            'CI')

    def test_a_name_that_contains_another_country(self):
        # A bare substring match filed these under the country inside them.
        for name, code in (('Papua New Guinea', 'PG'),
                           ('Guinea-Bissau', 'GW'),
                           ('Somaliland', 'SO'),
                           ('Guinea', 'GN')):
            with self.subTest(name=name):
                self.assertEqual(self.s._resolve_country(name), code)

    def test_a_donor_abbreviation_is_not_a_country(self):
        # The organisation fallback must not read 'EU' or 'GN' as a location,
        # or ensure_countries() invents a CountryConfig row for it.
        for org in ('EU', 'UN', 'AU', 'GN'):
            with self.subTest(org=org):
                self.assertEqual(self.s._resolve_country('', org), 'ZZ')

    def test_a_location_that_really_is_a_code_still_works(self):
        self.assertEqual(self.s._resolve_country('KE'), 'KE')


class ADateIsNotAReferenceTests(TestCase):
    """A leading date was being stored as the procurement reference."""

    def test_a_slashed_date_is_not_kept_as_a_reference(self):
        from tenders.scrapers.developmentaid import split_glued_reference
        ref, title = split_glued_reference(
            '12/09/2026Deadline extended for the Amman ring road')
        self.assertEqual(ref, '')
        self.assertEqual(title, 'Deadline extended for the Amman ring road')

    def test_a_real_reference_is_still_kept(self):
        from tenders.scrapers.developmentaid import split_glued_reference
        ref, title = split_glued_reference(
            'MCA-CI/CONSULTANT/QCBS-26-4016Services juridiques externes')
        self.assertEqual(ref, 'MCA-CI/CONSULTANT/QCBS-26-4016')
        self.assertEqual(title, 'Services juridiques externes')
