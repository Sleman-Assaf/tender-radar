"""
Settings for running the automated tests.

    .venv\\Scripts\\python.exe manage.py test --settings=tender_radar.settings_test

Optional — the suite also runs on MySQL now
-------------------------------------------
This module was written because `manage.py test` could not build a database from
the migration history on MySQL. Both causes are gone:

  * the duplicate `ai_service` column (fixed in
    tenders/migrations/0005_sitesettings_ai.py), and
  * scraper_factory 0002's oversized `url` index — that app has been deleted.

Verified: `migrate` on an empty MySQL database now applies all **125**
migrations and exits 0, and `manage.py test tenders.tests` passes against MySQL
without this module.

It is kept because it is still the faster way to run the suite — in-memory
SQLite, MD5 password hashing, no database privileges needed — not because
anything requires it.
"""
from .settings import *          # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Fast, deterministic password hashing — these tests create users, they do not
# measure hashing.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Keep test output about the application, not about its logging.
LOGGING['loggers'] = {                                    # noqa: F405
    name: {**cfg, 'handlers': [], 'level': 'CRITICAL'}
    for name, cfg in LOGGING['loggers'].items()           # noqa: F405
}
