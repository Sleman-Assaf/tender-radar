"""Data models for the Dynamic Source Management System (SAD §1, §13).

All tables are new and live only in this app. The single bridge to legacy is
``DynSource.shadow_source`` -> ``tenders.ScraperSource`` (a hidden, inactive row
that lets dynamic tenders flow through the untouched save pipeline and appear in
every existing view). No legacy table's schema is altered.

Choice values are defined as TextChoices so the admin, forms, and state machines
share one source of truth.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone


# Reserved marker written into the shadow ScraperSource.scraper_class so the
# legacy registry/scheduler can positively identify (and ignore) dynamic rows.
DYNAMIC_SCRAPER_CLASS = "__DYNAMIC__"


# ── Enumerations ──────────────────────────────────────────────────────────────

class EngineType(models.TextChoices):
    HTML_LIST = "HTML_LIST", "HTML list (CSS/XPath)"
    JSON_API = "JSON_API", "JSON API endpoint"
    RSS = "RSS", "RSS / Atom feed"
    PLAYWRIGHT_BROWSER = "PLAYWRIGHT_BROWSER", "Browser (Playwright)"
    CUSTOM = "CUSTOM", "Custom handler"


class SourceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    TESTING = "TESTING", "Testing"
    PENDING_APPROVAL = "PENDING_APPROVAL", "Pending approval"
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    DISABLED = "DISABLED", "Disabled"
    ARCHIVED = "ARCHIVED", "Archived"


class HealthStatus(models.TextChoices):
    UNKNOWN = "UNKNOWN", "Unknown"
    HEALTHY = "HEALTHY", "Healthy"
    WARNING = "WARNING", "Warning"
    NEEDS_ATTENTION = "NEEDS_ATTENTION", "Needs attention"
    CRITICAL = "CRITICAL", "Critical"


class LoginStatus(models.TextChoices):
    NOT_REQUIRED = "NOT_REQUIRED", "Not required"
    VALID = "VALID", "Valid"
    EXPIRING = "EXPIRING", "Expiring"
    EXPIRED = "EXPIRED", "Expired"
    FAILED = "FAILED", "Failed"


class AuthType(models.TextChoices):
    FORM_LOGIN = "FORM_LOGIN", "Form login"
    HTTP_BASIC = "HTTP_BASIC", "HTTP Basic"
    TOKEN_HEADER = "TOKEN_HEADER", "Bearer/token header"
    COOKIES = "COOKIES", "Cookies"
    API_KEY = "API_KEY", "API key"


class RunTrigger(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled"
    MANUAL = "MANUAL", "Manual"
    TEST = "TEST", "Test"
    REANALYZE = "REANALYZE", "Re-analyze"


class RunStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    SUCCESS = "SUCCESS", "Success"
    PARTIAL = "PARTIAL", "Partial"
    FAILED = "FAILED", "Failed"
    TIMEOUT = "TIMEOUT", "Timeout"
    CANCELLED = "CANCELLED", "Cancelled"


class ErrorCategory(models.TextChoices):
    NETWORK = "NETWORK", "Network"
    AUTH = "AUTH", "Authentication"
    PARSE = "PARSE", "Parsing"
    SELECTOR = "SELECTOR", "Selector"
    TIMEOUT = "TIMEOUT", "Timeout"
    BLOCKED = "BLOCKED", "Blocked / anti-bot"
    OFFLINE = "OFFLINE", "Website offline"
    ENGINE = "ENGINE", "Engine error"
    CONFIG = "CONFIG", "Configuration invalid"
    UNKNOWN = "UNKNOWN", "Unknown"


class VersionOrigin(models.TextChoices):
    AI_GENERATED = "AI_GENERATED", "AI generated"
    MANUAL = "MANUAL", "Manual"
    CLONED = "CLONED", "Cloned"
    REANALYZED = "REANALYZED", "Re-analyzed"
    ROLLBACK = "ROLLBACK", "Rollback"
    IMPORTED = "IMPORTED", "Imported"


class AuditAction(models.TextChoices):
    CREATE = "CREATE", "Create"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"
    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"
    ACTIVATE = "ACTIVATE", "Activate"
    PAUSE = "PAUSE", "Pause"
    RESUME = "RESUME", "Resume"
    DISABLE = "DISABLE", "Disable"
    ROLLBACK = "ROLLBACK", "Rollback"
    RUN_NOW = "RUN_NOW", "Run now"
    TEST = "TEST", "Test"
    CREDENTIAL_SET = "CREDENTIAL_SET", "Credential set"
    CREDENTIAL_CLEAR = "CREDENTIAL_CLEAR", "Credential clear"
    REANALYZE = "REANALYZE", "Re-analyze"
    OVERRIDE_VALIDATION = "OVERRIDE_VALIDATION", "Override validation"
    IMPORT = "IMPORT", "Import"
    EXPORT = "EXPORT", "Export"


class AlertRule(models.TextChoices):
    ZERO_RESULTS = "ZERO_RESULTS", "Zero results"
    VOLUME_DROP = "VOLUME_DROP", "Volume drop"
    LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
    PARSE_FAILED = "PARSE_FAILED", "Parse failed"
    SELECTOR_INVALID = "SELECTOR_INVALID", "Selector invalid"
    TIMEOUT = "TIMEOUT", "Timeout"
    OFFLINE = "OFFLINE", "Offline"
    HEALTH_DROP = "HEALTH_DROP", "Health drop"
    CONFIDENCE_DROP = "CONFIDENCE_DROP", "Confidence drop"
    SCHEDULER_STALLED = "SCHEDULER_STALLED", "Scheduler stalled"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER", "Circuit breaker"


class AlertSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"


class AlertStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
    RESOLVED = "RESOLVED", "Resolved"
    AUTO_RESOLVED = "AUTO_RESOLVED", "Auto-resolved"


class TemplateCategory(models.TextChoices):
    HTML_TABLE = "HTML_TABLE", "HTML table"
    HTML_CARDS = "HTML_CARDS", "HTML cards"
    JSON_API = "JSON_API", "JSON API"
    RSS = "RSS", "RSS"
    WORDPRESS = "WORDPRESS", "WordPress"
    ASPNET = "ASPNET", "ASP.NET"
    GOV_PORTAL = "GOV_PORTAL", "Government portal"


# ── Aggregate root ────────────────────────────────────────────────────────────

class DynSource(models.Model):
    """A tender website managed by the dynamic system (SAD §1.2)."""

    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True, verbose_name="Name (Arabic)")
    website_url = models.URLField(max_length=500)
    url_fingerprint = models.CharField(
        max_length=64, unique=True,
        help_text="SHA-256 of the normalized URL — prevents duplicate sources.",
    )

    shadow_source = models.OneToOneField(
        "tenders.ScraperSource", on_delete=models.PROTECT,
        related_name="dynamic_source",
        help_text="Hidden legacy row (is_active=False) bridging to the save pipeline.",
    )
    country = models.ForeignKey(
        "tenders.CountryConfig", on_delete=models.PROTECT,
        related_name="dyn_sources", to_field="code",
    )

    engine_type = models.CharField(max_length=20, choices=EngineType.choices)
    status = models.CharField(max_length=20, choices=SourceStatus.choices,
                              default=SourceStatus.DRAFT)
    health_status = models.CharField(max_length=20, choices=HealthStatus.choices,
                                     default=HealthStatus.UNKNOWN)
    health_score = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)])
    confidence_score = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)])

    active_version = models.ForeignKey(
        "DynSourceVersion", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )

    requires_login = models.BooleanField(default=False)
    login_status = models.CharField(max_length=16, choices=LoginStatus.choices,
                                    default=LoginStatus.NOT_REQUIRED)

    consecutive_failures = models.PositiveSmallIntegerField(default=0)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    avg_runtime_ms = models.PositiveIntegerField(default=0)
    avg_tender_count = models.FloatField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="dyn_sources_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="dyn_sources_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paused_reason = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dynamic Source"
        verbose_name_plural = "Dynamic Sources"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["status", "health_status"]),
            models.Index(fields=["engine_type"]),
            models.Index(fields=["health_status"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(health_score__lte=100),
                name="dyn_source_health_score_max",
            ),
            models.CheckConstraint(
                check=models.Q(confidence_score__lte=100),
                name="dyn_source_confidence_score_max",
            ),
        ]

    def __str__(self):
        return f"{self.name} [{self.engine_type}] ({self.status})"


# ── Immutable configuration versions ──────────────────────────────────────────

class DynSourceVersion(models.Model):
    """One immutable configuration version of a source (SAD §1.3)."""

    source = models.ForeignKey(DynSource, on_delete=models.CASCADE,
                               related_name="versions")
    version_number = models.PositiveIntegerField()
    config = models.JSONField()
    config_schema_version = models.CharField(max_length=8, default="1.0")
    origin = models.CharField(max_length=16, choices=VersionOrigin.choices,
                              default=VersionOrigin.MANUAL)
    comment = models.CharField(max_length=300, blank=True)
    validated_run = models.ForeignKey(
        "DynRun", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="validated_versions",
        help_text="The test run that validated this version.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="dyn_versions_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Source Config Version"
        ordering = ["source", "-version_number"]
        unique_together = [["source", "version_number"]]
        indexes = [models.Index(fields=["source", "-created_at"])]

    def __str__(self):
        return f"{self.source.name} v{self.version_number} ({self.origin})"


# ── Encrypted credentials ─────────────────────────────────────────────────────

class DynCredential(models.Model):
    """Encrypted per-source secrets and session state (SAD §1.4, §5)."""

    source = models.OneToOneField(DynSource, on_delete=models.CASCADE,
                                  related_name="credential")
    auth_type = models.CharField(max_length=16, choices=AuthType.choices)
    ciphertext = models.BinaryField()
    key_id = models.CharField(max_length=16, default="k1")

    session_blob = models.BinaryField(null=True, blank=True)
    session_expires_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)

    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Source Credential"

    def __str__(self):
        return f"Credential for {self.source.name} ({self.auth_type})"


# ── Per-source scheduling ─────────────────────────────────────────────────────

class DynSchedule(models.Model):
    """Per-source schedule and the scheduler's hot-path claim row (SAD §1.5)."""

    class Interval(models.IntegerChoices):
        MIN_15 = 15, "Every 15 minutes"
        MIN_30 = 30, "Every 30 minutes"
        HOUR_1 = 60, "Every hour"
        HOUR_6 = 360, "Every 6 hours"
        HOUR_12 = 720, "Every 12 hours"
        DAILY = 1440, "Daily"
        WEEKLY = 10080, "Weekly"

    source = models.OneToOneField(DynSource, on_delete=models.CASCADE,
                                  related_name="schedule")
    interval_minutes = models.PositiveIntegerField(
        choices=Interval.choices, default=Interval.HOUR_6)
    jitter_seconds = models.PositiveSmallIntegerField(default=120)
    next_run_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.CharField(max_length=64, blank=True)
    is_enabled = models.BooleanField(default=True)
    priority = models.SmallIntegerField(default=0)
    retry_backoff_level = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Source Schedule"
        indexes = [
            models.Index(fields=["next_run_at"]),
            models.Index(fields=["is_enabled", "claimed_at"]),
        ]

    def __str__(self):
        return f"{self.source.name} every {self.interval_minutes}m"


# ── Runs ──────────────────────────────────────────────────────────────────────

class DynRun(models.Model):
    """One execution — scheduled, manual, test, or re-analyze (SAD §1.6)."""

    source = models.ForeignKey(DynSource, on_delete=models.CASCADE,
                               related_name="runs")
    config_version = models.ForeignKey(DynSourceVersion, null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="runs")
    trigger = models.CharField(max_length=12, choices=RunTrigger.choices)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=RunStatus.choices,
                              default=RunStatus.QUEUED)

    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    runtime_ms = models.PositiveIntegerField(null=True, blank=True)

    tenders_found = models.PositiveIntegerField(default=0)
    tenders_new = models.PositiveIntegerField(default=0)
    tenders_updated = models.PositiveIntegerField(default=0)
    tenders_duplicate = models.PositiveIntegerField(default=0)

    error_category = models.CharField(max_length=16, choices=ErrorCategory.choices,
                                      blank=True)
    error_message = models.TextField(blank=True)
    http_status_last = models.PositiveSmallIntegerField(null=True, blank=True)
    response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    pages_fetched = models.PositiveSmallIntegerField(default=0)

    field_fill_rates = models.JSONField(default=dict, blank=True)
    sample_payload = models.JSONField(null=True, blank=True)
    screenshot = models.FileField(upload_to="dyn_screenshots/%Y/%m/",
                                  null=True, blank=True)

    # Resource metrics (SAD §13.3)
    bytes_downloaded = models.PositiveBigIntegerField(default=0)
    peak_mem_mb = models.PositiveIntegerField(null=True, blank=True)
    cpu_ms = models.PositiveIntegerField(null=True, blank=True)

    scrape_log = models.OneToOneField("tenders.ScrapeLog", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="+")
    worker_id = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "Source Run"
        ordering = ["-queued_at"]
        indexes = [
            models.Index(fields=["source", "-started_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["trigger"]),
            models.Index(fields=["error_category"]),
        ]

    def __str__(self):
        return f"{self.source.name} {self.trigger} {self.status} #{self.pk}"


# ── Daily health aggregates ───────────────────────────────────────────────────

class DynHealthSnapshot(models.Model):
    """One row per source per day for cheap 30-day trend charts (SAD §1.7)."""

    source = models.ForeignKey(DynSource, on_delete=models.CASCADE,
                               related_name="health_snapshots")
    snapshot_date = models.DateField()
    health_score = models.PositiveSmallIntegerField(default=0)
    confidence_score = models.PositiveSmallIntegerField(default=0)
    runs = models.PositiveSmallIntegerField(default=0)
    successes = models.PositiveSmallIntegerField(default=0)
    failures = models.PositiveSmallIntegerField(default=0)
    tenders_found = models.PositiveIntegerField(default=0)
    avg_runtime_ms = models.PositiveIntegerField(default=0)
    avg_response_ms = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Health Snapshot"
        unique_together = [["source", "snapshot_date"]]
        indexes = [models.Index(fields=["source", "snapshot_date"])]
        ordering = ["-snapshot_date"]

    def __str__(self):
        return f"{self.source.name} {self.snapshot_date} h={self.health_score}"


# ── AI analysis (wizard) ──────────────────────────────────────────────────────

class DynAIAnalysis(models.Model):
    """Artifacts of one wizard analysis session (SAD §1.8, §6)."""

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        COMPLETE = "COMPLETE", "Complete"
        FAILED = "FAILED", "Failed"

    source = models.ForeignKey(DynSource, null=True, blank=True,
                               on_delete=models.CASCADE, related_name="analyses")
    input_url = models.URLField(max_length=500)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.RUNNING)
    probe_report = models.JSONField(default=dict, blank=True)
    detected_features = models.JSONField(default=dict, blank=True)
    recommended_engine = models.CharField(max_length=20, blank=True)
    proposed_config = models.JSONField(null=True, blank=True)
    site_signature = models.CharField(max_length=64, blank=True)
    ai_service = models.CharField(max_length=20, blank=True)
    ai_model = models.CharField(max_length=60, blank=True)
    ai_confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="dyn_analyses")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "AI Analysis"
        verbose_name_plural = "AI Analyses"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Analysis {self.input_url} ({self.status})"


class DynAICorrection(models.Model):
    """A human correction of an AI proposal — the learning signal (SAD §13.4)."""

    analysis = models.ForeignKey(DynAIAnalysis, on_delete=models.CASCADE,
                                 related_name="corrections")
    field = models.CharField(max_length=100)
    ai_value = models.JSONField(null=True, blank=True)
    human_value = models.JSONField(null=True, blank=True)
    engine_type = models.CharField(max_length=20, blank=True)
    site_signature = models.CharField(max_length=64, blank=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "AI Correction"
        indexes = [models.Index(fields=["site_signature", "engine_type"])]

    def __str__(self):
        return f"Correction {self.field} ({self.engine_type})"


# ── Audit log ─────────────────────────────────────────────────────────────────

class DynAuditLog(models.Model):
    """Insert-only record of every consequential action (SAD §1.9)."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=24, choices=AuditAction.choices)
    source = models.ForeignKey(DynSource, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="audit_logs")
    target_model = models.CharField(max_length=40, blank=True)
    target_id = models.BigIntegerField(null=True, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Audit Log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["actor"]),
            models.Index(fields=["source", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.action} by {self.actor_id} @ {self.created_at:%Y-%m-%d %H:%M}"


# ── Alerts ────────────────────────────────────────────────────────────────────

class DynAlert(models.Model):
    """Smart-alert inbox entry (SAD §1.10, §7.3)."""

    source = models.ForeignKey(DynSource, on_delete=models.CASCADE,
                               related_name="alerts")
    rule = models.CharField(max_length=24, choices=AlertRule.choices)
    severity = models.CharField(max_length=10, choices=AlertSeverity.choices)
    message = models.CharField(max_length=300)
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=14, choices=AlertStatus.choices,
                              default=AlertStatus.OPEN)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notified_channels = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Alert"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source", "rule", "status"]),
            models.Index(fields=["severity"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"[{self.severity}] {self.rule} — {self.source.name}"


# ── Templates ─────────────────────────────────────────────────────────────────

class DynTemplate(models.Model):
    """Reusable config skeleton for the wizard (SAD §1.11, §13.2)."""

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=16, choices=TemplateCategory.choices)
    engine_type = models.CharField(max_length=20, choices=EngineType.choices)
    config = models.JSONField()
    is_builtin = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Source Template"
        ordering = ["category", "name"]

    def __str__(self):
        return f"{self.name} ({self.category})"


# ── Worker fleet (horizontal scaling — SAD §13.1) ─────────────────────────────

class DynWorker(models.Model):
    """One row per running worker process; heartbeat + capabilities."""

    class Status(models.TextChoices):
        STARTING = "STARTING", "Starting"
        RUNNING = "RUNNING", "Running"
        DRAINING = "DRAINING", "Draining"
        STOPPED = "STOPPED", "Stopped"

    worker_id = models.CharField(max_length=64, unique=True,
                                 help_text="hostname:pid")
    host = models.CharField(max_length=120, blank=True)
    pid = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.STARTING)
    capabilities = models.JSONField(default=list, blank=True,
                                    help_text='e.g. ["browser"]')
    max_slots = models.PositiveSmallIntegerField(default=6)
    active_jobs = models.PositiveSmallIntegerField(default=0)
    version = models.CharField(max_length=40, blank=True)

    # Latest sampled metrics (SAD §13.3)
    cpu_pct = models.FloatField(default=0)
    mem_mb = models.PositiveIntegerField(default=0)
    queue_depth = models.PositiveIntegerField(default=0)

    started_at = models.DateTimeField(auto_now_add=True)
    last_heartbeat = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Worker"
        indexes = [models.Index(fields=["last_heartbeat"])]

    def __str__(self):
        return f"{self.worker_id} ({self.status})"


class DynDomainLease(models.Model):
    """Cross-worker per-domain politeness lock (SAD §13.1)."""

    domain = models.CharField(max_length=255, unique=True)
    leased_until = models.DateTimeField()
    worker_id = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "Domain Lease"
        indexes = [models.Index(fields=["leased_until"])]

    def __str__(self):
        return f"{self.domain} until {self.leased_until:%H:%M:%S}"


class DynWorkerMetric(models.Model):
    """Time-series worker metrics for the Infrastructure panel (SAD §13.3)."""

    worker_id = models.CharField(max_length=64)
    ts = models.DateTimeField(default=timezone.now)
    cpu_pct = models.FloatField(default=0)
    mem_mb = models.PositiveIntegerField(default=0)
    active_jobs = models.PositiveSmallIntegerField(default=0)
    queue_depth = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Worker Metric"
        indexes = [models.Index(fields=["worker_id", "ts"])]
        ordering = ["-ts"]

    def __str__(self):
        return f"{self.worker_id} @ {self.ts:%H:%M:%S}"
