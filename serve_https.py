# Path: TenderRadar/serve_https.py
"""
HTTPS server for TenderRadar.
Uses wsgiref + ssl — simple, no extra dependencies beyond Python stdlib.
Waitress handles the WSGI app, ssl module wraps the socket.

Usage: python serve_https.py
Cert : ssl/cert.pem  +  ssl/key.pem
"""
import os
import ssl
import sys
from pathlib import Path
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler
import socketserver

# ── Load production.env ───────────────────────────────────────
BASE_DIR = Path(__file__).parent
env_file = BASE_DIR / 'production.env'
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip())

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')

import django
django.setup()

from tender_radar.wsgi import application

CERT_FILE = BASE_DIR / 'ssl' / 'cert.pem'
KEY_FILE  = BASE_DIR / 'ssl' / 'key.pem'
HOST      = '0.0.0.0'
PORT      = int(os.environ.get('PORT', 443))

# ── Validate certs ────────────────────────────────────────────
if not CERT_FILE.exists() or not KEY_FILE.exists():
    print(f'\n[ERROR] Certificate files not found:')
    print(f'  {CERT_FILE}')
    print(f'  {KEY_FILE}')
    print('\nRun deploy_https.bat to generate certificates.')
    sys.exit(1)

# ── SSL context ───────────────────────────────────────────────
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))

# ── Silent request handler ────────────────────────────────────
class QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        pass   # suppress default wsgiref noise
    def log_request(self, code='-', size='-'):
        from datetime import datetime
        print(f'[{datetime.now().strftime("%d/%b/%Y %H:%M:%S")}] '
              f'{self.command} {self.path} {code}')

# ── HTTPS server ──────────────────────────────────────────────
class SecureWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """Threaded WSGIServer with SSL."""
    daemon_threads = True

    def server_bind(self):
        super().server_bind()

    def get_request(self):
        raw, addr = self.socket.accept()
        try:
            ssl_conn = ctx.wrap_socket(raw, server_side=True)
            return ssl_conn, addr
        except ssl.SSLError as exc:
            # Browser sent plain HTTP to HTTPS port — send redirect response
            try:
                data = raw.recv(4096)
                if data and data[:3] in (b'GET', b'POS', b'HEA', b'PUT', b'DEL'):
                    # Plain HTTP request on HTTPS port — redirect to https://
                    first_line = data.split(b'\n')[0].decode(errors='ignore')
                    path = first_line.split(' ')[1] if len(first_line.split(' ')) > 1 else '/'
                    host = 'radar.AJGroup.com.local'
                    for line in data.split(b'\n'):
                        if line.lower().startswith(b'host:'):
                            host = line.split(b':', 1)[1].strip().decode(errors='ignore')
                            break
                    response = (
                        b'HTTP/1.1 301 Moved Permanently\r\n'
                        b'Location: https://' + host.encode() + path.encode() + b'\r\n'
                        b'Connection: close\r\n'
                        b'Content-Length: 0\r\n'
                        b'\r\n'
                    )
                    raw.sendall(response)
            except Exception:
                pass
            finally:
                raw.close()
            raise


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
    httpd = SecureWSGIServer((HOST, PORT), QuietHandler)
    httpd.set_app(application)

    print(f'\n{"="*60}')
    print(f'  TenderRadar HTTPS server started')
    print(f'  URL  : https://radar.AJGroup.com.local')
    print(f'  Port : {PORT}')
    print(f'  Cert : {CERT_FILE.name}')
    print(f'  Stop : Ctrl+C')
    print(f'{"="*60}\n')

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n[INFO] Server stopped.')
        httpd.server_close()


if __name__ == '__main__':
    main()
