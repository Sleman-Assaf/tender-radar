# Path: TenderRadar/serve_http.py
"""
Plain-HTTP production server for TenderRadar.

Same Django app as serve_https.py, but served over HTTP with no TLS and no
certificate. Uses Waitress (already in requirements.txt) so it is a real
production WSGI server, not `manage.py runserver`.

Binds 0.0.0.0 so the site is reachable from other machines on the network,
not just localhost.

Usage:
    python serve_http.py            # port 8005 (the default)
    set PORT=80 & python serve_http.py

Do NOT run serve_http_redirect.py alongside this — that one bounces port 80
traffic to https:// and would defeat the point.
"""
import os
import socket
from pathlib import Path

# ── Load production.env ───────────────────────────────────────
BASE_DIR = Path(__file__).parent
env_file = BASE_DIR / 'production.env'
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

# Belt and braces: this server never speaks TLS, so make sure the HTTPS-only
# security settings (HSTS, COOP) stay off even if production.env still says True.
os.environ['DJANGO_HTTPS'] = 'False'

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')

import django
django.setup()

from tender_radar.wsgi import application
from waitress import serve

HOST    = os.environ.get('HOST', '0.0.0.0')
# 8005 by default (2026-08-12). Override with the PORT environment variable;
# PORT=80 restores the bare http://host/ URL, which needs no port in the address.
PORT    = int(os.environ.get('PORT', 8005))
THREADS = int(os.environ.get('WAITRESS_THREADS', 8))


def _lan_ip():
    """Best-effort primary IPv4 of this machine, for the banner only."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return '127.0.0.1'


def _register_newspaper_schedule():
    """(Re)register the Newspaper Tenders daily schedule on every server
    start — idempotent, so nobody has to remember to run
    `manage.py setup_newspaper_schedule` after deploying. Never blocks the
    server: on a fresh install (beat tables not migrated yet) it just prints
    a note and moves on."""
    try:
        from django.core.management import call_command
        call_command('setup_newspaper_schedule')
    except Exception as exc:
        print(f'  ! Newspaper schedule not registered ({exc}) — '
              f'run manage.py migrate, then restart')


def main():
    _register_newspaper_schedule()
    ip   = _lan_ip()
    port = '' if PORT == 80 else f':{PORT}'

    print(f'\n{"=" * 60}')
    print(f'  TenderRadar HTTP server started (no TLS)')
    print(f'  Local   : http://localhost{port}')
    print(f'  Network : http://{ip}{port}')
    print(f'  Bind    : {HOST}:{PORT}')
    print(f'  Stop    : Ctrl+C')
    print(f'{"=" * 60}\n')

    serve(application, host=HOST, port=PORT, threads=THREADS)


if __name__ == '__main__':
    main()
