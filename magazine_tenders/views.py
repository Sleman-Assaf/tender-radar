# magazine_tenders/views.py
# News — الأخبار.
#
# Data source: tenders.Tender rows that carry a MagazineTenderMeta record.
# BUSINESS RULE: only is_aec=True magazine tenders appear here. Non-AEC rows
# stay in the database for auditing and are visible to admins on the Non-AEC
# page, exactly as the newspaper feature behaves. Whether a row is AEC was
# decided by the shared classification service — no rule in this file.

import logging
import re
import threading

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.db.models.functions import Cast, Substr
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import ListView

from tenders.models import TENDER_CATEGORY_FILTER_CHOICES, Tender, TenderStatus
from tenders.views import (annotate_bookmarked_for_user, annotate_new_for_user,
                           get_or_create_profile)

from .models import MagazineRun
from .pagination import FirstPageLargerPaginator
from . import sources
from . import freshness
from . import regions
from . import quarters
from .sources import MAGAZINES

logger = logging.getLogger('tenders')

#: The tag the news scrapers put at the front of a description to record WHY
#: an item was kept — "[TENDER] " or "[DESIGN] ". Useful in the audit list,
#: noise on a magazine card.
#: The keep-reason an item was stored under, which belongs on the audit
#: page and never in the reader's standfirst.
#:
#: ANY uppercase label, not a list of the ones that existed when this
#: was written. It named TENDER and DESIGN; PROJECT was added later and
#: leaked onto every project card as a literal '[PROJECT]' in front of
#: the text. A pattern that has to be edited whenever a label is added
#: is a pattern that will be forgotten again.
_KEEP_TAG_RE = re.compile(r'^\s*\[[A-Z][A-Z-]{1,20}\]\s*')

#: "expected Fourth Quarter 2026" as the Ashghal scraper writes it.
_EXPECTED_QUARTER_RE = re.compile(
    r'expected\s+(First|Second|Third|Fourth)\s+Quarter\s+(\d{4})', re.I)
_QUARTER_NO = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4}


def news_exemption_q() -> Q:
    """
    Rows the News page shows even though the classifier rejected them.

    Construction PROJECT news, and only that. The label already required an
    event and a named piece of built work, so it is a construction test in its
    own right - which is why it can stand in for the classifier on a news page
    and nowhere else.

    It lives here, as one function, because BOTH lists have to agree on it.
    News adds it to the AEC gate; the audit page subtracts it from the Non-AEC
    gate. When only the first of those existed, 121 announcements were listed
    on both pages at once - the two gates stopped being complements, and a
    reader saw the same story twice under contradictory headings.

    Returns a Q that matches nothing when the exemption is switched off, so
    both callers work unchanged: OR-ing it adds nothing and excluding it
    removes nothing.

    Sources marked `news_exemption: False` in the registry - the Arabic
    dailies and wires - are left out: their PROJECT label fires on ordinary
    government news ("developing the roads", "launching a programme in
    schools"), so for them the classifier alone decides, exactly as it does
    for TENDER and DESIGN items everywhere. Their rejected items therefore
    stay on the Non-AEC page, which keeps the two lists complements.
    """
    if not getattr(settings, 'MAGAZINE_NEWS_INCLUDES_PROJECTS', True):
        return Q(pk__in=[])
    q = Q(description__startswith='[PROJECT] ')
    off = sources.news_exemption_off_codes()
    if off:
        q &= ~Q(magazine_meta__magazine__in=off)
    return q


def expected_quarter(description: str) -> str:
    """
    "Q4 2026" from a forthcoming project's description, or '' when absent.

    This is what the photo slot shows for a source that has no photos. A
    Future Projects page is 27 Ashghal rows, and 27 identical blocks reading
    the source name say nothing; 27 blocks reading Q1 / Q3 / Q4 2026 are the
    one fact a reader scanning a pipeline actually wants.
    """
    m = _EXPECTED_QUARTER_RE.search(description or '')
    if not m:
        return ''
    return f'Q{_QUARTER_NO[m.group(1).lower()]} {m.group(2)}'


def standfirst_of(description: str) -> str:
    """The article's standfirst as the card shows it: tag off, whitespace flat."""
    text = _KEEP_TAG_RE.sub('', description or '')
    return ' '.join(text.split())


def read_url_of(tender) -> tuple[str, bool]:
    """
    (url, is_external) - where a News card sends the reader.

    The publisher's article, when the scraper recorded one: the card shows
    the headline and a summary, and the detail belongs to the story itself,
    read where it was written. The in-app page is the fallback for a row
    that has no article address.
    """
    meta = getattr(tender, 'magazine_meta', None)
    url = (getattr(meta, 'listing_url', '') or tender.source_url or '').strip()
    if url.lower().startswith(('http://', 'https://')):
        return url, True
    return reverse('tenders:detail', args=[tender.pk]), False


class _MagazineListBase(LoginRequiredMixin, ListView):
    """
    Shared queryset for the public (AEC) list and the admin Non-AEC audit list.

    The two pages differ only in which side of the `is_aec` flag they show, so
    they share everything else rather than keeping two copies of the filters in
    step by hand.
    """
    model = Tender
    template_name = 'magazine/list.html'
    context_object_name = 'tenders'
    # 24, not 25, because the card grid reflows between four, three and two
    # columns and 24 is the largest sensible page size divisible by all three.
    # At 25 every page after the first ended with a single orphan card and
    # three empty cells beside it - six of eight pages on the live data.
    #
    paginate_by = 24

    #: Columns the card grid shows on a desktop. Mirrors the CSS in
    #: list.html - `repeat(auto-fill, minmax(300px, 1fr))` - which cannot be
    #: read from Python, so a test keeps the two in step.
    GRID_COLUMNS = 4

    #: Draw the magazine card layout - featured strip then a grid of cards -
    #: rather than the compact table. True everywhere now, including the
    #: Non-AEC audit page: a reviewer reading rejected announcements wants to
    #: see the headline, the standfirst and the photograph, which is what the
    #: decision was made from, and the table showed none of them.
    #:
    #: The audit page once kept its own behaviour - plain date order and no
    #: freshness window. It no longer does: it takes the same ordering and the
    #: same window as News. The only thing that still separates the two lists
    #: is which side of the classifier each shows.
    photo_layout = True
    non_aec_mode = False

    def _mode_count(self, mode: str) -> int:
        """
        How many rows a given button would show.

        Deliberately runs the real get_queryset with the parameter overridden,
        rather than counting with a second hand-written filter. A separate
        count is a second implementation of the same rule, and the two drift —
        the badge then promises a number the click does not deliver.
        """
        original = self.request.GET
        try:
            params = original.copy()
            params['view'] = mode
            self.request.GET = params
            return self.get_queryset().count()
        finally:
            self.request.GET = original

    def get_paginator(self, queryset, per_page, orphans=0,
                      allow_empty_first_page=True, **kwargs):
        """
        Every page is the same size.

        Page one used to carry one extra card: its featured strip consumed
        five before the grid started, and the extra brought the grid back to
        whole rows. The strip is gone - every item is a card of the same
        size now - so the first page is an ordinary page. The paginator is
        kept, with no extra, so the page arithmetic the templates and tests
        rely on is unchanged.
        """
        return FirstPageLargerPaginator(
            queryset, per_page, first_page_extra=0, orphans=orphans,
            allow_empty_first_page=allow_empty_first_page, **kwargs)

    #: Countries that get their own tab above the list, in order. A country
    #: appears only when a registered source publishes from it, so adding a
    #: paper to sources.py is all it takes for its tab to appear — there is
    #: no second list here to keep in step.
    COUNTRY_TAB_ORDER = ('JO', 'SA')

    def _magazines_for(self, country: str) -> list:
        """
        The source filter options, narrowed to the chosen country.

        A country tab that still offered every source would promise rows the
        country filter has already excluded: pick Jordan, pick Construction
        Week, get nothing. The options and the rows have to agree.
        """
        codes = regions.codes_for(country) if country else None
        wanted = None
        if country:
            wanted = {c.upper() for c in (codes or [country])}
        return [(c, cfg.get('display_en') or c)
                for c, cfg in sorted(MAGAZINES.items())
                if wanted is None
                or (cfg.get('country_code') or '').upper() in wanted]

    def _country_tabs(self, chosen: str) -> list:
        """
        The Jordan / Saudi Arabia buttons, built from the registry.

        Each tab carries the count of what it would actually show, so an
        empty country is visible before it is clicked rather than after.
        """
        by_country = {}
        for cfg in MAGAZINES.values():
            code = (cfg.get('country_code') or '').upper()
            if code:
                by_country.setdefault(code, []).append(cfg)

        tabs = [{'code': '', 'name': 'All', 'name_ar': 'الكل',
                 'active': not chosen, 'count': self._country_count('')}]
        ordered = [c for c in self.COUNTRY_TAB_ORDER if c in by_country]
        ordered += sorted(c for c in by_country if c not in self.COUNTRY_TAB_ORDER)
        for code in ordered:
            names = {'JO': ('Jordan', 'الأردن'), 'SA': ('Saudi Arabia', 'السعودية'),
                     'AE': ('UAE', 'الإمارات'), 'QA': ('Qatar', 'قطر')}
            name, name_ar = names.get(code, (code, code))
            tabs.append({'code': code, 'name': name, 'name_ar': name_ar,
                         'active': chosen.upper() == code,
                         'count': self._country_count(code)})
        return tabs

    def _country_count(self, code: str) -> int:
        """
        How many rows that tab would show.

        Runs the real get_queryset with the parameter overridden, the same
        way _mode_count does, rather than counting with a second hand-written
        filter that would drift from the first.
        """
        original = self.request.GET
        try:
            params = original.copy()
            if code:
                params['country'] = code
            else:
                params.pop('country', None)
            params.pop('magazine', None)
            self.request.GET = params
            return self.get_queryset().count()
        finally:
            self.request.GET = original

    def get_queryset(self):
        today = timezone.now().date()
        user = self.request.user

        qs = (Tender.objects
              .select_related('country', 'source', 'magazine_meta')
              .filter(magazine_meta__isnull=False)
              .filter(is_relevant=True)
              # The same story from two papers is one story. Five Jordanian
              # sources cover one country and the trade titles syndicate
              # each other, so the Ajloun hotel arrived from Al-Rai and
              # Al-Ghad and sat on the page twice, one row apart. The later
              # copy is marked a mirror at save time (magazine_tenders/
              # dedupe.py); nothing is deleted, and it is still in admin and
              # reachable by search. Applied to BOTH lists, so the audit page
              # does not list a rejection twice either.
              .filter(is_mirror=False)
              .filter(Q(submission_deadline__gte=today) |
                      Q(submission_deadline__isnull=True)))

        # The AEC gate is the classification service's is_aec, widened to include
        # tenders whose title says supervision/design — AJi's actual work. See
        # tenders/aec_scope.py; nothing here writes is_aec.
        from tenders.aec_scope import aec_display_q, non_aec_display_q
        # The two lists are complements: every announcement belongs to exactly
        # one of them. News takes the classifier's side plus the exemption;
        # the audit page takes the other side MINUS the same exemption, so a
        # story promoted to News stops being listed as rejected.
        #
        # Subtracting it is what keeps the halves from overlapping. Adding the
        # exemption to News alone put 121 announcements on both pages, each
        # under a heading contradicting the other.
        if self.non_aec_mode:
            qs = qs.filter(non_aec_display_q()).exclude(news_exemption_q())
        elif getattr(settings, 'MAGAZINE_AEC_ONLY', True):
            # The classifier asks whether an item is separable design or
            # supervision work AJi could bid for - right for a tender, far too
            # narrow for a news page, where it was holding back the day's
            # ground-breakings and contract awards. TENDER and DESIGN items
            # still face it; see news_exemption_q for what does not.
            qs = qs.filter(aec_display_q() | news_exemption_q())

        # Layer three of the freshness window: applied to what is DISPLAYED.
        # The save-time guard stops old items arriving; it cannot expire a row
        # that grew old in place, and nothing else on this page can either —
        # it filters on submission_deadline, and news articles rarely carry
        # one. Without this, an item stored today is still presented as news
        # two years from now.
        #
        # Undated rows are kept: Future Projects lives entirely on them.
        #
        # The audit page used to be exempt, so that an admin asking "why is
        # this not on the News page?" could always find the row. It follows
        # the same window now, by request: the two pages behave alike, and an
        # audit of announcements nobody can act on any more is not worth
        # reading. The trade is real and worth knowing - an announcement older
        # than the window is no longer listed anywhere in the UI, though it is
        # still in the database and still reachable by search. Widen or switch
        # off MAGAZINE_MAX_AGE_MONTHS to see them again.
        cut = freshness.cutoff_date()
        if cut is not None:
            qs = qs.filter(Q(publication_date__gte=cut) |
                           Q(publication_date__isnull=True))

        # ── the Future Projects button ───────────────────────────────────
        # ?view=future narrows to the sources flagged 'forthcoming' in the
        # registry; ?view=news is everything else. Neither is the default:
        # with no parameter the page shows both, as it always did.
        view_mode = self.request.GET.get('view', '').strip().lower()
        forthcoming = sources.forthcoming_codes()
        if forthcoming:
            prefixes = [f'MAGAZINE_{c}' for c in forthcoming]
            if view_mode == 'future':
                qs = qs.filter(source__scraper_class__in=prefixes)
            elif view_mode == 'news':
                qs = qs.exclude(source__scraper_class__in=prefixes)

        categories_sel = [c for c in self.request.GET.getlist('category') if c.strip()]
        status = self.request.GET.get('status', '').strip()
        q      = self.request.GET.get('q', '').strip()
        mag    = self.request.GET.get('magazine', '').strip().upper()

        if categories_sel:
            qs = qs.filter(category__in=categories_sel)
        if status:
            qs = qs.filter(status=status)
        if mag:
            qs = qs.filter(magazine_meta__magazine=mag)

        # ── country / region ─────────────────────────────────────────────
        # One parameter carries both, because a region key is a word and a
        # country code is two letters, so the two cannot be confused. An
        # unrecognised value narrows to nothing rather than falling back to
        # the unfiltered list: a typo that silently shows everything is worse
        # than one that shows nothing, because nothing is visibly wrong.
        country = self.request.GET.get('country', '').strip()
        if country:
            codes = regions.codes_for(country)
            if codes is not None:
                qs = qs.filter(country__code__in=codes)
            else:
                qs = qs.filter(country__code__iexact=country)
        if q:
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(title_ar__icontains=q) |
                Q(reference_no__icontains=q) |
                Q(entity__icontains=q) |
                Q(entity_ar__icontains=q) |
                Q(description__icontains=q) |
                Q(description_ar__icontains=q)
            )

        # Newest publication first. Not '-scraped_at', which looks the
        # same on a fresh database and is meaningless on a real one: a sweep
        # re-reads the whole feed in one minute, so every row shares a scrape
        # time and the page came out in essentially random date order —
        # April, then December, then January.
        #
        # scraped_at stays as the tie-breaker: Gulf Construction publishes a
        # whole monthly column on one date, and without it those rows would
        # shuffle between page loads.
        #
        # nulls_last is explicit rather than left to the database. Every row
        # carries a date today, but an undated one must sink to the bottom,
        # not float to the top — which is what a NULL does by default on some
        # backends.
        # Items WITH a photo come first and the photo-less ones sit at the
        # bottom — a wall of photos, then the notices. Done in the query
        # rather than per page so it holds across pagination: every photo card
        # precedes every photo-less one. Within each group the order is still
        # newest publication first. This applies to the audit page too, which
        # draws the same cards.
        order = [F('publication_date').desc(nulls_last=True), '-scraped_at']
        # Future Projects runs by QUARTER, not by date. It is a pipeline:
        # nothing in it has happened, so the publication date orders it by
        # when somebody typed the notice, while the quarter is what a reader
        # scanning a pipeline is after.
        #
        # The CURRENT quarter opens the page, then the quarters to come, in
        # order. Quarters that have already passed - a notice that said Q1
        # and is still listed in October - come after those, most recent
        # first: still worth seeing, but not ahead of work that is actually
        # ahead. "Soonest first" alone put 67 stale Q1 notices above the
        # eleven for this quarter. Projects with no stated quarter go last,
        # because "not known yet" is a real answer and must not pose as Q1.
        future_keys = []
        if view_mode == 'future':
            current = quarters.current_key()
            qs = qs.annotate(
                quarter_bucket=Case(
                    When(magazine_meta__expected_quarter='', then=Value(2)),
                    When(magazine_meta__expected_quarter__lt=current, then=Value(1)),
                    default=Value(0), output_field=IntegerField()),
                # '2026Q4' -> 20264, so the key sorts as a number and the
                # passed quarters can run in reverse.
                quarter_num=(Cast(Substr('magazine_meta__expected_quarter', 1, 4),
                                  IntegerField()) * 10
                             + Cast(Substr('magazine_meta__expected_quarter', 6, 1),
                                    IntegerField())))
            qs = qs.annotate(quarter_sort=Case(
                When(quarter_bucket=1, then=-F('quarter_num')),
                default=F('quarter_num'), output_field=IntegerField()))
            future_keys = ['quarter_bucket', 'quarter_sort']
            order = future_keys + order
        qs = annotate_new_for_user(qs, user)
        # Photos first, then the photo-less ones, and inside that second
        # block an unread item leads. Applied on every list, the audit
        # page included: it draws the same cards now, and a grid that
        # mixes photographs with blank blocks at random reads as broken
        # whichever page it is on.
        qs = qs.annotate(has_photo=Case(
            When(magazine_meta__image_url='', then=Value(0)),
            default=Value(1), output_field=IntegerField()))
        # Inside the photo-less block ONLY, an item the reader has not
        # opened leads. That block sits below every photo card and its
        # items carry no picture to catch the eye, so a new arrival there
        # is the easiest thing on the page to miss. The photo group is
        # deliberately left in date order: it is the front page.
        #
        # Expressed as a sort key rather than a second query so it cannot
        # drift from the photo rule above, and so it holds across
        # pagination like that rule does.
        qs = qs.annotate(unread_first=Case(
            When(has_photo=0, is_new=True, then=Value(1)),
            default=Value(0), output_field=IntegerField()))
        # Photos lead on NEWS. On Future Projects the quarter outranks
        # them: that page is a pipeline read in date-of-work order, and
        # letting the handful of rows that happen to carry a picture jump
        # the queue restarts the sequence - Q1, Q3, Q4, then Q1 again.
        if view_mode == 'future':
            order = ['-has_photo', '-unread_first'] + order
            order = [k for k in order if k not in future_keys]
            order = future_keys + order
        else:
            order = ['-has_photo', '-unread_first'] + order
        qs = qs.order_by(*order)
        return annotate_bookmarked_for_user(qs, user)

    def _country_facets(self):
        """
        What the country menu should offer, and how much is behind each entry.

        Built from the page's own rows with every filter applied EXCEPT the
        country one - otherwise choosing Qatar would collapse the menu to
        Qatar alone and there would be no way back to another country without
        clearing the filter first.

        Reuses get_queryset with the parameter removed rather than counting
        with a second hand-written query, for the same reason _mode_count
        does: a separate count is a second implementation of the same rule,
        and the two drift.
        """
        original = self.request.GET
        try:
            params = original.copy()
            params.pop('country', None)
            self.request.GET = params
            ids = list(self.get_queryset().values_list('id', flat=True))
        finally:
            self.request.GET = original

        # Counted over a CLEAN queryset rather than the page's own. The page's
        # carries the sort annotations - has_photo, unread_first, is_new - and
        # Django puts every one of them into the GROUP BY of a
        # values().annotate(), which splits one country into a row per
        # combination: the first run of this listed Egypt three times and
        # offered 148 countries where the data holds 28.
        #
        # Taking the ids first keeps the filter rule in one place, which was
        # the point of reusing get_queryset at all.
        rows = (Tender.objects.filter(id__in=ids)
                .values('country__code', 'country__name_en',
                        'country__flag_emoji')
                .annotate(n=Count('id')))
        rows = [r for r in rows if r['country__code']]

        countries = sorted(
            ({'code': r['country__code'],
              'name': r['country__name_en'] or r['country__code'],
              'flag': r['country__flag_emoji'] or '',
              'count': r['n']} for r in rows),
            key=lambda c: (-c['count'], c['name']))

        # Region counts are ROWS, not countries - the number a reader wants
        # beside the name is how much they will get, not how many states it
        # spans.
        present = {c['code'] for c in countries}
        region_options = []
        for key, label, codes in regions.all_regions():
            n = sum(c['count'] for c in countries if c['code'] in codes)
            if present & codes:
                region_options.append({'key': key, 'label': label, 'count': n})
        return region_options, countries

    def get_context_data(self, **kwargs):
        """Same context vocabulary the newspaper page uses, so the two templates
        stay siblings instead of drifting into two dialects."""
        ctx = super().get_context_data(**kwargs)

        # The user's own AEC vote per row, so the vote buttons render in the
        # right state. Identical to the newspaper list view.
        from tenders.models import AECFeedback
        tenders = list(ctx.get(self.context_object_name) or [])
        ids = [t.pk for t in tenders]
        votes = {}
        if ids:
            for fb in AECFeedback.objects.filter(tender_id__in=ids,
                                                 user=self.request.user):
                votes[fb.tender_id] = fb.vote
        for t in tenders:
            t.user_aec_vote = votes.get(t.pk)
            t.standfirst = standfirst_of(t.description)
            # What the photo slot shows when there is no photo. Only the
            # forthcoming sources carry a quarter; everything else falls back
            # to the source name in the template.
            # Read the STORED quarter, never re-parsed from the prose:
            # DevelopmentAid forecasts have a quarter and no wording to
            # parse, so parsing showed the publisher name on a card the
            # page had just sorted by quarter.
            t.fallback_label = (t.magazine_meta.quarter_display
                                or expected_quarter(t.description))
            # Where the card sends the reader: the publisher's article.
            t.read_url, t.read_external = read_url_of(t)
            # The AI-written summary when there is one; the publisher's
            # excerpt until then (see summaries.py).
            t.summary_text = t.magazine_meta.summary or t.standfirst

        # ── the photo layout ─────────────────────────────────────────────
        # Every public tab reads as a wall of equal cards: photos where the
        # publisher has them, headline, summary, the way to the article.
        # Future Projects has no photos at all — the Ashghal list carries
        # none — so its blocks show the expected quarter instead. The Non-AEC
        # audit page uses the same cards: a reviewer judging a classification
        # needs the headline, the standfirst and the photograph it was made
        # from.
        view_mode = self.request.GET.get('view', '').strip().lower()
        photo_layout = self.photo_layout
        grid_items = tenders

        region_options, country_options = self._country_facets()
        chosen = self.request.GET.get('country', '').strip()
        country_label = regions.label_for(chosen) or next(
            (c['name'] for c in country_options
             if c['code'].lower() == chosen.lower()), '')

        ctx.update({
            'photo_layout': photo_layout,
            'grid_items':   grid_items,
            'profile':      get_or_create_profile(self.request.user),
            'categories':   TENDER_CATEGORY_FILTER_CHOICES,
            'statuses':     TenderStatus.choices,
            # The source list follows the country tab. Picking Jordan and
            # then being offered Construction Week in the source filter is
            # a promise the list cannot keep — the country filter has
            # already removed every row that option could match, so the
            # click returns nothing. Thirteen sources across two countries
            # made that visible; it will get worse as more are added.
            'magazines':    self._magazines_for(chosen),
            'country_tabs': self._country_tabs(chosen),
            'non_aec_mode': self.non_aec_mode,
            'last_run':     MagazineRun.objects.first(),   # Meta.ordering = newest first
            'is_running':   _is_running(),
            'next_run':     _next_scheduled_run(),
            'magazine_configured': bool(MAGAZINES),
            'region_options':  region_options,
            'country_options': country_options,
            'country_filter_label': country_label,
            'active_filters': {
                'category':  self.request.GET.getlist('category'),
                'status':    self.request.GET.get('status', ''),
                'q':         self.request.GET.get('q', ''),
                'magazine':  self.request.GET.get('magazine', ''),
                'view':      self.request.GET.get('view', ''),
                'country':   self.request.GET.get('country', '').strip(),
            },
            'view_mode':      self.request.GET.get('view', '').strip().lower(),
            'future_count':   self._mode_count('future'),
            'news_count':     self._mode_count('news'),
            'has_forthcoming': bool(sources.forthcoming_codes()),
        })

        # News ticker bar — items PUBLISHED today, same rule and same card as
        # the dashboard's news bar (tenders.views._today_news_tickers), so the
        # two never disagree about what "published today" means. Public News
        # page only: the audit page is a review tool, not a front page.
        if not self.non_aec_mode:
            from tenders.views import _today_news_tickers
            ctx['today_news_tickers'] = list(
                _today_news_tickers(timezone.localdate())[:200])

        return ctx


class MagazineTenderListView(_MagazineListBase):
    """/news/ — AEC announcements, for every signed-in user."""
    non_aec_mode = False


class NonAECMagazineListView(_MagazineListBase):
    """/news/non-aec/ — audit view, admins only (enforced in urls.py)."""
    non_aec_mode = True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['it_section'] = 'magazine_non_aec'
        return ctx


# ── Manual "check magazines now" trigger ──────────────────────────────────────
# Same shape as tenders.views.trigger_all_scrapers and the newspaper trigger: a
# background thread plus a busy-lock, so two clicks never start two sweeps. The
# runner is idempotent, so a race would be harmless — the lock is there to keep
# the answer to the user honest.
_trigger_lock = threading.Lock()
_trigger_running = False


def _is_running() -> bool:
    from . import runner
    return _trigger_running or runner.is_running()


def _next_scheduled_run():
    """Next automatic sweep, or None when the scheduler is not running here."""
    try:
        from .apps import next_scheduled_run
        return next_scheduled_run()
    except Exception:
        return None


def _run_magazines_now():
    global _trigger_running
    try:
        from .models import MagazineRun as _Run
        from .runner import run_all
        run_all(trigger=_Run.Trigger.MANUAL)
    except Exception as exc:
        logger.error(f'[magazine] manual sweep crashed: {exc}')
    finally:
        _trigger_running = False


@login_required
def trigger_magazine_check(request):
    """POST — start a sweep in the background. Returns JSON, never blocks."""
    global _trigger_running

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    if not MAGAZINES:
        return JsonResponse({'ok': False,
                             'error': 'No news sources are configured yet.'}, status=400)

    with _trigger_lock:
        if _is_running():
            return JsonResponse({'ok': False, 'busy': True,
                                 'error': 'A magazine sweep is already running.'})
        _trigger_running = True

    threading.Thread(target=_run_magazines_now, daemon=True,
                     name='magazine-manual-sweep').start()
    logger.info(f'[magazine] manual sweep started by {request.user.username}')
    return JsonResponse({'ok': True, 'started': True})


@login_required
def retry_run(request, pk):
    """POST — re-run the magazine that this run belonged to."""
    global _trigger_running

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    run = get_object_or_404(MagazineRun, pk=pk)

    with _trigger_lock:
        if _is_running():
            return JsonResponse({'ok': False, 'busy': True,
                                 'error': 'A magazine sweep is already running.'})
        _trigger_running = True

    def _retry():
        global _trigger_running
        try:
            from .models import MagazineRun as _Run
            from .runner import run_one
            run_one(run.magazine, trigger=_Run.Trigger.MANUAL)
        except Exception as exc:
            logger.error(f'[magazine] retry of {run.magazine} crashed: {exc}')
        finally:
            _trigger_running = False

    threading.Thread(target=_retry, daemon=True, name='magazine-retry').start()
    logger.info(f'[magazine] retry of {run.magazine} started by {request.user.username}')
    return JsonResponse({'ok': True, 'started': True, 'magazine': run.magazine})


# ─────────────────────────────────────────────────────────────────────────────
# Daily News Brief — موجز الأخبار — PDF for the CEO
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def news_brief_pdf(request):
    """
    GET — today's News Brief PDF, rendered on the fly.

    Opens inline for the "Review Brief" button; ?download=1 forces a download.
    Always regenerated so the review reflects the current data.
    """
    from django.http import HttpResponse

    from .brief import brief_filename, build_news_brief_pdf, get_today_news

    try:
        pdf = build_news_brief_pdf(get_today_news())
    except Exception as exc:
        logger.exception(f'[news-brief] PDF generation failed: {exc}')
        return JsonResponse({'ok': False,
                             'error': f'PDF generation failed: {exc}'}, status=500)

    disposition = 'attachment' if request.GET.get('download') else 'inline'
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'{disposition}; filename="{brief_filename()}"'
    return resp


@login_required
def news_brief_send(request):
    """
    POST — send today's News Brief PDF to the CEO.

    Always e-mails the PDF as an attachment to NEWS_BRIEF_CEO_EMAIL; when
    NEWS_BRIEF_CEO_WHATSAPP is configured and the WhatsApp integration is
    enabled, the document is also sent by WhatsApp (WaSenderAPI downloads it
    from the public site URL, so the brief is persisted under /media/ first).
    Returns per-channel JSON so the button can show exactly what happened.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    from tenders.integrations import MSGraphMailer, WhatsAppClient
    from tenders.models import IntegrationSettings

    from .brief import (brief_filename, build_news_brief_pdf, get_today_news,
                        save_news_brief)

    items = list(get_today_news())
    if not items:
        return JsonResponse({'ok': False,
                             'error': 'No news items were published today — '
                                      'nothing to send.'}, status=400)

    try:
        pdf = build_news_brief_pdf(items)
        _, media_url_path = save_news_brief(pdf)
    except Exception as exc:
        logger.exception(f'[news-brief] PDF generation failed: {exc}')
        return JsonResponse({'ok': False,
                             'error': f'PDF generation failed: {exc}'}, status=500)

    today = timezone.localdate()
    fname = brief_filename(today)
    n     = len(items)
    ceo_email = getattr(settings, 'NEWS_BRIEF_CEO_EMAIL', '')
    ceo_wa    = getattr(settings, 'NEWS_BRIEF_CEO_WHATSAPP', '')
    results   = {'email': None, 'whatsapp': None}

    # ── E-mail with the PDF attached ──────────────────────────────────────
    if ceo_email:
        mailer = MSGraphMailer()
        subj = (f'AJi Daily News Brief — {n} item{"s" if n != 1 else ""} '
                f'· {today:%d %b %Y}')
        body = (f'<p>Attached is the AJi daily news brief for '
                f'<strong>{today:%d %b %Y}</strong> — {n} news item'
                f'{"s" if n != 1 else ""} published today.</p>'
                f'<p>TenderRadar — automated message.</p>')
        ok = mailer.send_mail(subj, body, to=[ceo_email],
                              category='NEWS_BRIEF', triggered_by=request.user,
                              attachments=[(fname, pdf, 'application/pdf')])
        results['email'] = {'ok': ok, 'to': ceo_email,
                            'detail': '' if ok else mailer.last_error}
    else:
        results['email'] = {'ok': False, 'to': '',
                            'detail': 'NEWS_BRIEF_CEO_EMAIL is not configured.'}

    # ── WhatsApp document (optional — needs a number + enabled integration) ──
    if ceo_wa:
        cfg = IntegrationSettings.get()
        site = (cfg.digest_site_url or '').rstrip('/')
        if not site:
            results['whatsapp'] = {'ok': False, 'to': ceo_wa,
                                   'detail': 'digest_site_url is not set — the '
                                             'provider cannot download the PDF.'}
        else:
            wa = WhatsAppClient(cfg)
            caption = (f'AJi Daily News Brief · {today:%d %b %Y} — {n} news '
                       f'item{"s" if n != 1 else ""} published today.')
            ok, detail = wa.send_document(ceo_wa, f'{site}{media_url_path}',
                                          fname, caption,
                                          category='NEWS_BRIEF',
                                          triggered_by=request.user)
            results['whatsapp'] = {'ok': ok, 'to': ceo_wa,
                                   'detail': '' if ok else detail}

    any_ok = any(r and r['ok'] for r in results.values())
    logger.info(f'[news-brief] send by {request.user.username}: {results}')
    return JsonResponse({'ok': any_ok, 'count': n, 'results': results},
                        status=200 if any_ok else 502)
