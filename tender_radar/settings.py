# tender_radar/settings.py

import os
import sys
import logging.handlers
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-change-me-in-production-use-env-variable')

import os

# ── Environment-driven config (loaded from production.env in production) ─────
DEBUG         = True  # DEBUG MODE ON
# Temporarily enable detailed errors even in production — set DJANGO_DEBUG_ERRORS=True
if os.environ.get('DJANGO_DEBUG_ERRORS', 'False') == 'True':
    DEBUG = True
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'radar.AJGroup.com.local,localhost,127.0.0.1,172.10.0.19').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_celery_beat',
    'django_celery_results',
    'tenders.apps.TendersConfig',
    'bd.apps.BdConfig',
    # DEPRECATED — Dynamic Source Engine. Retained only so its (empty) tables
    # stay known to Django; it has no behaviour.
    'sources_dynamic.apps.SourcesDynamicConfig',
    # Newspaper Tenders — عطاءات الجريدة (isolated, removable feature)
    'newspaper_tenders.apps.NewspaperTendersConfig',
    'magazine_tenders.apps.MagazineTendersConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serves static files via WSGI
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'tender_radar.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'tenders.context_processors.active_countries',
                # Exposes user_role / is_super_admin / is_it_admin / can_administer
                # so navigation can hide unauthorised entries. The views enforce
                # the same rules server-side (tenders/permissions.py).
                'tenders.context_processors.rbac_context',
                'tenders.context_processors.bd_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'tender_radar.wsgi.application'

# ── Database ─────────────────────────────────────────────────────────────────
# Configured entirely through environment variables so the same code runs on
# SQLite (development / rollback) and MySQL (production) with no edits:
#
#   DB_ENGINE  DB_NAME  DB_USER  DB_PASSWORD  DB_HOST  DB_PORT
#
# Leave DB_ENGINE unset or blank and the project keeps using db.sqlite3 exactly
# as before — that is the default, so nothing changes until it is set.

def _load_db_env(path):
    """
    Read DB_* keys out of production.env into os.environ.

    Deliberately DB_* only: that file also carries DJANGO_DEBUG,
    DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS, which have always been supplied
    by the process environment (start_production.bat). Importing those here
    would silently change how a running server behaves — a new SECRET_KEY alone
    would invalidate every session. Values already in os.environ always win.
    """
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            if key.startswith('DB_') and key not in os.environ:
                os.environ[key] = value.strip()
    except OSError:
        pass          # no production.env — environment variables only


_load_db_env(BASE_DIR / 'production.env')

_DB_ENGINE = os.environ.get('DB_ENGINE', '').strip()

if _DB_ENGINE:
    DATABASES = {
        'default': {
            'ENGINE':   _DB_ENGINE,
            'NAME':     os.environ.get('DB_NAME', 'tenderradar'),
            'USER':     os.environ.get('DB_USER', ''),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST':     os.environ.get('DB_HOST', '127.0.0.1'),
            'PORT':     os.environ.get('DB_PORT', '3306'),
            'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '60')),
        }
    }
    if 'mysql' in _DB_ENGINE:
        # utf8mb4 is required: the data contains Arabic plus 4-byte emoji
        # (country flags, 📢). STRICT_TRANS_TABLES makes over-long values fail
        # loudly instead of being silently truncated.
        DATABASES['default']['OPTIONS'] = {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        }
        DATABASES['default']['TEST'] = {
            'CHARSET':   'utf8mb4',
            'COLLATION': 'utf8mb4_0900_ai_ci',
        }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# For production with MSSQL (AJi standard):
# DATABASES = {
#     'default': {
#         'ENGINE': 'mssql',
#         'NAME': 'TenderRadarDB',
#         'USER': 'your_user',
#         'PASSWORD': 'your_password',
#         'HOST': 'your_host',
#         'PORT': '1433',
#         'OPTIONS': {'driver': 'ODBC Driver 17 for SQL Server'},
#     }
# }

# LDAP is tried first and returns None when disabled or unreachable, so local
# Django accounts keep working either way.
AUTHENTICATION_BACKENDS = [
    'tenders.auth_backends.LDAPBackend',
    'django.contrib.auth.backends.ModelBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Amman'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── System backups (IT Dashboard → Backup & Restore) ──────────────────────────
# Where full-system archives are written. Point this at a separate volume in
# production so a disk failure does not take the backups with it.
BACKUP_ROOT = Path(os.environ.get('TENDERRADAR_BACKUP_ROOT', BASE_DIR / 'backups'))

# Folder holding mysqldump / mysql, used by the MySQL backup & restore path.
# Leave blank to search PATH and the usual install locations automatically.
MYSQL_BIN_DIR = os.environ.get('MYSQL_BIN_DIR', '')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Auth ──────────────────────────────────────────────────────────────────────
LOGIN_URL          = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Celery Configuration ──────────────────────────────────────────────────────
# Redis broker (required for Celery Beat scheduling).
# Install Redis on Windows: https://github.com/tporadowski/redis/releases
# Or use: pip install redis   then run: redis-server
CELERY_BROKER_URL              = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND          = 'redis://localhost:6379/1'
CELERY_ACCEPT_CONTENT          = ['json']
CELERY_TASK_SERIALIZER         = 'json'
CELERY_RESULT_SERIALIZER       = 'json'
CELERY_TIMEZONE                = 'Asia/Amman'
CELERY_BEAT_SCHEDULER          = 'django_celery_beat.schedulers:DatabaseScheduler'

# Worker concurrency — 1 = sequential, safe for scraping
CELERY_WORKER_CONCURRENCY      = 1
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Task time limits (scrapers can be slow)
CELERY_TASK_SOFT_TIME_LIMIT    = 6900   # 115 min soft
CELERY_TASK_TIME_LIMIT         = 7200   # 120 min hard

# Store task results so admin can see last run status
CELERY_TASK_TRACK_STARTED      = True
CELERY_TASK_IGNORE_RESULT      = False

# ── Scraper Schedule ─────────────────────────────────────────────────────────
SCRAPER_SCHEDULE_HOURS = 6   # run all scrapers every N hours automatically

# ── Scraper Configuration ─────────────────────────────────────────────────────
SCRAPER_REQUEST_TIMEOUT         = 45   # seconds — ASP.NET portals are slow
SCRAPER_DELAY_BETWEEN_REQUESTS  = 3    # polite delay between pages
SCRAPER_MAX_RETRIES             = 3
SCRAPER_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)
SCRAPER_PROXY = os.environ.get('SCRAPER_PROXY', 'http://172.10.0.90:3128')

# ── Scraper Factory ──────────────────────────────────────────────────────────
# Generates real Python scraper files into tenders/scrapers/.
# See docs/SCRAPER_FACTORY_ARCHITECTURE.md.

# ── AI backend: AJi AI's General Chat API ─────────────────────────────────────
#
#     Factory -> POST {AJIAI_API_BASE}/api/general-chat/ -> Ollama -> model
#
# The endpoint (AJi AI's api/general_chat.py) executes the prompt with NO RAG, no
# knowledge base and no grounding, and resolves the active model from AJi AI's own
# configuration — so the Factory names no model at all, not even in a setting. A
# model upgrade on the server requires nothing here.
#
# Health and model discovery come from GET {AJIAI_API_BASE}/api/general-chat/info/,
# which reports llm_online, the active model, the installed models and whether a
# key is required — one call in place of /api/tags plus a health route.
AJIAI_API_BASE  = os.environ.get('AJIAI_API_BASE', 'http://172.30.0.4:8000')
AJIAI_CHAT_PATH = os.environ.get('AJIAI_CHAT_PATH', '/api/general-chat/')
AJIAI_INFO_PATH = os.environ.get('AJIAI_INFO_PATH', '/api/general-chat/info/')

# The endpoint's optional shared secret (its GENERAL_CHAT_API_KEY). Empty means
# the server is open on the LAN and nothing is sent.
AJIAI_API_KEY = os.environ.get('AJIAI_API_KEY', '')

# Deterministic generation: temperature 0 plus a fixed seed, so two runs of one
# prompt produce the same file and a benchmark regression is attributable.
AJIAI_TEMPERATURE = float(os.environ.get('AJIAI_TEMPERATURE', '0'))
AJIAI_SEED        = int(os.environ.get('AJIAI_SEED', '42'))

# Body-level timeout. The server hard-caps at 600 (MAX_TIMEOUT in
# general_chat.py); the provider clamps to that and rides its HTTP timeout
# slightly above, so the server's 504 — which names the model — is the error
# that arrives rather than a silent client-side abort.
AJIAI_TIMEOUT = int(os.environ.get('AJIAI_TIMEOUT', '600'))

# Pin a specific model ONLY if code generation must differ from AJi AI's active
# model. Empty is the intended state: AJi AI decides.
AJIAI_MODEL = os.environ.get('AJIAI_MODEL', '')

AJIAI_TRUST_PROXY = os.environ.get(
    'AJIAI_TRUST_PROXY', 'False').strip().lower() in ('1', 'true', 'yes', 'on')

# Direct-Ollama settings, used only by FACTORY_CODE_MODEL='ollama' (local
# development) and by the AI & Site Settings fallback chain.
FACTORY_MODEL_BASE_URL = os.environ.get('FACTORY_MODEL_BASE_URL', '')
FACTORY_MODEL_NAME     = os.environ.get('FACTORY_MODEL_NAME', '')

# Which provider implementation serves generation. One production backend, the
# Ollama Chat API; the name exists so a different implementation can be selected
# without editing code (see AutoCodeModel.BACKENDS, and FACTORY_PROVIDERS in
# providers.py for replacing the interface outright).
FACTORY_CODE_MODEL = os.environ.get('FACTORY_CODE_MODEL', 'ajiai').strip()

# FakeCodeModel and its FACTORY_USE_FAKE_MODEL switch were removed on 2026-08-03
# after the first real end-to-end verification passed against production AJi AI.
# An unreachable model is now a hard, clearly-reported error — never simulation.

# Generation parameters, following AJiBrain's own long-output caller
# (rfp/views.py::_ollama_stream) — writing an RFP is the closest workload to
# writing a scraper.
FACTORY_MODEL_NUM_CTX     = int(os.environ.get('FACTORY_MODEL_NUM_CTX', '32768'))
FACTORY_MODEL_TIMEOUT     = int(os.environ.get('FACTORY_MODEL_TIMEOUT', '600'))
FACTORY_MODEL_NUM_THREAD  = int(os.environ.get('FACTORY_MODEL_NUM_THREAD', '16'))
FACTORY_MODEL_NUM_BATCH   = int(os.environ.get('FACTORY_MODEL_NUM_BATCH', '512'))

# Deprecated aliases. These settings were originally named for Qwen; they are now
# named for their role, because which model answers is configuration and the
# provider must not be tied to one. Still honoured so an existing deployment that
# sets the old names does not silently lose its configuration.
FACTORY_QWEN_BASE_URL = os.environ.get('FACTORY_QWEN_BASE_URL', '')
FACTORY_QWEN_MODEL    = os.environ.get('FACTORY_QWEN_MODEL', '')
FACTORY_QWEN_NUM_CTX  = FACTORY_MODEL_NUM_CTX
FACTORY_QWEN_TIMEOUT  = FACTORY_MODEL_TIMEOUT

# Where the corpus (the coding standard) lives, and where generated files go.
FACTORY_SCRAPERS_DIR   = BASE_DIR / 'tenders' / 'scrapers'

# Validation thresholds. Style score is a VERIFY-only measure: the validator
# never rewrites or reformats generated code.
FACTORY_MIN_STYLE_SCORE  = int(os.environ.get('FACTORY_MIN_STYLE_SCORE', '90'))
FACTORY_MAX_REPAIR_TRIES = int(os.environ.get('FACTORY_MAX_REPAIR_TRIES', '3'))
FACTORY_SANDBOX_TIMEOUT  = int(os.environ.get('FACTORY_SANDBOX_TIMEOUT', '180'))

# Dependency-inversion provider overrides (dotted paths); empty = defaults.
FACTORY_PROVIDERS = {}

# ── Deprecated: Dynamic Source Engine ────────────────────────────────────────
# Tables are retained (empty) until the Scraper Factory has proven itself.
DYNSRC_ENC_KEYS = os.environ.get('DYNSRC_ENC_KEYS', '')

# ── Logging ───────────────────────────────────────────────────────────────────
import os as _os
_LOG_DIR = BASE_DIR / 'logs'
_os.makedirs(_LOG_DIR, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'stream': 'ext://sys.stdout',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': _LOG_DIR / 'scraper.log',
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 3,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'tenders': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
        'tenders.apps.TendersConfig': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
        'api': {'handlers': ['console', 'file'], 'level': 'INFO', 'propagate': False},
    },
}


# ── Development HTTP settings ──────────────────────────────────────────────────
# Suppress COOP/COEP warnings when running on HTTP (dev only)
# On production, set DEBUG=False and configure proper HTTPS + reverse proxy
# ── HTTPS / Security ───────────────────────────────────────────────────────────
# Set DJANGO_HTTPS=True in production.env when serving over HTTPS
_HTTPS = os.environ.get('DJANGO_HTTPS', 'False') == 'True'

SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin' if _HTTPS else None
SECURE_SSL_REDIRECT                = False
SESSION_COOKIE_SECURE              = False
CSRF_COOKIE_SECURE                 = False
CSRF_TRUSTED_ORIGINS               = [
    'https://radar.AJGroup.com.local',
    'https://radar.ajgroup.com.local',
    'https://radar.ajigroup.com',
    'https://172.10.0.19',
    'http://radar.AJGroup.com.local',
    'http://localhost',
    'http://127.0.0.1',
]
SESSION_COOKIE_HTTPONLY            = True
CSRF_COOKIE_HTTPONLY               = False
SECURE_HSTS_SECONDS                = 31536000 if _HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS     = _HTTPS
SECURE_HSTS_PRELOAD                = _HTTPS
SECURE_CONTENT_TYPE_NOSNIFF        = True
X_FRAME_OPTIONS                    = 'SAMEORIGIN' 

# ── Production overrides (set via environment or local_settings.py) ────────────
# For production with HTTPS:
#   SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
#   SECURE_SSL_REDIRECT = True
#   SESSION_COOKIE_SECURE = True
#   CSRF_COOKIE_SECURE = True
#   SECURE_HSTS_SECONDS = 31536000

# ── AJiBrain API Integration ──────────────────────────────────────────────────
# AJiBrain runs on the same server on port 8001.
# Override via environment: AJIBRAIN_BASE_URL=http://127.0.0.1:8001
import os
AJIBRAIN_BASE_URL = os.environ.get('AJIBRAIN_BASE_URL', 'http://172.30.0.4:8000')


# ── Newspaper Tenders — عطاءات الجريدة ────────────────────────────────────────
# Daily Al-Rai newspaper edition → AJiAI Qwen Vision → existing tender pipeline.
# Feature is isolated in the newspaper_tenders app; disable with
# NEWSPAPER_ENABLED=False (existing scrapers/tenders are unaffected either way).
NEWSPAPER_ENABLED = os.environ.get(
    'NEWSPAPER_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# Discovery source (the advertisements section, hyphenated slug — verified live)
NEWSPAPER_SOURCE_URL = os.environ.get(
    'NEWSPAPER_SOURCE_URL',
    'https://alrai.com/%D8%A7%D9%84%D8%A7%D8%B9%D9%84%D8%A7%D9%86%D8%A7%D8%AA-'
    '%D8%A7%D9%84%D9%88%D8%B1%D9%82%D9%8A%D8%A9')

# Daily schedule (registered by: python manage.py setup_newspaper_schedule)
# Second AI pass per ad at full crop resolution — fixes headline misreads the
# whole-page pass makes (same endpoint, image upload; zero AJiAI changes).
NEWSPAPER_ZOOM_REREAD = os.environ.get('NEWSPAPER_ZOOM_REREAD', '1') == '1'

# Photo verification: OCR each ad crop and drop it when it reads as a non-tender
# notice (the AI sometimes attaches a neighbouring court/auction ad to a real
# tender). Needs EasyOCR installed; degrades to a no-op (photo kept) when it is
# not, so this can stay on everywhere. Set to 0 to disable entirely.
NEWSPAPER_PHOTO_VERIFY = os.environ.get(
    'NEWSPAPER_PHOTO_VERIFY', '1').strip().lower() in ('1', 'true', 'yes', 'on')

# Al-Rai heads its court-notice pages "إعلانات قضائية" (إخطار دائرة التنفيذ,
# مذكرة تبليغ حكم…). The vision model invents tenders from those pages, so the
# printed header is OCR'd first and a court page is never sent to the AI
# (newspaper_tenders/page_gate.py). Needs PyMuPDF + EasyOCR; without them, or
# when the header cannot be read, the page is read as before. Set 0 to disable.
NEWSPAPER_SKIP_COURT_PAGES = os.environ.get(
    'NEWSPAPER_SKIP_COURT_PAGES', '1').strip().lower() in ('1', 'true', 'yes', 'on')

# Non-AEC tenders with no usable photo — the AI returned NO crop at all, or only
# an unusable degenerate half-crop — are not worth keeping as a bare text row, so
# drop the whole tender and leave it out of the saved count. AEC tenders are
# ALWAYS kept (text-only) even without a photo, because they are the ones that
# matter. Set 0 to keep the old behaviour (non-AEC rows survive text-only). Only
# affects rows created in the same run, and only once classification has run.
NEWSPAPER_DROP_NONAEC_WITHOUT_PHOTO = os.environ.get(
    'NEWSPAPER_DROP_NONAEC_WITHOUT_PHOTO', '1').strip().lower() in ('1', 'true', 'yes', 'on')

# Cross-newspaper de-duplication: the same government tender is advertised in
# several papers (Al-Rai, Al-Ghad, Ad-Dustour). The per-source unique key does
# not catch that, so without this each paper's copy becomes a separate row.
# When on, a tender already saved from ANOTHER newspaper is skipped. Matching is
# conservative (same entity + same reference, or same entity + same title within
# a few days) — see pipeline.cross_source_duplicate(). Set 0 to disable.
NEWSPAPER_CROSS_SOURCE_DEDUP = os.environ.get(
    'NEWSPAPER_CROSS_SOURCE_DEDUP', '1').strip().lower() in ('1', 'true', 'yes', 'on')

# Entity names the AJiAI extraction is known to FABRICATE — it invents these
# placeholder tenders instead of returning "no tenders" for a page. Rows whose
# entity contains any of these are dropped as hallucinations. Comma-separated;
# extend as new fabrications are found. (Template companies named after a
# parenthesised foreign city are caught separately in pipeline.py.)
NEWSPAPER_FAKE_ENTITIES = [s.strip() for s in os.environ.get(
    'NEWSPAPER_FAKE_ENTITIES',
    'البتراء للخدمات الطبية').split(',') if s.strip()]

# Titles containing any of these are never AEC (rent / sale / auction ads) —
# a deterministic override applied after the AI classification, newspaper only.
NEWSPAPER_NON_AEC_KEYWORDS = [k for k in os.environ.get(
    'NEWSPAPER_NON_AEC_KEYWORDS',
    'استئجار,إستئجار,تأجير,إيجار,ايجار,بيع,مزايدة,مزاد').split(',') if k]

# The daily run happens as an APScheduler job inside the web process — the
# same mechanism that actually drives the scrapers and the News sweep in this
# Waitress deployment. Set 0 to run it from Celery Beat (or only by hand)
# instead; a second web worker against the same database also wants 0, so that
# only one process schedules.
NEWSPAPER_INPROCESS_SCHEDULER = os.environ.get(
    'NEWSPAPER_INPROCESS_SCHEDULER', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
NEWSPAPER_SCHEDULE_TIME = os.environ.get('NEWSPAPER_SCHEDULE_TIME', '08:00')
NEWSPAPER_TIMEZONE      = os.environ.get('NEWSPAPER_TIMEZONE', 'Asia/Amman')
# Hour range (crontab syntax) for the retry sweep that picks up PDFs Al-Rai
# attaches after the article is first published, and retries failed files.
NEWSPAPER_RETRY_HOURS   = os.environ.get('NEWSPAPER_RETRY_HOURS', '7-18')

# AJiAI Newspaper Vision API. Empty → falls back to AJIBRAIN_BASE_URL.
NEWSPAPER_AI_API_URL = os.environ.get('NEWSPAPER_AI_API_URL', '')
NEWSPAPER_AI_TIMEOUT = int(os.environ.get('NEWSPAPER_AI_TIMEOUT', '900'))

# Download / validation limits
NEWSPAPER_DOWNLOAD_TIMEOUT = int(os.environ.get('NEWSPAPER_DOWNLOAD_TIMEOUT', '60'))
NEWSPAPER_MAX_RETRIES      = int(os.environ.get('NEWSPAPER_MAX_RETRIES', '3'))
# 160MB: Al-Ghad's full-newspaper daily PDF is usually ~26MB but reached
# 93MB on 30 Aug 2026 — a photo-heavy edition — and the old 64MB limit
# rejected it as "File too large" (Al-Rai pages stay ~1MB).
NEWSPAPER_MAX_FILE_SIZE    = int(os.environ.get('NEWSPAPER_MAX_FILE_SIZE',
                                                str(160 * 1024 * 1024)))

# ── Per-newspaper switches ────────────────────────────────────────────────────
NEWSPAPER_ALRAI_ENABLED  = os.environ.get(
    'NEWSPAPER_ALRAI_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
NEWSPAPER_ALGHAD_ENABLED = os.environ.get(
    'NEWSPAPER_ALGHAD_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
NEWSPAPER_DUSTOUR_ENABLED = os.environ.get(
    'NEWSPAPER_DUSTOUR_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
# Al-Ghad publishes one full-newspaper PDF/day WITH a text layer; only pages
# whose text carries tender markers go to the AI. MIN_HITS = how many times a
# weak marker ("عطاء") must appear; MAX_PAGES caps AI cost per edition.
NEWSPAPER_SPLIT_MIN_HITS  = int(os.environ.get('NEWSPAPER_SPLIT_MIN_HITS', '2'))
NEWSPAPER_SPLIT_MAX_PAGES = int(os.environ.get('NEWSPAPER_SPLIT_MAX_PAGES', '8'))
# Al-Rai needs a real browser User-Agent (Cloudflare). Empty → uses
# SCRAPER_USER_AGENT.
NEWSPAPER_USER_AGENT = os.environ.get('NEWSPAPER_USER_AGENT', '')

# عطاءات الجريدة shows AEC tenders only (the business rule). Set False to
# show every extracted tender (demo / review mode).
NEWSPAPER_AEC_ONLY = os.environ.get(
    'NEWSPAPER_AEC_ONLY', 'True').strip().lower() in ('1', 'true', 'yes', 'on')


# ── News — الأخبار ─────────────────────────────────────
# Scrapes tender announcements from magazine websites. Feature is isolated in
# the magazine_tenders app; disable with MAGAZINE_ENABLED=0 and it disappears
# without touching anything else.
MAGAZINE_ENABLED = os.environ.get(
    'MAGAZINE_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# The magazines page shows AEC tenders only (the business rule). Set False to
# show every scraped announcement (demo / review mode). Non-AEC rows are kept
# either way and remain visible to admins on the audit page.
MAGAZINE_AEC_ONLY = os.environ.get(
    'MAGAZINE_AEC_ONLY', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# ── Daily News Brief — موجز الأخبار — PDF for the CEO ────────────────────────
# Recipient of the "Send to CEO" button on the News page. The e-mail receives
# the PDF as an attachment; WhatsApp additionally receives the document when a
# number is configured (WaSenderAPI downloads it from the public site URL, so
# digest_site_url must be reachable by the provider).
NEWS_BRIEF_CEO_EMAIL = os.environ.get(
    'NEWS_BRIEF_CEO_EMAIL', 'Hamzeh_Awwad@ajigroup.com').strip()
NEWS_BRIEF_CEO_WHATSAPP = os.environ.get('NEWS_BRIEF_CEO_WHATSAPP', '').strip()

# The daily sweep runs as an APScheduler job inside the web process — the same
# mechanism that actually drives scraping in this Waitress deployment. Set 0 to
# run it from Celery Beat (or only manually) instead.
MAGAZINE_INPROCESS_SCHEDULER = os.environ.get(
    'MAGAZINE_INPROCESS_SCHEDULER', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
MAGAZINE_SCHEDULE_TIME = os.environ.get('MAGAZINE_SCHEDULE_TIME', '07:00')
MAGAZINE_TIMEZONE      = os.environ.get('MAGAZINE_TIMEZONE', 'Asia/Amman')

# Cap pages walked per magazine, so a pagination bug cannot crawl a whole site.
# How old a News item may be before it is no longer news, in months. Applied in
# three places: the WordPress query (so old posts never travel), the save step
# (the backstop for feeds that cannot filter by date) and the News page itself
# (a row that grew old in place). Undated items are always kept - Future
# Projects consists of them. Set to 0 to switch the whole window off.
MAGAZINE_MAX_AGE_MONTHS = int(os.environ.get('MAGAZINE_MAX_AGE_MONTHS', '10'))

# Whether the News page carries construction PROJECT news - contracts awarded,
# ground broken, schemes unveiled - alongside open tenders and design
# commissions. Without it the trade feeds yield roughly 2% of what they publish
# and the page reads as weeks out of date. Set to 0 for tenders and design
# news only, which is what the page showed before.
# Whether construction PROJECT news reaches the News page even when the
# classifier marks it Non-AEC. The classifier asks whether an item is
# separable design or supervision work - the right question for a tender and
# too narrow for a news page. Set to 0 to make News obey the classifier alone.
MAGAZINE_NEWS_INCLUDES_PROJECTS = os.environ.get(
    'MAGAZINE_NEWS_INCLUDES_PROJECTS', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

MAGAZINE_KEEP_PROJECT_NEWS = os.environ.get(
    'MAGAZINE_KEEP_PROJECT_NEWS', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# The summary on a News card is written by AJi AI (the General Chat API the
# AJIAI_* settings above point at) once, when the article is first stored.
# Set to 0 to stop asking; cards then show the publisher's excerpt. The
# timeout is per article, in seconds (a summary takes about three), clamped
# by the server at 600.
MAGAZINE_AI_SUMMARIES = os.environ.get(
    'MAGAZINE_AI_SUMMARIES', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
MAGAZINE_SUMMARY_TIMEOUT = int(os.environ.get('MAGAZINE_SUMMARY_TIMEOUT', '60'))
# How gently the summaries are asked for: at most this many articles after
# each sweep (the rest wait for the next one), with this many seconds between
# consecutive requests. The AI host did not survive a sweep that asked for
# hundreds back to back.
MAGAZINE_SUMMARY_BATCH = int(os.environ.get('MAGAZINE_SUMMARY_BATCH', '40'))
MAGAZINE_SUMMARY_PAUSE = float(os.environ.get('MAGAZINE_SUMMARY_PAUSE', '5'))
# Never under the test runner: the runner tests store real rows, and each
# would otherwise cost a live call to AJi AI (and a fetch of example.com).
# The summary tests switch it back on with override_settings.
if 'test' in sys.argv:
    MAGAZINE_AI_SUMMARIES = False

MAGAZINE_MAX_PAGES = int(os.environ.get('MAGAZINE_MAX_PAGES', '5'))

# Empty → falls back to SCRAPER_USER_AGENT. Some publisher sites reject the
# default python-requests agent outright.
MAGAZINE_USER_AGENT = os.environ.get('MAGAZINE_USER_AGENT', '')


# ── What counts as AEC on the listing pages ───────────────────────────────────
# AJi's work on a project is الاشراف والتصميم — supervision and design. The
# classification service reads "Design AND construction of a clinic" as
# construction and returns is_aec=False, which hides a tender AJi would bid on.
#
# With this on, the Magazines and Newspaper pages ALSO show tenders whose own
# title says the work is supervision / design / consultancy. It changes only
# what those pages DISPLAY: Tender.is_aec still holds exactly what the service
# decided, so the audit trail is intact and this is reversible.
# See tenders/aec_scope.py. Set 0 to show only what the service flagged.
AEC_INCLUDE_DESIGN_SUPERVISION = os.environ.get(
    'AEC_INCLUDE_DESIGN_SUPERVISION', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# ── Per-magazine switches ─────────────────────────────────────────────────────
# One flag per registry code: MAGAZINE_<CODE>_ENABLED. Absent flags default to
# True, so a magazine added to sources.py works without a settings edit; set a
# flag to 0 to park a site that has started failing, with no deploy.


# ── eSupply Dubai credentials ─────────────────────────────────────────────────
# Set these to your registered supplier account on esupply.dubai.gov.ae
ESUPPLY_USERNAME = os.environ.get('ESUPPLY_USERNAME', 'AJi')
ESUPPLY_PASSWORD = os.environ.get('ESUPPLY_PASSWORD', 'AJi2026@')

# HTML snapshots (F8) — evidence for reproducibility and regression diffing.
FACTORY_SNAPSHOT_INLINE_MAX = int(os.environ.get('FACTORY_SNAPSHOT_INLINE_MAX', '20000'))
FACTORY_SNAPSHOT_RETENTION_DAYS = int(os.environ.get('FACTORY_SNAPSHOT_RETENTION_DAYS', '180'))

# Investigation (Phase 1). Tier 1 is HTTP + static analysis and always runs;
# Tier 2 launches a browser only when Tier 1 cannot prove where the tender data
# comes from. Turning Tier 2 off makes those sites report UNKNOWN rather than
# guess, which sends them to the developer queue.
FACTORY_TIER2_ENABLED = os.environ.get(
    'FACTORY_TIER2_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
FACTORY_TIER2_TIMEOUT_MS = int(os.environ.get('FACTORY_TIER2_TIMEOUT_MS', '45000'))
FACTORY_TIER2_SETTLE_MS  = int(os.environ.get('FACTORY_TIER2_SETTLE_MS', '2500'))
FACTORY_TIER2_MAX_SCROLLS = int(os.environ.get('FACTORY_TIER2_MAX_SCROLLS', '3'))
FACTORY_INVESTIGATION_MAX_PROBES = int(
    os.environ.get('FACTORY_INVESTIGATION_MAX_PROBES', '6'))

# Tender validation (Phase 2). A row must score at least FACTORY_MIN_TENDER_SCORE
# to count as a tender, and at least FACTORY_MIN_VALID_ROW_RATIO of the rows must
# qualify before an extraction is accepted. Both floors were set against live
# data: at these values the validator accepts 99.5% of the tenders the
# hand-written scrapers have already collected.
FACTORY_MIN_TENDER_SCORE = int(os.environ.get('FACTORY_MIN_TENDER_SCORE', '50'))
FACTORY_MIN_VALID_ROW_RATIO = float(
    os.environ.get('FACTORY_MIN_VALID_ROW_RATIO', '0.5'))

# Health. A generated scraper that collects nothing is marked DEGRADED and keeps
# running; only this many consecutive failures disables it, so a portal outage
# does not silently remove a source.
FACTORY_DISABLE_AFTER_FAILURES = int(
    os.environ.get('FACTORY_DISABLE_AFTER_FAILURES', '5'))

# Ollama is reached directly, not through SCRAPER_PROXY. The proxy exists to give
# scrapers a route to the public internet; the model server is ours. Routing model
# traffic through it turns a clear connection error into a timeout that names the
# proxy instead of the model host. Set True only if the endpoint really sits
# behind the proxy.
OLLAMA_USE_ENV_PROXY = os.environ.get(
    'OLLAMA_USE_ENV_PROXY', 'False').strip().lower() in ('1', 'true', 'yes', 'on')

# Health observer (Phase 4A). The Factory watches ScrapeLog rather than editing
# the scheduler, so runs of the hand-written scrapers are unaffected. Turning
# this off stops health tracking; it does not stop any scraper.
FACTORY_HEALTH_OBSERVER_ENABLED = os.environ.get(
    'FACTORY_HEALTH_OBSERVER_ENABLED', 'True'
).strip().lower() in ('1', 'true', 'yes', 'on')

# Scheduler queue (Phase 4). The Factory keeps its own queue for the scrapers it
# authored; the APScheduler loop still owns the 77 production sources.
FACTORY_LEASE_SECONDS = int(os.environ.get('FACTORY_LEASE_SECONDS', '1800'))

# Retention. Each sweep is bounded; scheduling history is kept longest because it
# is the audit trail, not merely telemetry.
FACTORY_METRIC_RETENTION_DAYS  = int(os.environ.get('FACTORY_METRIC_RETENTION_DAYS', '365'))
FACTORY_HISTORY_RETENTION_DAYS = int(os.environ.get('FACTORY_HISTORY_RETENTION_DAYS', '730'))
FACTORY_JOB_RETENTION_DAYS     = int(os.environ.get('FACTORY_JOB_RETENTION_DAYS', '30'))
FACTORY_ALERT_RETENTION_DAYS   = int(os.environ.get('FACTORY_ALERT_RETENTION_DAYS', '180'))
FACTORY_STAGING_RETENTION_HOURS = int(os.environ.get('FACTORY_STAGING_RETENTION_HOURS', '48'))
FACTORY_BENCHMARK_KEEP_RUNS    = int(os.environ.get('FACTORY_BENCHMARK_KEEP_RUNS', '50'))

# Resource ceilings. A sandbox forks a process and Tier 2 launches Chromium;
# without a bound, a burst of parallel work can take the web server down with it.
FACTORY_MAX_CONCURRENT_SANDBOX    = int(os.environ.get('FACTORY_MAX_CONCURRENT_SANDBOX', '2'))
FACTORY_MAX_CONCURRENT_BROWSER    = int(os.environ.get('FACTORY_MAX_CONCURRENT_BROWSER', '2'))
FACTORY_MAX_CONCURRENT_GENERATION = int(os.environ.get('FACTORY_MAX_CONCURRENT_GENERATION', '1'))
FACTORY_RESOURCE_WAIT_SECONDS     = int(os.environ.get('FACTORY_RESOURCE_WAIT_SECONDS', '120'))

# Benchmark. Threads, because every case waits on the network.
FACTORY_BENCHMARK_WORKERS = int(os.environ.get('FACTORY_BENCHMARK_WORKERS', '6'))
FACTORY_BENCHMARK_RETRIES = int(os.environ.get('FACTORY_BENCHMARK_RETRIES', '1'))
FACTORY_BENCHMARK_CASE_TIMEOUT = int(
    os.environ.get('FACTORY_BENCHMARK_CASE_TIMEOUT', '120'))

# Factory memory. Lessons are keyed by structural signature, never by URL.
FACTORY_LEARNING_ENABLED = os.environ.get(
    'FACTORY_LEARNING_ENABLED', 'True').strip().lower() in ('1', 'true', 'yes', 'on')
FACTORY_LEARNING_LIMIT = int(os.environ.get('FACTORY_LEARNING_LIMIT', '5'))

# Retrieval (F6) — how much context we send to the model.
FACTORY_REFERENCE_COUNT = int(os.environ.get('FACTORY_REFERENCE_COUNT', '3'))
FACTORY_REFERENCE_MAX   = int(os.environ.get('FACTORY_REFERENCE_MAX', '5'))
FACTORY_PROMPT_TOKEN_BUDGET = int(os.environ.get('FACTORY_PROMPT_TOKEN_BUDGET', '24000'))
FACTORY_HTML_SAMPLE_CHARS   = int(os.environ.get('FACTORY_HTML_SAMPLE_CHARS', '14000'))

# A dry run's purpose is "did it generate working code, and what did it find?".
# The sandbox already answers both in milliseconds. Re-running the scraper
# through the full save pipeline afterwards only proves persistence, and it pays
# the per-tender AI-enrichment cost — which is minutes when AJiBrain is
# unreachable. Skip it by default so testing is fast; set this False (or pass
# --with-save) when you specifically want to exercise persistence.
FACTORY_DRYRUN_SKIP_SAVE = os.environ.get(
    'FACTORY_DRYRUN_SKIP_SAVE', 'True').strip().lower() in ('1', 'true', 'yes', 'on')

# Pause (seconds) between CVs in a bulk-import batch, so interactive
# AJiAI requests (Hub assist, chat) are not starved by the batch queue.
BD_BATCH_ITEM_DELAY = int(os.environ.get('BD_BATCH_ITEM_DELAY', '8'))
