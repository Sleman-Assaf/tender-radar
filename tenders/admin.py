# tenders/admin.py
"""
Django admin registrations — currently DORMANT.

The admin panel is no longer mounted: `path('admin/', admin.site.urls)` was
removed from tender_radar/urls.py, because it duplicated the System Dashboard
with a raw, unguarded view of the same models. Everything in this module still
imports and registers, there is simply no URL that reaches it, so `/admin/`
returns 404 for everyone including Super Admins.

This file and templates/admin/sitesettings_change.html are kept intact and
consistent with each other, so restoring the panel is a one-line change to the
URLconf. Anything here that reverses an `admin:` URL only runs inside the panel
and is therefore unreachable while it stays unmounted.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count
from .models import (
    Tender, ScraperSource, ScrapeLog,
    RelevanceKeyword, CategoryKeyword,
    CountryConfig, TenderingURL, TenderView,
    SiteSettings,
)
try:
    from .tasks import run_scraper
except ImportError:
    run_scraper = None


# ── Country Configuration ─────────────────────────────────────────────────────

@admin.register(CountryConfig)
class CountryConfigAdmin(admin.ModelAdmin):
    list_display       = ('code', 'flag_emoji', 'name_en', 'name_ar', 'currency',
                          'source_count', 'is_active', 'sort_order')
    list_display_links = ('code',)
    list_editable      = ('flag_emoji', 'name_en', 'name_ar', 'currency', 'is_active', 'sort_order')
    list_filter        = ('is_active',)
    search_fields      = ('code', 'name_en', 'name_ar')
    list_per_page      = 30

    fieldsets = (
        ('Identity', {
            'fields': ('code', 'flag_emoji', 'sort_order', 'is_active')
        }),
        ('Names', {
            'fields': ('name_en', 'name_ar'),
        }),
        ('Defaults', {
            'fields': ('currency',),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_sc=Count('sources'))

    def source_count(self, obj):
        return obj._sc
    source_count.short_description = 'Sources'
    source_count.admin_order_field = '_sc'


# ── Relevance Keywords ────────────────────────────────────────────────────────

@admin.register(RelevanceKeyword)
class RelevanceKeywordAdmin(admin.ModelAdmin):
    list_display  = ('keyword', 'language', 'is_active', 'note', 'created_at')
    list_filter   = ('language', 'is_active')
    search_fields = ('keyword', 'note')
    list_editable = ('is_active',)
    list_per_page = 50
    ordering      = ('language', 'keyword')

    fieldsets = (
        (None, {
            'fields': ('keyword', 'language', 'is_active', 'note'),
            'description': (
                'أضف كلمة أو عبارة (عربية أو إنجليزية). إذا وُجدت في عنوان العطاء، '
                'يُعتبر ذا صلة بمجموعة AJi.<br>'
                'Add a word or phrase — if found in a tender it will be flagged as relevant.'
            )
        }),
    )
    actions = ['activate_keywords', 'deactivate_keywords']

    @admin.action(description='✓ Activate selected')
    def activate_keywords(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f'Activated {queryset.count()} keywords.')

    @admin.action(description='✗ Deactivate selected')
    def deactivate_keywords(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f'Deactivated {queryset.count()} keywords.')


# ── Category Keywords ─────────────────────────────────────────────────────────

@admin.register(CategoryKeyword)
class CategoryKeywordAdmin(admin.ModelAdmin):
    list_display  = ('keyword', 'category', 'language', 'is_active', 'created_at')
    list_filter   = ('category', 'language', 'is_active')
    search_fields = ('keyword',)
    list_editable = ('is_active',)
    list_per_page = 50
    ordering      = ('category', 'language', 'keyword')

    fieldsets = (
        (None, {
            'fields': ('keyword', 'language', 'category', 'is_active'),
            'description': (
                'اربط كلمة بتصنيف معين. مثال: "إشراف" ← إشراف إنشائي.<br>'
                'Map a keyword to a category. Example: "supervision" → Construction Supervision.'
            )
        }),
    )
    actions = ['activate_keywords', 'deactivate_keywords']

    @admin.action(description='✓ Activate selected')
    def activate_keywords(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f'Activated {queryset.count()} keywords.')

    @admin.action(description='✗ Deactivate selected')
    def deactivate_keywords(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f'Deactivated {queryset.count()} keywords.')


# ── Tendering URLs (inline) ───────────────────────────────────────────────────

class TenderingURLInline(admin.TabularInline):
    model       = TenderingURL
    extra       = 1
    can_delete  = True
    ordering    = ('sort_order', 'label')
    fields      = (
        'label', 'label_ar', 'url', 'pagination_style',
        'page_param', 'max_pages', 'extra_params',
        'notes', 'is_active', 'sort_order',
    )
    show_change_link = True


class ScrapeLogInline(admin.TabularInline):
    model           = ScrapeLog
    extra           = 0
    max_num         = 8
    can_delete      = False
    readonly_fields = (
        'started_at', 'finished_at', 'status',
        'tenders_found', 'tenders_new', 'tenders_updated', 'error_message'
    )
    ordering = ('-started_at',)

    def has_add_permission(self, request, obj=None):
        return False


# ── Scraper Sources ───────────────────────────────────────────────────────────

@admin.register(ScraperSource)
class ScraperSourceAdmin(admin.ModelAdmin):
    list_display  = (
        'name', 'country_flag', 'scraper_class', 'url_count',
        'tender_count', 'is_active', 'last_scraped_at', 'run_action'
    )
    list_filter   = ('country', 'is_active')
    search_fields = ('name', 'name_ar', 'base_url')
    list_editable = ('is_active',)
    list_per_page = 30
    inlines       = [TenderingURLInline, ScrapeLogInline]
    readonly_fields = ('last_scraped_at', 'created_at')

    fieldsets = (
        ('English', {'fields': ('name', 'description', 'is_active')}),
        ('Arabic — العربي', {
            'fields': ('name_ar', 'description_ar'),
            'classes': ('collapse',),
        }),
        ('Technical', {
            'fields': ('country', 'base_url', 'scraper_class'),
            'description': (
                '<b>scraper_class</b> must match a class in '
                '<code>tenders/scrapers/__init__.py</code>.<br>'
                'Use <b>GenericHTMLScraper</b> for standard HTML portals — '
                'no coding needed, just add URLs below.'
            )
        }),
        ('Info', {'fields': ('last_scraped_at', 'created_at')}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _tc=Count('tenders', distinct=True),
            _uc=Count('tendering_urls', distinct=True),
        )

    def tender_count(self, obj):
        return obj._tc
    tender_count.short_description = 'Tenders'
    tender_count.admin_order_field = '_tc'

    def url_count(self, obj):
        return format_html('<span title="Active tendering URLs">🔗 {}</span>', obj._uc)
    url_count.short_description = 'URLs'
    url_count.admin_order_field = '_uc'

    def country_flag(self, obj):
        return format_html('{} {}', obj.country.flag_emoji or '🌐', obj.country.name_en)
    country_flag.short_description = 'Country'

    def run_action(self, obj):
        return format_html(
            '<a style="background:#c9a84c;color:#000;padding:4px 10px;border-radius:4px;'
            'font-size:.8rem;font-weight:600;text-decoration:none;" '
            'href="#" onclick="fetch(\'/scraper/run/{}/\',{{method:\'POST\','
            'headers:{{\'X-CSRFToken\':document.cookie.match(/csrftoken=([^;]+)/)[1]}}}}'
            ').then(r=>r.ok?alert(\'Scraper queued!\'):alert(\'Error\')).catch(()=>alert(\'Error\'));'
            'return false;">▶ Run</a>',
            obj.pk
        )
    run_action.short_description = 'Action'

    actions = ['trigger_scrape', 'activate_sources', 'deactivate_sources']

    @admin.action(description='▶ Trigger scrape for selected sources')
    def trigger_scrape(self, request, queryset):
        if run_scraper is None:
            self.message_user(request, 'Celery tasks not available.', level='error')
            return
        for source in queryset.filter(is_active=True):
            run_scraper.delay(source.pk)
        self.message_user(request, f'Queued {queryset.count()} scrapers.')

    @admin.action(description='✓ Activate selected sources')
    def activate_sources(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, f'Activated {queryset.count()} sources.')

    @admin.action(description='✗ Deactivate selected sources')
    def deactivate_sources(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, f'Deactivated {queryset.count()} sources.')


# ── TenderingURL standalone view ──────────────────────────────────────────────

@admin.register(TenderingURL)
class TenderingURLAdmin(admin.ModelAdmin):
    list_display  = (
        'label', 'source', 'short_url', 'pagination_style',
        'max_pages', 'is_active', 'sort_order'
    )
    list_filter   = ('source__country', 'pagination_style', 'is_active')
    search_fields = ('label', 'label_ar', 'url', 'source__name')
    list_editable = ('is_active', 'sort_order', 'max_pages')
    list_per_page = 40
    ordering      = ('source', 'sort_order')

    fieldsets = (
        ('Identity', {
            'fields': ('source', 'label', 'label_ar', 'is_active', 'sort_order')
        }),
        ('URL & Pagination', {
            'fields': ('url', 'pagination_style', 'page_param', 'max_pages', 'extra_params'),
            'description': (
                '<b>Pagination styles:</b><br>'
                '• <b>Query param</b>: adds <code>?page=N</code> — most common<br>'
                '• <b>Path segment</b>: appends <code>/N/</code> to URL<br>'
                '• <b>Single page</b>: scrapes URL once, no pagination<br>'
                '• <b>JSON API</b>: for portals with a REST API<br>'
                '<b>page_param</b>: the query param name (e.g. <code>page</code>, <code>Page</code>, <code>p</code>)'
            )
        }),
        ('Notes', {'fields': ('notes',)}),
    )

    def short_url(self, obj):
        url = obj.url
        short = url[:60] + '…' if len(url) > 60 else url
        return format_html('<a href="{}" target="_blank">{}</a>', url, short)
    short_url.short_description = 'URL'


# ── Tenders ───────────────────────────────────────────────────────────────────

@admin.register(Tender)
class TenderAdmin(admin.ModelAdmin):
    list_display  = (
        'truncated_title', 'country', 'category', 'status',
        'entity', 'submission_deadline', 'is_mirror', 'bookmark_count', 'is_aec', 'scraped_at'
    )
    # Bookmarks moved to the per-user TenderBookmark model, so there is no
    # is_bookmarked column on Tender to list, filter or edit here.
    list_filter   = ('country', 'category', 'status', 'is_relevant', 'is_mirror', 'is_aec', 'is_aec_checked')
    search_fields = (
        'title', 'title_ar', 'reference_no',
        'entity', 'entity_ar', 'description', 'description_ar'
    )
    readonly_fields = ('scraped_at', 'updated_at', 'content_hash', 'source_url')
    date_hierarchy  = 'scraped_at'
    list_per_page   = 50

    fieldsets = (
        ('Core — أساسي', {
            'fields': ('title', 'title_ar', 'reference_no',
                       'description', 'description_ar',
                       'source', 'tendering_url', 'source_url', 'external_id')
        }),
        ('Classification — تصنيف', {
            'fields': ('country', 'entity', 'entity_ar', 'category', 'status')
        }),
        ('Financials — مالي', {
            'fields': ('estimated_value', 'currency'),
            'classes': ('collapse',),
        }),
        ('Dates — تواريخ', {
            'fields': ('publication_date', 'submission_deadline', 'opening_date')
        }),
        ('Mirror / Cross-country copy', {
            'fields': ('is_mirror', 'mirror_of'),
            'classes': ('collapse',),
            'description': (
                'Mirror rows are auto-created for WB / UN / DE / IsDB tenders '
                'so the tender appears under the beneficiary country.'
            ),
        }),
        ('Meta', {
            # Notes moved to the per-user TenderUserNote model, so there is no
            # notes column on Tender to edit here.
            'fields': ('is_relevant', 'is_aec', 'is_aec_checked',
                       'content_hash', 'scraped_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Bookmarks', ordering='bookmarks__count')
    def bookmark_count(self, obj):
        """How many users have saved this tender."""
        return obj.bookmarks.count()

    def truncated_title(self, obj):
        display = obj.title_ar if obj.title_ar else obj.title
        prefix  = '🔁 ' if obj.is_mirror else ''
        label   = display[:70] + ('…' if len(display) > 70 else '')
        return format_html('{}{}', prefix, label)
    truncated_title.short_description = 'Title / العنوان'


# ── Scrape Logs ───────────────────────────────────────────────────────────────

@admin.register(ScrapeLog)
class ScrapeLogAdmin(admin.ModelAdmin):
    list_display = (
        'source', 'status', 'tenders_found', 'tenders_new',
        'tenders_updated', 'started_at', 'duration_seconds'
    )
    list_filter     = ('status', 'source')
    readonly_fields = (
        'source', 'tendering_url', 'started_at', 'finished_at', 'status',
        'tenders_found', 'tenders_new', 'tenders_updated',
        'error_message', 'duration_seconds'
    )


@admin.register(TenderView)
class TenderViewAdmin(admin.ModelAdmin):
    list_display  = ('user', 'tender', 'viewed_at')
    list_filter   = ('user',)
    readonly_fields = ('tender', 'user', 'viewed_at')


# ── Site Settings (singleton, custom toggle UI) ──────────────────────────────

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """
    Singleton admin for the global scraping-mode toggle.
    Uses a fully custom change_view that renders a visual on/off switch.
    The form POSTs back to the same URL; CSRF is handled via get_token().
    """

    def has_add_permission(self, request):
        return False          # only ever one row

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Redirect the list view straight to the singleton change page."""
        from django.urls import reverse
        url = reverse('admin:tenders_sitesettings_change', args=[1])
        from django.shortcuts import redirect
        return redirect(url)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        from django.middleware.csrf import get_token
        from django.shortcuts import redirect
        from django.template.response import TemplateResponse

        obj = SiteSettings.get()

        # ── Handle form POST ──────────────────────────────────────────────
        if request.method == 'POST':
            new_mode = request.POST.get('scraping_mode', '')
            if new_mode in (SiteSettings.ScrapingMode.FILTERED,
                            SiteSettings.ScrapingMode.ALL):
                obj.scraping_mode = new_mode
                obj.updated_by    = request.user
                obj.save()
                level = 'success' if new_mode == SiteSettings.ScrapingMode.FILTERED                         else 'warning'
                self.message_user(
                    request,
                    f'Scraping mode updated to: {obj.get_scraping_mode_display()}',
                    level=level,
                )
            return redirect('.')

        # ── Build context for template ────────────────────────────────────
        is_all     = obj.scraping_mode == SiteSettings.ScrapingMode.ALL
        updated_by = obj.updated_by.username if obj.updated_by else 'system'
        updated_at = obj.updated_at.strftime('%Y-%m-%d %H:%M') if obj.updated_at else '—'

        ctx = self.admin_site.each_context(request)
        ctx.update({
            'title':       'Site Settings — Scraping Mode',
            'opts':        self.model._meta,
            'object':      obj,
            'is_all':      is_all,
            'updated_by':  updated_by,
            'updated_at':  updated_at,
            'csrf_token':  get_token(request),
            'current_mode': obj.get_scraping_mode_display(),
        })
        return TemplateResponse(request, 'admin/sitesettings_change.html', ctx)


# ── Admin site branding ───────────────────────────────────────────────────────

admin.site.site_header = '⬡ TenderRadar — AJi Group'
admin.site.site_title  = 'TenderRadar Admin'
admin.site.index_title = 'إدارة النظام — System Management' 


from .models import UserProfile

class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ['user', 'role', 'department', 'industry', 'updated_at']
    list_filter   = ['role', 'department', 'industry']
    search_fields = ['user__username', 'user__email']
    filter_horizontal = ['countries']

admin.site.register(UserProfile, UserProfileAdmin)


# ── Roles, Departments, Backups, Audit ────────────────────────────────────────
# Day-to-day administration happens in the System Dashboard (/it/); these
# registrations exist for raw inspection and emergency repair only.

from .models import (
    AdminAuditLog, BackupRestore, BackupSchedule, Department, SystemBackup,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display  = ['name', 'code', 'head', 'employee_count', 'is_active']
    list_filter   = ['is_active']
    search_fields = ['name', 'name_ar', 'code']


@admin.register(SystemBackup)
class SystemBackupAdmin(admin.ModelAdmin):
    list_display   = ['filename', 'status', 'trigger', 'size_display',
                      'created_by', 'created_at']
    list_filter    = ['status', 'trigger']
    search_fields  = ['filename', 'note']
    readonly_fields = ['filename', 'file_path', 'size_bytes', 'created_at', 'finished_at']


@admin.register(BackupRestore)
class BackupRestoreAdmin(admin.ModelAdmin):
    list_display = ['backup', 'status', 'restored_by', 'restored_at']
    list_filter  = ['status']


@admin.register(BackupSchedule)
class BackupScheduleAdmin(admin.ModelAdmin):
    list_display = ['frequency', 'hour', 'enabled', 'retention_count', 'last_run_at']

    def has_add_permission(self, request):
        return not BackupSchedule.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display   = ['created_at', 'actor', 'action', 'target', 'ip_address']
    list_filter    = ['action']
    search_fields  = ['target', 'detail', 'actor__username']
    readonly_fields = ['actor', 'action', 'target', 'detail', 'ip_address', 'created_at']

    def has_add_permission(self, request):
        return False                      # append-only, written by the app


# ── Integration Settings (singleton) ──────────────────────────────────────────

from .models import IntegrationSettings


@admin.register(IntegrationSettings)
class IntegrationSettingsAdmin(admin.ModelAdmin):
    """
    Singleton admin. Day-to-day editing happens on the styled page at
    /manage/integrations/ — this is only a fallback.
    """
    fieldsets = (
        ('Microsoft Graph API — E-mail', {
            'fields': ('graph_enabled', 'graph_tenant_id', 'graph_client_id',
                       'graph_client_secret', 'graph_sender_email'),
        }),
        ('Microsoft LDAP / Active Directory — Login', {
            'fields': ('ldap_enabled', 'ldap_server_uri', 'ldap_use_ssl',
                       'ldap_domain', 'ldap_bind_dn', 'ldap_bind_password',
                       'ldap_user_search_base', 'ldap_user_filter',
                       'ldap_auto_create_users'),
        }),
        ('WhatsApp — Alerts', {
            'fields': ('wa_enabled', 'wa_provider', 'wa_api_url', 'wa_api_key'),
        }),
        ('Audit', {'fields': ('updated_at', 'updated_by')}),
    )
    readonly_fields = ('updated_at', 'updated_by')

    def has_add_permission(self, request):
        return not IntegrationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse
        IntegrationSettings.get()
        return redirect(reverse('admin:tenders_integrationsettings_change', args=[1]))

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
