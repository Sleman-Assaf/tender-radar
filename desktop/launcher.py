"""
TenderRadar desktop launcher — the source of TenderRadar.exe.

Double-clicking the EXE:
    1. finds the application folder (the EXE sits in the project root)
    2. checks the MySQL service is running and the database is reachable
    3. picks a free port, starting at 8005 (see "Port selection" below)
    4. starts the Django production server (Waitress) as a hidden child process
    5. waits until Django actually answers HTTP 200
    6. opens the default browser at the port it actually used
    7. shows a small status window with Open / Stop buttons
    8. on exit, stops the whole child process tree it started

Port selection
--------------
Port 8005 is not reserved for TenderRadar, and something else on the machine can
hold it. The launcher used to treat *any* server answering on 8005 as "TenderRadar
is already running" and point the browser at it — so double-clicking TenderRadar
opened whatever unrelated program owned the port.

A listening port is therefore identified, not assumed. Waitress is started with
ident='TenderRadar' (serve_desktop.py), so our own server always answers with a
``Server: TenderRadar`` header; nothing else on the machine will. Each candidate
port is one of three things:

    ours     -> reuse it, just reopen the browser (single-instance behaviour)
    foreign  -> skip it, this is somebody else's program
    free     -> start here

Candidates are 8005..8024. A running instance anywhere in that range is found and
reused in preference to starting a second one.

Deliberately a thin launcher rather than a frozen copy of Django: the Scraper
Factory writes new .py modules into tenders/scrapers/ at runtime and rewrites
that package's __init__.py registry, which a read-only PyInstaller bundle can
never import. Backup & Restore also shells out to mysqldump/mysql. The
application therefore stays on disk exactly as it is; only the launcher is
compiled.
"""
from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import traceback
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

APP_NAME = 'TenderRadar'
HOST = '127.0.0.1'
# The port we prefer. PORT is the one actually chosen at startup and may differ —
# always read PORT/URL/READY_URL, never rebuild them from DEFAULT_PORT.
DEFAULT_PORT = int(os.environ.get('TENDERRADAR_PORT', '8005'))
PORT_CANDIDATES = 20         # 8005..8024
# Waitress is started with ident=APP_NAME, so this is the header our own server
# sends and no other program's does. See serve_desktop.py.
SERVER_IDENT = APP_NAME

PORT = DEFAULT_PORT
URL = f'http://localhost:{PORT}'
READY_URL = f'http://{HOST}:{PORT}/login/'


def set_port(port: int) -> None:
    """Point the launcher at `port`. Everything downstream reads these globals."""
    global PORT, URL, READY_URL
    PORT = port
    URL = f'http://localhost:{port}'
    READY_URL = f'http://{HOST}:{port}/login/'
MYSQL_SERVICE = os.environ.get('TENDERRADAR_MYSQL_SERVICE', 'MySQL80')
READY_TIMEOUT = 180          # seconds to wait for Django to come up
CREATE_NO_WINDOW = 0x08000000
DETACHED_FLAGS = CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


# ── Kill-on-close job object ─────────────────────────────────────────────────
# Guarantees the server can never outlive the launcher, even if the launcher is
# killed from Task Manager or crashes. Windows tears down every process in the
# job when the last handle to it closes, which happens when we exit.

def _make_kill_on_close_job():
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL('kernel32', use_last_error=True)

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
                ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ('ReadOperationCount', 'WriteOperationCount',
                         'OtherOperationCount', 'ReadTransferCount',
                         'WriteTransferCount', 'OtherTransferCount')]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _assign_to_job(job, pid: int) -> bool:
    try:
        import ctypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
        h = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not h:
            return False
        try:
            return bool(k32.AssignProcessToJobObject(job, h))
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


# ── Locating the installation ────────────────────────────────────────────────

def app_root() -> Path:
    """
    The TenderRadar project folder.

    Frozen: the folder containing the EXE (also accepts the EXE living in a
    bin/ subfolder). Script: the parent of desktop/. TENDERRADAR_HOME wins.
    """
    override = os.environ.get('TENDERRADAR_HOME')
    if override:
        return Path(override).resolve()

    if getattr(sys, 'frozen', False):
        here = Path(sys.executable).resolve().parent
        for candidate in (here, here.parent):
            if (candidate / 'manage.py').is_file():
                return candidate
        return here
    return Path(__file__).resolve().parent.parent


ROOT = app_root()
LOG_DIR = ROOT / 'logs'
LOG_FILE = LOG_DIR / 'desktop_launcher.log'


def log(msg: str) -> None:
    line = f'{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}'
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


# ── The browser window TenderRadar owns ──────────────────────────────────────
# Chrome refuses to let another program close a tab it did not open, so "Stop
# and Exit" could never tidy up after itself. The fix is ownership: TenderRadar
# starts its own Chrome window with its own profile folder, which makes that
# window a child process we may close again. The user's ordinary Chrome — their
# tabs, bookmarks and extensions — is never touched, never closed, and never
# read. Without Chrome installed we fall back to the default browser and simply
# cannot close it; that is logged rather than pretended away.

BROWSER_PROFILE = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / APP_NAME / 'browser-profile'


def _from_app_paths(exe: str) -> Path | None:
    """Where Windows itself says a browser is installed."""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                    hive, r'Software\Microsoft\Windows\CurrentVersion'
                          rf'\App Paths\{exe}') as key:
                path = Path(winreg.QueryValue(key, None))
                if path.is_file():
                    return path
        except OSError:
            continue
    return None


# Chrome first, because that is what people here use; Edge second, because it is
# on every Windows machine and — being Chromium too — takes the same flags.
APP_BROWSERS = (
    ('Chrome', 'chrome.exe', ('Google', 'Chrome', 'Application', 'chrome.exe')),
    ('Edge',   'msedge.exe', ('Microsoft', 'Edge', 'Application', 'msedge.exe')),
)


def find_app_browser() -> tuple[Path, str] | None:
    """A Chromium browser that can host TenderRadar in its own window."""
    for name, exe, parts in APP_BROWSERS:
        path = _from_app_paths(exe)
        if path:
            return path, name
        for base in (os.environ.get('PROGRAMFILES'),
                     os.environ.get('PROGRAMFILES(X86)'),
                     os.environ.get('LOCALAPPDATA')):
            if base:
                path = Path(base).joinpath(*parts)
                if path.is_file():
                    return path, name
    return None


class AppWindow:
    """The Chrome window this launcher opened — and can therefore close."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.owned = False

    def is_open(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def open(self, url: str, job=None) -> None:
        found = find_app_browser()
        if found is None:
            log('no Chrome or Edge found - opening the default browser '
                '(Exit cannot close it)')
            webbrowser.open(url)
            return
        browser, name = found

        # Already ours and still alive: let the browser raise the existing
        # window. The second process hands off to the first and exits at once,
        # so the handle we can actually close is deliberately left as it was.
        if self.is_open():
            subprocess.Popen([str(browser), f'--user-data-dir={BROWSER_PROFILE}',
                              f'--app={url}'], creationflags=DETACHED_FLAGS)
            log('reopened the TenderRadar window')
            return

        try:
            BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
            self.proc = subprocess.Popen(
                [str(browser),
                 f'--app={url}',
                 f'--user-data-dir={BROWSER_PROFILE}',
                 '--no-first-run',
                 '--no-default-browser-check',
                 '--window-size=1400,900'],
                creationflags=DETACHED_FLAGS,
            )
            self.owned = True
            # Backstop: if the launcher is killed rather than closed, Windows
            # tears the window down with it.
            if job:
                _assign_to_job(job, self.proc.pid)
            log(f'opened the TenderRadar window in {name} '
                f'(pid={self.proc.pid})')
        except OSError as exc:
            log(f'could not start {name} ({exc}) - using the default browser')
            self.proc = None
            self.owned = False
            webbrowser.open(url)

    def close(self) -> None:
        """Close the window we opened. Never touches any other browser."""
        if not self.owned or self.proc is None:
            return
        if self.proc.poll() is not None:      # the user closed it already
            return
        try:
            subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
            log('closed the TenderRadar window')
        except OSError as exc:
            log(f'could not close the TenderRadar window: {exc}')


def venv_python() -> Path | None:
    """
    The Python that runs the Django server.

    Installed layout first (runtime\\python.exe — the bundled interpreter that
    ships with the installer), then a developer virtualenv. This is why an
    installed copy needs no Python on the machine.
    """
    for rel in (r'runtime\python.exe',
                r'.venv\Scripts\python.exe',
                r'venv\Scripts\python.exe'):
        p = ROOT / rel
        if p.is_file():
            return p
    return None


# ── Preflight checks ─────────────────────────────────────────────────────────

def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


# ── Port selection ───────────────────────────────────────────────────────────
# See the "Port selection" section of the module docstring. The rule is that a
# listening port is *identified* before anything is done with it.

FREE, OURS, FOREIGN = 'free', 'ours', 'foreign'


def _port_free(host: str, port: int) -> bool:
    """
    True if `port` can be bound right now.

    Binding is both the right question and the fast one — it answers instantly
    whether the port is free or taken. Connecting is neither: on this network a
    *closed* loopback port does not answer with RST, so connect() runs to the
    full socket timeout. Probing the whole candidate range that way cost one
    timeout per free port — 20 seconds — during which the launcher sat on the
    previous step's caption and looked like it had hung on "Checking database".
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _is_our_server(headers) -> bool:
    """True if this HTTP response came from our own Waitress instance."""
    for field in ('Server', 'Via'):
        value = headers.get(field) or ''
        if value.strip().lower().startswith(SERVER_IDENT.lower()):
            return True
    return False


def probe_port(port: int, timeout: float = 2.0) -> str:
    """FREE, OURS or FOREIGN — never guesses from the port number alone."""
    if _port_free(HOST, port):
        return FREE
    url = f'http://{HOST}:{port}/login/'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return OURS if _is_our_server(r.headers) else FOREIGN
    except urllib.error.HTTPError as e:
        # An error page is still our server if it carries our ident — a booting
        # or broken TenderRadar must not be mistaken for a stranger.
        return OURS if _is_our_server(e.headers) else FOREIGN
    except Exception:
        # Listening, but not speaking HTTP we can read. Not ours; leave it alone.
        return FOREIGN


def choose_port(log=None) -> tuple[int, str]:
    """
    Pick the port to use: (port, OURS | FREE | 'exhausted').

    The common case — nothing is using the default port — costs a single bind
    test and returns immediately. Only when something *is* holding a port do we
    pay for an HTTP request to find out whose it is.

    Beyond that fast path, a running TenderRadar anywhere in the candidate range
    wins over an unused port, so launching twice reopens the browser instead of
    starting a second server: a free 8006 must not shadow our instance on 8007.
    """
    if _port_free(HOST, DEFAULT_PORT):
        return DEFAULT_PORT, FREE

    first_free: int | None = None
    for offset in range(PORT_CANDIDATES):
        port = DEFAULT_PORT + offset
        state = probe_port(port)
        if state == OURS:
            return port, OURS
        if state == FREE:
            if first_free is None:
                first_free = port
        elif log is not None and port == DEFAULT_PORT:
            log(f'Port {port} is in use by another program.')
    if first_free is not None:
        return first_free, FREE
    return DEFAULT_PORT, 'exhausted'


def start_service(name: str, wait: int = 60) -> tuple[bool, str]:
    """
    Try to start a stopped Windows service. Returns (started, reason).

    Fails fast when Windows refuses outright — access denied, service disabled,
    logon failure — instead of polling for a start that will never happen. That
    matters for the user experience: waiting a silent minute on a request that
    was rejected in the first 200 ms is indistinguishable from a hang.

    Polling is only used while a start is genuinely under way (START_PENDING),
    because MySQL can legitimately take 10-30 seconds to open its tablespaces.

    MySQL is never *stopped* by TenderRadar — it is a shared service other
    applications may rely on.
    """
    try:
        r = subprocess.run(['net', 'start', name], capture_output=True, text=True,
                           timeout=wait, creationflags=CREATE_NO_WINDOW,
                           encoding='utf-8', errors='replace')
    except Exception as exc:
        return False, f'could not run "net start {name}": {type(exc).__name__}'

    out = ((r.stdout or '') + (r.returncode and (r.stderr or '') or '')).strip()
    if r.returncode != 0:
        low = out.lower()
        # Windows refused. Nothing will change by waiting.
        if ('access is denied' in low or 'error 5' in low
                or 'ordnungsgemäß' in low):                  # localised denial
            return False, 'access denied — administrator rights are required'
        if 'disabled' in low or 'error 1058' in low:
            return False, 'the service is disabled'
        if 'already been started' in low or 'error 1056' in low:
            return service_state(name) == 'RUNNING', 'already starting'
        if service_state(name) not in ('STARTING',):
            return False, (out.splitlines()[-1][:160] if out
                           else f'net start returned {r.returncode}')

    deadline = time.time() + wait
    while time.time() < deadline:
        st = service_state(name)
        if st == 'RUNNING':
            return True, 'started'
        if st in ('NOT_INSTALLED',):
            return False, 'the service is not installed'
        time.sleep(1.5)
    return service_state(name) == 'RUNNING', 'timed out waiting for the service'


def service_state(name: str) -> str:
    """RUNNING / STOPPED / NOT_INSTALLED / UNKNOWN — no admin rights needed."""
    try:
        out = subprocess.run(['sc', 'query', name], capture_output=True, text=True,
                             timeout=15, creationflags=CREATE_NO_WINDOW).stdout.upper()
    except Exception:
        return 'UNKNOWN'
    if 'DOES NOT EXIST' in out or 'FAILED' in out:
        return 'NOT_INSTALLED'
    if 'RUNNING' in out:
        return 'RUNNING'
    if 'STOPPED' in out:
        return 'STOPPED'
    return 'UNKNOWN'


def read_db_config() -> dict:
    """DB_* values from production.env (same precedence settings.py uses)."""
    cfg = {}
    env_file = ROOT / 'production.env'
    if env_file.is_file():
        try:
            for raw in env_file.read_text(encoding='utf-8').splitlines():
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                if k.strip().startswith('DB_'):
                    cfg[k.strip()] = v.strip()
        except OSError:
            pass
    for k in ('DB_ENGINE', 'DB_NAME', 'DB_USER', 'DB_HOST', 'DB_PORT'):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def import_first_run():
    """Load desktop/first_run.py from the application folder.

    Imported from disk rather than frozen into the EXE so that an application
    update ships new provisioning logic without rebuilding the launcher.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from desktop import first_run
    return first_run


def import_env_vault():
    """
    Load desktop/env_vault.py from the application folder (see import_first_run).

    Returns None if it is unavailable: an older installation will not have the
    file, and losing the credential backup must never stop the app from starting.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from desktop import env_vault
        return env_vault
    except Exception as exc:
        log(f'env_vault unavailable: {exc}')
        return None


def preflight() -> list[tuple[bool, str]]:
    """(ok, message) for each check. A False entry blocks startup."""
    results: list[tuple[bool, str]] = []

    results.append(((ROOT / 'manage.py').is_file(),
                    f'Application folder: {ROOT}'))

    py = venv_python()
    if py is None:
        label = r'Python runtime: MISSING (runtime\python.exe not found)'
    else:
        kind = 'bundled runtime' if py.parent.name == 'runtime' else 'developer virtualenv'
        label = f'Python runtime: {kind}'
    results.append((py is not None, label))

    results.append(((ROOT / 'desktop' / 'serve_desktop.py').is_file(),
                    'Server entry point: desktop/serve_desktop.py'))

    env_ok = (ROOT / 'production.env').is_file()
    results.append((env_ok, 'Configuration: production.env'
                            + ('' if env_ok else ' MISSING')))

    cfg = read_db_config()
    engine = cfg.get('DB_ENGINE', '')
    if 'mysql' in engine:
        state = service_state(MYSQL_SERVICE)
        host = cfg.get('DB_HOST', '127.0.0.1')
        port = int(cfg.get('DB_PORT', '3306') or 3306)
        reachable = port_open(host, port)
        if state == 'RUNNING':
            results.append((True, f'MySQL service {MYSQL_SERVICE}: running'))
        elif state == 'STOPPED':
            # Requirement: start MySQL automatically when it is merely stopped.
            log(f'{MYSQL_SERVICE} is stopped - attempting to start it')
            started, why = start_service(MYSQL_SERVICE)
            log(f'start_service({MYSQL_SERVICE}) -> {started}: {why}')
            reachable = port_open(host, port) if started else reachable
            results.append((started or reachable,
                            f'MySQL service {MYSQL_SERVICE}: '
                            + ('was stopped, started automatically' if started
                               else 'stopped and could not be started '
                                    '(administrator rights may be required)')))
        elif state == 'NOT_INSTALLED':
            results.append((reachable,
                            f'MySQL service {MYSQL_SERVICE}: not installed on this machine'
                            + (f' (but {host}:{port} is reachable — remote server)'
                               if reachable else '')))
        else:
            results.append((reachable,
                            f'MySQL service {MYSQL_SERVICE}: {state.lower()}'))
        results.append((reachable, f'MySQL reachable at {host}:{port}'
                                   + ('' if reachable else ' — CONNECTION REFUSED'))),
    else:
        results.append((True, 'Database: SQLite (no service required)'))

    return results


# ── Server process management ────────────────────────────────────────────────

class ServerProcess:
    def __init__(self, dev: bool = False) -> None:
        self.proc: subprocess.Popen | None = None
        self.output: queue.Queue[str] = queue.Queue()
        self.started_by_us = False
        # Live reload. Off unless the user asks for it, so the shipped default
        # is exactly the production server it has always been.
        self.dev = dev
        # Held open for the launcher's lifetime; closing it kills the server.
        self.job = _make_kill_on_close_job()

    def start(self) -> None:
        py = venv_python()
        if py is None:
            raise RuntimeError('.venv not found — cannot start the server')

        env = os.environ.copy()
        env['TENDERRADAR_PORT'] = str(PORT)
        env['PYTHONUNBUFFERED'] = '1'
        # The child's stdout is a pipe, so Python picks the ANSI code page —
        # cp1252 here — and every scraper that prints a box-drawing rule or an
        # Arabic title dies with UnicodeEncodeError('charmap'). That killed 49 of
        # 78 scrapers on each scheduled run, silently, because the crash happened
        # inside a logging call. Forced, not setdefault: an inherited value is
        # exactly as likely to be wrong. ':replace' so a stray surrogate in
        # scraped text can never crash a scraper either.
        env['PYTHONIOENCODING'] = 'utf-8:replace'
        env.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')

        # Live reload changes one thing in the worker: Django's cached template
        # loader is dropped, so a template edit shows on the next refresh.
        # DEBUG stays False in both modes — this is never the Django dev server.
        if self.dev:
            env['TENDERRADAR_DEV'] = '1'
        else:
            env.pop('TENDERRADAR_DEV', None)

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [str(py), str(ROOT / 'desktop' / 'serve_desktop.py')],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_FLAGS,       # <- no console window
            text=True, encoding='utf-8', errors='replace', bufsize=1,
        )
        self.started_by_us = True
        if self.job and _assign_to_job(self.job, self.proc.pid):
            log('server assigned to kill-on-close job object')
        else:
            log('WARNING: job object unavailable - relying on explicit shutdown')
        log(f'server started pid={self.proc.pid}')
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                self.output.put(line)
                log(f'server: {line}')

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def drain_output(self, limit: int = 60) -> str:
        """
        Everything the server printed, for the "View Technical Details" panel.

        Keeps the tail rather than the head: when Waitress or Django dies the
        useful part — the exception and its traceback — is at the end. The
        caller shows this only behind the technical-details button, never as the
        primary message, and errors.redact() scrubs it before it is logged.
        """
        lines: list[str] = []
        while not self.output.empty():
            try:
                lines.append(self.output.get_nowait())
            except Exception:
                break
        if self.proc is not None:
            rc = self.proc.poll()
            if rc is not None:
                lines.append(f'[server process exited with code {rc}]')
        return '\n'.join(lines[-limit:]) if lines else \
            '[the web service produced no output before it stopped]'

    def stop(self, timeout: float = 20) -> None:
        """Terminate the server and every process it spawned."""
        if not self.proc:
            return
        pid = self.proc.pid
        log(f'stopping server pid={pid}')
        try:
            import psutil
            try:
                parent = psutil.Process(pid)
                kids = parent.children(recursive=True)
                for c in kids:
                    c.terminate()
                parent.terminate()
                gone, alive = psutil.wait_procs([parent, *kids], timeout=timeout)
                for p in alive:
                    p.kill()
            except psutil.NoSuchProcess:
                pass
        except ImportError:
            # Fallback: taskkill the whole tree
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        log('server stopped')


def wait_until_ready(deadline: float, on_tick=None) -> tuple[bool, str]:
    """Poll the login page until Django answers 200."""
    last = ''
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(READY_URL, timeout=5) as r:
                if r.status == 200:
                    return True, 'ready'
                last = f'HTTP {r.status}'
        except urllib.error.HTTPError as e:
            if e.code in (200, 302):
                return True, 'ready'
            last = f'HTTP {e.code}'
        except Exception as e:
            last = type(e).__name__
        if on_tick:
            on_tick(last)
        time.sleep(1)
    return False, last or 'timeout'


# ── UI ───────────────────────────────────────────────────────────────────────
# A step-based startup window. The user sees the six things TenderRadar does,
# ticked off as they succeed. If one fails, the list stops there with a cross
# against the step that failed, and desktop/errors.py explains it in plain
# language with a Retry button. Nothing is ever reported only as a traceback,
# and the window never closes itself on failure.

STEPS = [
    ('install',  'Checking installation'),
    ('mysql',    'Checking MySQL'),
    ('database', 'Checking database'),
    ('web',      'Starting web service'),
    ('ready',    'Waiting for TenderRadar'),
    ('browser',  'Opening browser'),
]


def import_errors():
    """Load desktop/errors.py from the application folder (see import_first_run)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from desktop import errors
    return errors


def import_watch():
    """
    Load desktop/watch.py (see import_first_run).

    Returns None when it is absent — an installation from before live reload
    existed must still start normally, simply without the feature.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from desktop import watch
        return watch
    except Exception as exc:
        log(f'live reload unavailable: {type(exc).__name__}: {exc}')
        return None


# ── Remembering the user's choice between runs ───────────────────────────────

PREFS_FILE = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / APP_NAME / 'launcher.json'


def load_prefs() -> dict:
    try:
        import json
        return json.loads(PREFS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_prefs(prefs: dict) -> None:
    try:
        import json
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding='utf-8')
    except OSError as exc:
        log(f'could not save preferences: {exc}')


class StepList:
    """The ✓ / ✗ checklist. Purely presentational."""

    PENDING, ACTIVE, DONE, FAILED, SKIPPED = '○', '▸', '✓', '✗', '–'

    def __init__(self, parent, bg, fg, muted, ok_colour, err_colour):
        import tkinter as tk
        self.bg, self.fg, self.muted = bg, fg, muted
        self.ok_colour, self.err_colour = ok_colour, err_colour
        self.frame = tk.Frame(parent, bg=bg)
        self.frame.pack(fill='x', padx=36, pady=(6, 4))
        self.rows: dict[str, tuple] = {}
        for key, label in STEPS:
            row = tk.Frame(self.frame, bg=bg)
            row.pack(fill='x', pady=2)
            mark = tk.Label(row, text=self.PENDING, bg=bg, fg=muted, width=2,
                            anchor='w', font=('Segoe UI', 11))
            mark.pack(side='left')
            text = tk.Label(row, text=label, bg=bg, fg=muted, anchor='w',
                            font=('Segoe UI', 10))
            text.pack(side='left')
            note = tk.Label(row, text='', bg=bg, fg=muted, anchor='w',
                            font=('Segoe UI', 9))
            note.pack(side='left', padx=(8, 0))
            self.rows[key] = (mark, text, note)

    def set(self, key: str, state: str, note: str = '') -> None:
        if key not in self.rows:
            return
        mark, text, note_lbl = self.rows[key]
        colour = {self.DONE: self.ok_colour, self.FAILED: self.err_colour,
                  self.ACTIVE: self.fg, self.PENDING: self.muted,
                  self.SKIPPED: self.muted}[state]
        mark.configure(text=state, fg=colour)
        text.configure(fg=self.fg if state in (self.DONE, self.ACTIVE)
                       else (self.err_colour if state == self.FAILED
                             else self.muted))
        note_lbl.configure(text=note, fg=colour if state == self.FAILED
                           else self.muted)

    def reset(self) -> None:
        for key, _ in STEPS:
            self.set(key, self.PENDING)


def run_gui() -> int:
    import tkinter as tk
    from tkinter import scrolledtext, simpledialog

    E = None
    try:
        E = import_errors()
    except Exception as exc:                    # errors.py itself is missing
        log(f'errors module unavailable: {type(exc).__name__}: {exc}')

    # Live reload is remembered between runs, and TENDERRADAR_DEV=1 forces it on
    # for anyone who prefers to start the launcher from a command prompt.
    want_live = (load_prefs().get('live_reload', False)
                 or os.environ.get('TENDERRADAR_DEV') == '1')
    server = ServerProcess(dev=want_live)
    window = AppWindow()
    root = tk.Tk()
    root.title(f'{APP_NAME}')
    root.geometry('660x470')
    root.minsize(600, 420)
    try:
        ico = ROOT / 'desktop' / 'TenderRadar.ico'
        if ico.is_file():
            root.iconbitmap(str(ico))
    except Exception:
        pass

    BG, FG, ACCENT, MUTED = '#0F0F0D', '#F6F6F3', '#FCD700', '#9c9b90'
    OK_GREEN, ERR_RED = '#4ade80', '#f87171'
    root.configure(bg=BG)

    tk.Label(root, text=APP_NAME, bg=BG, fg=FG,
             font=('Segoe UI', 20, 'bold')).pack(pady=(16, 0))
    tk.Label(root, text='AJi Group · Tender Intelligence', bg=BG, fg=MUTED,
             font=('Segoe UI', 9)).pack()

    status = tk.Label(root, text=f'Starting {APP_NAME}…', bg=BG, fg=ACCENT,
                      font=('Segoe UI', 11, 'bold'))
    status.pack(pady=(14, 4))

    steps = StepList(root, BG, FG, MUTED, OK_GREEN, ERR_RED)

    # The old technical log is still here, just collapsed. Support can open it;
    # the user never has to look at it.
    detail_holder = tk.Frame(root, bg=BG)
    box = scrolledtext.ScrolledText(detail_holder, height=9, bg='#1a1a17',
                                    fg='#d9d8cf', font=('Consolas', 8),
                                    relief='flat', bd=0, insertbackground=FG)
    box.pack(fill='both', expand=True, padx=16, pady=(0, 4))
    box.configure(state='disabled')
    detail_open = {'value': False}

    def say(msg: str, ok: bool | None = None) -> None:
        prefix = {True: '  [OK]   ', False: '  [FAIL] ', None: '         '}[ok]
        box.configure(state='normal')
        box.insert('end', prefix + str(msg) + '\n')
        box.see('end')
        box.configure(state='disabled')
        log(str(msg))
        root.update_idletasks()

    def toggle_detail() -> None:
        if detail_open['value']:
            detail_holder.pack_forget()
            detail_btn.configure(text='Show details')
        else:
            detail_holder.pack(fill='both', expand=True, before=btns)
            detail_btn.configure(text='Hide details')
        detail_open['value'] = not detail_open['value']

    btns = tk.Frame(root, bg=BG)
    btns.pack(side='bottom', pady=(6, 14))

    def open_browser() -> None:
        """
        Your own browser by default — your profile, your tabs, your extensions.

        Neither Chrome nor Edge will let another program close a tab it did not
        open, so that choice costs the ability to close it again on exit. Ticking
        "own window" trades the shared profile for a window TenderRadar owns.
        """
        if own_window.get():
            window.open(URL, server.job)
        else:
            log('opening TenderRadar in the default browser')
            webbrowser.open(URL)

    open_btn = tk.Button(btns, text='Open TenderRadar', command=open_browser,
                         bg=ACCENT, fg=BG, font=('Segoe UI', 10, 'bold'),
                         relief='flat', padx=18, pady=7, state='disabled',
                         cursor='hand2', activebackground='#FFE74D')
    open_btn.pack(side='left', padx=6)

    def do_quit() -> None:
        if server.started_by_us and server.is_alive():
            import tkinter.messagebox as mb
            extra = (' The TenderRadar window will close.'
                     if window.is_open() else '')
            if not mb.askokcancel(
                    APP_NAME,
                    'Stop TenderRadar and close?\n\n'
                    f'The server will be shut down.{extra} Anyone using it '
                    'will be disconnected.'):
                return
        status.configure(text='Stopping…')
        root.update_idletasks()
        # The window first, so it disappears rather than showing a dead page
        # for the second it takes the server to stop.
        window.close()
        server.stop()
        say('Server stopped.', True)
        root.after(400, root.destroy)

    tk.Button(btns, text='Stop and Exit', command=do_quit,
              bg='#33332e', fg=FG, font=('Segoe UI', 10),
              relief='flat', padx=18, pady=7, cursor='hand2').pack(side='left',
                                                                   padx=6)
    detail_btn = tk.Button(btns, text='Show details', command=toggle_detail,
                           bg='#2a2a25', fg=MUTED, font=('Segoe UI', 9),
                           relief='flat', padx=12, pady=7, cursor='hand2',
                           activebackground='#3a3a33', activeforeground=FG)
    detail_btn.pack(side='left', padx=6)

    root.protocol('WM_DELETE_WINDOW', do_quit)

    # ── Live reload ──────────────────────────────────────────────────────────
    # Off by default, so what ships behaves exactly as it always has. Turned on,
    # the server picks up edits while it runs: templates and static files on the
    # next refresh, Python after an automatic ~2 second worker restart. MySQL,
    # the launcher and the browser window are never restarted.

    reload_row = tk.Frame(root, bg=BG)
    reload_row.pack(side='bottom', pady=(0, 2))
    live = tk.BooleanVar(value=server.dev)
    # Default off: TenderRadar opens in the browser you already use, with your
    # tabs and your profile, exactly as it always did.
    own_window = tk.BooleanVar(value=load_prefs().get('own_window', False))
    reloading = {'value': False}

    def reload_now(changes) -> None:
        """A code change landed. Called on the UI thread."""
        if reloading['value']:
            return
        reloading['value'] = True
        try:
            names = ', '.join(p.name for p in changes[:3])
            more = f' (+{len(changes) - 3} more)' if len(changes) > 3 else ''
            say(f'Code changed: {names}{more} — restarting the server…')
            status.configure(text='Applying your changes…', fg=ACCENT)
            root.update_idletasks()
            started = time.time()
            server.stop()
            try:
                server.start()
            except Exception as exc:
                say(f'Could not restart the server: {exc}', False)
                status.configure(text='Reload failed — see the details',
                                 fg=ERR_RED)
                return
            ok, _ = wait_until_ready(time.time() + 90)
            if ok:
                say(f'Your changes are live ({time.time() - started:.1f}s). '
                    f'Refresh the page.', True)
                status.configure(text=f'Running — {URL} · live reload',
                                 fg=OK_GREEN)
            else:
                say('The server did not come back — fix the error above and '
                    'save again. The launcher is still watching.', False)
                status.configure(text='Reload failed — see the details',
                                 fg=ERR_RED)
        finally:
            reloading['value'] = False

    def on_change(changes) -> None:
        """Called from the watcher thread; Tk work must happen on its own."""
        root.after(0, reload_now, changes)

    W = import_watch()
    watcher = W.Watcher(ROOT, on_change) if W else None

    def apply_live_mode(restart: bool) -> None:
        want = bool(live.get())
        if watcher is None:
            say('Live reload is unavailable in this installation.', False)
            live.set(False)
            return

        prefs = load_prefs()
        prefs['live_reload'] = want
        save_prefs(prefs)
        server.dev = want

        if want:
            count = watcher.start()
            say(f'Live reload ON — watching {count} Python files. '
                f'Templates and CSS need only a refresh.', True)
        else:
            watcher.stop()
            say('Live reload OFF — the server runs in its normal mode.', True)

        # The template-caching decision is made when the worker starts, so the
        # switch only takes full effect after one restart.
        if restart and server.started_by_us and server.is_alive():
            reload_now([Path('live reload switched')])

    def check(parent, variable, text, command):
        return tk.Checkbutton(
            parent, variable=variable, command=command, text=text,
            bg=BG, fg=MUTED, font=('Segoe UI', 9), relief='flat',
            activebackground=BG, activeforeground=FG, selectcolor='#2a2a25',
            highlightthickness=0, borderwidth=0, cursor='hand2')

    check(reload_row, live, 'Apply my code changes live (no restart)',
          lambda: apply_live_mode(True)).pack(side='left', padx=(0, 14))

    def on_own_window() -> None:
        prefs = load_prefs()
        prefs['own_window'] = bool(own_window.get())
        save_prefs(prefs)
        if own_window.get():
            say('TenderRadar will open in its own window from now on — and '
                'Stop and Exit will close it.', True)
        else:
            say('TenderRadar will open in your normal browser, alongside your '
                'tabs. Stop and Exit cannot close a tab it did not open.', True)

    check(reload_row, own_window,
          'Open in its own window (so Exit closes it)',
          on_own_window).pack(side='left')

    # ── problem reporting ────────────────────────────────────────────────────

    def report(problem, step_key: str, note: str = 'failed') -> str:
        """Mark the step failed, explain it, and return the user's choice."""
        steps.set(step_key, StepList.FAILED, note)
        status.configure(text='Stopped — see the message', fg=ERR_RED)
        say(f'{problem.step}: {problem.title}', False)
        root.update_idletasks()
        if E is None:                        # errors.py missing: still not silent
            import tkinter.messagebox as mb
            mb.showerror(APP_NAME, f'{problem.title}\n\n{problem.what}\n\n'
                                   f'Log: {LOG_FILE}')
            return 'exit'
        return E.show_problem(problem, ROOT, parent=root, port=PORT)

    def fallback_problem(step_label: str, exc: BaseException):
        """Used when errors.py is present — the generic, still-friendly case."""
        return E.unexpected(step_label, exc) if E else None

    # ── the six startup steps ────────────────────────────────────────────────
    # Each returns None on success, or a Problem. Every one is safe to re-run,
    # which is what makes Retry safe.

    def step_install():
        steps.set('install', StepList.ACTIVE)
        say(f'Application folder: {ROOT}')
        for rel, purpose in (('manage.py', 'to run the application'),
                             ('desktop/serve_desktop.py',
                              'to start the web service')):
            p = ROOT / rel
            if not p.is_file():
                return E.missing_file(p, purpose) if E else RuntimeError(rel)
        py = venv_python()
        if py is None:
            return E.runtime_missing(ROOT) if E else RuntimeError('runtime')
        kind = 'bundled runtime' if py.parent.name == 'runtime' \
            else 'developer virtualenv'
        say(f'Python runtime: {kind}', True)
        steps.set('install', StepList.DONE)
        return None

    def step_mysql():
        steps.set('mysql', StepList.ACTIVE)
        cfg = read_db_config()
        if 'mysql' not in cfg.get('DB_ENGINE', ''):
            # SQLite or not configured yet; the database step decides.
            steps.set('mysql', StepList.DONE, 'not required yet')
            return None
        host = cfg.get('DB_HOST', '127.0.0.1')
        port = int(cfg.get('DB_PORT', '3306') or 3306)
        state = service_state(MYSQL_SERVICE)

        if state == 'STOPPED':
            say(f'{MYSQL_SERVICE} is stopped — starting it automatically…')
            status.configure(text='Starting the database…')
            root.update_idletasks()
            started, why = start_service(MYSQL_SERVICE)
            say(f'{MYSQL_SERVICE}: {why}', started)
            if not started:
                if port_open(host, port):
                    say('The service would not start, but the database is '
                        'reachable anyway.', True)
                elif E and 'administrator' in why:
                    return E.mysql_stopped_start_failed(
                        MYSQL_SERVICE,
                        technical=f'service_state={state}; net start '
                                  f'{MYSQL_SERVICE}: {why}')
                elif E:
                    # It accepted the request but the service did not come up.
                    return E.mysql_service_failed(
                        MYSQL_SERVICE,
                        technical=f'service_state={state}; net start '
                                  f'{MYSQL_SERVICE}: {why}')
                else:
                    return RuntimeError('mysql')
            else:
                say(f'{MYSQL_SERVICE} was stopped and has been started.', True)

        if not port_open(host, port):
            st = service_state(MYSQL_SERVICE)
            if st == 'NOT_INSTALLED':
                say('MySQL is not installed on this computer.', False)
                steps.set('mysql', StepList.FAILED, 'not installed')
                return None                  # the database step offers to install
            return (E.mysql_unreachable(host, port, st.lower().replace('_', ' '),
                                        technical=f'service={st}; '
                                                  f'connect({host},{port}) '
                                                  f'refused') if E
                    else RuntimeError('mysql'))
        say(f'MySQL reachable at {host}:{port}', True)
        steps.set('mysql', StepList.DONE)
        return None

    def step_database():
        steps.set('database', StepList.ACTIVE)
        try:
            FR = import_first_run()
        except Exception as exc:
            log(f'first_run unavailable: {exc}')
            steps.set('database', StepList.DONE, 'not checked')
            return None

        # A deleted production.env used to mean "ask for the MySQL root password
        # or you are stuck", because the app account's password cannot be
        # re-derived. If we hold an encrypted copy, put the file back and carry
        # on — the user never sees a prompt. Runs before classify() so the
        # restored file is what gets classified.
        VAULT = import_env_vault()
        if VAULT is not None:
            VAULT.restore(ROOT / 'production.env', say)

        try:
            state, info = FR.classify(ROOT)
        except Exception as exc:
            return fallback_problem('Checking database', exc)
        say(f'Database state: {state}')

        if state in (FR.READY, FR.SQLITE):
            # Capture the working credentials (and any rotation of them) so the
            # restore above can succeed next time.
            if VAULT is not None and state == FR.READY:
                VAULT.backup(ROOT / 'production.env', log)
            steps.set('database', StepList.DONE)
            steps.set('mysql', StepList.DONE)
            return None

        if state == FR.UNREACHABLE:
            cfg = info.get('cfg', {})
            return E.mysql_unreachable(
                cfg.get('DB_HOST', '127.0.0.1'),
                int(cfg.get('DB_PORT', '3306') or 3306),
                str(info.get('service', 'unknown')).lower().replace('_', ' '),
                technical=f'first_run.classify -> {state}') if E else None

        if state == FR.NEEDS_SERVER:
            has_msi = FR.bundled_msi(ROOT) is not None
            if not has_msi:
                return E.mysql_not_installed(will_install=False) if E else None
            # Explain first, then install. Requirement: tell the user MySQL is
            # not installed and that TenderRadar will handle it.
            choice = report(E.mysql_not_installed(will_install=True),
                            'database', 'setup needed') if E else 'exit'
            if choice != 'retry':
                return 'cancelled'
            status.configure(text='Installing the database — a few minutes…',
                             fg=ACCENT)
            steps.set('database', StepList.ACTIVE, 'installing MySQL')
            root.update_idletasks()
            ok, msg = FR.elevate_for_server_install(ROOT)
            say(msg, ok)
            if not ok:
                low = msg.lower()
                if 'declin' in low or 'administrator permission is required' in low:
                    return E.needs_admin('install the database service') if E \
                        else None
                return E.install_step_failed(
                    'MySQL installation',
                    'Windows could not install the required database service.',
                    why='The installation was refused or did not complete.',
                    technical=msg,
                    safety=E.DataSafety.NO_DATABASE_YET) if E else None
            state, info = FR.classify(ROOT)
            if state in (FR.READY, FR.SQLITE):
                steps.set('database', StepList.DONE)
                steps.set('mysql', StepList.DONE)
                return None

        # NEEDS_PROVISION: MySQL is up, the database/user/config are not.
        steps.set('mysql', StepList.DONE)
        steps.set('database', StepList.ACTIVE, 'first-time setup')
        say('MySQL is installed. TenderRadar needs to create its database.')
        status.configure(text='One-time setup…', fg=ACCENT)
        pw = simpledialog.askstring(
            f'{APP_NAME} — one-time setup',
            'TenderRadar needs to create its own database.\n\n'
            'Please enter the MySQL administrator (root) password.\n\n'
            'It is used once, to create the database and a restricted account '
            'for TenderRadar. It is not saved anywhere.',
            show='*', parent=root)
        if not pw:
            return E.Problem(
                step='Database setup',
                title='Setup needs the database administrator password.',
                what='TenderRadar cannot create its database without the MySQL '
                     'administrator (root) password.',
                why='The password box was closed or left empty.',
                actions=[
                    'Click Retry and enter the password.',
                    'The password was set when MySQL was installed on this '
                    'computer.',
                    f'If you do not know it, ask {E.SUPPORT_CONTACT}.',
                ],
                data_safety=E.DataSafety.NOT_ATTEMPTED) if E else None

        bin_dir = info.get('bin_dir')
        if bin_dir is not None:
            try:
                sys.path.insert(0, str(ROOT))
                from desktop import mysql_setup as M
                if M.server_version(bin_dir, 'root', pw) is None:
                    del pw
                    return E.mysql_bad_credentials('root') if E else None
            except Exception:
                pass

        status.configure(text='Setting up the database…', fg=ACCENT)
        root.update_idletasks()

        def plog(msg: str) -> None:
            say(msg)
            steps.set('database', StepList.ACTIVE, str(msg)[:44])
            root.update_idletasks()

        try:
            ok, msg = FR.provision(ROOT, pw, plog)
        except Exception as exc:
            del pw
            return fallback_problem('Setting up the database', exc)
        del pw

        if not ok:
            return E.install_step_failed(
                'Database setup', msg,
                why='TenderRadar could create neither its database nor its '
                    'restricted user account.',
                safety=E.DataSafety.NOT_ATTEMPTED) if E else None
        say('First-time setup finished. Existing data was preserved.', True)
        # Back up the credentials provisioning just generated. Doing it here as
        # well as on the ready path means the very first launch is already
        # protected — the file can be lost before a second start ever happens.
        if VAULT is not None:
            VAULT.backup(ROOT / 'production.env', log)
        steps.set('database', StepList.DONE, 'set up')
        return None

    def step_web():
        steps.set('web', StepList.ACTIVE)

        # Identify what is on each candidate port rather than assuming anything
        # answering on 8005 is us. Previously a foreign server on 8005 was read
        # as "already running" and the browser was pointed at that program.
        port, state = choose_port(say)

        if state == 'exhausted':
            holder = E._port_holder(DEFAULT_PORT)[1] if E is not None else ''
            return E.port_in_use(DEFAULT_PORT, holder, PORT_CANDIDATES) if E else None

        set_port(port)

        if state == OURS:
            say(f'{APP_NAME} is already running on port {port}.', True)
            steps.set('web', StepList.DONE, 'already running')
            steps.set('ready', StepList.DONE)
            return None

        if port != DEFAULT_PORT:
            holder = E._port_holder(DEFAULT_PORT)[1] if E is not None else ''
            say(f'Port {DEFAULT_PORT} is used by {holder or "another program"} — '
                f'{APP_NAME} will use port {port} instead.', True)
            steps.set('web', StepList.ACTIVE, f'port {port}')

        status.configure(text='Starting the web service…', fg=ACCENT)
        say('Starting the Django production server (Waitress, hidden)…')
        try:
            server.start()
        except Exception as exc:
            return (E.django_start_failed(
                technical=''.join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)))
                if E else exc)
        time.sleep(1.0)
        if not server.is_alive():
            return E.django_start_failed(
                technical=server.drain_output()) if E else None
        steps.set('web', StepList.DONE)
        return None

    def step_ready():
        if steps.rows and steps.rows['ready'][0].cget('text') == StepList.DONE:
            return None
        steps.set('ready', StepList.ACTIVE)
        dots = {'n': 0}

        def tick(reason: str) -> None:
            dots['n'] += 1
            status.configure(text='Waiting for TenderRadar'
                                  + '.' * (dots['n'] % 4), fg=ACCENT)
            steps.set('ready', StepList.ACTIVE, f'{dots["n"]}s')
            while not server.output.empty():
                line = server.output.get()
                if line.startswith('TENDERRADAR_READY_INFO'):
                    say(line.replace('TENDERRADAR_READY_INFO ', 'Django: '), True)
                elif line.startswith('TENDERRADAR_SERVING'):
                    say(line.replace('TENDERRADAR_SERVING ', 'Listening on '), True)
                elif line.startswith('TENDERRADAR_FATAL'):
                    say(line, False)
                else:
                    # Everything else goes to the details pane unmarked. A
                    # traceback here is not proof of failure — the embeddable
                    # runtime prints harmless ones during start-up — and marking
                    # it [FAIL] on a successful launch is simply wrong. If the
                    # server really dies, drain_output() carries the whole tail
                    # into the technical details.
                    say(line)
            if server.started_by_us and not server.is_alive():
                raise RuntimeError('server process exited')
            root.update_idletasks()

        try:
            ok, why = wait_until_ready(time.time() + READY_TIMEOUT, tick)
        except RuntimeError:
            server.stop()
            return E.django_start_failed(
                technical=server.drain_output()) if E else None
        if not ok:
            tech = server.drain_output()
            server.stop()
            return E.django_not_ready(READY_TIMEOUT, why, tech) if E else None
        say(f'Django is ready at {URL}', True)
        steps.set('ready', StepList.DONE)
        return None

    def step_browser():
        steps.set('browser', StepList.ACTIVE)
        open_browser()
        say('Opening browser…', True)
        steps.set('browser', StepList.DONE)
        return None

    SEQUENCE = [
        ('install', 'Checking installation', step_install),
        ('mysql', 'Checking MySQL', step_mysql),
        ('database', 'Checking database', step_database),
        ('web', 'Starting the web service', step_web),
        ('ready', 'Waiting for TenderRadar', step_ready),
        ('browser', 'Opening browser', step_browser),
    ]

    def startup(attempt: int = 1) -> None:
        """Run the sequence. On failure, explain and offer Retry."""
        log(f'--- launcher start (root={ROOT}, attempt {attempt}) ---')
        if attempt > 1:
            say(f'--- retrying (attempt {attempt}) ---')
        steps.reset()
        status.configure(text=f'Starting {APP_NAME}…', fg=ACCENT)

        for key, label, fn in SEQUENCE:
            try:
                outcome = fn()
            except Exception as exc:            # nothing escapes to a traceback
                log(f'step {key} crashed: '
                    + ''.join(traceback.format_exception(type(exc), exc,
                                                         exc.__traceback__)))
                outcome = fallback_problem(label, exc)
                if outcome is None:
                    import tkinter.messagebox as mb
                    mb.showerror(APP_NAME,
                                 f'{APP_NAME} could not start.\n\n'
                                 f'Step: {label}\n\nLog: {LOG_FILE}')
                    return

            if outcome is None:
                continue
            if outcome == 'cancelled':
                status.configure(text='Setup cancelled', fg=MUTED)
                say('The user cancelled setup.', False)
                return
            if not hasattr(outcome, 'title'):   # errors.py missing
                import tkinter.messagebox as mb
                mb.showerror(APP_NAME, f'{APP_NAME} could not start.\n\n'
                                       f'Step: {label}\n\nLog: {LOG_FILE}')
                return

            choice = report(outcome, key)
            if choice == 'retry':
                root.after(150, lambda: startup(attempt + 1))
            else:
                status.configure(text='TenderRadar did not start', fg=ERR_RED)
            return

        status.configure(text=f'Running — {URL}', fg=OK_GREEN)
        open_btn.configure(state='normal')
        say('TenderRadar is running. Leave this window open while you use it.')
        if live.get():
            # Remembered from last time, or TENDERRADAR_DEV=1. The worker was
            # already started in the right mode, so there is nothing to restart.
            apply_live_mode(False)

    def watchdog() -> None:
        if reloading['value']:                  # a reload owns the server now
            root.after(3000, watchdog)
            return
        if server.started_by_us and not server.is_alive():
            steps.set('web', StepList.FAILED, 'stopped')
            status.configure(text='The web service stopped unexpectedly',
                             fg=ERR_RED)
            say('Server process exited unexpectedly.', False)
            if E is not None:
                p = E.Problem(
                    step='Web service',
                    title=f'{APP_NAME} stopped running.',
                    what='The web service that shows TenderRadar\'s pages closed '
                         'unexpectedly while you were using it.',
                    why='The service ran out of memory, was closed by another '
                        'program, or ran into an internal problem.',
                    actions=[
                        'Click Retry to start TenderRadar again.',
                        'Any page you had open will need to be reloaded.',
                        'If this keeps happening, restart Windows.',
                    ],
                    data_safety=E.DataSafety.NOT_ATTEMPTED,
                    technical=server.drain_output())
                if E.show_problem(p, ROOT, parent=root, port=PORT) == 'retry':
                    root.after(150, lambda: startup(1))
                    return
            return
        root.after(3000, watchdog)

    root.after(300, startup)
    root.after(4000, watchdog)
    root.mainloop()
    # belt and braces: never leave an orphaned server behind
    if server.started_by_us and server.is_alive():
        server.stop()
    return 0


if __name__ == '__main__':
    # Re-entry through UAC: this copy exists only to install and configure the
    # MySQL server with administrator rights, then exit. No window, no server.
    if '--provision-mysql' in sys.argv:
        try:
            sys.exit(import_first_run().provision_server_elevated())
        except Exception as exc:
            log(f'elevated provisioning crashed: {type(exc).__name__}: {exc}')
            sys.exit(1)

    try:
        sys.exit(run_gui())
    except Exception as exc:
        # Last line of defence. Even a crash before the window exists must reach
        # the user as an explanation, never as a traceback and never as silence.
        log('launcher crashed: '
            + ''.join(traceback.format_exception(type(exc), exc,
                                                 exc.__traceback__)))
        try:
            E = import_errors()
            E.show_problem(E.unexpected('starting TenderRadar', exc), ROOT,
                           port=PORT)
        except Exception:
            try:
                import tkinter.messagebox as mb
                mb.showerror(
                    APP_NAME,
                    f'{APP_NAME} could not start.\n\n'
                    f'Something went wrong before the application finished '
                    f'loading. Your data has not been changed.\n\n'
                    f'Please restart {APP_NAME}. If the problem continues, '
                    f'reinstall it — reinstalling does not delete your data.\n\n'
                    f'Technical log: {LOG_FILE}')
            except Exception:
                pass
        sys.exit(1)
