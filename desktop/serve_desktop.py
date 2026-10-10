"""
Production WSGI server used by the TenderRadar desktop launcher.

Runs the existing Django project under Waitress. Nothing about the application
is changed here: the same settings module, the same MySQL connection from
production.env, the same migrations, media, static and scraper code.

DEBUG is forced off for the packaged app (it is left as-is in settings.py so
development behaviour is untouched). With DEBUG off, static files are served by
the WhiteNoise middleware that is already in MIDDLEWARE — WHITENOISE_USE_FINDERS
is enabled in settings, so no collectstatic step is required.

Started by launcher.py as a hidden child process. Also runnable by hand:

    .venv\\Scripts\\python.exe desktop\\serve_desktop.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
os.chdir(APP_ROOT)

# Scrapers print box-drawing rules and Arabic tender titles. When stdout is a
# pipe — which it always is under the launcher — Python defaults to the ANSI code
# page (cp1252 here), and those characters raise UnicodeEncodeError('charmap').
# The exception surfaced inside a logging call, so the scraper died with its real
# error replaced by an encoding one: 49 of 78 sources failed on every run.
#
# The launcher also sets PYTHONIOENCODING; this covers the documented case of
# running this file directly, and anything that re-wraps the stream later.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass          # already detached or not a text stream — nothing to fix

HOST = os.environ.get('TENDERRADAR_HOST', '127.0.0.1')
PORT = int(os.environ.get('TENDERRADAR_PORT', '8005'))
THREADS = int(os.environ.get('TENDERRADAR_THREADS', '16'))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')

# Internal services (AJiBrain / AJiAI / Ollama) live on the LAN and must not be
# sent through the corporate proxy. Mirrors the machine-level NO_PROXY fix so a
# freshly-installed copy behaves correctly even before the user re-logs in.
_no_proxy = os.environ.get('NO_PROXY', 'localhost,127.0.0.1,::1,.local')
for extra in ('172.30.0.4', '172.30.0.0/16'):
    if extra not in _no_proxy:
        _no_proxy += ',' + extra
os.environ['NO_PROXY'] = os.environ['no_proxy'] = _no_proxy


#: DEVELOPMENT mode. Set by desktop/devserver.py; never set by the service.
DEV = os.environ.get('TENDERRADAR_DEV') == '1'


def _enable_live_templates(settings) -> bool:
    """
    Turn off the cached template loader so template edits show on refresh.

    Measured, not assumed: with DEBUG=False Django wraps the filesystem and
    app_directories loaders in django.template.loaders.cached.Loader, which holds
    every compiled template in memory. A template edit is then invisible until
    the process restarts. Static files are already live — WHITENOISE_AUTOREFRESH
    is on — so this is the only piece that needs handling.

    Replacing the loaders explicitly (and clearing APP_DIRS, which Django refuses
    to accept alongside an explicit `loaders`) leaves DEBUG=False intact: no
    debug pages, no unbounded query log, no development server. Only the caching
    changes.
    """
    from django.template import engines

    for tpl in settings.TEMPLATES:
        if tpl.get('BACKEND') != 'django.template.backends.django.DjangoTemplates':
            continue
        tpl['APP_DIRS'] = False
        tpl.setdefault('OPTIONS', {})['loaders'] = [
            'django.template.loaders.filesystem.Loader',
            'django.template.loaders.app_directories.Loader',
        ]
    # drop any engine already built from the previous configuration
    try:
        engines._engines = {}
    except Exception:
        pass
    return True


def main() -> int:
    import django
    django.setup()

    from django.conf import settings

    # Packaged app runs as production: no debug pages, no unbounded query log.
    # DEBUG stays False in development mode too — see _enable_live_templates.
    settings.DEBUG = False
    for host in (HOST, 'localhost', '127.0.0.1'):
        if host not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append(host)

    if DEV:
        _enable_live_templates(settings)
        print('TENDERRADAR_DEV templates=live static=live '
              '(python changes are reloaded by devserver.py)', flush=True)

    from django.db import connection
    engine = settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1]

    # Fail loudly and early if the database is unreachable — the launcher shows
    # this text to the user instead of a blank browser page.
    with connection.cursor() as cur:
        cur.execute('SELECT 1')
        cur.fetchone()

    from tender_radar.wsgi import application
    from waitress import serve

    print(f'TENDERRADAR_READY_INFO engine={engine} '
          f'db={settings.DATABASES["default"]["NAME"]} '
          f'debug={settings.DEBUG} threads={THREADS}', flush=True)
    print(f'TENDERRADAR_SERVING http://{HOST}:{PORT}', flush=True)

    serve(application, host=HOST, port=PORT, threads=THREADS,
          connection_limit=512, channel_timeout=120, ident='TenderRadar')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:                       # surfaced in the launcher log
        print(f'TENDERRADAR_FATAL {type(exc).__name__}: {exc}', flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
