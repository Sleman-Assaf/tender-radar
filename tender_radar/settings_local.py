"""SQLite development with automatic external scans disabled by default.

Environment variables are read from the process; .env is documentation only.
"""
import os

os.environ.setdefault('TENDERRADAR_SCHEDULER', '0')

from .settings import *  # noqa: F401,F403,E402

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',  # noqa: F405
    }
}
DEBUG = os.environ.get('DJANGO_DEBUG', 'true').lower() in ('1', 'true', 'yes', 'on')
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
SCRAPER_PROXY = os.environ.get('SCRAPER_PROXY', '')
AJIAI_API_BASE = os.environ.get('AJIAI_API_BASE', 'http://127.0.0.1:8001')
AJIBRAIN_BASE_URL = os.environ.get('AJIBRAIN_BASE_URL', 'http://127.0.0.1:8001')
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/1')
NEWSPAPER_INPROCESS_SCHEDULER = False
MAGAZINE_INPROCESS_SCHEDULER = False
