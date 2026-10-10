# tenders/views.py

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Exists, OuterRef
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView
import json
import logging

logger = logging.getLogger('tenders')

from .models import (CountryConfig, Tender, TenderBookmark, TenderCategory,
                     TENDER_CATEGORY_FILTER_CHOICES,
                     TenderStatus, ScraperSource, ScrapeLog, TenderUserNote,
                     TenderView, UserProfile, IndustryChoice,
                     INDUSTRY_SCRAPER_CATEGORY_MAP, AECFeedback)
from .forms import UserProfileForm
from .tasks import run_scraper  # kept for any other task references


def annotate_new_for_user(qs, user):
    """Annotate queryset with is_new (True if user has never opened the tender)."""
    viewed_subq = TenderView.objects.filter(tender=OuterRef('pk'), user=user)
    return qs.annotate(is_new=~Exists(viewed_subq))


def annotate_bookmarked_for_user(qs, user):
    """
    Annotate queryset with is_bookmarked for *this* user.

    Bookmarks live in TenderBookmark, one row per user per tender. The annotation
    keeps the name the templates already use, so a row shows the reader's own
    bookmark state and never anybody else's.
    """
    bookmark_subq = TenderBookmark.objects.filter(tender=OuterRef('pk'), user=user)
    return qs.annotate(is_bookmarked=Exists(bookmark_subq))


def get_or_create_profile(user) -> UserProfile:
    """Return the user's profile, creating a default one if missing."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def apply_industry_filter(qs, profile: UserProfile):
    """
    Apply industry-based filtering to a tender queryset.
    - GEN  → no filter (see everything)
    - AEC  → filter by is_aec=True
    - others → filter by scraper category codes mapped to this industry
    """
    if profile.is_general:
        return qs
    if profile.is_aec_industry:
        return qs.filter(is_aec=True)
    cats = INDUSTRY_SCRAPER_CATEGORY_MAP.get(profile.industry, [])
    if cats:
        return qs.filter(category__in=cats)
    return qs


def apply_country_filter(qs, profile: UserProfile):
    """Filter by the user's focus countries (if any selected)."""
    codes = profile.get_focus_country_codes()
    if codes:
        return qs.filter(country__code__in=codes)
    return qs


class TenderListView(LoginRequiredMixin, ListView):
    model = Tender
    template_name = 'tenders/list.html'
    context_object_name = 'tenders'
    paginate_by = 25

    def get_queryset(self):
        today    = timezone.now().date()
        # Support multiple values: ?country=JO&country=AE&country=SA
        countries = [c for c in self.request.GET.getlist('country') if c.strip()]
        sources   = [s for s in self.request.GET.getlist('source')  if s.strip()]
        user      = self.request.user
        profile   = get_or_create_profile(user)

        qs = Tender.objects.select_related('source', 'country', 'mirror_of__source', 'mirror_of__country').filter(
            is_relevant=True,
        ).filter(
            Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True)
        )

        # A BOOKMARK IS AN EXPLICIT ACT. The filters below this line are
        # policy - what the site chooses to show someone browsing - and none
        # of them should be able to hide a row the user deliberately saved.
        # Read here rather than further down because those filters come first.
        viewing_bookmarks = bool(self.request.GET.get('bookmarked', '').strip())

        # Newspaper tenders (عطاءات الجريدة) and magazine announcements
        # (الأخبار) live under their own sections, so they are hidden
        # from BROWSING here — but they ARE included when the user SEARCHES
        # (?q=) and in the personal BOOKMARKS view, so nothing a user cares
        # about can be missed.
        if (not self.request.GET.get('q', '').strip()
                and not viewing_bookmarks):
            qs = qs.exclude(
                Q(source__scraper_class__startswith='NEWSPAPER_') |
                Q(source__scraper_class__startswith='MAGAZINE_')
            )

        # ── Site-wide policy: TenderRadar only shows AEC tenders ───────────────
        # Superusers may pass ?all=1 to see everything (AEC and non-AEC).
        #
        # Not in the bookmarks view. Since News began carrying construction
        # PROJECT news the classifier had marked Non-AEC, this filter was
        # emptying that page: the user bookmarked a story, the row was saved,
        # and the list refused to show it back. Nothing told them why.
        show_all = user.is_superuser and self.request.GET.get('all', '').strip()
        if not show_all and not viewing_bookmarks:
            qs = qs.filter(is_aec=True)

        if not countries and not viewing_bookmarks:
            qs = qs.filter(is_mirror=False)

        # ── Industry / profile filter (skip for superusers) ────────────────
        # Also skipped for bookmarks: a saved row must come back even if the
        # reader's industry or country preferences have changed since.
        if not user.is_superuser and not viewing_bookmarks:
            qs = apply_industry_filter(qs, profile)
            if not countries:
                qs = apply_country_filter(qs, profile)

        categories_sel = [c for c in self.request.GET.getlist('category') if c.strip()]
        status     = self.request.GET.get('status', '').strip()
        q          = self.request.GET.get('q', '').strip()
        bookmarked = viewing_bookmarks

        if countries:
            qs = qs.filter(country__code__in=countries)
        if sources:
            qs = qs.filter(source__id__in=sources)
        if categories_sel:
            qs = qs.filter(category__in=categories_sel)
        if status:
            qs = qs.filter(status=status)
        if bookmarked:
            # This user's own bookmarks. unique_together on (tender, user) means
            # the join cannot duplicate rows.
            qs = qs.filter(bookmarks__user=user)

        # Date range filter (scraped_at = discovered date)
        date_from = self.request.GET.get('date_from', '').strip()
        date_to   = self.request.GET.get('date_to', '').strip()
        quick     = self.request.GET.get('quick', '').strip()
        if quick == 'today':
            qs = qs.filter(scraped_at__date=today)
        elif quick == '3days':
            from datetime import timedelta
            qs = qs.filter(scraped_at__date__gte=today - timedelta(days=3))
        elif quick == 'week':
            from datetime import timedelta
            qs = qs.filter(scraped_at__date__gte=today - timedelta(days=7))
        elif quick == 'month':
            from datetime import timedelta
            qs = qs.filter(scraped_at__date__gte=today - timedelta(days=30))
        else:
            if date_from:
                try:
                    from datetime import datetime
                    qs = qs.filter(scraped_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
                except ValueError:
                    pass
            if date_to:
                try:
                    from datetime import datetime
                    qs = qs.filter(scraped_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
                except ValueError:
                    pass

        # ?aec=1 — show only AEC-related tenders
        aec_only = self.request.GET.get('aec', '').strip()
        if aec_only:
            qs = qs.filter(is_aec=True)

        # ?new=1 — show only unseen tenders (not yet opened by this user)
        new_only = self.request.GET.get('new', '').strip()
        if new_only:
            viewed_ids = TenderView.objects.filter(
                user=self.request.user
            ).values_list('tender_id', flat=True)
            qs = qs.exclude(id__in=viewed_ids)

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

        # Apply user-defined source order if set
        profile = get_or_create_profile(self.request.user)
        ordered_src_ids = profile.get_ordered_source_ids()
        if ordered_src_ids and not sources:
            # Use CASE WHEN to sort by user-preferred source order
            from django.db.models import Case, When, IntegerField, Value
            order_cases = [
                When(source__id=src_id, then=Value(i))
                for i, src_id in enumerate(ordered_src_ids)
            ]
            qs = annotate_new_for_user(qs, user).annotate(
                src_pos=Case(
                    *order_cases,
                    default=Value(len(ordered_src_ids)),
                    output_field=IntegerField(),
                )
            ).order_by('src_pos', 'sort_order', '-scraped_at')
        else:
            qs = annotate_new_for_user(qs, user).order_by('sort_order', '-scraped_at')

        # Each row shows the reader's own bookmark, not a flag shared by everyone.
        return annotate_bookmarked_for_user(qs, user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = get_or_create_profile(self.request.user)

        # Build user AEC vote map for all AEC tenders on this page
        user_aec_votes = {}
        tenders = ctx.get('object_list', [])
        if self.request.user.is_authenticated and tenders:
            aec_ids = [t.pk for t in tenders if getattr(t, 'is_aec', False)]
            if aec_ids:
                for fb in AECFeedback.objects.filter(
                    tender_id__in=aec_ids, user=self.request.user
                ):
                    user_aec_votes[fb.tender_id] = fb.vote

        # Annotate each tender with its user vote
        for t in tenders:
            t.user_aec_vote = user_aec_votes.get(t.pk)

        ctx.update({
            'countries':  CountryConfig.objects.filter(is_active=True, is_organization=False).order_by('sort_order'),
            'categories': TENDER_CATEGORY_FILTER_CHOICES,
            'statuses':   TenderStatus.choices,
            'profile':    profile,
            'active_filters': {
                'country':    self.request.GET.getlist('country'),
                'category':   self.request.GET.getlist('category'),
                'status':     self.request.GET.get('status', ''),
                'source':     self.request.GET.getlist('source'),
                'q':          self.request.GET.get('q', ''),
                'bookmarked': self.request.GET.get('bookmarked', ''),
                'new':        self.request.GET.get('new', ''),
                'aec':        self.request.GET.get('aec', ''),
                'date_from':  self.request.GET.get('date_from', ''),
                'date_to':    self.request.GET.get('date_to', ''),
                'quick':      self.request.GET.get('quick', ''),
                'all':        self.request.GET.get('all', ''),
            },
            'all_sources': ScraperSource.objects.filter(is_active=True).order_by('country__sort_order','name'),
            'active_source_names': list(
                ScraperSource.objects.filter(id__in=self.request.GET.getlist('source'))
                .values_list('name', flat=True)
            ),
        })
        return ctx


class TenderDetailView(LoginRequiredMixin, DetailView):
    model = Tender
    template_name = 'tenders/detail.html'
    context_object_name = 'tender'

    def get_template_names(self):
        """
        A News item renders as an article, everything else as a tender.

        The article template EXTENDS the tender one and replaces only the
        parts that do not fit an article — status pill, lifecycle, deadline —
        so notes, team notes and the share modal are inherited, not copied.
        """
        if getattr(self.object, 'magazine_meta', None) is not None:
            return ['magazine/detail.html', self.template_name]
        return [self.template_name]

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        # Check if new BEFORE marking as viewed
        self._is_new = not TenderView.objects.filter(tender=self.object, user=request.user).exists()
        # Mark as viewed
        TenderView.objects.get_or_create(tender=self.object, user=request.user)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        tender = self.get_object()
        if 'save_notes' in request.POST:
            # The reader's own note. This used to write a single field on the
            # tender, so saving overwrote whatever another user had written.
            body = request.POST.get('notes', '').strip()
            if body:
                TenderUserNote.objects.update_or_create(
                    tender=tender, user=request.user, defaults={'body': body})
                messages.success(request, 'Your notes were saved.')
            else:
                deleted, _ = TenderUserNote.objects.filter(
                    tender=tender, user=request.user).delete()
                if deleted:
                    messages.success(request, 'Your notes were cleared.')
        return redirect('tenders:detail', pk=tender.pk)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        t   = self.object
        ctx['related'] = Tender.objects.filter(
            category=t.category, country=t.country,
            is_mirror=False,
        ).exclude(pk=t.pk).order_by('-scraped_at')[:5]
        ctx['is_new'] = getattr(self, '_is_new', False)
        # Mirror info: if this is a mirror, show original; if original, show its mirrors
        if t.is_mirror and t.mirror_of:
            ctx['mirror_original'] = t.mirror_of
            ctx['mirror_siblings']  = t.mirror_of.mirrors.exclude(pk=t.pk).select_related('country')[:10]
        else:
            ctx['mirror_copies'] = t.mirrors.select_related('country').all()[:20]
        # AEC feedback counts, current user vote, and dates for lifecycle
        if t.is_aec:
            ctx['aec_up_count']   = t.aec_feedbacks.filter(vote='UP').count()
            ctx['aec_down_count'] = t.aec_feedbacks.filter(vote='DOWN').count()
            if self.request.user.is_authenticated:
                fb = t.aec_feedbacks.filter(user=self.request.user).first()
                ctx['user_aec_vote']    = fb.vote if fb else None
                ctx['user_aec_fb_date'] = fb.updated_at if fb else None
            else:
                ctx['user_aec_vote']    = None
                ctx['user_aec_fb_date'] = None
        else:
            ctx['user_aec_vote']    = None
            ctx['user_aec_fb_date'] = None

        # This reader's own bookmark and note, if any — never another user's.
        signed_in = self.request.user.is_authenticated
        own_bookmark = (
            TenderBookmark.objects.filter(tender=t, user=self.request.user).first()
            if signed_in else None
        )
        ctx['is_bookmarked'] = own_bookmark is not None
        ctx['bookmarked_at'] = own_bookmark.created_at if own_bookmark else None
        ctx['my_note'] = (
            TenderUserNote.objects.filter(tender=t, user=self.request.user).first()
            if signed_in else None
        )

        # ── Teams + team-scoped @mention data ─────────────────────────────────
        # A note is shared with one chosen team; you may only @mention people who
        # belong to that team. We pass, per team, its members (each with avatar)
        # so the compose box can restrict the mention list to the selected team.
        from django.templatetags.static import static
        ctx['avatar_base'] = static('img/avatars/')

        if self.request.user.is_authenticated:
            from .models import Team
            from django.db.models import Q

            teams = list(
                Team.objects.filter(
                    Q(leader=self.request.user) | Q(members=self.request.user)
                ).distinct().order_by('name')
                .select_related('leader', 'leader__profile')
                .prefetch_related('members', 'members__profile')
            )
            ctx['user_teams'] = teams

            def _udict(u):
                av = ''
                try:
                    av = u.profile.avatar or ''
                except Exception:
                    av = ''
                return {'u': u.username, 'n': (u.get_full_name() or u.username), 'av': av}

            teams_json = []
            for t in teams:
                roster = list(t.members.all())
                if all(m.id != t.leader_id for m in roster):
                    roster = [t.leader] + roster
                # de-dupe + stable sort by display name
                seen, people = set(), []
                for u in roster:
                    if u.id in seen:
                        continue
                    seen.add(u.id)
                    people.append(_udict(u))
                people.sort(key=lambda d: d['n'].lower())
                teams_json.append({
                    'id': t.pk,
                    'name': t.name,
                    'leader': (t.leader.get_full_name() or t.leader.username),
                    'members': people,
                })
            ctx['teams_json'] = teams_json
        else:
            ctx['user_teams'] = []
            ctx['teams_json'] = []

        meta = getattr(t, 'magazine_meta', None)
        if meta is not None:
            ctx.update(self._article_context(t, meta))
        return ctx

    def _article_context(self, t, meta) -> dict:
        """
        What the article page needs beyond the tender's own fields.

        Imported inside the method: magazine_tenders.views imports from this
        module at load time, so a module-level import here would be circular.
        """
        from django.db.models import F, Q
        from django.urls import reverse

        from magazine_tenders import sources
        from magazine_tenders.views import expected_quarter, standfirst_of

        from .aec_scope import aec_display_q

        forthcoming = meta.magazine in sources.forthcoming_codes()
        more = (Tender.objects
                .filter(magazine_meta__magazine=meta.magazine, is_relevant=True)
                .filter(aec_display_q())
                .exclude(pk=t.pk)
                .select_related('country', 'magazine_meta')
                .order_by(F('publication_date').desc(nulls_last=True), '-scraped_at')[:3])
        more_rows = list(more)
        for m in more_rows:
            # The small cards show the quarter where there is no photo, like
            # the list does.
            m.fallback_label = (m.magazine_meta.quarter_display
                                or expected_quarter(m.description))
        return {
            # The AI-written summary leads the article page too; the
            # publisher's standfirst when there is none yet.
            'standfirst':       meta.summary or standfirst_of(t.description),
            'fallback_label':   (meta.quarter_display
                                 or expected_quarter(t.description)),
            'is_forthcoming':   forthcoming,
            'news_back_url':    reverse('magazine_tenders:list')
                                + ('?view=future' if forthcoming else '?view=news'),
            'more_from_source': more_rows,
        }




# ── Country coordinates for map bubbles ───────────────────────────────────────
_COUNTRY_COORDS = {
    # Middle East & Gulf
    'JO': (31.24, 36.51), 'SA': (23.89, 45.08), 'AE': (24.00, 54.00),
    'KW': (29.37, 47.98), 'QA': (25.35, 51.18), 'BH': (26.02, 50.55),
    'OM': (21.51, 55.92), 'YE': (15.55, 48.52), 'IQ': (33.22, 43.68),
    'SY': (34.80, 38.99), 'LB': (33.85, 35.86), 'PS': (31.95, 35.23),
    'IL': (31.05, 34.85), 'IR': (32.43, 53.69),
    # Africa
    'EG': (26.82, 30.80), 'LY': (26.34, 17.23), 'TN': (33.89, 9.54),
    'DZ': (28.03, 1.66),  'MA': (31.79, -7.09), 'SD': (12.86, 30.22),
    'ET': (9.14, 40.49),  'KE': (0.02, 37.91),  'TZ': (-6.37, 34.89),
    'UG': (1.37, 32.29),  'RW': (-1.94, 29.87), 'NG': (9.08, 8.68),
    'GH': (7.95, -1.02),  'SN': (14.50, -14.45),'CI': (7.54, -5.55),
    'CM': (3.85, 11.50),  'MZ': (-18.66, 35.53),'ZM': (-13.13, 27.85),
    'ZA': (-30.56, 22.94),'AO': (-11.20, 17.87),'TG': (8.62, 0.82),
    'BJ': (9.31, 2.32),   'MG': (-18.77, 46.87),'GW': (11.80, -15.18),
    # Asia Pacific
    'AD': (28.70, 77.10), 'IN': (20.59, 78.96), 'CN': (35.86, 104.20),
    'PK': (30.38, 69.35), 'BD': (23.68, 90.36), 'LK': (7.87, 80.77),
    'NP': (28.39, 84.12), 'PH': (12.88, 121.77),'ID': (-0.79, 113.92),
    'VN': (14.06, 108.28),'KH': (12.57, 104.99),'MM': (16.87, 96.19),
    'TH': (15.87, 100.99),'KR': (35.91, 127.77),'JP': (36.20, 138.25),
    # Europe & International
    'DE': (51.17, 10.45), 'FR': (46.23, 2.21),  'GB': (55.38, -3.44),
    'TR': (38.96, 35.24), 'UA': (48.38, 31.17),
    # World Bank / Multilateral (virtual positions)
    'WB': (0, 20),  'AB': (-5, 20),  'BA': (-5, 25), 'AD': (25, 85),
}


def _ordered_by_country(country_qs, profile):
    """Return country stats list ordered by user's preferred country order."""
    rows = list(country_qs)
    ordered_codes = profile.get_focus_country_codes()
    if not ordered_codes:
        return rows
    code_pos = {code: i for i, code in enumerate(ordered_codes)}
    def sort_key(row):
        code = row.get('country__code', '')
        return (code_pos.get(code, len(ordered_codes)), -row.get('n', 0))
    return sorted(rows, key=sort_key)


def _build_aec_map_data(country_codes: list = None, unseen_ids: set = None):
    """
    Return a dict of {country_code: {n, lat, lon, name, flag, code}}
    for AEC tenders. Counts only UNSEEN tenders when unseen_ids is provided.
    Filtered to country_codes when provided (empty list = all countries).
    """
    from django.utils import timezone as tz
    today = tz.now().date()
    qs = (
        Tender.objects
              .filter(is_aec=True, is_mirror=False)
              .filter(Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True))
              # News has its own pages; counting it here would put its totals
              # on the map and in the country tiles.
              .exclude(NEWS_SOURCES_Q)
    )
    if country_codes:
        qs = qs.filter(country__code__in=country_codes)
    if unseen_ids is not None:
        qs = qs.filter(id__in=unseen_ids)

    rows = qs.values('country__code', 'country__name_en', 'country__flag_emoji').annotate(n=Count('id'))

    result = {}
    for row in rows:
        code = row['country__code']
        if not code:
            continue
        coords = _COUNTRY_COORDS.get(code)
        if not coords:
            continue
        result[code] = {
            'n':    row['n'],
            'lat':  coords[0],
            'lon':  coords[1],
            'name': row['country__name_en'] or code,
            'flag': row['country__flag_emoji'] or '',
            'code': code,
        }
    return result


#: News items are excluded from the dashboard — every list, and every counter.
#:
#: The dashboard is for TENDERS: the portal sources and the newspaper
#: advertisements, both of which are things that can be bid on. News is not.
#: It also arrives in bulk — one Ashghal sweep added 97 forthcoming projects —
#: and every dashboard list sorts by scraped_at, so a single import took every
#: slot and the headline read 43 open AEC tenders when 7 were tenders.
#:
#: NEWSPAPER_ is deliberately NOT excluded. Newspaper rows are real tenders
#: that happen to have been read out of a newspaper rather than a portal, so
#: they belong on the dashboard alongside the portal ones.
NEWS_SOURCES_Q = Q(source__scraper_class__startswith='MAGAZINE_')


def _today_news_tickers(today):
    """
    News — الأخبار — items PUBLISHED today, for the dashboard news bar.

    Published, not scraped. A sweep re-reads the same articles every morning,
    so "scraped today" made the bar show the whole backlog again each day —
    on 10 Sep it was 53 items, of which none had actually been published that
    day. The date the publisher put on the article is the honest one.

    Undated rows drop out on their own here, which is right: they are the
    Ashghal prospected projects, a standing pipeline rather than something
    that was published today.
    """
    from django.conf import settings as dj_settings

    from .aec_scope import aec_display_q

    qs = (Tender.objects
          .select_related('country', 'source', 'magazine_meta')
          .filter(magazine_meta__isnull=False,
                  is_relevant=True,
                  publication_date=today))
    if getattr(dj_settings, 'MAGAZINE_AEC_ONLY', True):
        qs = qs.filter(aec_display_q())
    return qs.order_by('-scraped_at')


def _today_newspaper_tickers(today):
    """
    Newspaper Tenders — عطاءات الجريدة — scraped today, for the dashboard's
    own newspaper bar. Mirrors the Newspaper page's display rule
    (NEWSPAPER_AEC_ONLY + the widened AEC scope). Scraped, not published:
    a printed edition is processed once, on the day it is read, so the two
    dates agree — and the edition date is what the card shows.
    """
    from django.conf import settings as dj_settings

    from .aec_scope import aec_display_q

    qs = (Tender.objects
          .select_related('country', 'source', 'newspaper_meta',
                          'newspaper_meta__edition')
          .filter(newspaper_meta__isnull=False,
                  is_relevant=True,
                  scraped_at__date=today))
    if getattr(dj_settings, 'NEWSPAPER_AEC_ONLY', True):
        qs = qs.filter(aec_display_q())
    return qs.order_by('-scraped_at')


class DashboardView(LoginRequiredMixin, ListView):
    model = Tender
    template_name = 'tenders/dashboard.html'
    context_object_name = 'recent_tenders'

    def get_queryset(self):
        today   = timezone.now().date()
        user    = self.request.user
        profile = get_or_create_profile(user)

        qs = Tender.objects.select_related('country').filter(
            is_relevant=True, is_aec=True, status='OPEN', is_mirror=False,
        ).filter(
            Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True)
        )

        # This list is browsing. News is kept off it; newspaper tenders stay,
        # because they are tenders — read from a newspaper instead of a portal.
        qs = qs.exclude(NEWS_SOURCES_Q)

        if not user.is_superuser:
            qs = apply_country_filter(qs, profile)

        return annotate_new_for_user(qs, user).order_by('-scraped_at')[:10]

    def get_context_data(self, **kwargs):
        from .apps import full_run_is_active, next_scheduled_run

        ctx     = super().get_context_data(**kwargs)
        today   = timezone.now().date()
        user    = self.request.user
        profile = get_or_create_profile(user)

        def _base_qs():
            """
            Originals-only base queryset. Site-wide policy: AEC tenders only.
            News is excluded (it is not biddable work); newspaper tenders are
            included, because they are tenders.
            """
            qs = Tender.objects.filter(
                is_relevant=True, is_aec=True, is_mirror=False,
            ).filter(
                Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True)
            ).exclude(NEWS_SOURCES_Q)
            if not user.is_superuser:
                qs = apply_country_filter(qs, profile)
            return qs

        def _country_qs():
            """
            Includes mirrors — matches what the user sees when clicking a
            country. AEC only, and excludes News so the per-country figures
            agree with the totals above them.
            """
            qs = Tender.objects.filter(
                is_relevant=True, is_aec=True,
            ).filter(
                Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True)
            ).exclude(NEWS_SOURCES_Q)
            if not user.is_superuser:
                qs = apply_country_filter(qs, profile)
            return qs

        base_qs    = _base_qs()
        country_qs = _country_qs()

        # ── Admin-only: stats over ALL tenders, AEC and non-AEC alike ──────
        if user.is_superuser:
            all_qs = Tender.objects.filter(is_mirror=False)
            ctx['admin_stats'] = {
                'all_total':   all_qs.count(),
                'all_aec':     all_qs.filter(is_aec=True).count(),
                'all_non_aec': all_qs.filter(is_aec=False).count(),
                'open_all':    all_qs.filter(status='OPEN').count(),
                'all_today':   all_qs.filter(scraped_at__date=today).count(),
                'irrelevant':  all_qs.filter(is_relevant=False).count(),
            }

        viewed_ids = TenderView.objects.filter(
            user=user
        ).values_list('tender_id', flat=True)

        new_by_country = {
            row['country__code']: row['n']
            for row in country_qs.exclude(id__in=viewed_ids)
                                  .values('country__code')
                                  .annotate(n=Count('id'))
        }
        aec_by_country = {
            row['country__code']: row['n']
            for row in country_qs.filter(is_aec=True)
                                  .values('country__code')
                                  .annotate(n=Count('id'))
        }

        qs = base_qs
        # Top Countries — sorted by tender count (highest first)
        by_country = list(
            country_qs.filter(country__is_organization=False)
                       .values('country__code', 'country__name_en', 'country__flag_emoji')
                       .annotate(n=Count('id')).order_by('-n')
        )
        # Favourite countries (per-user, in click order)
        favorite_codes = profile.get_favorite_codes()
        _by_code = {r['country__code']: r for r in by_country}
        favorites = [_by_code[c] for c in favorite_codes if c in _by_code]
        ctx.update({
            'profile':          profile,
            'industry_label':   profile.get_industry_display(),
            'total_tenders':    qs.count(),
            'open_tenders':     qs.filter(status='OPEN').count(),
            # This user's own saved tenders, not everybody's.
            'bookmarked':       qs.filter(bookmarks__user=user).count(),
            'new_tenders':      qs.exclude(id__in=viewed_ids).count(),
            'aec_tenders':      qs.filter(is_aec=True).count(),
            'expiring_7d':      qs.filter(
                status='OPEN',
                submission_deadline__gte=today,
                submission_deadline__lte=today + timezone.timedelta(days=7)
            ).count(),
            'by_country':       by_country,
            'favorites':        favorites,
            'favorite_codes':   favorite_codes,
            # largest country count — used to scale the Top-Countries bars
            'top_country_n':    max((r['n'] for r in by_country), default=1),
            'new_by_country':   new_by_country,
            'aec_by_country':   aec_by_country,
            'by_category': list(
                qs.values('category').annotate(n=Count('id')).order_by('-n')[:8]
            ),
            # 10 newest AEC tenders — filtered by user's focus countries
            'aec_latest': list(
                apply_country_filter(
                    Tender.objects.select_related('country', 'source')
                                  .filter(is_aec=True, is_mirror=False)
                                  .filter(Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True))
                                  .exclude(NEWS_SOURCES_Q)
                                  .order_by('-scraped_at'),
                    profile
                )[:10]
            ),
            # AEC map — NEW (unseen) tenders only, filtered by user's focus countries
            'aec_map_data': _build_aec_map_data(
                country_codes=profile.get_focus_country_codes() if not user.is_superuser else [],
                unseen_ids=set(Tender.objects.filter(is_aec=True, is_mirror=False)
                                             .exclude(NEWS_SOURCES_Q)
                                             .exclude(id__in=viewed_ids)
                                             .values_list('id', flat=True)),
            ),
            # exclude(NEWSPAPER_ALRAI): the Newspaper Tenders source row is an
            # internal anchor for عطاءات الجريدة, not a scraper — never listed
            'sources':          ScraperSource.objects.select_related('country').exclude(Q(scraper_class__startswith='NEWSPAPER_') | Q(scraper_class__startswith='MAGAZINE_')).order_by('country'),
            'recent_logs':      ScrapeLog.objects.select_related('source').order_by('-started_at')[:10],
            'last_scrape':      ScrapeLog.objects.order_by('-started_at').first(),
            # When the automatic scan next runs, so "Run All Scanners" is an
            # option rather than the only way anyone knows scanning happens.
            'next_scan_at':     next_scheduled_run(),
            'scan_running':     full_run_is_active(),
            'category_choices': dict(TenderCategory.choices),
            # Today's discovered AEC tenders for the ticker bar. Newspaper
            # rows are kept out: they have their own bar below, and one item
            # scrolling in two bars reads as a duplicate.
            'today_tickers': list(
                apply_country_filter(
                    Tender.objects.select_related('country', 'source')
                                  .filter(
                                      is_relevant=True,
                                      is_aec=True,
                                      is_mirror=False,
                                      scraped_at__date=today,
                                      newspaper_meta__isnull=True,
                                  )
                                  .filter(
                                      Q(submission_deadline__gte=today) | Q(submission_deadline__isnull=True)
                                  )
                                  .exclude(NEWS_SOURCES_Q)
                                  .order_by('-scraped_at'),
                    profile
                )[:200]
            ),
            # Newspaper Tenders — عطاءات الجريدة — scraped today, for their
            # own ticker bar (small ad photo + title). Not filtered by the
            # user's countries: the printed dailies are AJi's home market.
            'today_newspaper_tickers': list(_today_newspaper_tickers(today)[:200]),
            # News — الأخبار — PUBLISHED today, for the news ticker bar.
            # Same display rule as the News page (magazine rows, widened AEC
            # scope, MAGAZINE_AEC_ONLY); not filtered by the user's countries
            # because news items are regional by nature. The bar hides itself
            # when nothing was published — see the {% if %} in dashboard.html.
            'today_news_tickers': list(_today_news_tickers(today)[:200]),
        })
        return ctx


class SourceListView(LoginRequiredMixin, ListView):
    model = ScraperSource
    template_name = 'tenders/sources.html'
    context_object_name = 'sources'

    def get_queryset(self):
        # The NEWSPAPER_/MAGAZINE_ rows are internal anchors for the
        # Newspaper Tenders and News features — those are their
        # own sections, not scrapers in this list.
        return ScraperSource.objects.select_related('country').exclude(
            Q(scraper_class__startswith='NEWSPAPER_') |
            Q(scraper_class__startswith='MAGAZINE_'),
        ).annotate(
            tender_count=Count('tenders', distinct=True),
            url_count=Count('tendering_urls', distinct=True),
        ).order_by('country', 'name')

    def get_context_data(self, **kwargs):
        from django.conf import settings
        from .apps import full_run_is_active, next_scheduled_run

        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        ctx['total_sources'] = qs.count()
        ctx['active_sources'] = qs.filter(is_active=True).count()
        agg = qs.aggregate(
            total_urls=Count('tendering_urls', distinct=True),
            total_tenders=Count('tenders', distinct=True),
        )
        ctx['total_urls'] = agg['total_urls'] or 0
        ctx['total_tenders'] = agg['total_tenders'] or 0

        # What actually schedules scraping: APScheduler inside this web process.
        # The page used to describe Celery Beat, which does not run here.
        ctx['schedule_hours'] = getattr(settings, 'SCRAPER_SCHEDULE_HOURS', 6)
        ctx['next_scan_at']   = next_scheduled_run()
        ctx['scan_running']   = full_run_is_active()
        return ctx


# ── Actions ───────────────────────────────────────────────────────────────────

@login_required
@require_POST
def delete_tender(request, pk):
    """
    Permanently delete a tender from the database. Admin-only (Super Admin /
    System Administrator) — the button is hidden for everyone else, and this
    check enforces it server-side. Works for scraper AND newspaper tenders;
    a newspaper tender's stored ad photo file is removed from disk too
    (the CASCADE only deletes the DB row, not the file).
    """
    from .permissions import can_administer
    if not can_administer(request.user):
        return JsonResponse({'error': 'Admin only'}, status=403)

    tender = get_object_or_404(Tender, pk=pk)
    meta = getattr(tender, 'newspaper_meta', None)
    if meta is not None and meta.ad_image:
        meta.ad_image.delete(save=False)
    title = tender.title
    tender.delete()
    logger.info(f'[tenders] #{pk} deleted by {request.user.username}: '
                f'{title[:80]!r}')
    return JsonResponse({'deleted': True})


@login_required
@require_POST
def toggle_aec(request, pk):
    """
    Admin override of the AI's AEC classification, stored in TenderRadar
    only (AJi AI is untouched). Flips is_aec and keeps is_aec_checked=True
    so the enrichment pipeline never re-classifies over a human decision.
    """
    from .permissions import can_administer
    if not can_administer(request.user):
        return JsonResponse({'error': 'Admin only'}, status=403)

    tender = get_object_or_404(Tender, pk=pk)
    tender.is_aec = not tender.is_aec
    tender.is_aec_checked = True
    tender.save(update_fields=['is_aec', 'is_aec_checked'])
    logger.info(f'[tenders] #{pk} is_aec manually set to {tender.is_aec} '
                f'by {request.user.username}')
    return JsonResponse({'is_aec': tender.is_aec})


@login_required
@require_POST
def toggle_bookmark(request, pk):
    """
    Add or remove *this user's* bookmark on a tender.

    POST only. Every caller in the app already posts with a CSRF token
    (list.html, detail.html); on GET this used to flip the bookmark for anyone
    who merely loaded the URL — a link preview, a browser prefetch or an
    <img src> was enough to change stored data.
    """
    tender = get_object_or_404(Tender, pk=pk)

    bookmark, created = TenderBookmark.objects.get_or_create(
        tender=tender, user=request.user)
    if not created:
        bookmark.delete()

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'bookmarked': created})
    return redirect('tenders:detail', pk=pk)


@login_required
def trigger_scraper(request, source_id):
    """
    Start one scraper in a background thread.

    Open to every signed-in user: a scan only reads public tender listings and
    changes no configuration.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from .apps import start_source_run

    # start_source_run claims the slot before it answers, so 'started' means a
    # scan really did start.
    if not start_source_run(source_id):
        return JsonResponse({
            'status':  'busy',
            'message': 'This source is already being scanned — the results will appear shortly.',
        })
    return JsonResponse({'status': 'started', 'source_id': source_id})


@login_required
def trigger_all_scrapers(request):
    """
    Start a full scan of every active source in a background thread.

    Open to every signed-in user — the button sits on the dashboard, which
    everyone sees. Only one full scan runs at a time; a second request while one
    is in progress is answered with 'busy' rather than starting a duplicate run
    that would fetch every portal twice over.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from .apps import start_full_run

    # start_full_run takes the run slot before it answers, so two people pressing
    # the button at the same moment cannot both be told the scan started.
    if not start_full_run():
        return JsonResponse({
            'status':  'busy',
            'message': 'A full scan is already running — new tenders appear as each source finishes.',
        })
    return JsonResponse({'status': 'started'})


@login_required
def scraper_status(request):
    """Recent scrape runs, plus whether a scan is active and when the next is due."""
    from .apps import full_run_is_active, next_scheduled_run

    logs = ScrapeLog.objects.select_related('source').order_by('-started_at')[:20]
    data = [{
        'source':   str(log.source),
        'status':   log.status,
        'found':    log.tenders_found,
        'new':      log.tenders_new,
        'updated':  log.tenders_updated,
        'started':  log.started_at.isoformat(),
        'duration': log.duration_seconds,
        'error':    log.error_message[:200] if log.error_message else '',
    } for log in logs]

    nxt = next_scheduled_run()
    return JsonResponse({
        'logs':      data,
        'running':   full_run_is_active(),
        'next_run':  nxt.isoformat() if nxt else None,
    })


# scrape_logs() removed — it listed the newest 100 ScrapeLog rows for every
# signed-in user. System Dashboard → System Logs → Scraper Runs shows the same
# model, paginated across all 13,000+ rows and restricted to administrators, so
# this was the weaker of two implementations of one feature.
# Template templates/tenders/scrape_logs.html deleted with it.


@login_required
@require_POST
def toggle_favorite_country(request, code):
    """
    Toggle a country as a favourite for the current user (append in click order).

    POST only, for the same reason as toggle_bookmark: dashboard.html already
    posts with a CSRF token, and on GET this wrote to the profile of anyone who
    just loaded the URL.
    """
    import json
    profile = get_or_create_profile(request.user)
    code = (code or '').upper()
    codes = profile.get_favorite_codes()
    if code in codes:
        codes.remove(code)
        favorite = False
    else:
        codes.append(code)
        favorite = True
    profile.favorite_countries = json.dumps(codes)
    profile.save(update_fields=['favorite_countries'])
    return JsonResponse({'favorite': favorite, 'favorites': codes})


# ── User Profile ──────────────────────────────────────────────────────────────

def split_username(username):
    """Derive (first, last) from a username like 'samer_kilani' → ('Samer', 'Kilani')."""
    parts = [p for p in (username or '').replace('.', '_').replace('-', '_').split('_') if p]
    if not parts:
        return (username or '', '')
    first = parts[0].capitalize()
    last = ' '.join(p.capitalize() for p in parts[1:])
    return (first, last)


# Allowed preset avatars (role-based creative set in static/img/avatars/)
AVATAR_CHOICES = ['avf1.svg', 'avf2.svg', 'avf3.svg', 'avm1.svg', 'avm2.svg', 'avm3.svg', 'av7.svg', 'av8.svg']

# Country dialing codes + exact national (mobile) digit lengths for validation.
PHONE_COUNTRIES = [
    {'code': '+968', 'name': 'Oman',         'iso': 'OM', 'digits': 8},
    {'code': '+962', 'name': 'Jordan',       'iso': 'JO', 'digits': 9},
    {'code': '+971', 'name': 'UAE',          'iso': 'AE', 'digits': 9},
    {'code': '+966', 'name': 'Saudi Arabia', 'iso': 'SA', 'digits': 9},
    {'code': '+20',  'name': 'Egypt',        'iso': 'EG', 'digits': 10},
]
_PHONE_DIGITS = {c['code']: c['digits'] for c in PHONE_COUNTRIES}


def _split_phone(stored):
    """Split a stored phone ('+962 798286418') into (code, national_digits)."""
    import re
    stored = (stored or '').strip()
    for c in sorted(_PHONE_DIGITS, key=len, reverse=True):
        if stored.replace(' ', '').startswith(c):
            return c, re.sub(r'\D', '', stored[len(c):] if not stored.startswith(c + ' ') else stored[len(c):])
    digits = re.sub(r'\D', '', stored)
    # default to Jordan when we can't detect a code
    return '+962', digits


@login_required
def user_profile(request):
    """View / edit the current user's industry profile."""
    import re
    from django.shortcuts import render
    from .models import ScraperSource
    profile = get_or_create_profile(request.user)

    # Check if new order fields exist (migration may not have run yet)
    has_order_fields = hasattr(profile, 'country_order')

    # Phone editing state (re-populated on POST so values survive a failed save)
    phone_code, phone_number = _split_phone(profile.phone)
    phone_error = None

    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=profile)

        # ── Phone: strict per-country digit validation ───────────────────────
        phone_code   = (request.POST.get('phone_code') or '+962').strip()
        phone_number = re.sub(r'\D', '', request.POST.get('phone_number') or '')
        if phone_code not in _PHONE_DIGITS:
            phone_code = '+962'
        phone_combined = ''
        if phone_number:
            expected = _PHONE_DIGITS[phone_code]
            if len(phone_number) != expected:
                phone_error = f'Enter exactly {expected} digits for {phone_code}. You entered {len(phone_number)}.'
            else:
                phone_combined = f'{phone_code} {phone_number}'

        if form.is_valid() and not phone_error:
            saved = form.save(commit=False)
            # Enforce AEC-only if user doesn't have permission to see all
            if not profile.can_see_all_tenders:
                saved.industry = 'AEC'
            saved.save()
            form.save_m2m()
            # Save ordering fields
            country_order = request.POST.get('country_order', '')
            source_order  = request.POST.get('source_order', '')
            # Avatar choice (from the preset set)
            avatar = request.POST.get('avatar', '').strip()
            if avatar in AVATAR_CHOICES or avatar == '':
                profile.avatar = avatar
            # Identity — first / last name (username stays locked) + phone
            u = request.user
            u.first_name = request.POST.get('first_name', '').strip()[:30]
            u.last_name  = request.POST.get('last_name', '').strip()[:30]
            u.save(update_fields=['first_name', 'last_name'])
            profile.phone = phone_combined[:40]
            try:
                profile.country_order = country_order
                profile.source_order  = source_order
                profile.save(update_fields=['country_order', 'source_order', 'avatar', 'phone'])
            except Exception as e:
                import logging
                logging.getLogger('tenders').warning(f'[Profile] order save failed: {e}')
            messages.success(request, 'Profile updated successfully.')
            return redirect('tenders:profile')
        elif phone_error:
            messages.error(request, phone_error)
    else:
        form = UserProfileForm(instance=profile)

    # ── Build sources list in saved order ────────────────────────────────────
    all_sources = list(ScraperSource.objects.select_related('country').filter(
        is_active=True).order_by('country__sort_order', 'name'))

    try:
        ordered_source_ids = [int(x) for x in profile.get_ordered_source_ids()]
    except Exception:
        ordered_source_ids = []

    if ordered_source_ids:
        src_map        = {s.id: s for s in all_sources}
        sources_for_ui = [src_map[i] for i in ordered_source_ids if i in src_map]
        sources_for_ui += [s for s in all_sources if s.id not in ordered_source_ids]
    else:
        sources_for_ui = all_sources

    # ── Build countries list in saved order ───────────────────────────────────
    import json as _json
    selected_countries = list(profile.countries.select_related().all())

    try:
        saved_country_order = _json.loads(profile.country_order) if profile.country_order else []
    except Exception:
        saved_country_order = []

    if saved_country_order:
        country_map        = {c.code: c for c in selected_countries}
        countries_for_order = [country_map[code] for code in saved_country_order if code in country_map]
        countries_for_order += [c for c in selected_countries if c.code not in saved_country_order]
    else:
        countries_for_order = selected_countries

    # Derive first/last from the username (e.g. "samer_kilani" → "Samer", "Kilani")
    first_name, last_name = split_username(request.user.username)
    first_name = request.user.first_name or first_name
    last_name  = request.user.last_name or last_name

    return render(request, 'tenders/profile.html', {
        'form':               form,
        'profile':            profile,
        'sources_for_ui':     sources_for_ui,
        'countries_for_order': countries_for_order,
        'has_order_fields':   has_order_fields,
        'avatars':            AVATAR_CHOICES,
        'first_name':         first_name,
        'last_name':          last_name,
        'phone':              profile.phone,
        'phone_countries':    PHONE_COUNTRIES,
        'phone_code':         phone_code,
        'phone_number':       phone_number,
        'phone_error':        phone_error,
    })


# ── AEC Feedback ──────────────────────────────────────────────────────────────

def aec_feedback(request, pk):
    """POST: submit thumbs up/down on AEC classification. Returns JSON."""
    from django.http import JsonResponse
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Login required'}, status=403)

    tender = get_object_or_404(Tender, pk=pk, is_aec=True)
    vote   = request.POST.get('vote') or ''
    if vote not in ('UP', 'DOWN'):
        return JsonResponse({'error': 'Invalid vote'}, status=400)

    obj, created = AECFeedback.objects.update_or_create(
        tender=tender, user=request.user,
        defaults={'vote': vote},
    )

    # Return updated counts
    ups   = tender.aec_feedbacks.filter(vote='UP').count()
    downs = tender.aec_feedbacks.filter(vote='DOWN').count()
    return JsonResponse({
        'vote': vote, 'ups': ups, 'downs': downs, 'created': created
    })


def aec_feedback_report(request):
    """Report page — accuracy stats + charts + tender lists."""
    from django.db.models import Count, Q
    # Aggregate per tender
    tenders_with_feedback = Tender.objects.filter(
        is_aec=True, aec_feedbacks__isnull=False
    ).distinct().annotate(
        up_count   = Count('aec_feedbacks', filter=Q(aec_feedbacks__vote='UP')),
        down_count = Count('aec_feedbacks', filter=Q(aec_feedbacks__vote='DOWN')),
    ).order_by('-up_count', '-down_count')

    total_votes = AECFeedback.objects.count()
    total_up    = AECFeedback.objects.filter(vote='UP').count()
    total_down  = AECFeedback.objects.filter(vote='DOWN').count()
    accuracy_pct = round(total_up / total_votes * 100, 1) if total_votes else 0

    # Split lists
    accurate    = [t for t in tenders_with_feedback if t.up_count >= t.down_count and t.up_count > 0]
    inaccurate  = [t for t in tenders_with_feedback if t.down_count > t.up_count]
    no_feedback = Tender.objects.filter(is_aec=True, aec_feedbacks__isnull=True).count()

    # Per-user contribution stats
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user_stats = AECFeedback.objects.values(
        'user__username', 'user__id'
    ).annotate(
        total  = Count('id'),
        ups    = Count('id', filter=Q(vote='UP')),
        downs  = Count('id', filter=Q(vote='DOWN')),
    ).order_by('-total')

    return render(request, 'tenders/aec_feedback_report.html', {
        'total_votes':    total_votes,
        'total_up':       total_up,
        'total_down':     total_down,
        'accuracy_pct':   accuracy_pct,
        'accurate':       accurate,
        'inaccurate':     inaccurate,
        'no_feedback':    no_feedback,
        'total_aec':      Tender.objects.filter(is_aec=True).count(),
        'user_stats':     list(user_stats),
    })


# ══════════════════════════════════════════════════════════════════════════════
# TEAM & NOTES VIEWS
# ══════════════════════════════════════════════════════════════════════════════

def is_team_leader(user):
    """
    Kept as a thin alias so older callers keep working. New code should use the
    predicates in tenders.permissions, which is the single source of truth for
    who may do what.
    """
    from .permissions import can_create_team
    return can_create_team(user)


@login_required
def my_teams(request):
    """
    Teams the user leads or belongs to — plus, for administrators, every other
    team on the system so they can manage teams they did not create.
    """
    from .models import Team
    from django.shortcuts import render
    from .permissions import can_create_team, can_administer_teams

    led     = Team.objects.filter(leader=request.user).prefetch_related('members')
    joined  = Team.objects.filter(members=request.user).exclude(leader=request.user)

    is_admin  = can_administer_teams(request.user)
    all_teams = []
    if is_admin:
        # Everything the two lists above do not already show, so a team never
        # appears twice on the page.
        all_teams = (Team.objects
                     .exclude(leader=request.user)
                     .exclude(members=request.user)
                     .select_related('leader')
                     .prefetch_related('members')
                     .distinct())

    return render(request, 'tenders/teams/list.html', {
        'led_teams':    led,
        'joined_teams': joined,
        'all_teams':    all_teams,
        'is_admin':     is_admin,
        'is_leader':    can_create_team(request.user),
    })


def _resolve_leader(request, fallback):
    """
    The leader a submitted form is asking for.

    Only an administrator may name somebody else — for anyone else the field is
    ignored entirely, so a posted `leader` cannot be used to hand a team away or
    to seize one. Falls back to `fallback` if the id is missing or not a real
    active user.
    """
    from django.contrib.auth import get_user_model
    from .permissions import can_administer_teams

    if not can_administer_teams(request.user):
        return fallback
    raw = (request.POST.get('leader') or '').strip()
    if not raw:
        return fallback
    User = get_user_model()
    return User.objects.filter(pk=raw, is_active=True).first() or fallback


@login_required
def team_create(request):
    from .models import Team
    from django.shortcuts import render, redirect
    from django.contrib.auth import get_user_model
    from .permissions import can_create_team, can_administer_teams

    if not can_create_team(request.user):
        messages.error(request, 'Only Team Leaders and administrators can create teams.')
        return redirect('tenders:my_teams')

    User = get_user_model()
    is_admin = can_administer_teams(request.user)
    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        desc  = request.POST.get('description', '').strip()
        mids  = request.POST.getlist('members')
        leader = _resolve_leader(request, request.user)
        if not name:
            messages.error(request, 'Team name is required.')
        elif Team.objects.filter(name__iexact=name).exists():
            # name is unique=True — catch it here rather than as an IntegrityError
            messages.error(request, f'A team called "{name}" already exists.')
        else:
            team = Team.objects.create(name=name, description=desc, leader=leader)
            # The leader is a member implicitly (see Team.all_member_ids), so
            # never store them in the through table as well.
            team.members.set(User.objects.filter(id__in=mids).exclude(id=leader.id))
            if leader != request.user:
                messages.success(request, f'Team "{name}" created, led by {leader.username}.')
            else:
                messages.success(request, f'Team "{name}" created.')
            return redirect('tenders:my_teams')

    users = User.objects.filter(is_active=True).order_by('username')
    return render(request, 'tenders/teams/form.html', {
        # An admin may set up a team led by somebody else and still be a member
        # of it, so they must appear in their own member list. A team leader is
        # always the leader, and the leader is a member implicitly.
        'users': users if is_admin else users.exclude(id=request.user.id),
        'leader_choices': users if is_admin else None,
        'is_admin': is_admin,
        'team': None,
    })


@login_required
def team_edit(request, pk):
    from .models import Team
    from django.shortcuts import render, redirect, get_object_or_404
    from django.contrib.auth import get_user_model
    from .permissions import can_edit_team, can_administer_teams

    team = get_object_or_404(Team, pk=pk)
    if not can_edit_team(request.user, team):
        messages.error(request, 'You can only edit teams you lead.')
        return redirect('tenders:my_teams')

    User = get_user_model()
    is_admin = can_administer_teams(request.user)
    if request.method == 'POST':
        name = request.POST.get('name', team.name).strip()
        if not name:
            messages.error(request, 'Team name is required.')
            return redirect('tenders:team_edit', pk=pk)
        if Team.objects.filter(name__iexact=name).exclude(pk=team.pk).exists():
            messages.error(request, f'A team called "{name}" already exists.')
            return redirect('tenders:team_edit', pk=pk)
        team.name        = name
        team.description = request.POST.get('description', '').strip()
        team.leader      = _resolve_leader(request, team.leader)
        mids = request.POST.getlist('members')
        team.save()
        team.members.set(User.objects.filter(id__in=mids).exclude(id=team.leader_id))
        messages.success(request, 'Team updated.')
        return redirect('tenders:my_teams')

    users = User.objects.filter(is_active=True).order_by('username')
    return render(request, 'tenders/teams/form.html', {
        # Admins see everyone: changing the leader must leave the previous one
        # selectable as an ordinary member.
        'users': users if is_admin else users.exclude(id=team.leader_id),
        'leader_choices': users if is_admin else None,
        'is_admin': is_admin,
        'team': team,
    })


@login_required
def team_delete(request, pk):
    from .models import Team
    from django.shortcuts import get_object_or_404, redirect
    from .permissions import can_edit_team

    team = get_object_or_404(Team, pk=pk)
    if not can_edit_team(request.user, team):
        messages.error(request, 'You can only delete teams you lead.')
        return redirect('tenders:my_teams')
    if request.method == 'POST':
        name = team.name
        team.delete()
        messages.success(request, f'Team "{name}" deleted.')
    return redirect('tenders:my_teams')


@login_required
def post_note(request, pk):
    """Post a team note on a tender, optionally targeting a specific team."""
    from .models import TenderNote, Team
    from django.shortcuts import get_object_or_404, redirect
    from django.db.models import Q
    tender = get_object_or_404(Tender, pk=pk)
    if request.method == 'POST':
        body    = request.POST.get('team_note_body', '').strip()
        team_id = request.POST.get('team_id', '').strip()
        if body:
            team = None
            if team_id:
                team = Team.objects.filter(
                    Q(leader=request.user) | Q(members=request.user),
                    pk=team_id
                ).first()
            note = TenderNote.objects.create(
                tender=tender,
                author=request.user,
                body=body,
                team=team,
            )
            # @mentions → notify tagged users
            import re
            from django.contrib.auth import get_user_model
            from .models import NoteNotification
            handles = {h.lower() for h in re.findall(r'@([A-Za-z0-9_.\-]+)', body)}
            if handles:
                U = get_user_model()
                for mu in U.objects.filter(is_active=True).exclude(pk=request.user.pk):
                    if mu.username.lower() in handles:
                        NoteNotification.objects.get_or_create(note=note, recipient=mu)
            messages.success(request, f'Note shared with {team.name if team else "your teams"}.')
    return redirect('tenders:detail', pk=pk)


@login_required
def share_to_team(request, pk):
    """Send a tender to one of the user's teams — posts a visible share note and
    notifies every team member. Returns JSON (called from the Quick Actions popup)."""
    from .models import TenderNote, Team
    from django.shortcuts import get_object_or_404
    from django.http import JsonResponse
    from django.db.models import Q

    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    tender  = get_object_or_404(Tender, pk=pk)
    team_id = (request.POST.get('team_id') or '').strip()
    team = Team.objects.filter(
        Q(leader=request.user) | Q(members=request.user),
        pk=team_id,
    ).first()
    if not team:
        return JsonResponse({'error': 'You are not a member of that team.'}, status=403)

    note = (request.POST.get('note') or '').strip()
    body = 'Shared this tender with the team for review.'
    if note:
        body += f'\n{note[:400]}'

    # Creating the note fires TenderNote._notify_team → notifications for members.
    TenderNote.objects.create(tender=tender, author=request.user, body=body, team=team)

    recipients = len(team.all_member_ids() - {request.user.id})
    return JsonResponse({'ok': True, 'team': team.name, 'recipients': recipients})


@login_required
def delete_note(request, note_pk):
    """Delete own note."""
    from .models import TenderNote
    from django.shortcuts import get_object_or_404, redirect
    note = get_object_or_404(TenderNote, pk=note_pk, author=request.user)
    tender_pk = note.tender_id
    if request.method == 'POST':
        note.delete()
        messages.success(request, 'Note deleted.')
    return redirect('tenders:detail', pk=tender_pk)


@login_required
def notifications(request):
    """Mark notifications as read and return combined unread count as JSON."""
    from .models import NoteNotification, FeedbackNotification
    from django.http import JsonResponse

    if request.method == 'POST':
        # Mark note notifications read
        nids = request.POST.getlist('ids')
        if nids:
            NoteNotification.objects.filter(
                recipient=request.user, id__in=[i for i in nids if not i.startswith('fb-')]
            ).update(is_read=True)
            fb_ids = [i.replace('fb-', '') for i in nids if i.startswith('fb-')]
            if fb_ids:
                FeedbackNotification.objects.filter(
                    feedback__author=request.user, id__in=fb_ids
                ).update(is_read=True)
        unread_notes = NoteNotification.objects.filter(recipient=request.user, is_read=False).count()
        unread_fb    = FeedbackNotification.objects.filter(feedback__author=request.user, is_read=False).count()
        return JsonResponse({'unread': unread_notes + unread_fb})

    # GET — return combined unread list
    note_notifs = NoteNotification.objects.filter(
        recipient=request.user, is_read=False
    ).select_related('note__tender', 'note__author').order_by('-created_at')[:15]

    fb_notifs = FeedbackNotification.objects.filter(
        feedback__author=request.user, is_read=False
    ).select_related('feedback').order_by('-created_at')[:10]

    data = []
    for n in note_notifs:
        data.append({
            'id':           str(n.id),
            'type':         'note',
            'tender_title': (n.note.tender.title or n.note.tender.title_ar or '')[:60],
            'author':       n.note.author.get_full_name() or n.note.author.username,
            'preview':      n.note.body[:100],
            'created_at':   n.created_at.strftime('%d %b %Y %H:%M'),
            'url':          f'/tenders/{n.note.tender_id}/#team-notes',
            'icon':         '💬',
        })
    for n in fb_notifs:
        data.append({
            'id':           f'fb-{n.id}',
            'type':         'feedback',
            'tender_title': n.feedback.title[:60],
            'author':       'Admin',
            'preview':      n.feedback.admin_note[:100],
            'created_at':   n.created_at.strftime('%d %b %Y %H:%M'),
            'url':          f'/feedback/mine/#fb-{n.feedback_id}',
            'icon':         '📣',
        })

    # Sort combined by created_at desc (approximate — using string)
    data.sort(key=lambda x: x['created_at'], reverse=True)
    unread_notes = NoteNotification.objects.filter(recipient=request.user, is_read=False).count()
    unread_fb    = FeedbackNotification.objects.filter(feedback__author=request.user, is_read=False).count()
    return JsonResponse({'notifications': data, 'unread': unread_notes + unread_fb})


@login_required
def feedback_response(request, pk):
    """Mark feedback notification read and show the response."""
    from .models import UserFeedback, FeedbackNotification
    from django.shortcuts import get_object_or_404, redirect
    fb = get_object_or_404(UserFeedback, pk=pk, author=request.user)
    # Mark as read
    FeedbackNotification.objects.filter(feedback=fb).update(is_read=True)
    return redirect(f'/feedback/mine/#fb-{pk}')


@login_required
def team_detail(request, pk):
    """Team notes history — all notes posted by any team member, across all tenders."""
    from .models import Team, TenderNote
    from django.shortcuts import get_object_or_404, render
    from django.db.models import Q

    # Members and the leader can view; administrators can view any team, since
    # they are responsible for managing teams they are not part of.
    from .permissions import can_administer_teams, can_edit_team

    if can_administer_teams(request.user):
        team = get_object_or_404(Team, pk=pk)
    else:
        team = get_object_or_404(
            Team.objects.filter(
                Q(leader=request.user) | Q(members=request.user),
                pk=pk
            ).distinct()
        )

    # Notes posted to this specific team (team field set)
    # plus fallback: notes by team members with no team set (legacy)
    from django.db.models import Q as DQ
    member_ids = team.all_member_ids()
    notes = (
        TenderNote.objects
        .filter(
            DQ(team=team) |                          # explicitly posted to this team
            DQ(author_id__in=member_ids, team__isnull=True)  # legacy notes with no team
        )
        .select_related('author', 'tender', 'tender__country', 'tender__source', 'team')
        .order_by('-created_at')
    )

    # Mark notifications for this team's notes as read
    from .models import NoteNotification
    NoteNotification.objects.filter(
        recipient=request.user,
        note__in=notes,
        is_read=False,
    ).update(is_read=True)

    return render(request, 'tenders/teams/detail.html', {
        'team':       team,
        'notes':      notes,
        'is_leader':  can_edit_team(request.user, team),
        'member_ids': member_ids,
    })


# ══════════════════════════════════════════════════════════════════════════════
# USER FEEDBACK VIEWS
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def submit_feedback(request):
    from .models import UserFeedback
    from django.shortcuts import render, redirect
    if request.method == 'POST':
        fb_type    = request.POST.get('feedback_type', 'OTHER')
        title      = request.POST.get('title', '').strip()
        body       = request.POST.get('body', '').strip()
        attachment = request.FILES.get('attachment')

        # Validate attachment size (max 10 MB)
        if attachment and attachment.size > 10 * 1024 * 1024:
            messages.error(request, 'Attachment too large — max 10 MB.')
        elif title and body:
            fb = UserFeedback(
                author        = request.user,
                feedback_type = fb_type,
                title         = title,
                body          = body,
            )
            if attachment:
                fb.attachment = attachment
            fb.save()
            # E-mail alert to opted-in admins (background thread, never blocks)
            from .feedback_notify import notify_feedback_submitted
            notify_feedback_submitted(fb)
            messages.success(request, 'Your feedback has been submitted. Thank you!')
            return redirect('tenders:my_feedback')
        else:
            messages.error(request, 'Please fill in both subject and details.')
    return render(request, 'tenders/feedback/submit.html', {
        'types': UserFeedback.FeedbackType.choices,
    })


@login_required
def my_feedback(request):
    """User's own feedback history."""
    from .models import UserFeedback, FeedbackNotification
    from django.shortcuts import render
    feedbacks = UserFeedback.objects.filter(author=request.user)
    # IDs with unread admin response
    unread_ids = set(
        FeedbackNotification.objects.filter(
            feedback__author=request.user, is_read=False
        ).values_list('feedback_id', flat=True)
    )
    # Mark all as read now that user is viewing the page
    FeedbackNotification.objects.filter(
        feedback__author=request.user, is_read=False
    ).update(is_read=True)
    return render(request, 'tenders/feedback/my_list.html', {
        'feedbacks':  feedbacks,
        'unread_ids': unread_ids,
    })
