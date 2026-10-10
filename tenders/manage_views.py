# tenders/manage_views.py
"""
Superuser-only CRUD views for managing:
  - Countries
  - Scraper Sources
  - Tendering URLs
  - Relevance Keywords
  - Category Keywords
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import (
    Tender, CountryConfig, ScraperSource, TenderingURL,
    RelevanceKeyword, CategoryKeyword, TenderCategory,
)
from .forms import (
    CountryForm, SourceForm, TenderingURLForm,
    RelevanceKeywordForm, CategoryKeywordForm,
)
from .permissions import admin_required, super_admin_required

superuser_required = user_passes_test(lambda u: u.is_active and u.is_superuser, login_url='/login/')


def su(view_func):
    """
    Administrator gate for the tender-system control panel.

    Open to both admin roles. The two differ only over who may administer admin
    *accounts* (see permissions.can_edit_user); everything the system itself can
    do, a System Administrator can do. Managing countries, sources, keywords and
    tender access is system work, not account work.

    Delegates to tenders.permissions so the whole app answers unauthorised
    requests the same way: anonymous → login redirect, signed-in but not an
    administrator → HTTP 403 (rather than a confusing bounce back to /login/).
    """
    return admin_required(view_func)


# ─────────────────────────────────────────────────────────────────────────────
# Management Hub
# ─────────────────────────────────────────────────────────────────────────────

@su
def manage_hub(request):
    ctx = {
        'country_count':    CountryConfig.objects.count(),
        'source_count':     ScraperSource.objects.count(),
        'url_count':        TenderingURL.objects.count(),
        'rel_kw_count':     RelevanceKeyword.objects.count(),
        'cat_kw_count':     CategoryKeyword.objects.count(),
        'active_countries': CountryConfig.objects.filter(is_active=True).count(),
        'active_sources':   ScraperSource.objects.filter(is_active=True).count(),
        'active_urls':      TenderingURL.objects.filter(is_active=True).count(),
    }
    from .models import IntegrationSettings
    try:
        ctx['intg'] = IntegrationSettings.get()
    except Exception:
        ctx['intg'] = None          # table missing (pre-migration)

    # Newspaper Tenders stats (عطاءات الجريدة). Guarded so the hub keeps
    # working if the newspaper_tenders app is ever removed.
    try:
        from newspaper_tenders.models import (NewspaperEdition,
                                              NewspaperTenderMeta)
        ctx['np_names']    = (NewspaperEdition.objects
                              .values('source').distinct().count()) or 1
        ctx['np_editions'] = NewspaperEdition.objects.count()
        ctx['np_tenders']  = NewspaperTenderMeta.objects.count()
        ctx['np_aec']      = NewspaperTenderMeta.objects.filter(
            tender__is_aec=True).count()
        ctx['np_enabled']  = True
    except Exception:
        ctx['np_enabled']  = False
    return render(request, 'manage/hub.html', ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Countries
# ─────────────────────────────────────────────────────────────────────────────

@su
def country_list(request):
    countries = CountryConfig.objects.annotate(
        source_count=Count('sources', distinct=True),
        tender_count=Count('tenders', distinct=True),
    ).order_by('sort_order', 'name_en')
    return render(request, 'manage/country_list.html', {'countries': countries})


@su
def country_create(request):
    form = CountryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'Country "{obj.name_en}" created.')
        return redirect('tenders:manage_countries')
    return render(request, 'manage/country_form.html', {'form': form, 'action': 'Add Country'})


@su
def country_edit(request, code):
    obj  = get_object_or_404(CountryConfig, code=code)
    form = CountryForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Country "{obj.name_en}" updated.')
        return redirect('tenders:manage_countries')
    return render(request, 'manage/country_form.html', {'form': form, 'action': 'Edit Country', 'obj': obj})


@su
@require_POST
def country_delete(request, code):
    obj = get_object_or_404(CountryConfig, code=code)
    name = obj.name_en
    obj.delete()
    messages.success(request, f'Country "{name}" deleted.')
    return redirect('tenders:manage_countries')


@su
@require_POST
def country_toggle(request, code):
    obj = get_object_or_404(CountryConfig, code=code)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    return JsonResponse({'active': obj.is_active})


# ─────────────────────────────────────────────────────────────────────────────
# Scraper Sources
# ─────────────────────────────────────────────────────────────────────────────

@su
def source_list(request):
    country_filter = request.GET.get('country')
    # The NEWSPAPER_/MAGAZINE_ rows are internal anchors managed by the
    # newspaper_tenders and magazine_tenders apps, not here.
    sources = ScraperSource.objects.select_related('country').exclude(
        Q(scraper_class__startswith='NEWSPAPER_') |
        Q(scraper_class__startswith='MAGAZINE_'),
    ).annotate(
        url_count=Count('tendering_urls', distinct=True),
        tender_count=Count('tenders', distinct=True),
    ).order_by('country__sort_order', 'name')
    if country_filter:
        sources = sources.filter(country__code=country_filter)
    countries = CountryConfig.objects.filter(is_active=True).order_by('sort_order')
    return render(request, 'manage/source_list.html', {
        'sources': sources,
        'countries': countries,
        'country_filter': country_filter,
    })


@su
def source_create(request):
    form = SourceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'Source "{obj.name}" created.')
        return redirect('tenders:manage_source_urls', source_id=obj.pk)
    return render(request, 'manage/source_form.html', {'form': form, 'action': 'Add Source'})


@su
def source_edit(request, pk):
    obj  = get_object_or_404(ScraperSource, pk=pk)
    form = SourceForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Source "{obj.name}" updated.')
        return redirect('tenders:manage_sources')
    return render(request, 'manage/source_form.html', {'form': form, 'action': 'Edit Source', 'obj': obj})


@su
@require_POST
def source_delete(request, pk):
    obj = get_object_or_404(ScraperSource, pk=pk)
    name = obj.name
    obj.delete()
    messages.success(request, f'Source "{name}" deleted.')
    return redirect('tenders:manage_sources')


@su
@require_POST
@login_required
def flush_today(request):
    """Delete all tenders scraped today across all sources."""
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    from django.utils import timezone
    from .models import Tender
    today = timezone.now().date()
    deleted, _ = Tender.objects.filter(scraped_at__date=today).delete()
    messages.success(request, f'Flushed {deleted} tender(s) scraped today ({today}).')
    return redirect('tenders:manage_sources')


@su
@require_POST
def source_flush(request, pk):
    """Delete every tender belonging to one source. Destructive — Super Admin only."""
    source = get_object_or_404(ScraperSource, pk=pk)
    deleted, _ = Tender.objects.filter(source=source).delete()
    return JsonResponse({'deleted': deleted, 'name': source.name})


@su
@require_POST
def source_toggle(request, pk):
    obj = get_object_or_404(ScraperSource, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    return JsonResponse({'active': obj.is_active})


# ─────────────────────────────────────────────────────────────────────────────
# Tendering URLs
# ─────────────────────────────────────────────────────────────────────────────

@su
def url_list(request, source_id):
    source = get_object_or_404(ScraperSource.objects.select_related('country'), pk=source_id)
    urls   = source.tendering_urls.all().order_by('sort_order', 'label')
    return render(request, 'manage/url_list.html', {'source': source, 'urls': urls})


@su
def url_create(request, source_id):
    source = get_object_or_404(ScraperSource, pk=source_id)
    form   = TenderingURLForm(request.POST or None, source=source)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'URL "{obj.label}" added.')
        return redirect('tenders:manage_source_urls', source_id=source.pk)
    return render(request, 'manage/url_form.html', {
        'form': form, 'source': source, 'action': 'Add URL'
    })


@su
def url_edit(request, pk):
    obj    = get_object_or_404(TenderingURL.objects.select_related('source'), pk=pk)
    form   = TenderingURLForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'URL "{obj.label}" updated.')
        return redirect('tenders:manage_source_urls', source_id=obj.source.pk)
    return render(request, 'manage/url_form.html', {
        'form': form, 'source': obj.source, 'action': 'Edit URL', 'obj': obj
    })


@su
@require_POST
def url_delete(request, pk):
    obj       = get_object_or_404(TenderingURL, pk=pk)
    source_id = obj.source.pk
    label     = obj.label
    obj.delete()
    messages.success(request, f'URL "{label}" deleted.')
    return redirect('tenders:manage_source_urls', source_id=source_id)


@su
@require_POST
def url_toggle(request, pk):
    obj = get_object_or_404(TenderingURL, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    return JsonResponse({'active': obj.is_active})


# ─────────────────────────────────────────────────────────────────────────────
# Relevance Keywords
# ─────────────────────────────────────────────────────────────────────────────

@su
def relevance_keyword_list(request):
    lang = request.GET.get('lang', '')
    q    = request.GET.get('q', '').strip()
    kws  = RelevanceKeyword.objects.all().order_by('language', 'keyword')
    if lang:
        kws = kws.filter(language=lang)
    if q:
        kws = kws.filter(Q(keyword__icontains=q) | Q(note__icontains=q))
    return render(request, 'manage/keyword_list.html', {
        'keywords': kws, 'lang': lang, 'q': q,
        'kind': 'relevance',
        'title': 'Relevance Keywords — كلمات الصلة',
        'subtitle': 'Keywords that flag a tender as relevant to AJi Group',
    })


@su
def relevance_keyword_create(request):
    form = RelevanceKeywordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'Keyword "{obj.keyword}" added.')
        return redirect('tenders:manage_rel_keywords')
    return render(request, 'manage/keyword_form.html', {
        'form': form, 'action': 'Add Relevance Keyword',
        'back_url': 'tenders:manage_rel_keywords',
    })


@su
def relevance_keyword_edit(request, pk):
    obj  = get_object_or_404(RelevanceKeyword, pk=pk)
    form = RelevanceKeywordForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Keyword "{obj.keyword}" updated.')
        return redirect('tenders:manage_rel_keywords')
    return render(request, 'manage/keyword_form.html', {
        'form': form, 'action': 'Edit Relevance Keyword',
        'obj': obj, 'back_url': 'tenders:manage_rel_keywords',
    })


@su
@require_POST
def relevance_keyword_delete(request, pk):
    obj = get_object_or_404(RelevanceKeyword, pk=pk)
    kw  = obj.keyword
    obj.delete()
    messages.success(request, f'Keyword "{kw}" deleted.')
    return redirect('tenders:manage_rel_keywords')


@su
@require_POST
def relevance_keyword_toggle(request, pk):
    obj = get_object_or_404(RelevanceKeyword, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    return JsonResponse({'active': obj.is_active})


# ─────────────────────────────────────────────────────────────────────────────
# Category Keywords
# ─────────────────────────────────────────────────────────────────────────────

@su
def category_keyword_list(request):
    cat  = request.GET.get('cat', '')
    lang = request.GET.get('lang', '')
    q    = request.GET.get('q', '').strip()
    kws  = CategoryKeyword.objects.all().order_by('category', 'language', 'keyword')
    if cat:
        kws = kws.filter(category=cat)
    if lang:
        kws = kws.filter(language=lang)
    if q:
        kws = kws.filter(keyword__icontains=q)
    return render(request, 'manage/cat_keyword_list.html', {
        'keywords':   kws,
        'cat':        cat,
        'lang':       lang,
        'q':          q,
        'categories': TenderCategory.choices,
    })


@su
def category_keyword_create(request):
    form = CategoryKeywordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'Keyword "{obj.keyword}" → {obj.category} added.')
        return redirect('tenders:manage_cat_keywords')
    return render(request, 'manage/keyword_form.html', {
        'form': form, 'action': 'Add Category Keyword',
        'back_url': 'tenders:manage_cat_keywords',
    })


@su
def category_keyword_edit(request, pk):
    obj  = get_object_or_404(CategoryKeyword, pk=pk)
    form = CategoryKeywordForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Keyword "{obj.keyword}" updated.')
        return redirect('tenders:manage_cat_keywords')
    return render(request, 'manage/keyword_form.html', {
        'form': form, 'action': 'Edit Category Keyword',
        'obj': obj, 'back_url': 'tenders:manage_cat_keywords',
    })


@su
@require_POST
def category_keyword_delete(request, pk):
    obj = get_object_or_404(CategoryKeyword, pk=pk)
    kw  = obj.keyword
    obj.delete()
    messages.success(request, f'Keyword "{kw}" deleted.')
    return redirect('tenders:manage_cat_keywords')


@su
@require_POST
def category_keyword_toggle(request, pk):
    obj = get_object_or_404(CategoryKeyword, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    return JsonResponse({'active': obj.is_active})


# ── AI / Site Settings ────────────────────────────────────────────────────────
# Reached from System Dashboard → Settings. Administrators only: this page holds the
# OpenAI API key and the site-wide scraping mode.
@admin_required
def manage_site_settings(request):
    from .models import SiteSettings
    cfg = SiteSettings.get()

    if request.method == 'POST':
        cfg.scraping_mode         = request.POST.get('scraping_mode', cfg.scraping_mode)
        cfg.ai_service            = request.POST.get('ai_service', cfg.ai_service)
        openai_key                = request.POST.get('openai_api_key', '').strip()
        if openai_key:
            cfg.openai_api_key    = openai_key
        cfg.openai_model          = request.POST.get('openai_model', cfg.openai_model).strip() or 'gpt-4o-mini'
        try:
            cfg.openai_classify_batch  = int(request.POST.get('openai_classify_batch', 50))
            cfg.openai_translate_batch = int(request.POST.get('openai_translate_batch', 30))
        except (ValueError, TypeError):
            pass
        cfg.ollama_base_url      = request.POST.get('ollama_base_url', cfg.ollama_base_url).strip() or 'http://localhost:11434'
        cfg.ollama_model         = request.POST.get('ollama_model', cfg.ollama_model).strip() or 'qwen2.5:7b'
        try:
            cfg.ollama_classify_batch  = int(request.POST.get('ollama_classify_batch', 20))
            cfg.ollama_translate_batch = int(request.POST.get('ollama_translate_batch', 10))
        except (ValueError, TypeError):
            pass
        cfg.updated_by = request.user
        cfg.save()
        import logging as _log
        _log.getLogger('tenders').info(
            f'[SiteSettings] saved by {request.user}: '
            f'ai_service={cfg.ai_service!r} '
            f'model={cfg.openai_model!r}'
        )
        from django.contrib import messages
        messages.success(
            request,
            f'Settings saved. AI service: {cfg.get_ai_service_display()}'
        )
        from django.shortcuts import redirect
        return redirect('tenders:manage_site_settings')

    from django.shortcuts import render
    from django.conf import settings as _s
    return render(request, 'manage/site_settings.html', {
        'cfg':         cfg,
        'AJIBRAIN_URL': getattr(_s, 'AJIBRAIN_BASE_URL', 'http://127.0.0.1:8001'),
    })


# ── Feedback management ──────────────────────────────────────────────────────

# Tender-domain administration: kept on the Super Admin gate that already
# guards the rest of /manage/. The IT role administers people and
# infrastructure from the System Dashboard, not tender content.
@su
def manage_feedback(request):
    """Admin list of all feedback submissions."""
    from .models import UserFeedback
    from django.shortcuts import render

    status_filter = request.GET.get('status', '')
    type_filter   = request.GET.get('type', '')
    qs = UserFeedback.objects.select_related('author', 'reviewed_by')
    if status_filter:
        qs = qs.filter(status=status_filter)
    if type_filter:
        qs = qs.filter(feedback_type=type_filter)

    return render(request, 'manage/feedback_list.html', {
        'feedbacks':     qs,
        'status_filter': status_filter,
        'type_filter':   type_filter,
        'statuses':      UserFeedback.Status.choices,
        'types':         UserFeedback.FeedbackType.choices,
        'counts': {
            'new':       UserFeedback.objects.filter(status='NEW').count(),
            'in_review': UserFeedback.objects.filter(status='IN_REVIEW').count(),
            'planned':   UserFeedback.objects.filter(status='PLANNED').count(),
        },
    })


@su
def manage_feedback_detail(request, pk):
    """Admin view/update a single feedback."""
    from .models import UserFeedback
    from django.shortcuts import render, redirect, get_object_or_404
    fb = get_object_or_404(UserFeedback, pk=pk)

    if request.method == 'POST':
        old_note       = fb.admin_note
        fb.status      = request.POST.get('status', fb.status)
        fb.priority    = request.POST.get('priority', fb.priority)
        fb.admin_note  = request.POST.get('admin_note', '').strip()
        fb.reviewed_by = request.user
        fb.save()
        # Notify user if admin_note was added/changed
        if fb.admin_note and fb.admin_note != old_note:
            from .models import FeedbackNotification
            FeedbackNotification.objects.update_or_create(
                feedback=fb,
                defaults={'is_read': False},
            )
        messages.success(request, 'Feedback updated.')
        return redirect('tenders:manage_feedback')

    return render(request, 'manage/feedback_detail.html', {
        'fb':       fb,
        'statuses': UserFeedback.Status.choices,
        'priorities': UserFeedback.Priority.choices,
    })


# ── User permissions management ──────────────────────────────────────────────

@su
def manage_users(request):
    """Tender-access control — who may switch between AEC-only and All Tenders.

    Account administration (create/edit/delete users, roles, departments) lives
    in the System Dashboard: tenders/it_views.py.
    """
    from django.contrib.auth import get_user_model
    from .models import UserProfile
    from django.shortcuts import render
    User = get_user_model()

    if request.method == 'POST':
        uid    = request.POST.get('user_id')
        action = request.POST.get('action')
        try:
            user    = User.objects.get(pk=uid)
            profile = UserProfile.objects.get_or_create(user=user)[0]
            if action == 'grant_all':
                profile.can_see_all_tenders = True
                profile.save(update_fields=['can_see_all_tenders'])
                messages.success(request, f'{user.username} can now see All Tenders.')
            elif action == 'revoke_all':
                profile.can_see_all_tenders = False
                profile.industry = 'AEC'   # reset to AEC
                profile.save(update_fields=['can_see_all_tenders', 'industry'])
                messages.success(request, f'{user.username} restricted to AEC tenders.')
            elif action == 'enable_fb_alerts':
                profile.notify_feedback_email = True
                profile.save(update_fields=['notify_feedback_email'])
                if not user.email:
                    messages.warning(request,
                        f'{user.username} has no e-mail address on their account — '
                        f'alerts enabled but nothing can be delivered until one is set.')
                else:
                    messages.success(request,
                        f'{user.username} will now receive feedback e-mail alerts.')
            elif action == 'disable_fb_alerts':
                profile.notify_feedback_email = False
                profile.save(update_fields=['notify_feedback_email'])
                messages.success(request,
                    f'Feedback e-mail alerts disabled for {user.username}.')
        except User.DoesNotExist:
            messages.error(request, 'User not found.')
        from django.shortcuts import redirect
        return redirect('tenders:manage_users')

    # Build user list with profile info
    users = User.objects.select_related('profile').filter(
        is_active=True
    ).order_by('username')

    user_data = []
    for u in users:
        profile = getattr(u, 'profile', None)
        user_data.append({
            'user':              u,
            'can_see_all':       profile.can_see_all_tenders if profile else False,
            'fb_alerts':         profile.notify_feedback_email if profile else False,
            'industry':          profile.get_industry_display() if profile else '—',
            'in_team_leaders':   u.groups.filter(name='Team Leaders').exists(),
        })

    return render(request, 'manage/users.html', {
        'user_data': user_data,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Integrations — MS Graph e-mail · MS LDAP login · WhatsApp (WaSenderAPI)
# ─────────────────────────────────────────────────────────────────────────────

def sysadmin(view_func):
    """
    Super Admins, the System Administrator role, or members of the legacy
    'System Admins' group. Reached from System Dashboard → Settings.
    """
    from .models import IntegrationSettings
    from .permissions import can_administer

    def _wrapped(request, *args, **kwargs):
        if not (can_administer(request.user)
                or IntegrationSettings.user_can_manage(request.user)):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('System Admins only')
        return view_func(request, *args, **kwargs)

    _wrapped.__name__ = view_func.__name__
    _wrapped.__doc__ = view_func.__doc__
    return login_required(_wrapped)


def _post_bool(request, name: str) -> bool:
    return request.POST.get(name) in ('on', 'true', '1', 'yes')


def _keep_secret(request, name: str, current: str) -> str:
    """Blank input means 'keep the stored secret'."""
    new = (request.POST.get(name) or '').strip()
    return new if new else current


@sysadmin
def manage_integration_settings(request):
    from .models import IntegrationSettings
    cfg = IntegrationSettings.get()

    if request.method == 'POST':
        # Only update a section the form actually submitted, so fields absent
        # from the page are left as they are rather than blanked.
        if 'section_graph' in request.POST or 'graph_tenant_id' in request.POST:
            _old_creds = (cfg.graph_tenant_id, cfg.graph_client_id, cfg.graph_client_secret,
                          cfg.graph_auth_mode, cfg.graph_sender_email, cfg.graph_mailbox_password)
            cfg.graph_enabled           = _post_bool(request, 'graph_enabled')
            cfg.graph_tenant_id         = (request.POST.get('graph_tenant_id') or '').strip()
            cfg.graph_client_id         = (request.POST.get('graph_client_id') or '').strip()
            cfg.graph_client_secret     = _keep_secret(request, 'graph_client_secret', cfg.graph_client_secret)
            cfg.graph_sender_email      = (request.POST.get('graph_sender_email') or '').strip()
            # Only APP (client credentials) and DELEGATED (mailbox sign-in) are offered.
            _mode = request.POST.get('graph_auth_mode', cfg.graph_auth_mode)
            cfg.graph_auth_mode         = (_mode if _mode in ('APP', 'DELEGATED')
                                           else IntegrationSettings.GraphAuthMode.APP)
            cfg.graph_mailbox_password  = _keep_secret(request, 'graph_mailbox_password', cfg.graph_mailbox_password)
            # Credentials / mode changed → the cached OAuth token is no longer valid.
            if (cfg.graph_tenant_id, cfg.graph_client_id, cfg.graph_client_secret,
                    cfg.graph_auth_mode, cfg.graph_sender_email, cfg.graph_mailbox_password) != _old_creds:
                cfg.graph_access_token      = ''
                cfg.graph_token_acquired_at = None
                cfg.graph_token_expires_at  = None
                cfg.graph_refresh_token     = ''
                cfg.graph_signed_in_user    = ''

        # ── LDAP / Active Directory ─────────────────────────────────────────
        if 'section_ldap' in request.POST:
            cfg.ldap_enabled           = _post_bool(request, 'ldap_enabled')
            cfg.ldap_server_uri        = (request.POST.get('ldap_server_uri') or '').strip()
            cfg.ldap_use_ssl           = _post_bool(request, 'ldap_use_ssl')
            cfg.ldap_domain            = (request.POST.get('ldap_domain') or '').strip()
            cfg.ldap_bind_dn           = (request.POST.get('ldap_bind_dn') or '').strip()
            cfg.ldap_bind_password     = _keep_secret(request, 'ldap_bind_password', cfg.ldap_bind_password)
            cfg.ldap_user_search_base  = (request.POST.get('ldap_user_search_base') or '').strip()
            cfg.ldap_user_filter       = (request.POST.get('ldap_user_filter') or '').strip() or '(sAMAccountName={username})'
            cfg.ldap_auto_create_users = _post_bool(request, 'ldap_auto_create_users')

        # ── WhatsApp ────────────────────────────────────────────────────────
        if 'section_whatsapp' in request.POST:
            cfg.wa_enabled            = _post_bool(request, 'wa_enabled')
            cfg.wa_provider           = request.POST.get('wa_provider', cfg.wa_provider)
            cfg.wa_api_url            = (request.POST.get('wa_api_url') or '').strip() or 'https://wasenderapi.com/api'
            cfg.wa_api_key            = _keep_secret(request, 'wa_api_key', cfg.wa_api_key)

        cfg.updated_by = request.user
        cfg.save()

        import logging as _log
        _log.getLogger('tenders').info(
            f'[Integrations] saved by {request.user}: '
            f'graph={cfg.graph_enabled} ldap={cfg.ldap_enabled} whatsapp={cfg.wa_enabled}'
        )
        messages.success(request, 'Integration settings saved.')
        return redirect('tenders:manage_integrations')

    return render(request, 'manage/integration_settings.html', {'cfg': cfg})


# ── Connection tests (AJAX, called from the Integrations page) ───────────────

@sysadmin
@require_POST
def integration_get_graph_token(request):
    """
    Step 1 of the Microsoft Graph integration: request an OAuth 2.0 access
    token from https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token
    (client-credentials grant) and cache it with its expiry.

    Called by the "Get Access Token" button on the Integrations page.
    """
    from .integrations import MSGraphMailer
    mailer = MSGraphMailer()
    ok, detail = mailer.acquire_token(force=True)
    cfg = mailer.cfg
    return JsonResponse({
        'ok': ok,
        'detail': detail,
        'token_status': cfg.graph_token_status(),
        'acquired_at': (cfg.graph_token_acquired_at.strftime('%Y-%m-%d %H:%M UTC')
                        if cfg.graph_token_acquired_at else None),
        'expires_at':  (cfg.graph_token_expires_at.strftime('%Y-%m-%d %H:%M UTC')
                        if cfg.graph_token_expires_at else None),
    })


@sysadmin
@require_POST
def integration_test_graph(request):
    """
    Validate the Graph integration. Uses the cached token when still valid
    (acquiring one first if needed); optionally sends a test e-mail.
    """
    from .integrations import MSGraphMailer
    mailer = MSGraphMailer()

    # Ensure we hold a valid token (cached, or freshly acquired).
    ok, detail = mailer.acquire_token(force=False)

    test_to = (request.POST.get('test_to') or '').strip()
    if ok and test_to:
        if not mailer.enabled:
            ok, detail = False, ('Token OK, but the integration switch is OFF — '
                                 'enable it and save before sending e-mails.')
        else:
            sent = mailer.send_mail(
                subject='TenderRadar — test e-mail',
                body_html=(
                    '<p>This is a test message from <strong>TenderRadar</strong>.</p>'
                    '<p>Microsoft Graph e-mail integration is working.</p>'
                ),
                to=[test_to],
            )
            detail = (f'Token valid and test e-mail sent to {test_to}.' if sent
                      else f'Token valid, but sending to {test_to} failed — '
                           f'{mailer.last_error or "check the sender mailbox and Mail.Send permission."}')
            ok = sent

    cfg = mailer.cfg
    return JsonResponse({
        'ok': ok,
        'detail': detail,
        'token_status': cfg.graph_token_status(),
        'acquired_at': (cfg.graph_token_acquired_at.strftime('%Y-%m-%d %H:%M UTC')
                        if cfg.graph_token_acquired_at else None),
        'expires_at':  (cfg.graph_token_expires_at.strftime('%Y-%m-%d %H:%M UTC')
                        if cfg.graph_token_expires_at else None),
    })


@sysadmin
@require_POST
def integration_test_ldap(request):
    from .integrations import test_ldap_connection
    ok, detail = test_ldap_connection()
    return JsonResponse({'ok': ok, 'detail': detail})


@sysadmin
@require_POST
def integration_test_whatsapp(request):
    """Send a test WhatsApp message to the given number (or the first default)."""
    from .integrations import WhatsAppClient
    from .models import IntegrationSettings

    client = WhatsAppClient()
    if not client.enabled:
        return JsonResponse({
            'ok': False,
            'detail': 'Enable WhatsApp and save the API key before testing.',
        })

    to = (request.POST.get('test_to') or '').strip()
    if not to:
        defaults = IntegrationSettings.get().wa_recipient_list()
        if not defaults:
            return JsonResponse({
                'ok': False,
                'detail': 'Enter a phone number, or set default recipients first.',
            })
        to = defaults[0]

    ok, detail = client.send_message(
        to, 'TenderRadar test message — WhatsApp integration is working.'
    )
    return JsonResponse({'ok': ok, 'detail': f'{to}: {detail}'})


# ─────────────────────────────────────────────────────────────────────────────
# Daily Tender Digest — settings, send-now, test
# ─────────────────────────────────────────────────────────────────────────────

@sysadmin
def manage_digest(request):
    """Superuser page: digest toggle + settings, per-user overview, send-now."""
    from django.contrib.auth.models import User
    from .models import IntegrationSettings
    from .digest import get_user_country_codes
    from .integrations import MSGraphMailer

    cfg = IntegrationSettings.get()

    if request.method == 'POST':
        cfg.digest_enabled  = _post_bool(request, 'digest_enabled')
        try:
            cfg.digest_hour = max(0, min(23, int(request.POST.get('digest_hour', 9))))
        except (TypeError, ValueError):
            cfg.digest_hour = 9
        cfg.digest_test_email = (request.POST.get('digest_test_email') or '').strip()
        cfg.digest_site_url   = (request.POST.get('digest_site_url') or '').strip()
        if cfg.digest_site_url.startswith('http://'):
            cfg.digest_site_url = 'https://' + cfg.digest_site_url[len('http://'):]
        cfg.save()
        messages.success(request, 'Daily digest settings saved.')
        return redirect('tenders:manage_digest')

    users = []
    for u in (User.objects.filter(is_active=True)
              .select_related('profile').order_by('username')):
        codes = get_user_country_codes(u)
        users.append({
            'id':        u.id,
            'username':  u.username,
            'full_name': u.get_full_name(),
            'email':     u.email,
            'countries': codes,
            'eligible':  bool(u.email and codes),
        })

    return render(request, 'manage/digest.html', {
        'cfg':           cfg,
        'users':         users,
        'eligible_count': sum(1 for u in users if u['eligible']),
        'graph_ready':   MSGraphMailer().enabled,
    })



def _ensure_digest_site_url(request, cfg):
    """Capture/normalize the digest base URL — TenderRadar is served over HTTPS,
    so always store an https:// URL even if the request arrived as http
    (e.g. behind a proxy without X-Forwarded-Proto)."""
    if not cfg.digest_site_url:
        cfg.digest_site_url = request.build_absolute_uri('/').rstrip('/')
    if cfg.digest_site_url.startswith('http://'):
        cfg.digest_site_url = 'https://' + cfg.digest_site_url[len('http://'):]
    cfg.save(update_fields=['digest_site_url'])


@sysadmin
@require_POST
def digest_send_now(request):
    """Send the digest immediately — to all users or to the selected ones."""
    from .models import IntegrationSettings
    from .digest import run_daily_digest

    # The e-mail's "Open in TenderRadar" links need the public base URL; if
    # it was never configured, capture it from this request once.
    cfg = IntegrationSettings.get()
    _ensure_digest_site_url(request, cfg)

    mode = request.POST.get('mode', 'all')
    user_ids = None
    if mode == 'selected':
        user_ids = [int(x) for x in request.POST.getlist('user_ids') if x.isdigit()]
        if not user_ids:
            return JsonResponse({'ok': False, 'detail': 'No users selected.'})

    summary = run_daily_digest(user_ids=user_ids)
    detail = (f'Done — sent: {summary["sent"]}, failed: {summary["failed"]}, '
              f'no preferred countries: {summary["no_countries"]}, '
              f'no new tenders: {summary["no_tenders"]}, '
              f'no e-mail: {summary["no_email"]}.')
    return JsonResponse({'ok': summary['failed'] == 0, 'detail': detail,
                         'lines': summary['details']})


@sysadmin
@require_POST
def digest_send_test(request):
    """Send a sample digest to the test address for format/flow verification."""
    from .models import IntegrationSettings
    from .digest import send_test_digest

    cfg = IntegrationSettings.get()
    _ensure_digest_site_url(request, cfg)
    email = (request.POST.get('email') or '').strip() or cfg.digest_test_email
    if not email:
        return JsonResponse({'ok': False, 'detail': 'Enter a test e-mail address.'})

    if email != cfg.digest_test_email:
        cfg.digest_test_email = email
        cfg.save(update_fields=['digest_test_email'])

    ok, detail = send_test_digest(email)
    return JsonResponse({'ok': ok, 'detail': detail})
