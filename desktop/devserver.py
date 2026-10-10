"""
TenderRadar DEVELOPMENT mode — the server, with automatic reload.

    .venv\\Scripts\\python.exe desktop\\devserver.py
    .venv\\Scripts\\python.exe desktop\\devserver.py --port 8006

What it is
----------
A supervisor that owns one Django/Waitress worker and restarts *only that
worker* when Python source changes:

    devserver.py  (supervisor — keeps running)
          │
          └── serve_desktop.py  (Waitress worker — replaced on a .py change)

MySQL is never touched. Nothing else on the machine is restarted. In the
packaged product this same layer is what the Windows service will supervise, so
production keeps a stable Waitress worker and development gets reload — the two
modes differ in this file, not in the application.

What actually needs a restart (measured, not assumed)
-----------------------------------------------------
    static files (CSS/JS/images)  no restart — WHITENOISE_AUTOREFRESH is on
    templates                     no restart — devserver runs the worker with
                                  TENDERRADAR_DEV=1, which disables Django's
                                  cached template loader
    Python / views / urls / models  worker restart — done here, automatically

So a template or CSS edit needs a browser refresh and nothing more; only Python
edits cost a ~2 second worker restart.

This is NOT the Django development server. The worker is the same Waitress
process the service runs, with DEBUG=False. Nothing here runs in production.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / 'desktop' / 'serve_desktop.py'
LOG_DIR = ROOT / 'logs'
LOG_FILE = LOG_DIR / 'devserver.log'

# What to watch lives in watch.py, so the launcher's live-reload mode and this
# one can never drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from watch import (                                              # noqa: E402
    DEBOUNCE_SECONDS, IGNORED_DIRS, WATCHED_SUFFIXES,            # noqa: F401
    changed_files, iter_watched_files as _iter, snapshot as _snapshot,
)

READY_TIMEOUT = 60


def log(message: str, *, echo: bool = True) -> None:
    line = f'{datetime.now():%Y-%m-%d %H:%M:%S}  {message}'
    if echo:
        print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


def python_exe() -> Path:
    """The interpreter that runs the worker — bundled runtime first."""
    for rel in (r'runtime\python.exe', r'.venv\Scripts\python.exe',
                r'venv\Scripts\python.exe'):
        p = ROOT / rel
        if p.is_file():
            return p
    return Path(sys.executable)


class Worker:
    """One Waitress process, started and stopped as a unit."""

    def __init__(self, port: int, host: str = '127.0.0.1'):
        self.port = port
        self.host = host
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env['TENDERRADAR_DEV'] = '1'          # live templates in the worker
        env['TENDERRADAR_PORT'] = str(self.port)
        env['TENDERRADAR_HOST'] = self.host
        env['PYTHONUNBUFFERED'] = '1'
        # Scrapers print box-drawing rules and Arabic; a pipe defaults to the
        # ANSI code page and would raise UnicodeEncodeError.
        env['PYTHONIOENCODING'] = 'utf-8:replace'
        env.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')

        self.proc = subprocess.Popen(
            [str(python_exe()), str(WORKER)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        log(f'worker started (pid {self.proc.pid}) on {self.host}:{self.port}')

    def _pump(self) -> None:
        """Relay the worker's output so a traceback is not swallowed."""
        proc = self.proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(f'  worker | {line}', echo=True)

    def stop(self, timeout: float = 10) -> None:
        proc = self.proc
        if not proc or proc.poll() is not None:
            return
        pid = proc.pid
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
            log(f'worker stopped (pid {pid})')
        except subprocess.TimeoutExpired:
            log(f'worker {pid} did not exit in {timeout}s — killing it')
            proc.kill()
            proc.wait(timeout=5)
        self.proc = None

    def is_alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def wait_until_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        """Poll the login page until the worker answers, as the launcher does."""
        url = f'http://{self.host}:{self.port}/login/'
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_alive():
                return False
            try:
                with urllib.request.urlopen(url, timeout=3) as r:
                    if r.status == 200:
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code in (200, 302):
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        return False


def iter_watched_files():
    return _iter(ROOT)


def snapshot() -> dict[Path, float]:
    return _snapshot(ROOT)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='TenderRadar development server')
    ap.add_argument('--port', type=int,
                    default=int(os.environ.get('TENDERRADAR_PORT', '8005')))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--interval', type=float, default=1.0,
                    help='seconds between change scans')
    ap.add_argument('--no-reload', action='store_true',
                    help='run the worker without watching for changes')
    args = ap.parse_args(argv)

    log('=' * 68)
    log(f'DEVELOPMENT mode — supervisor pid {os.getpid()}')
    log(f'application : {ROOT}')
    log(f'interpreter : {python_exe()}')
    log('templates and static files are live; Python changes restart the worker')
    log('=' * 68)

    worker = Worker(args.port, args.host)
    worker.start()
    if worker.wait_until_ready():
        log(f'ready at http://{args.host}:{args.port}/')
    else:
        log('worker did not become ready — see the lines above for the reason')

    if args.no_reload:
        log('reload disabled (--no-reload); watching nothing')

    watched = snapshot()
    log(f'watching {len(watched)} Python files for changes')

    try:
        while True:
            time.sleep(args.interval)

            # crash recovery: the worker died on its own
            if not worker.is_alive():
                log('worker exited unexpectedly — restarting it')
                worker.start()
                worker.wait_until_ready()
                watched = snapshot()
                continue

            if args.no_reload:
                continue

            current = snapshot()
            changes = changed_files(watched, current)
            if not changes:
                continue

            time.sleep(DEBOUNCE_SECONDS)       # let the editor finish writing
            current = snapshot()
            changes = changed_files(watched, current)
            watched = current
            if not changes:
                continue

            shown = ', '.join(str(p.relative_to(ROOT)) for p in changes[:4])
            more = f' (+{len(changes) - 4} more)' if len(changes) > 4 else ''
            log(f'change detected: {shown}{more}')
            log('restarting the worker (MySQL and the supervisor are untouched)')

            started = time.time()
            worker.stop()
            worker.start()
            if worker.wait_until_ready():
                log(f'reloaded in {time.time() - started:.1f}s — '
                    f'http://{args.host}:{args.port}/')
            else:
                log('worker failed to come back — fix the error above and save '
                    'again; the supervisor is still running')
    except KeyboardInterrupt:
        log('stopping (Ctrl+C)')
    finally:
        worker.stop()
        log('development server stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
