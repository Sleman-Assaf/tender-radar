# tenders/models.py

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import RegexValidator


# ── Country Configuration (DB-managed, not hardcoded) ─────────────────────────

class CountryConfig(models.Model):
    """
    Staff-managed country registry.
    Replaces the hardcoded Country TextChoices enum.
    New countries can be added by staff without touching code.
    """
    code        = models.CharField(
        max_length=3, unique=True, primary_key=True,
        validators=[RegexValidator(r'^[A-Z]{2,3}$', 'Use 2–3 uppercase letters, e.g. JO, SA, UAE')],
        help_text='ISO 2-letter country code, e.g. JO, SA, AE'
    )
    name_en     = models.CharField(max_length=100, verbose_name='Name (English)')
    name_ar     = models.CharField(max_length=100, verbose_name='الاسم بالعربي')
    flag_emoji  = models.CharField(max_length=10, blank=True, verbose_name='Flag Emoji',
                                   help_text='e.g. 🇯🇴')
    currency    = models.CharField(max_length=10, blank=True,
                                   help_text='e.g. JOD, SAR, AED')
    is_active      = models.BooleanField(default=True)
    is_organization = models.BooleanField(
        default=False,
        help_text='If True, this is an international org (World Bank, AfDB…), '
                  'not a country — excluded from country filter dropdown.',
    )
    sort_order  = models.PositiveSmallIntegerField(default=0,
                                                    help_text='Display order (lower = first)')
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['sort_order', 'name_en']
        verbose_name        = 'Country'
        verbose_name_plural = 'Countries — الدول'

    def __str__(self):
        flag = self.flag_emoji or ''
        return f'{flag} {self.name_en} ({self.code})'

    @property
    def display_name(self):
        return f'{self.flag_emoji} {self.name_en}' if self.flag_emoji else self.name_en


class TenderCategory(models.TextChoices):
    """
    Tender categories — exactly THREE user-facing categories.
    Classification is done by the AJiAI API (/api/classify-tender/),
    which returns is_aec + one of these categories.
    OTHER is an internal default for unclassified / non-matching tenders
    and is NOT shown in the filter UI.
    """
    BUILDING        = 'BUILDING', 'Building — مباني'
    INFRASTRUCTURE  = 'INFRA',    'Infrastructure — بنية تحتية'
    HEALTHCARE      = 'HEALTH',   'Health care — رعاية صحية'
    OTHER           = 'OTHER',    'Other — أخرى'


# The three categories shown in filter dropdowns (OTHER excluded)
TENDER_CATEGORY_FILTER_CHOICES = [
    (TenderCategory.BUILDING.value,       TenderCategory.BUILDING.label),
    (TenderCategory.INFRASTRUCTURE.value, TenderCategory.INFRASTRUCTURE.label),
    (TenderCategory.HEALTHCARE.value,     TenderCategory.HEALTHCARE.label),
]


class TenderStatus(models.TextChoices):
    OPEN        = 'OPEN',   'Open — مفتوح'
    CLOSED      = 'CLOSED', 'Closed — مغلق'
    AWARDED     = 'AWARD',  'Awarded — مُرسى'
    CANCELLED   = 'CANCEL', 'Cancelled — ملغى'
    UNKNOWN     = 'UNK',    'Unknown — غير محدد'


# ── Keyword Management ────────────────────────────────────────────────────────

class RelevanceKeyword(models.Model):
    keyword    = models.CharField(max_length=200, unique=True,
                                  help_text='Keyword or phrase (English or Arabic)')
    language   = models.CharField(max_length=2, choices=[('EN', 'English'), ('AR', 'Arabic')],
                                  default='EN')
    is_active  = models.BooleanField(default=True)
    note       = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['language', 'keyword']
        verbose_name        = 'Relevance Keyword'
        verbose_name_plural = 'Relevance Keywords — كلمات الصلة'

    def __str__(self):
        return f'[{self.language}] {self.keyword}'


class CategoryKeyword(models.Model):
    category   = models.CharField(max_length=10, choices=TenderCategory.choices)
    keyword    = models.CharField(max_length=200)
    language   = models.CharField(max_length=2, choices=[('EN', 'English'), ('AR', 'Arabic')],
                                  default='EN')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['category', 'language', 'keyword']
        unique_together     = [['category', 'keyword']]
        verbose_name        = 'Category Keyword'
        verbose_name_plural = 'Category Keywords — كلمات التصنيف'

    def __str__(self):
        return f'[{self.category}][{self.language}] {self.keyword}'


# ── Scraper Sources & URLs ────────────────────────────────────────────────────

class ScraperSource(models.Model):
    """
    A government portal that we scrape.
    Each source can have multiple TenderingURL records — one per listing page,
    category filter, or pagination endpoint.
    """
    name           = models.CharField(max_length=200)
    name_ar        = models.CharField(max_length=200, blank=True,
                                      verbose_name='Name (Arabic) — الاسم بالعربي')
    country        = models.ForeignKey(
        CountryConfig, on_delete=models.PROTECT,
        related_name='sources', to_field='code',
        help_text='Select country from the Countries list'
    )
    base_url       = models.URLField(max_length=500,
                                     help_text='Root URL of the portal, e.g. https://www.gtd.gov.jo')
    scraper_class  = models.CharField(
        max_length=100,
        help_text='Python class name registered in tenders/scrapers/__init__.py'
    )
    is_active      = models.BooleanField(default=True)
    description    = models.TextField(blank=True)
    description_ar = models.TextField(blank=True, verbose_name='Description (Arabic)')
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['country', 'name']
        verbose_name        = 'Scraper Source'
        verbose_name_plural = 'Scraper Sources — مصادر العطاءات'

    def __str__(self):
        return f'{self.country.flag_emoji} {self.country.name_en} — {self.name}'

    @property
    def active_urls(self):
        return self.tendering_urls.filter(is_active=True)


class TenderingURL(models.Model):
    """
    A specific listing URL within a ScraperSource.
    Each source can have many URLs — e.g. one per ministry, category, or region.
    The scraper iterates all active URLs for a source automatically.
    """

    class PaginationStyle(models.TextChoices):
        QUERY_PARAM = 'QUERY',  'Query param  (?page=N)'
        PATH_PARAM  = 'PATH',   'Path segment (/page/N/)'
        OFFSET      = 'OFFSET', 'Offset param (?start=N)'
        CURSOR      = 'CURSOR', 'Cursor / token (custom)'
        SINGLE      = 'SINGLE', 'Single page (no pagination)'
        API_JSON    = 'API',    'JSON API endpoint'

    source           = models.ForeignKey(ScraperSource, on_delete=models.CASCADE,
                                         related_name='tendering_urls')
    label            = models.CharField(max_length=200,
                                        help_text='Friendly label, e.g. "Ministry of Health tenders"')
    label_ar         = models.CharField(max_length=200, blank=True,
                                        verbose_name='Label (Arabic)')
    url              = models.URLField(max_length=1000,
                                       help_text='Full listing URL, e.g. https://www.gtd.gov.jo/tenders')
    pagination_style = models.CharField(max_length=30, choices=PaginationStyle.choices,
                                        default=PaginationStyle.QUERY_PARAM)
    page_param       = models.CharField(max_length=50, default='page',
                                        help_text='Query param name for page number, e.g. "page", "Page", "p"')
    max_pages        = models.PositiveSmallIntegerField(default=20,
                                                        help_text='Max pages to scrape (safety limit)')
    extra_params     = models.JSONField(default=dict, blank=True,
                                        help_text='Extra query params as JSON, e.g. {"status": "open"}')
    notes            = models.TextField(blank=True,
                                        help_text='Notes about this URL (scope, filters, etc.)')
    is_active        = models.BooleanField(default=True)
    sort_order       = models.PositiveSmallIntegerField(default=0)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['source', 'sort_order', 'label']
        verbose_name        = 'Tendering URL'
        verbose_name_plural = 'Tendering URLs — روابط العطاءات'

    def __str__(self):
        return f'{self.source.name} › {self.label}'

    def build_page_url(self, page: int) -> str:
        """Return the URL for a specific page number based on pagination_style."""
        if self.pagination_style == self.PaginationStyle.SINGLE:
            return self.url
        if self.pagination_style == self.PaginationStyle.PATH_PARAM:
            return f'{self.url.rstrip("/")}/{page}/'
        if self.pagination_style == self.PaginationStyle.OFFSET:
            sep = '&' if '?' in self.url else '?'
            return f'{self.url}{sep}{self.page_param}={page}'
        # Default: QUERY_PARAM and API_JSON
        sep = '&' if '?' in self.url else '?'
        return f'{self.url}{sep}{self.page_param}={page}'


# ── Tenders ───────────────────────────────────────────────────────────────────

class Tender(models.Model):
    source          = models.ForeignKey(ScraperSource, on_delete=models.SET_NULL,
                                        null=True, related_name='tenders')
    tendering_url   = models.ForeignKey(TenderingURL, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name='tenders',
                                        help_text='Which URL this tender was scraped from')
    external_id     = models.CharField(max_length=300, blank=True)
    source_url      = models.URLField(max_length=2000)
    content_hash    = models.CharField(max_length=64, blank=True)

    title           = models.TextField()
    title_ar        = models.TextField(blank=True, verbose_name='Title (Arabic)')
    reference_no    = models.CharField(max_length=200, blank=True)
    description     = models.TextField(blank=True)
    description_ar  = models.TextField(blank=True, verbose_name='Description (Arabic)')
    country         = models.ForeignKey(
        CountryConfig, on_delete=models.SET_NULL, null=True,
        related_name='tenders', to_field='code'
    )
    entity          = models.CharField(max_length=5000, blank=True)
    entity_ar       = models.CharField(max_length=5000, blank=True,
                                       verbose_name='Entity (Arabic)')
    category        = models.CharField(max_length=30, choices=TenderCategory.choices,
                                       default=TenderCategory.OTHER)
    status          = models.CharField(max_length=30, choices=TenderStatus.choices,
                                       default=TenderStatus.UNKNOWN)

    estimated_value     = models.DecimalField(max_digits=18, decimal_places=2,
                                              null=True, blank=True)
    currency            = models.CharField(max_length=10, blank=True)

    publication_date    = models.DateField(null=True, blank=True)
    submission_deadline = models.DateField(null=True, blank=True)
    opening_date        = models.DateField(null=True, blank=True)

    is_relevant     = models.BooleanField(default=True)
    # Bookmarks are per-user; see the TenderBookmark model below. There is
    # deliberately no is_bookmarked field here — as a column on the tender it was
    # one shared flag for all users.
    sort_order      = models.PositiveIntegerField(
        default=0,
        help_text='Display order within same scrape run. WorldBank: 0=known deadline, 9999=unknown.',
    )
    is_aec          = models.BooleanField(
        default=False,
        verbose_name='AEC Related',
        help_text='True if this tender is related to Architecture, Engineering & Construction industry',
        db_index=True,
    )
    is_aec_checked  = models.BooleanField(
        default=False,
        help_text='True once the AJiBrain AEC classification API has been called for this tender',
    )
    # Notes are per-user; see the TenderUserNote model below. There is
    # deliberately no notes field here — as a column on the tender it was one
    # shared box that any user could read and overwrite.
    scraped_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    # Mirror / cross-country copy
    is_mirror       = models.BooleanField(
        default=False,
        help_text='True when this row is a country-mirror copy of a WB/UN/DE/IB tender'
    )
    mirror_of       = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='mirrors',
        help_text='Points to the original tender this row was copied from'
    )

    class Meta:
        ordering        = ['-publication_date', '-scraped_at']
        unique_together = [['source', 'external_id']]
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['category']),
            models.Index(fields=['submission_deadline']),
        ]

    def __str__(self):
        country_name = self.country.name_en if self.country else '?'
        return f'[{country_name}] {self.title[:80]}'

    @property
    def is_expired(self):
        if self.submission_deadline:
            return self.submission_deadline < timezone.now().date()
        return False

    @property
    def days_remaining(self):
        if self.submission_deadline:
            return (self.submission_deadline - timezone.now().date()).days
        return None

    @property
    def country_code(self):
        return self.country.code if self.country else ''


# ── Scrape Logs ───────────────────────────────────────────────────────────────

class ScrapeLog(models.Model):
    class RunStatus(models.TextChoices):
        RUNNING = 'RUNNING', 'Running'
        SUCCESS = 'SUCCESS', 'Success'
        PARTIAL = 'PARTIAL', 'Partial'
        FAILED  = 'FAILED',  'Failed'

    source          = models.ForeignKey(ScraperSource, on_delete=models.CASCADE,
                                        related_name='logs')
    tendering_url   = models.ForeignKey(TenderingURL, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name='logs')
    started_at      = models.DateTimeField(auto_now_add=True)
    finished_at     = models.DateTimeField(null=True, blank=True)
    status          = models.CharField(max_length=10, choices=RunStatus.choices,
                                       default=RunStatus.RUNNING)
    tenders_found   = models.IntegerField(default=0)
    tenders_new     = models.IntegerField(default=0)
    tenders_updated = models.IntegerField(default=0)
    error_message   = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.source} | {self.started_at:%Y-%m-%d %H:%M} | {self.status}'

    @property
    def duration_seconds(self):
        if self.finished_at:
            return (self.finished_at - self.started_at).seconds
        return None


# ── Tender View Tracking ──────────────────────────────────────────────────────

class TenderView(models.Model):
    """Tracks which tenders each user has opened/viewed."""
    tender     = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name='views')
    user       = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='tender_views')
    viewed_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['tender', 'user']]
        indexes = [models.Index(fields=['user', 'tender'])]
        verbose_name        = 'Tender View'
        verbose_name_plural = 'Tender Views'

    def __str__(self):
        return f'{self.user.username} viewed #{self.tender_id}'


# ── Bookmarks ─────────────────────────────────────────────────────────────────

class TenderBookmark(models.Model):
    """
    One user's bookmark on one tender.

    Bookmarking used to be a single boolean column on Tender, shared by
    everybody: one person starring a tender put it in every user's "Bookmarked"
    list, the dashboard counted it for all of them, and anyone could remove it
    again. A bookmark is a personal shortlist, so it is stored per user — the
    same shape as TenderView above.

    `created_at` carries a default rather than auto_now_add so the migration that
    split the old shared flag apart could keep each bookmark's original date.
    """
    tender     = models.ForeignKey(Tender, on_delete=models.CASCADE,
                                   related_name='bookmarks')
    user       = models.ForeignKey('auth.User', on_delete=models.CASCADE,
                                   related_name='tender_bookmarks')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together     = [['tender', 'user']]      # one bookmark per user per tender
        ordering            = ['-created_at']
        indexes             = [models.Index(fields=['user', 'tender'])]
        verbose_name        = 'Tender Bookmark'
        verbose_name_plural = 'Tender Bookmarks — المحفوظات'

    def __str__(self):
        return f'{self.user.username} bookmarked #{self.tender_id}'


class TenderUserNote(models.Model):
    """
    One user's private working note on one tender.

    `Tender.notes` was a single text box on the tender shared by everybody: any
    user could read what another had written and overwrite it by saving, and
    because the box was labelled "internal — AJi group only" people used it as a
    message board rather than a notepad. One of the two notes on this database
    opens "Dear IT Team".

    A note is now private to its author. Sharing has its own feature: TenderNote
    posts a note to a chosen team and notifies the members.
    """
    tender     = models.ForeignKey(Tender, on_delete=models.CASCADE,
                                   related_name='user_notes')
    user       = models.ForeignKey('auth.User', on_delete=models.CASCADE,
                                   related_name='tender_user_notes')
    body       = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together     = [['tender', 'user']]   # one notepad per user per tender
        ordering            = ['-updated_at']
        indexes             = [models.Index(fields=['user', 'tender'])]
        verbose_name        = 'Private Note'
        verbose_name_plural = 'Private Notes — ملاحظات خاصة'

    def __str__(self):
        return f'{self.user.username} note on #{self.tender_id}'


# ── Site-wide Settings (singleton) ───────────────────────────────────────────

class SiteSettings(models.Model):
    """
    Global configuration singleton for TenderRadar.
    Only one row is ever created (pk=1).  Use SiteSettings.get() everywhere.
    """

    class ScrapingMode(models.TextChoices):
        FILTERED = 'FILTERED', 'Filtered — relevant tenders only (uses keyword list)'
        ALL      = 'ALL',      'All — save every tender regardless of keywords'

    class AIService(models.TextChoices):
        AJIBRAIN = 'AJIBRAIN', 'AJiBrain (local Ollama — default)'
        OPENAI   = 'OPENAI',   'OpenAI API (GPT-4o-mini — batch per scraper)'
        OLLAMA   = 'OLLAMA',   'Ollama (direct — local Claude/Llama/Qwen models)'

    scraping_mode = models.CharField(
        max_length=10,
        choices=ScrapingMode.choices,
        default=ScrapingMode.FILTERED,
        verbose_name='Scraping Mode',
        help_text=(
            'FILTERED: only tenders matching the relevance keyword list are saved. '
            'ALL: every scraped tender is saved and marked relevant — '
            'ignores keyword matching entirely.'
        ),
    )

    # ── AI Service settings ────────────────────────────────────────────────────
    ai_service = models.CharField(
        max_length=10,
        choices=AIService.choices,
        default=AIService.AJIBRAIN,
        verbose_name='AI Service',
        help_text=(
            'AJiBrain: uses local Ollama via AJiBrain API (one tender at a time). '
            'OpenAI: sends batch titles per scraper run — faster and more accurate.'
        ),
    )
    openai_api_key = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='OpenAI API Key',
        help_text='Required when AI Service = OpenAI. Stored encrypted in DB.',
    )
    openai_model = models.CharField(
        max_length=60, blank=True, default='gpt-4o-mini',
        verbose_name='OpenAI Model',
        help_text='Model to use: gpt-4o-mini (fast/cheap), gpt-4o (best quality).',
    )
    openai_classify_batch = models.PositiveIntegerField(
        default=50,
        verbose_name='Classification Batch Size',
        help_text='Number of tender titles sent in one OpenAI classify request.',
    )
    openai_translate_batch = models.PositiveIntegerField(
        default=30,
        verbose_name='Translation Batch Size',
        help_text='Number of titles translated in one OpenAI translate request.',
    )

    # ── Ollama direct settings ──────────────────────────────────────────────
    ollama_base_url = models.CharField(
        max_length=200, blank=True, default='http://localhost:11434',
        verbose_name='Ollama Base URL',
        help_text='Ollama API URL, e.g. http://localhost:11434',
    )
    ollama_model = models.CharField(
        max_length=100, blank=True, default='qwen2.5:7b',
        verbose_name='Ollama Model',
        help_text='Model name as shown in "ollama list", e.g. qwen2.5:7b, llama3.1:8b, claude3-haiku',
    )
    ollama_classify_batch = models.PositiveIntegerField(
        default=20,
        verbose_name='Ollama Classification Batch Size',
        help_text='Titles per classify request (smaller than OpenAI — local model context limits).',
    )
    ollama_translate_batch = models.PositiveIntegerField(
        default=10,
        verbose_name='Ollama Translation Batch Size',
        help_text='Titles per translate request.',
    )

    # ── Legacy fields from 0015_sitesettings_ai_fields ───────────────────────
    ai_classify   = models.BooleanField(default=False, verbose_name='AI Classification (legacy)')
    ai_translate  = models.BooleanField(default=False, verbose_name='AI Translation (legacy)')
    ajibrain_url  = models.CharField(blank=True, default='http://localhost:8001', max_length=200, verbose_name='AJiBrain URL')
    ajibrain_model= models.CharField(blank=True, default='qwen2.5:14b', max_length=100, verbose_name='AJiBrain Model')

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        verbose_name='Last changed by',
    )

    class Meta:
        verbose_name        = 'Site Settings'
        verbose_name_plural = 'Site Settings'

    def __str__(self):
        return f'Site Settings — mode: {self.scraping_mode}'

    @classmethod
    def get(cls) -> 'SiteSettings':
        """Always returns the singleton row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def scraping_mode_value(cls) -> str:
        """Fast read of just the scraping_mode value."""
        try:
            return cls.objects.values_list('scraping_mode', flat=True).get(pk=1)
        except cls.DoesNotExist:
            return cls.ScrapingMode.FILTERED


# ── Integration Settings (singleton) ─────────────────────────────────────────

class IntegrationSettings(models.Model):
    """
    Third-party integration configuration singleton (pk=1).

      * Microsoft Graph API  → outgoing e-mail alerts & reports
      * Microsoft LDAP / AD  → login authentication
      * WhatsApp (WaSenderAPI) → outgoing WhatsApp alerts

    Always read through IntegrationSettings.get().
    """

    SYSADMIN_GROUP = 'System Admins'

    class WhatsAppProvider(models.TextChoices):
        WASENDER = 'WASENDER', 'WaSenderAPI (https://wasenderapi.com/api)'
        CUSTOM   = 'CUSTOM',   'Custom / Other provider (WaSender-compatible)'

    # ── Microsoft Graph API — e-mail ────────────────────────────────────────
    class GraphAuthMode(models.TextChoices):
        APP         = 'APP',         'Application — client credentials (admin consent, no mailbox password)'
        DELEGATED   = 'DELEGATED',   'Delegated — sign in with the mailbox e-mail & password'
        INTERACTIVE = 'INTERACTIVE', 'Interactive — Microsoft sign-in popup (refresh token stored)'

    graph_enabled = models.BooleanField(
        default=False, verbose_name='Enable MS Graph e-mail',
        help_text='Send e-mail alerts & reports through Microsoft Graph API.',
    )
    graph_tenant_id = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Tenant ID (Directory ID)',
    )
    graph_client_id = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Client ID (Application ID)',
    )
    graph_client_secret = models.CharField(
        max_length=300, blank=True, default='',
        verbose_name='Client Secret',
        help_text='App registration secret. Requires Mail.Send application permission.',
    )
    graph_sender_email = models.EmailField(
        blank=True, default='',
        verbose_name='Sender mailbox',
        help_text='Mailbox used as From: (e.g. tenders@company.com).',
    )
    graph_auth_mode = models.CharField(
        max_length=12, choices=GraphAuthMode.choices, default=GraphAuthMode.APP,
        verbose_name='Authentication mode',
        help_text=('Application: token via client credentials — requires Mail.Send '
                   '*application* permission + admin consent. '
                   'Delegated: token via the mailbox username & password — requires '
                   'Mail.Send *delegated* permission and MFA disabled for that account.'),
    )
    graph_mailbox_password = models.CharField(
        max_length=300, blank=True, default='',
        verbose_name='Mailbox password',
        help_text='Only used in Delegated mode — password of the sender mailbox.',
    )
    graph_report_recipients = models.TextField(
        blank=True, default='',
        verbose_name='Default report recipients',
        help_text='Comma-separated e-mail addresses for scheduled alerts & reports.',
    )

    # ── Microsoft Graph — OAuth 2.0 access token (client-credentials flow) ──
    # Acquired on first integration ("Get Access Token") and cached here.
    # Automatically refreshed when expired.
    graph_access_token = models.TextField(
        blank=True, default='', editable=False,
        verbose_name='Graph access token (cached)',
    )
    graph_token_expires_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        verbose_name='Graph token expires at',
    )
    graph_token_acquired_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        verbose_name='Graph token acquired at',
    )
    # Interactive mode only — long-lived refresh token from the sign-in popup,
    # used to renew the access token silently (~90 days rolling).
    graph_refresh_token = models.TextField(
        blank=True, default='', editable=False,
        verbose_name='Graph refresh token (interactive sign-in)',
    )
    graph_signed_in_user = models.CharField(
        max_length=200, blank=True, default='', editable=False,
        verbose_name='Signed-in Microsoft account',
    )

    # ── Daily tender digest e-mail ───────────────────────────────────────
    digest_enabled = models.BooleanField(
        default=False,
        verbose_name='Daily digest enabled',
        help_text='Send each user a daily HTML e-mail with new tenders from their preferred countries.',
    )
    digest_hour = models.PositiveSmallIntegerField(
        default=9,
        verbose_name='Send hour (0–23)',
        help_text='Local server hour the daily digest is sent (default 9 AM).',
    )
    digest_test_email = models.EmailField(
        blank=True, default='Munaf_Dhuhairat@ajigroup.com',
        verbose_name='Test recipient',
    )
    digest_site_url = models.URLField(
        blank=True, default='',
        verbose_name='TenderRadar base URL',
        help_text='Used for "Open in TenderRadar" links in the e-mail, e.g. https://tenders.ajigroup.com — leave blank to link to the source portals only.',
    )
    digest_last_run_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        verbose_name='Digest last run at',
    )

    # ── Microsoft LDAP / Active Directory — login ───────────────────────────
    ldap_enabled = models.BooleanField(
        default=False, verbose_name='Enable LDAP login',
        help_text='Authenticate users against Microsoft LDAP / Active Directory.',
    )
    ldap_server_uri = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='LDAP server URI',
        help_text='e.g. ldap://dc1.company.local:389 or ldaps://dc1.company.local:636',
    )
    ldap_use_ssl = models.BooleanField(default=False, verbose_name='Use SSL (LDAPS)')
    ldap_domain = models.CharField(
        max_length=120, blank=True, default='',
        verbose_name='AD domain / UPN suffix',
        help_text='e.g. company.local — used to build username@domain for binding.',
    )
    ldap_bind_dn = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='Service bind DN / user',
        help_text='Optional service account for user lookup, e.g. svc_ldap@company.local',
    )
    ldap_bind_password = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='Service bind password',
    )
    ldap_user_search_base = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='User search base',
        help_text='e.g. OU=Users,DC=company,DC=local',
    )
    ldap_user_filter = models.CharField(
        max_length=255, blank=True, default='(sAMAccountName={username})',
        verbose_name='User search filter',
        help_text='{username} is replaced with the login name.',
    )
    ldap_auto_create_users = models.BooleanField(
        default=True, verbose_name='Auto-create users on first LDAP login',
    )

    # ── WhatsApp ────────────────────────────────────────────────────────────
    wa_enabled = models.BooleanField(default=False, verbose_name='Enable WhatsApp alerts')
    wa_provider = models.CharField(
        max_length=12, choices=WhatsAppProvider.choices,
        default=WhatsAppProvider.WASENDER,
        verbose_name='WhatsApp provider',
    )
    wa_api_url = models.CharField(
        max_length=200, blank=True, default='https://wasenderapi.com/api',
        verbose_name='API base URL',
    )
    wa_api_key = models.CharField(
        max_length=300, blank=True, default='',
        verbose_name='API key / token',
        help_text='WaSenderAPI session API key (sent as Bearer token).',
    )
    wa_default_recipients = models.TextField(
        blank=True, default='',
        verbose_name='Default recipients',
        help_text='Comma-separated phone numbers in international format, e.g. +9627xxxxxxxx',
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        verbose_name='Last changed by',
    )

    class Meta:
        verbose_name        = 'Integration Settings'
        verbose_name_plural = 'Integration Settings'

    def __str__(self):
        parts = []
        if self.graph_enabled:
            parts.append('Graph')
        if self.ldap_enabled:
            parts.append('LDAP')
        if self.wa_enabled:
            parts.append('WhatsApp')
        return 'Integrations — ' + (', '.join(parts) if parts else 'none enabled')

    # ── Helpers ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> 'IntegrationSettings':
        """Always returns the singleton row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @staticmethod
    def _split(raw: str) -> list[str]:
        if not raw:
            return []
        return [p.strip() for p in raw.replace(';', ',').replace('\n', ',').split(',') if p.strip()]

    def report_recipient_list(self) -> list[str]:
        """Default e-mail recipients for alerts & reports."""
        return self._split(self.graph_report_recipients)

    # ── Graph token helpers ─────────────────────────────────────────────────

    TOKEN_SAFETY_MARGIN = 300  # seconds — refresh 5 min before real expiry

    def graph_token_is_valid(self) -> bool:
        """True when a cached token exists and won't expire in the next 5 min."""
        from django.utils import timezone
        from datetime import timedelta
        if not self.graph_access_token or not self.graph_token_expires_at:
            return False
        return timezone.now() < (
            self.graph_token_expires_at - timedelta(seconds=self.TOKEN_SAFETY_MARGIN)
        )

    def graph_token_status(self) -> str:
        """'valid' | 'expired' | 'none' — used by the settings UI."""
        if not self.graph_access_token:
            return 'none'
        return 'valid' if self.graph_token_is_valid() else 'expired'

    def store_graph_token(self, token: str, expires_in: int,
                          refresh_token: str | None = None,
                          account: str | None = None):
        """Persist a freshly acquired token together with its expiry.

        `refresh_token` / `account` are set by the interactive sign-in popup
        (and on refresh-token rotation); pass None to leave them untouched.
        """
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        self.graph_access_token      = token
        self.graph_token_acquired_at = now
        self.graph_token_expires_at  = now + timedelta(seconds=int(expires_in or 3599))
        fields = ['graph_access_token', 'graph_token_acquired_at', 'graph_token_expires_at']
        if refresh_token is not None:
            self.graph_refresh_token = refresh_token
            fields.append('graph_refresh_token')
        if account is not None:
            self.graph_signed_in_user = account
            fields.append('graph_signed_in_user')
        self.save(update_fields=fields)

    def clear_graph_token(self):
        """Drop the cached token & sign-in (e.g. when credentials change)."""
        self.graph_access_token      = ''
        self.graph_token_acquired_at = None
        self.graph_token_expires_at  = None
        self.graph_refresh_token     = ''
        self.graph_signed_in_user    = ''
        self.save(update_fields=[
            'graph_access_token', 'graph_token_acquired_at', 'graph_token_expires_at',
            'graph_refresh_token', 'graph_signed_in_user',
        ])

    def wa_recipient_list(self) -> list[str]:
        """Default WhatsApp recipients (international format)."""
        return self._split(self.wa_default_recipients)

    @classmethod
    def user_can_manage(cls, user) -> bool:
        """Superusers or members of the 'System Admins' group."""
        if not user or not user.is_authenticated or not user.is_active:
            return False
        return user.is_superuser or user.groups.filter(name=cls.SYSADMIN_GROUP).exists()


# ── System Roles & Departments (RBAC) ─────────────────────────────────────────

class SystemRole(models.TextChoices):
    """
    The two roles TenderRadar recognises. Stored on UserProfile.role and
    mirrored onto the Django auth flags so the existing is_superuser / is_staff
    checks scattered through the app keep behaving exactly as before:

        SUPER_ADMIN  →  is_superuser=True,  is_staff=True
        USER         →  is_superuser=False, is_staff=False

    The separate System Administrator (IT) role was removed on 2026-08-24 —
    the Super Admin now does everything. Migration 0050 converts any stored
    'IT' role to SUPER_ADMIN.

    Never test `role` directly in a view — use tenders.permissions helpers.
    """
    SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'
    USER        = 'USER',        'Regular User'


class Company(models.Model):
    """
    A company in the group. The organisational hierarchy is:

        Company  →  Department  →  User (UserProfile.department)

    Users are never assigned to a company directly — their company is derived
    from their department, so the two can never contradict each other.
    """
    name        = models.CharField(max_length=120, unique=True, verbose_name='Company Name')
    name_ar     = models.CharField(max_length=120, blank=True, default='', verbose_name='الاسم بالعربي')
    code        = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Optional short code, e.g. AJI, AJC.',
    )
    description = models.TextField(blank=True, default='')
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['name']
        verbose_name        = 'Company'
        verbose_name_plural = 'Companies — الشركات'

    def __str__(self):
        return f'{self.name} ({self.code})' if self.code else self.name

    @property
    def department_count(self) -> int:
        return self.departments.count()

    @property
    def employee_count(self) -> int:
        return UserProfile.objects.filter(department__company=self).count()

    @property
    def is_empty(self) -> bool:
        return self.department_count == 0


class Department(models.Model):
    """
    An organisational department inside a Company. Each user belongs to at most
    one department (UserProfile.department); one of those users may also be its
    head. Department names are unique per company, not globally — two companies
    may each have an "Engineering".
    """
    company     = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.PROTECT,
        related_name='departments', verbose_name='Company',
        help_text='The company this department belongs to.',
    )
    name        = models.CharField(max_length=120, verbose_name='Department Name')
    name_ar     = models.CharField(max_length=120, blank=True, default='', verbose_name='الاسم بالعربي')
    code        = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Optional short code, e.g. ENG, FIN, IT.',
    )
    description = models.TextField(blank=True, default='')
    head        = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='headed_departments', verbose_name='Department Head',
        help_text='The user who leads this department. Can be changed at any time.',
    )
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ['company__name', 'name']
        unique_together     = [['company', 'name']]
        verbose_name        = 'Department'
        verbose_name_plural = 'Departments — الأقسام'

    def __str__(self):
        return f'{self.name} ({self.code})' if self.code else self.name

    @property
    def employee_count(self) -> int:
        return self.member_profiles.count()

    @property
    def is_empty(self) -> bool:
        return self.employee_count == 0

    def members(self):
        """Users assigned to this department, head first."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        return (User.objects
                .filter(profile__department=self)
                .select_related('profile')
                .order_by('username'))


# ── User Profile ──────────────────────────────────────────────────────────────

class IndustryChoice(models.TextChoices):
    AEC  = 'AEC', 'AEC — Architecture, Engineering & Construction'
    GEN  = 'GEN', 'ALL — All Industries (no filter)'


# Map each industry to tender category codes (TenderCategory values)
INDUSTRY_CATEGORY_MAP = {
    'AEC':  ['BUILDING', 'INFRA', 'HEALTH', 'OTHER'],
    'GEN':  [],   # empty = no filter, see all
}

# Map industry to is_aec flag + broader keyword filter
INDUSTRY_AEC_ONLY = {
    'AEC': True,   # use is_aec=True filter
}

# Map industry to tender category for the scraper general category field
INDUSTRY_SCRAPER_CATEGORY_MAP = {
    'AEC': ['WORKS', 'CONSULTANCY', 'SERVICES', 'OTHER'],
    'GEN': [],   # empty = no filter, see all
}


class UserProfile(models.Model):
    """
    One-to-one extension of Django's User model.
    Stores the user's industry interest and dashboard preferences.
    """
    user       = models.OneToOneField(
        'auth.User', on_delete=models.CASCADE,
        related_name='profile',
    )
    industry   = models.CharField(
        max_length=10,
        choices=IndustryChoice.choices,
        default=IndustryChoice.GEN,
        verbose_name='Industry Interest',
        help_text='Controls which tenders appear on your dashboard.',
    )
    # Countries the user wants to focus on (empty = all countries)
    countries  = models.ManyToManyField(
        CountryConfig,
        blank=True,
        related_name='user_profiles',
        verbose_name='Focus Countries',
        help_text='Leave blank to see tenders from all countries.',
    )
    # JSON-encoded ordered list of country codes e.g. ["JO","AE","SA"]
    country_order = models.TextField(
        blank=True, default='',
        verbose_name='Country Display Order',
        help_text='JSON list of country codes in preferred order.',
    )
    # JSON-encoded ordered list of source IDs e.g. [3,1,7]
    source_order  = models.TextField(
        blank=True, default='',
        verbose_name='Source Display Order',
        help_text='JSON list of source IDs in preferred order.',
    )
    # JSON-encoded ordered list of favourited country codes (click order)
    favorite_countries = models.TextField(
        blank=True, default='',
        verbose_name='Favorite Countries',
        help_text='JSON list of favourited country codes, in the order they were starred.',
    )
    # Chosen avatar filename from the preset set in static/img/avatars/
    avatar = models.CharField(
        max_length=40, blank=True, default='',
        verbose_name='Avatar',
        help_text='Filename of the chosen preset avatar (in static/img/avatars/).',
    )
    phone = models.CharField(
        max_length=40, blank=True, default='',
        verbose_name='Phone Number',
    )
    can_see_all_tenders = models.BooleanField(
        default=False,
        verbose_name='Can See All Tenders',
        help_text=(
            'When enabled (admin-only setting), user can switch between AEC and All Tenders. '
            'When disabled (default), user is locked to AEC tenders only.'
        ),
    )
    notify_feedback_email = models.BooleanField(
        default=False,
        verbose_name='Feedback E-mail Alerts',
        help_text='Receive an e-mail notification whenever any user submits feedback.',
    )
    # ── RBAC ──────────────────────────────────────────────────────────────────
    role = models.CharField(
        max_length=20,
        choices=SystemRole.choices,
        default=SystemRole.USER,
        verbose_name='System Role',
        help_text='Super Admin = unrestricted. System Administrator = System Dashboard. '
                  'Regular User = tender modules only.',
    )
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='member_profiles', verbose_name='Department',
        help_text='An employee belongs to exactly one department.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'User Profile'
        verbose_name_plural = 'User Profiles — ملفات المستخدمين'

    def __str__(self):
        return f'{self.user.username} — {self.get_industry_display()}'

    # ── Role helpers (read-only; see tenders.permissions for the real gate) ───
    @property
    def is_super_admin(self) -> bool:
        return self.role == SystemRole.SUPER_ADMIN

    @property
    def can_administer(self) -> bool:
        """True for the only role allowed into the System Dashboard."""
        return self.role == SystemRole.SUPER_ADMIN

    def sync_auth_flags(self, save: bool = True) -> None:
        """
        Mirror `role` onto the Django auth flags so every pre-existing
        is_superuser / is_staff check in the project stays correct.
        """
        user = self.user
        is_super = self.role == SystemRole.SUPER_ADMIN
        is_staff = is_super
        if user.is_superuser != is_super or user.is_staff != is_staff:
            user.is_superuser = is_super
            user.is_staff     = is_staff
            if save:
                user.save(update_fields=['is_superuser', 'is_staff'])

    @property
    def company(self):
        """
        The company this user belongs to — always derived from their
        department, never stored separately, so the two cannot contradict.
        """
        if self.department_id and self.department.company_id:
            return self.department.company
        return None

    @property
    def is_general(self):
        return self.industry == IndustryChoice.GEN

    @property
    def is_aec_industry(self):
        return self.industry == IndustryChoice.AEC

    def get_scraper_categories(self) -> list:
        """Return the list of scraper-level category codes for this industry."""
        return INDUSTRY_SCRAPER_CATEGORY_MAP.get(self.industry, [])

    def get_focus_country_codes(self) -> list:
        """Return country codes in user-defined order."""
        import json
        codes = list(self.countries.values_list('code', flat=True))
        if self.country_order:
            try:
                ordered = json.loads(self.country_order)
                # Put ordered codes first, then any remaining
                result  = [c for c in ordered if c in codes]
                result += [c for c in codes if c not in result]
                return result
            except (ValueError, TypeError):
                pass
        return codes

    def get_ordered_source_ids(self) -> list:
        """Return source IDs in user-defined order."""
        import json
        if self.source_order:
            try:
                return json.loads(self.source_order)
            except (ValueError, TypeError):
                pass
        return []

    def get_favorite_codes(self) -> list:
        """Return favourited country codes in click order."""
        import json
        if self.favorite_countries:
            try:
                v = json.loads(self.favorite_countries)
                return [c for c in v if isinstance(c, str)]
            except (ValueError, TypeError):
                pass
        return []


# ── AEC Feedback ──────────────────────────────────────────────────────────────

class AECFeedback(models.Model):
    """User thumbs-up / thumbs-down on AEC relevance classification accuracy."""

    VOTE_UP   = 'UP'
    VOTE_DOWN = 'DOWN'
    VOTE_CHOICES = [(VOTE_UP, 'Accurate'), (VOTE_DOWN, 'Inaccurate')]

    tender    = models.ForeignKey(
        Tender, on_delete=models.CASCADE, related_name='aec_feedbacks'
    )
    user      = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE, related_name='aec_feedbacks'
    )
    vote      = models.CharField(max_length=4, choices=VOTE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tender', 'user')  # one vote per user per tender
        verbose_name = 'AEC Feedback'
        verbose_name_plural = 'AEC Feedbacks'

    def __str__(self):
        return f'{self.user} — {self.tender_id} — {self.vote}'


# ══════════════════════════════════════════════════════════════════════════════
# TEAM COLLABORATION
# ══════════════════════════════════════════════════════════════════════════════

class Team(models.Model):
    """A team created by a Team Leader to share tender notes."""
    name        = models.CharField(max_length=120, unique=True, verbose_name='Team Name')
    description = models.TextField(blank=True)
    leader      = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='led_teams',
        verbose_name='Team Leader',
    )
    members     = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='TeamMembership',
        related_name='teams',
        blank=True,
    )
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.leader.username})'

    def all_member_ids(self):
        """All user IDs including leader."""
        ids = set(self.members.values_list('id', flat=True))
        ids.add(self.leader_id)
        return ids


class TeamMembership(models.Model):
    team    = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    user    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='team_memberships')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} → {self.team.name}'


class TenderNote(models.Model):
    """A note left by any user on a tender — shared with a specific team."""
    tender    = models.ForeignKey('Tender', on_delete=models.CASCADE, related_name='team_notes')
    author    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='tender_notes')
    team      = models.ForeignKey('Team', on_delete=models.CASCADE, related_name='notes',
                                  null=True, blank=True)
    body      = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Note by {self.author.username} on #{self.tender_id}'

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            self._notify_team()

    def _notify_team(self):
        """Create notifications for all members of the selected team."""
        # If a specific team was chosen, notify only that team
        if self.team_id:
            teams = [self.team]
        else:
            # Fallback: notify all teams the author belongs to
            teams = list(Team.objects.filter(
                models.Q(members=self.author) | models.Q(leader=self.author)
            ).prefetch_related('members'))
        if not teams:
            return
        member_ids = set()
        for team in teams:
            member_ids.update(team.all_member_ids())
        member_ids.discard(self.author_id)
        NoteNotification.objects.bulk_create(
            [NoteNotification(note=self, recipient_id=uid) for uid in member_ids],
            ignore_conflicts=True
        )


class NoteNotification(models.Model):
    """Notification for a team member when a note is posted."""
    note       = models.ForeignKey(TenderNote, on_delete=models.CASCADE, related_name='notifications')
    recipient  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='note_notifications')
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('note', 'recipient')
        ordering = ['-created_at']

    def __str__(self):
        return f'Notif → {self.recipient.username} for note #{self.note_id}'


# ══════════════════════════════════════════════════════════════════════════════
# USER FEEDBACK
# ══════════════════════════════════════════════════════════════════════════════

class UserFeedback(models.Model):

    class FeedbackType(models.TextChoices):
        BUG      = 'BUG',     'Bug / Issue'
        FEATURE  = 'FEATURE', 'Feature Request'
        CHANGE   = 'CHANGE',  'Modification'
        OTHER    = 'OTHER',   'Other'

    class Priority(models.TextChoices):
        LOW    = 'LOW',    'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH   = 'HIGH',   'High'

    class Status(models.TextChoices):
        NEW        = 'NEW',        'New'
        IN_REVIEW  = 'IN_REVIEW',  'In Review'
        PLANNED    = 'PLANNED',    'Planned'
        DONE       = 'DONE',       'Done'
        DECLINED   = 'DECLINED',   'Declined'

    author       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedbacks',
    )
    feedback_type = models.CharField(
        max_length=10, choices=FeedbackType.choices,
        default=FeedbackType.OTHER,
        verbose_name='Type',
    )
    title        = models.CharField(max_length=200, verbose_name='Subject')
    body         = models.TextField(verbose_name='Details')
    status       = models.CharField(
        max_length=12, choices=Status.choices,
        default=Status.NEW,
    )
    priority     = models.CharField(
        max_length=8, choices=Priority.choices,
        default=Priority.MEDIUM,
        verbose_name='Priority',
    )
    attachment   = models.FileField(
        upload_to='feedback_attachments/',
        blank=True, null=True,
        verbose_name='Attachment',
        help_text='Optional image or document (max 10 MB).',
    )
    admin_note   = models.TextField(
        blank=True,
        verbose_name='Admin Response',
        help_text='Response visible to the user.',
    )
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    reviewed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_feedbacks',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.feedback_type}] {self.title} — {self.author.username}'


class FeedbackNotification(models.Model):
    """Notifies a user when admin responds to their feedback."""
    feedback   = models.OneToOneField(
        'UserFeedback',
        on_delete=models.CASCADE,
        related_name='notification',
    )
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'FeedbackNotif → {self.feedback.author.username} #{self.feedback_id}'


# ── Analytics / Activity Tracking ────────────────────────────────────────────

class UserLoginLog(models.Model):
    """Tracks every login event for a user."""
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='login_logs')
    logged_in_at = models.DateTimeField(auto_now_add=True)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    user_agent  = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-logged_in_at']
        verbose_name = 'User Login Log'
        verbose_name_plural = 'User Login Logs'

    def __str__(self):
        return f'{self.user.username} @ {self.logged_in_at:%Y-%m-%d %H:%M}'


class TenderPortalClick(models.Model):
    """Tracks when a user clicks 'Open in Source Portal' on a tender."""
    tender      = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name='portal_clicks')
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='portal_clicks')
    clicked_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-clicked_at']
        verbose_name = 'Portal Click'
        verbose_name_plural = 'Portal Clicks'

    def __str__(self):
        return f'{self.user.username} → tender #{self.tender_id}'


# ── Backup & Restore ──────────────────────────────────────────────────────────

class SystemBackup(models.Model):
    """
    One archive produced by tenders.backup.create_backup().
    The archive itself lives on disk under settings.BACKUP_ROOT; this row is
    the catalogue entry (history, size, who made it, what it contains).
    """

    class Status(models.TextChoices):
        RUNNING = 'RUNNING', 'Running'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED  = 'FAILED',  'Failed'

    class Trigger(models.TextChoices):
        MANUAL      = 'MANUAL',      'Manual'
        SCHEDULED   = 'SCHEDULED',   'Scheduled'
        PRE_RESTORE = 'PRE_RESTORE', 'Safety copy (pre-restore)'
        UPLOADED    = 'UPLOADED',    'Uploaded archive'

    filename          = models.CharField(max_length=200)
    file_path         = models.CharField(max_length=500, blank=True, default='')
    size_bytes        = models.BigIntegerField(default=0)
    status            = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    trigger           = models.CharField(max_length=15, choices=Trigger.choices, default=Trigger.MANUAL)
    created_by        = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='system_backups',
    )
    created_at        = models.DateTimeField(auto_now_add=True)
    finished_at       = models.DateTimeField(null=True, blank=True)
    includes_database = models.BooleanField(default=True)
    includes_media    = models.BooleanField(default=True)
    includes_config   = models.BooleanField(default=True)
    includes_code     = models.BooleanField(default=False)
    includes_docs     = models.BooleanField(default=False)
    note              = models.CharField(max_length=255, blank=True, default='')
    error_message     = models.TextField(blank=True, default='')

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'System Backup'
        verbose_name_plural = 'System Backups — النسخ الاحتياطية'

    def __str__(self):
        return f'{self.filename} ({self.get_status_display()})'

    @property
    def exists_on_disk(self) -> bool:
        import os
        return bool(self.file_path) and os.path.isfile(self.file_path)

    @property
    def size_display(self) -> str:
        size = float(self.size_bytes or 0)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024 or unit == 'GB':
                return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
            size /= 1024
        return f'{size:.1f} GB'

    @property
    def contents_display(self) -> str:
        parts = []
        if self.includes_database: parts.append('Database')
        if self.includes_media:    parts.append('Files')
        if self.includes_config:   parts.append('Config')
        if self.includes_code:     parts.append('Code')
        if self.includes_docs:     parts.append('Docs')
        return ' · '.join(parts) or '—'

    #: Product name stamped into every archive's version label / version.txt.
    PROJECT_NAME = 'TenderRadar'

    @property
    def version_label(self) -> str:
        """
        Human-readable identity of this backup: project name + date + time.

        Shown in Backup History instead of forcing the reader to decode the
        raw filename. Uses the created timestamp (local time) so it always
        matches what the row displays, even for archives re-indexed from disk.
        """
        from django.utils import timezone
        when = timezone.localtime(self.created_at) if self.created_at else None
        stamp = when.strftime('%d %b %Y, %H:%M') if when else '—'
        return f'{self.PROJECT_NAME} · {stamp}'

    @property
    def duration_seconds(self):
        if not self.finished_at:
            return None
        return (self.finished_at - self.created_at).total_seconds()


class BackupRestore(models.Model):
    """Audit row for every restore attempt."""

    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILED  = 'FAILED',  'Failed'

    backup        = models.ForeignKey(SystemBackup, on_delete=models.CASCADE, related_name='restores')
    safety_backup = models.ForeignKey(
        SystemBackup, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='restore_safety_for',
        help_text='Automatic backup taken immediately before this restore.',
    )
    restored_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='backup_restores',
    )
    restored_at        = models.DateTimeField(auto_now_add=True)
    status             = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    restored_database  = models.BooleanField(default=False)
    restored_media     = models.BooleanField(default=False)
    restored_config    = models.BooleanField(default=False)
    restored_code      = models.BooleanField(default=False)
    restored_docs      = models.BooleanField(default=False)
    error_message      = models.TextField(blank=True, default='')

    class Meta:
        ordering            = ['-restored_at']
        verbose_name        = 'Backup Restore'
        verbose_name_plural = 'Backup Restores'

    def __str__(self):
        return f'restore of {self.backup.filename} @ {self.restored_at:%Y-%m-%d %H:%M}'


class BackupSchedule(models.Model):
    """Singleton (pk=1) controlling the automatic backup task."""

    class Frequency(models.TextChoices):
        DAILY   = 'DAILY',   'Daily'
        WEEKLY  = 'WEEKLY',  'Weekly'
        MONTHLY = 'MONTHLY', 'Monthly'

    enabled           = models.BooleanField(default=False, verbose_name='Automatic Backups')
    frequency         = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.DAILY)
    hour              = models.PositiveSmallIntegerField(
        default=2, verbose_name='Run At (hour, 0–23)',
        help_text='Server local time (Asia/Amman).',
    )
    weekday           = models.PositiveSmallIntegerField(
        default=0, verbose_name='Weekday',
        help_text='0 = Monday … 6 = Sunday. Used when frequency is Weekly.',
    )
    day_of_month      = models.PositiveSmallIntegerField(
        default=1, verbose_name='Day of Month',
        help_text='1–28. Used when frequency is Monthly.',
    )
    includes_database = models.BooleanField(default=True, verbose_name='Include Database')
    includes_media    = models.BooleanField(default=True, verbose_name='Include Uploaded Files')
    includes_config   = models.BooleanField(default=True, verbose_name='Include Configuration')
    includes_code     = models.BooleanField(default=False, verbose_name='Include Application Code')
    includes_docs     = models.BooleanField(default=False, verbose_name='Include Documentation')
    retention_count   = models.PositiveSmallIntegerField(
        default=10, verbose_name='Keep Last N Backups',
        help_text='Applies to manual and scheduled backups alike — older '
                  'archives are deleted automatically. 0 = keep everything.',
    )
    last_run_at       = models.DateTimeField(null=True, blank=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Backup Schedule'
        verbose_name_plural = 'Backup Schedule'

    WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday',
                     'Friday', 'Saturday', 'Sunday']

    def __str__(self):
        return f'{self.get_frequency_display()} @ {self.hour:02d}:00 ({"on" if self.enabled else "off"})'

    def describe(self) -> str:
        """
        One plain sentence saying exactly what the schedule will do, for people
        who should not have to work it out from four separate fields.
        """
        if not self.enabled:
            return 'Automatic backups are off — nothing will run.'

        if self.frequency == self.Frequency.WEEKLY:
            when = f'every {self.WEEKDAY_NAMES[self.weekday % 7]}'
        elif self.frequency == self.Frequency.MONTHLY:
            when = f'on day {self.day_of_month} of every month'
        else:
            when = 'every day'

        parts = [name for name, on in (('the database',   self.includes_database),
                                       ('uploaded files', self.includes_media),
                                       ('configuration',  self.includes_config),
                                       ('the code',       self.includes_code),
                                       ('documentation',  self.includes_docs)) if on]
        if not parts:
            return (f'Set to run {when} at {self.hour:02d}:00 — but nothing is '
                    f'selected to include, so the archive would be empty.')
        what = parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ' and ' + parts[-1]
        keep = ('keeping every archive' if self.retention_count == 0
                else f'keeping the last {self.retention_count}')
        return f'Backs up {what} {when} at {self.hour:02d}:00, {keep}.'

    @classmethod
    def get(cls) -> 'BackupSchedule':
        """Always returns the singleton row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def next_run_at(self):
        """Next scheduled fire time, or None when automatic backups are off."""
        if not self.enabled:
            return None
        import calendar
        from datetime import timedelta
        now  = timezone.localtime()
        base = now.replace(hour=min(self.hour, 23), minute=0, second=0, microsecond=0)

        if self.frequency == self.Frequency.DAILY:
            return base if base > now else base + timedelta(days=1)

        if self.frequency == self.Frequency.WEEKLY:
            delta = (self.weekday - base.weekday()) % 7
            nxt   = base + timedelta(days=delta)
            return nxt if nxt > now else nxt + timedelta(days=7)

        # Monthly
        day = max(1, min(self.day_of_month, 28))
        nxt = base.replace(day=day)
        if nxt <= now:
            year, month = (nxt.year + 1, 1) if nxt.month == 12 else (nxt.year, nxt.month + 1)
            day = min(day, calendar.monthrange(year, month)[1])
            nxt = nxt.replace(year=year, month=month, day=day)
        return nxt


# ── Administrative audit trail ────────────────────────────────────────────────

class AdminAuditLog(models.Model):
    """
    Append-only record of every administrative action performed through the
    System Dashboard. Surfaced under System Dashboard → System Logs.
    """

    class Action(models.TextChoices):
        USER_CREATE      = 'USER_CREATE',      'User created'
        USER_UPDATE      = 'USER_UPDATE',      'User updated'
        USER_DELETE      = 'USER_DELETE',      'User deleted'
        USER_ACTIVATE    = 'USER_ACTIVATE',    'User activated'
        USER_DEACTIVATE  = 'USER_DEACTIVATE',  'User deactivated'
        PASSWORD_RESET   = 'PASSWORD_RESET',   'Password reset'
        ROLE_CHANGE      = 'ROLE_CHANGE',      'Role changed'
        DEPT_CREATE      = 'DEPT_CREATE',      'Department created'
        DEPT_UPDATE      = 'DEPT_UPDATE',      'Department updated'
        DEPT_DELETE      = 'DEPT_DELETE',      'Department deleted'
        DEPT_HEAD_CHANGE = 'DEPT_HEAD_CHANGE', 'Department head changed'
        USER_MOVE        = 'USER_MOVE',        'User moved between departments'
        COMPANY_CREATE   = 'COMPANY_CREATE',   'Company created'
        COMPANY_UPDATE   = 'COMPANY_UPDATE',   'Company updated'
        COMPANY_DELETE   = 'COMPANY_DELETE',   'Company deleted'
        BACKUP_CREATE    = 'BACKUP_CREATE',    'Backup created'
        BACKUP_DELETE    = 'BACKUP_DELETE',    'Backup deleted'
        BACKUP_DOWNLOAD  = 'BACKUP_DOWNLOAD',  'Backup downloaded'
        BACKUP_UPLOAD    = 'BACKUP_UPLOAD',    'Backup uploaded'
        BACKUP_RESTORE   = 'BACKUP_RESTORE',   'Backup restored'
        SCHEDULE_UPDATE  = 'SCHEDULE_UPDATE',  'Backup schedule updated'
        # The developer documentation describes source code, architecture and
        # credential handling, and is marked confidential — who takes a copy is
        # worth recording.
        DOC_DOWNLOAD     = 'DOC_DOWNLOAD',     'Documentation downloaded'
        ACCESS_DENIED    = 'ACCESS_DENIED',    'Access denied'

    actor      = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='admin_actions',
    )
    action     = models.CharField(max_length=20, choices=Action.choices)
    target     = models.CharField(max_length=200, blank=True, default='')
    detail     = models.TextField(blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Admin Audit Log'
        verbose_name_plural = 'Admin Audit Logs — سجل العمليات'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        who = self.actor.username if self.actor else 'system'
        return f'{who} · {self.get_action_display()} · {self.target}'

    @classmethod
    def record(cls, request, action, target='', detail=''):
        """Convenience writer — never raises, auditing must not break a view."""
        try:
            ip = None
            if request is not None:
                ip = request.META.get('HTTP_X_FORWARDED_FOR')
                ip = ip.split(',')[0].strip() if ip else request.META.get('REMOTE_ADDR')
            actor = getattr(request, 'user', None)
            if actor is not None and not actor.is_authenticated:
                actor = None
            return cls.objects.create(
                actor=actor, action=action,
                target=str(target)[:200], detail=str(detail),
                ip_address=ip,
            )
        except Exception:                                    # pragma: no cover
            import logging
            logging.getLogger('tenders').exception('AdminAuditLog.record failed')
            return None


# ── Notification log ──────────────────────────────────────────────────────────

class NotificationLog(models.Model):
    """
    One row per outgoing notification — every e-mail and WhatsApp message that
    leaves the system, whether it succeeded or not.

    Written at the two chokepoints in tenders/integrations.py
    (MSGraphMailer.send_mail and WhatsAppClient.send_message), so every caller
    — digest, feedback alerts, tests, future alerts — is covered automatically.
    Logging is best-effort: a failure to write a log row must never stop a
    message from being sent (see integrations.log_notification).

    The message body is stored too (capped at BODY_MAX_CHARS) so an
    administrator can open any row and see exactly what was sent. Surfaced
    under System Dashboard → System Logs → Notifications.
    """

    #: Bodies larger than this are truncated on save — a digest e-mail is big,
    #: and the log must never grow the database faster than the data it logs.
    BODY_MAX_CHARS = 500_000

    class Channel(models.TextChoices):
        EMAIL    = 'EMAIL',    'E-mail'
        WHATSAPP = 'WHATSAPP', 'WhatsApp'

    class Category(models.TextChoices):
        DIGEST   = 'DIGEST',   'Daily digest'
        FEEDBACK = 'FEEDBACK', 'Feedback alert'
        ALERT    = 'ALERT',    'System alert'
        TEST     = 'TEST',     'Test message'
        GENERAL  = 'GENERAL',  'General'

    class Status(models.TextChoices):
        SENT   = 'SENT',   'Sent'
        FAILED = 'FAILED', 'Failed'

    channel       = models.CharField(max_length=10, choices=Channel.choices)
    category      = models.CharField(max_length=10, choices=Category.choices,
                                     default=Category.GENERAL)
    recipient     = models.CharField(max_length=500, blank=True, default='',
                                     help_text='Address(es) / phone number(s), comma-separated')
    subject       = models.CharField(max_length=300, blank=True, default='')
    body          = models.TextField(
        blank=True, default='',
        help_text='The message as sent — HTML for e-mail, plain text for WhatsApp',
    )
    is_html       = models.BooleanField(default=False)
    status        = models.CharField(max_length=10, choices=Status.choices)
    error_message = models.TextField(blank=True, default='')
    triggered_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='notification_logs',
        help_text='Who caused this message (empty for scheduled / automatic sends)',
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ['-created_at']
        verbose_name        = 'Notification Log'
        verbose_name_plural = 'Notification Logs — سجل الإشعارات'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['channel']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return (f'{self.get_channel_display()} · {self.get_category_display()} '
                f'→ {self.recipient[:60]} · {self.status}')
