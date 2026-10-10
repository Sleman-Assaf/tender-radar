# tenders/analytics_views.py

import json
from datetime import timedelta, date

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.db.models.functions import TruncDate, TruncWeek
from django.shortcuts import render
from django.utils import timezone
from django.http import JsonResponse

from .models import (
    UserLoginLog, TenderView, TenderPortalClick,
    Tender, AECFeedback, UserFeedback, ScrapeLog,
)

User = get_user_model()


def _su(view_fn):
    """
    Require an administrator — Super Admin or System Administrator.

    Was `is_superuser`, which locked out the System Administrator role. Reports
    are a system capability, not account administration, so both roles get them.
    """
    from functools import wraps

    from django.core.exceptions import PermissionDenied

    from .permissions import can_administer

    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        if not can_administer(request.user):
            raise PermissionDenied
        return view_fn(request, *args, **kwargs)
    return login_required(wrapper)


def _days_range(request, default=30):
    try:
        return max(7, min(365, int(request.GET.get('days', default))))
    except (ValueError, TypeError):
        return default


# ─── 1. Analytics Hub ─────────────────────────────────────────────────────────

@_su
def analytics_hub(request):
    days = _days_range(request)
    since = timezone.now() - timedelta(days=days)

    users_total   = User.objects.filter(is_active=True).count()
    logins_period = UserLoginLog.objects.filter(logged_in_at__gte=since).count()
    views_period  = TenderView.objects.filter(viewed_at__gte=since).count()
    clicks_period = TenderPortalClick.objects.filter(clicked_at__gte=since).count()
    open_tenders  = Tender.objects.filter(status='OPEN').count()
    aec_tenders   = Tender.objects.filter(is_aec=True).count()
    feedbacks     = UserFeedback.objects.filter(created_at__gte=since).count()

    # Active users (had at least 1 view in period)
    active_users = TenderView.objects.filter(viewed_at__gte=since).values('user').distinct().count()

    ctx = {
        'days': days,
        'users_total':   users_total,
        'logins_period': logins_period,
        'views_period':  views_period,
        'clicks_period': clicks_period,
        'open_tenders':  open_tenders,
        'aec_tenders':   aec_tenders,
        'feedbacks':     feedbacks,
        'active_users':  active_users,
    }
    return render(request, 'analytics/hub.html', ctx)


# ─── 2. Login Activity Report ─────────────────────────────────────────────────

@_su
def report_logins(request):
    days  = _days_range(request)
    since = timezone.now() - timedelta(days=days)

    # Daily login counts
    daily_logins = (
        UserLoginLog.objects
        .filter(logged_in_at__gte=since)
        .annotate(day=TruncDate('logged_in_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # Per-user login counts (bar chart)
    user_logins = (
        UserLoginLog.objects
        .filter(logged_in_at__gte=since)
        .values('user__username', 'user__first_name', 'user__last_name')
        .annotate(count=Count('id'))
        .order_by('-count')[:20]
    )

    # Recent logins table
    recent = UserLoginLog.objects.filter(logged_in_at__gte=since).select_related('user').order_by('-logged_in_at')[:100]

    # Chart data
    daily_labels  = [str(r['day']) for r in daily_logins]
    daily_counts  = [r['count']    for r in daily_logins]
    user_labels   = [r['user__username'] for r in user_logins]
    user_counts   = [r['count']           for r in user_logins]

    ctx = {
        'days': days,
        'recent': recent,
        'daily_labels_json': json.dumps(daily_labels),
        'daily_counts_json': json.dumps(daily_counts),
        'user_labels_json':  json.dumps(user_labels),
        'user_counts_json':  json.dumps(user_counts),
        'total_logins': sum(daily_counts),
        'unique_users': len(set(r['user__username'] for r in user_logins)),
    }
    return render(request, 'analytics/logins.html', ctx)


# ─── 3. Open Tenders Report ───────────────────────────────────────────────────

@_su
def report_open_tenders(request):
    days  = _days_range(request, default=60)
    since = timezone.now() - timedelta(days=days)

    open_qs = Tender.objects.filter(status='OPEN')

    # By country
    by_country = (
        open_qs.values('country__name_en', 'country')
        .annotate(count=Count('id'))
        .order_by('-count')[:15]
    )

    # By source
    by_source = (
        open_qs.values('source__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:15]
    )

    # New tenders over time (scraped in period)
    new_over_time = (
        Tender.objects
        .filter(scraped_at__gte=since, status='OPEN')
        .annotate(day=TruncDate('scraped_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )

    # AEC relevance breakdown
    aec_yes   = open_qs.filter(is_aec=True).count()
    aec_no    = open_qs.filter(is_aec=False).count()
    aec_unk   = open_qs.filter(is_aec__isnull=True).count()

    # Most viewed open tenders
    top_viewed = (
        open_qs
        .annotate(view_count=Count('views'))
        .order_by('-view_count')[:10]
    )

    ctx = {
        'days': days,
        'total_open':    open_qs.count(),
        'aec_yes':       aec_yes,
        'aec_no':        aec_no,
        'aec_unk':       aec_unk,
        'top_viewed':    top_viewed,
        'country_labels_json': json.dumps([r['country__name_en'] or r['country'] or 'Unknown' for r in by_country]),
        'country_counts_json': json.dumps([r['count'] for r in by_country]),
        'source_labels_json':  json.dumps([r['source__name'] or 'Unknown' for r in by_source]),
        'source_counts_json':  json.dumps([r['count'] for r in by_source]),
        'new_labels_json':     json.dumps([str(r['day']) for r in new_over_time]),
        'new_counts_json':     json.dumps([r['count'] for r in new_over_time]),
    }
    return render(request, 'analytics/open_tenders.html', ctx)


# ─── 4. Portal Clicks Report ──────────────────────────────────────────────────

@_su
def report_portal_clicks(request):
    days  = _days_range(request)
    since = timezone.now() - timedelta(days=days)

    qs = TenderPortalClick.objects.filter(clicked_at__gte=since)

    # Daily portal clicks
    daily = (
        qs.annotate(day=TruncDate('clicked_at'))
        .values('day').annotate(count=Count('id')).order_by('day')
    )

    # Per-user portal clicks (bar chart)
    user_clicks = (
        qs.values('user__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:20]
    )

    # Most clicked tenders (by portal clicks)
    top_tenders = (
        qs.values('tender__id', 'tender__title', 'tender__source__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    # Recent clicks
    recent = qs.select_related('user', 'tender', 'tender__source').order_by('-clicked_at')[:50]

    # Also pull TenderView data for comparison (detail page opens)
    vqs = TenderView.objects.filter(viewed_at__gte=since)

    daily_views = (
        vqs.annotate(day=TruncDate('viewed_at'))
        .values('day').annotate(count=Count('id')).order_by('day')
    )
    user_views = (
        vqs.values('user__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:20]
    )

    # Build combined date spine for dual-line chart
    all_days = sorted(set(
        [str(r['day']) for r in daily] +
        [str(r['day']) for r in daily_views]
    ))
    click_by_day = {str(r['day']): r['count'] for r in daily}
    view_by_day  = {str(r['day']): r['count'] for r in daily_views}

    ctx = {
        'days': days,
        'total_clicks':      qs.count(),
        'total_views':       vqs.count(),
        'unique_clickers':   qs.values('user').distinct().count(),
        'unique_viewers':    vqs.values('user').distinct().count(),
        'recent':            recent,
        'top_tenders':       top_tenders,
        # Dual line chart
        'spine_json':        json.dumps(all_days),
        'clicks_data_json':  json.dumps([click_by_day.get(d, 0) for d in all_days]),
        'views_data_json':   json.dumps([view_by_day.get(d, 0)  for d in all_days]),
        # Bar chart
        'user_labels_json':  json.dumps([r['user__username'] for r in user_clicks]),
        'user_counts_json':  json.dumps([r['count'] for r in user_clicks]),
        # Bar chart views comparison
        'view_user_labels_json': json.dumps([r['user__username'] for r in user_views]),
        'view_user_counts_json': json.dumps([r['count'] for r in user_views]),
    }
    return render(request, 'analytics/portal_clicks.html', ctx)


# ─── 5. Evaluation / AEC Feedback Report ─────────────────────────────────────

@_su
def report_evaluation(request):
    days  = _days_range(request)
    since = timezone.now() - timedelta(days=days)

    qs = AECFeedback.objects.filter(created_at__gte=since)

    # Breakdown by vote (UP / DOWN)
    by_vote = (
        qs.values('vote')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # Per-user evaluations
    by_user = (
        qs.values('user__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:20]
    )

    # Daily evaluations
    daily = (
        qs.annotate(day=TruncDate('created_at'))
        .values('day').annotate(count=Count('id')).order_by('day')
    )

    recent = qs.select_related('user', 'tender').order_by('-created_at')[:30]

    vote_map = {'UP': 'Accurate (UP)', 'DOWN': 'Inaccurate (DOWN)'}

    ctx = {
        'days': days,
        'total': qs.count(),
        'recent': recent,
        'decision_labels_json': json.dumps([vote_map.get(r['vote'], r['vote']) for r in by_vote]),
        'decision_counts_json': json.dumps([r['count'] for r in by_vote]),
        'user_labels_json':     json.dumps([r['user__username'] for r in by_user]),
        'user_counts_json':     json.dumps([r['count'] for r in by_user]),
        'daily_labels_json':    json.dumps([str(r['day']) for r in daily]),
        'daily_counts_json':    json.dumps([r['count'] for r in daily]),
    }
    return render(request, 'analytics/evaluation.html', ctx)


# ─── 6. User Interactivity (per-user line chart) ──────────────────────────────

@_su
def report_user_activity(request):
    days  = _days_range(request)
    since = timezone.now() - timedelta(days=days)

    # Build date spine
    date_spine = [(since.date() + timedelta(days=i)) for i in range(days + 1)]
    spine_str  = [str(d) for d in date_spine]

    # All active users who had any activity in range
    active_user_ids = set(
        list(TenderView.objects.filter(viewed_at__gte=since).values_list('user_id', flat=True)) +
        list(TenderPortalClick.objects.filter(clicked_at__gte=since).values_list('user_id', flat=True)) +
        list(UserLoginLog.objects.filter(logged_in_at__gte=since).values_list('user_id', flat=True))
    )
    active_users = User.objects.filter(id__in=active_user_ids).order_by('username')

    # Views per user per day
    views_by_user_day = {}
    for row in (TenderView.objects
                .filter(viewed_at__gte=since, user__in=active_users)
                .annotate(day=TruncDate('viewed_at'))
                .values('user__username', 'day')
                .annotate(count=Count('id'))):
        views_by_user_day.setdefault(row['user__username'], {})[str(row['day'])] = row['count']

    # Clicks per user per day
    clicks_by_user_day = {}
    for row in (TenderPortalClick.objects
                .filter(clicked_at__gte=since, user__in=active_users)
                .annotate(day=TruncDate('clicked_at'))
                .values('user__username', 'day')
                .annotate(count=Count('id'))):
        clicks_by_user_day.setdefault(row['user__username'], {})[str(row['day'])] = row['count']

    # Build datasets
    colors = [
        '#FCD700','#1565C0','#2E7D32','#D32F2F','#E65100',
        '#6A1B9A','#00838F','#AD1457','#558B2F','#1565C0',
    ]
    view_datasets  = []
    click_datasets = []

    for i, u in enumerate(active_users):
        color = colors[i % len(colors)]
        uname = u.username
        view_datasets.append({
            'label': uname,
            'data':  [views_by_user_day.get(uname, {}).get(d, 0) for d in spine_str],
            'borderColor': color,
            'backgroundColor': color + '22',
            'tension': 0.3,
            'fill': False,
        })
        click_datasets.append({
            'label': uname,
            'data':  [clicks_by_user_day.get(uname, {}).get(d, 0) for d in spine_str],
            'borderColor': color,
            'backgroundColor': color + '22',
            'tension': 0.3,
            'fill': False,
        })

    # User comparison bar chart (total activity score)
    user_totals = []
    for u in active_users:
        uname = u.username
        total_views  = sum(views_by_user_day.get(uname, {}).values())
        total_clicks = sum(clicks_by_user_day.get(uname, {}).values())
        total_logins = UserLoginLog.objects.filter(user=u, logged_in_at__gte=since).count()
        user_totals.append({
            'username': uname,
            'full_name': u.get_full_name() or uname,
            'views':  total_views,
            'clicks': total_clicks,
            'logins': total_logins,
            'score':  total_views + total_clicks * 2 + total_logins,
        })
    user_totals.sort(key=lambda x: x['score'], reverse=True)

    bar_labels  = [u['username']  for u in user_totals]
    bar_views   = [u['views']     for u in user_totals]
    bar_clicks  = [u['clicks']    for u in user_totals]
    bar_logins  = [u['logins']    for u in user_totals]

    ctx = {
        'days': days,
        'spine_json':          json.dumps(spine_str),
        'view_datasets_json':  json.dumps(view_datasets),
        'click_datasets_json': json.dumps(click_datasets),
        'bar_labels_json':     json.dumps(bar_labels),
        'bar_views_json':      json.dumps(bar_views),
        'bar_clicks_json':     json.dumps(bar_clicks),
        'bar_logins_json':     json.dumps(bar_logins),
        'user_totals':         user_totals,
    }
    return render(request, 'analytics/user_activity.html', ctx)


# ─── 7. Scraper Health Report ─────────────────────────────────────────────────

@_su
def report_scraper_health(request):
    days  = _days_range(request, default=14)
    since = timezone.now() - timedelta(days=days)

    logs = ScrapeLog.objects.filter(started_at__gte=since).select_related('source').order_by('-started_at')

    # Success vs fail per source
    by_source = (
        ScrapeLog.objects.filter(started_at__gte=since)
        .values('source__name')
        .annotate(
            total=Count('id'),
            success=Count('id', filter=Q(status='SUCCESS')),
            failed=Count('id', filter=Q(status='FAILED')),
            total_found=Count('tenders_found'),
        )
        .order_by('-total')[:20]
    )

    # Daily scraper runs
    daily = (
        ScrapeLog.objects.filter(started_at__gte=since)
        .annotate(day=TruncDate('started_at'))
        .values('day')
        .annotate(
            total=Count('id'),
            success=Count('id', filter=Q(status='SUCCESS')),
            failed=Count('id', filter=Q(status='FAILED')),
        )
        .order_by('day')
    )

    ctx = {
        'days': days,
        'logs': logs[:50],
        'total_runs':    logs.count(),
        'total_success': logs.filter(status='SUCCESS').count(),
        'total_failed':  logs.filter(status='FAILED').count(),
        'source_labels_json':   json.dumps([r['source__name'] or 'Unknown' for r in by_source]),
        'source_success_json':  json.dumps([r['success'] for r in by_source]),
        'source_failed_json':   json.dumps([r['failed']  for r in by_source]),
        'daily_labels_json':    json.dumps([str(r['day']) for r in daily]),
        'daily_success_json':   json.dumps([r['success'] for r in daily]),
        'daily_failed_json':    json.dumps([r['failed']  for r in daily]),
    }
    return render(request, 'analytics/scraper_health.html', ctx)


# ─── API: log portal click (called via JS fetch) ──────────────────────────────

from django.views.decorators.csrf import csrf_exempt

def log_portal_click(request, pk):
    """
    Server-side redirect sensor for "Open Source Portal" clicks.
    User visits /tenders/<pk>/portal-click/ → click recorded → redirected to source_url.
    No JS fetch/beacon needed — 100% reliable, works with all browsers.
    """
    from django.shortcuts import redirect as dj_redirect
    from .models import Tender

    try:
        tender = Tender.objects.get(pk=pk)
        # Record click only for authenticated users
        if request.user.is_authenticated:
            TenderPortalClick.objects.create(user=request.user, tender=tender)
        # Redirect to the external portal
        if tender.source_url:
            return dj_redirect(tender.source_url)
        return dj_redirect('tenders:detail', pk=pk)
    except Tender.DoesNotExist:
        from django.http import Http404
        raise Http404
