"""
TenderRadar Windows Service.

    TenderRadarService                     (this file, run by Windows)
          │  checks MySQL, waits for the database
          └── serve_desktop.py             (the same Waitress worker as always)

Install / remove (both need an elevated command prompt):

    python desktop\\service.py install      register + set Automatic startup
    python desktop\\service.py start
    python desktop\\service.py stop
    python desktop\\service.py restart
    python desktop\\service.py status
    python desktop\\service.py remove

Design
------
* The worker is `desktop/serve_desktop.py` — the identical production Waitress
  process the launcher has always used, with DEBUG off. The service is a
  supervisor, not a second implementation of the server, and it never enables
  development auto-reload.
* MySQL is *started if stopped*, never reinstalled, reconfigured or restarted
  when it is already running. The database is never created or migrated here.
* If the worker dies the service restarts it, backing off so a worker that
  cannot start does not spin.
* Every state change is written to logs/service.log in plain language, because
  a Windows service has nowhere else to tell the user anything.

This mirrors desktop/devserver.py, which supervises the same worker for
development. The two share the worker and differ only in the supervisor.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVICE_NAME = 'TenderRadar'
SERVICE_DISPLAY = 'TenderRadar'
SERVICE_DESCRIPTION = (
    'Runs the TenderRadar tender-intelligence server. Starts MySQL if it is '
    'stopped, serves the application on http://127.0.0.1:8005 and restarts it '
    'automatically if it stops unexpectedly.'
)

LOG_DIR = ROOT / 'logs'
LOG_FILE = LOG_DIR / 'service.log'

HOST = '127.0.0.1'
DEFAULT_PORT = int(os.environ.get('TENDERRADAR_PORT', '8005'))
CREATE_NO_WINDOW = 0x08000000

MYSQL_WAIT_SECONDS = 90
WORKER_READY_TIMEOUT = 120
#: Backoff between restarts, so a worker that cannot start does not spin.
RESTART_DELAYS = [2, 5, 10, 20, 30, 60]


# ── Logging ──────────────────────────────────────────────────────────────────

def log(message: str) -> None:
    """Append one line to the service log. Never raises — this is a service."""
    line = f'{datetime.now():%Y-%m-%d %H:%M:%S}  {message}'
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8', errors='replace') as fh:
            fh.write(line + '\n')
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


# ── Pieces reused from the existing desktop code ─────────────────────────────

def worker_python() -> Path:
    """The interpreter that runs the worker — bundled runtime first."""
    for rel in (r'runtime\python.exe', r'.venv\Scripts\python.exe',
                r'venv\Scripts\python.exe'):
        p = ROOT / rel
        if p.is_file():
            return p
    return Path(sys.executable)


def ensure_mysql() -> tuple[bool, str]:
    """
    Make sure MySQL is available. Start it only if it is stopped.

    Reuses desktop/mysql_setup.py rather than re-implementing service control,
    so the service agrees with the installer and the launcher about what
    "MySQL is running" means.
    """
    try:
        from desktop import mysql_setup as M
    except Exception as exc:
        return False, f'MySQL helper unavailable ({type(exc).__name__}: {exc})'

    state = M.service_state()
    if state == 'RUNNING':
        if M.port_open():
            return True, 'MySQL is already running — left untouched'
        return True, 'MySQL service is running but 3306 is not answering yet'

    if state == 'NOT_INSTALLED':
        if M.port_open():
            return True, ('No MySQL80 service, but a server is answering on '
                          '127.0.0.1:3306 — using it')
        return False, ('MySQL is not installed on this computer. TenderRadar '
                       'cannot start without a database. Run TenderRadar Setup '
                       'to install it.')

    if state in ('STOPPED', 'STARTING', 'UNKNOWN'):
        log(f'MySQL is {state.lower()} — starting it')
        if not M.start_service():
            return False, ('MySQL is installed but could not be started. Open '
                           'Services, start "MySQL80", then start TenderRadar '
                           'again.')
        deadline = time.time() + MYSQL_WAIT_SECONDS
        while time.time() < deadline:
            if M.port_open():
                return True, 'MySQL started'
            time.sleep(1)
        return False, (f'MySQL was started but did not accept connections '
                       f'within {MYSQL_WAIT_SECONDS} seconds.')
    return False, f'MySQL is in an unexpected state: {state}'


def database_ready() -> tuple[bool, str]:
    """Ask the existing first-run logic whether the database is usable."""
    try:
        from desktop import first_run as FR
    except Exception as exc:
        return True, f'database state not checked ({type(exc).__name__})'
    try:
        state, _info = FR.classify(ROOT)
    except Exception as exc:
        return True, f'database state not checked ({type(exc).__name__}: {exc})'
    if state in (FR.READY, FR.SQLITE):
        return True, f'database {state}'
    if state == FR.UNREACHABLE:
        return False, ('The database is configured but not answering. Check '
                       'that MySQL is running.')
    if state == FR.NEEDS_SERVER:
        return False, ('No MySQL server was found. Run TenderRadar Setup to '
                       'install it.')
    return False, ('TenderRadar has not finished its first-time setup. Start '
                   'TenderRadar from its shortcut once to complete it, then '
                   'start the service.')


def port_is_taken(port: int) -> tuple[bool, str]:
    """
    True if something already holds the port. Prevents a second server.

    Uses the launcher's own probe so "ours" and "somebody else's" mean the same
    thing everywhere: our Waitress answers with a `Server: TenderRadar` header.
    """
    try:
        from desktop import launcher as L
    except Exception:
        return False, ''
    try:
        state = L.probe_port(port)
    except Exception:
        return False, ''
    if state == L.FREE:
        return False, ''
    if state == L.OURS:
        return True, (f'TenderRadar is already serving on port {port}. The '
                      f'service will not start a second copy.')
    return True, (f'Port {port} is already being used by another application, '
                  f'so TenderRadar cannot listen on it.')


# ── The worker ───────────────────────────────────────────────────────────────

class Worker:
    """One production Waitress process, owned by the service."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env['TENDERRADAR_PORT'] = str(self.port)
        env['TENDERRADAR_HOST'] = HOST
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8:replace'
        env.pop('TENDERRADAR_DEV', None)      # production: never auto-reload
        env.setdefault('DJANGO_SETTINGS_MODULE', 'tender_radar.settings')

        self.proc = subprocess.Popen(
            [str(worker_python()), str(ROOT / 'desktop' / 'serve_desktop.py')],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
        )
        log(f'server process started (pid {self.proc.pid}) on '
            f'{HOST}:{self.port}')

    def drain(self, limit: int = 40) -> str:
        """Whatever the worker printed — the reason it failed, usually."""
        if not self.proc or not self.proc.stdout:
            return ''
        lines: list[str] = []
        try:
            while len(lines) < limit:
                line = self.proc.stdout.readline()
                if not line:
                    break
                lines.append(line.rstrip())
        except Exception:
            pass
        return '\n'.join(lines)

    def is_alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def wait_until_ready(self, timeout: float = WORKER_READY_TIMEOUT) -> bool:
        url = f'http://{HOST}:{self.port}/login/'
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
            time.sleep(0.5)
        return False

    def stop(self, timeout: float = 20) -> None:
        """Terminate cleanly, then forcibly. Leaves no orphan behind."""
        proc = self.proc
        if not proc or proc.poll() is not None:
            self.proc = None
            return
        pid = proc.pid
        log(f'stopping the server process (pid {pid})')
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
            log(f'server process stopped (pid {pid})')
        except subprocess.TimeoutExpired:
            log(f'server did not exit within {timeout}s — forcing it')
            try:
                subprocess.run(['taskkill', '/PID', str(pid), '/F', '/T'],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                proc.kill()
        except Exception as exc:
            log(f'error while stopping the server: {type(exc).__name__}: {exc}')
        self.proc = None


# ── Supervisor loop, shared by the service and by `run` for testing ──────────

class Supervisor:
    """Starts MySQL, starts the worker, keeps it alive. No Windows API here."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.worker = Worker(port)
        self.stopping = False
        self.failures = 0

    def start_once(self) -> tuple[bool, str]:
        ok, message = ensure_mysql()
        log(f'MySQL: {message}')
        if not ok:
            return False, message

        ok, message = database_ready()
        log(f'Database: {message}')
        if not ok:
            return False, message

        taken, why = port_is_taken(self.port)
        if taken:
            log(f'Port: {why}')
            return False, why

        self.worker.start()
        if self.worker.wait_until_ready():
            log(f'TenderRadar is ready at http://{HOST}:{self.port}/')
            return True, 'running'

        output = self.worker.drain()
        self.worker.stop()
        if output:
            log('the server printed:\n' + output)
        return False, ('TenderRadar could not start. Open Logs to see the '
                       'reason.')

    def supervise(self, poll: float = 2.0) -> None:
        """Restart the worker whenever it dies, until asked to stop."""
        while not self.stopping:
            time.sleep(poll)
            if self.stopping or self.worker.is_alive():
                if self.worker.is_alive():
                    self.failures = 0
                continue

            output = self.worker.drain()
            log('the server process stopped unexpectedly')
            if output:
                log('last output:\n' + output)

            delay = RESTART_DELAYS[min(self.failures, len(RESTART_DELAYS) - 1)]
            self.failures += 1
            log(f'restarting it in {delay}s (attempt {self.failures})')
            for _ in range(int(delay * 2)):
                if self.stopping:
                    return
                time.sleep(0.5)
            if self.stopping:
                return

            ok, message = ensure_mysql()
            if not ok:
                log(f'cannot restart yet — {message}')
                continue
            self.worker.start()
            if self.worker.wait_until_ready():
                log('TenderRadar recovered and is serving again')
                self.failures = 0
            else:
                log('the server did not come back on this attempt')
                self.worker.stop()

    def stop(self) -> None:
        self.stopping = True
        self.worker.stop()


# ── Windows service wrapper ──────────────────────────────────────────────────

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    HAVE_PYWIN32 = True
except ImportError:                                    # pragma: no cover
    HAVE_PYWIN32 = False
    win32serviceutil = None


if HAVE_PYWIN32:

    class TenderRadarService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.supervisor = Supervisor()

        def SvcStop(self):
            """Windows asked the service to stop."""
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            log('service stop requested')
            self.supervisor.stop()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            log('=' * 68)
            log(f'service starting (pid {os.getpid()}), application at {ROOT}')
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ''))
            try:
                ok, message = self.supervisor.start_once()
                if not ok:
                    log(f'service could not start: {message}')
                    servicemanager.LogErrorMsg(f'TenderRadar: {message}')
                    # Report a clean stop rather than dying silently; the reason
                    # is in the log and in the Windows event log.
                    self.ReportServiceStatus(win32service.SERVICE_STOPPED)
                    return
                self.ReportServiceStatus(win32service.SERVICE_RUNNING)
                log('service is running')

                import threading
                watcher = threading.Thread(target=self.supervisor.supervise,
                                           daemon=True)
                watcher.start()
                win32event.WaitForSingleObject(self.stop_event,
                                               win32event.INFINITE)
            except Exception as exc:                   # pragma: no cover
                import traceback
                log(f'unexpected service error: {type(exc).__name__}: {exc}')
                log(traceback.format_exc())
                servicemanager.LogErrorMsg(
                    f'TenderRadar service error: {exc}')
            finally:
                self.supervisor.stop()
                log('service stopped')


# ── Command line ─────────────────────────────────────────────────────────────

ADMIN_HELP = (
    'What failed : {action} the TenderRadar service.\n'
    'Why         : Windows only allows services to be {verb} by an '
    'administrator.\n'
    'What to do  : Close this window, right-click Command Prompt (or '
    'TenderRadar Setup)\n'
    '              and choose "Run as administrator", then try again.'
)


def is_elevated() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── Per-user server (no administrator rights) ────────────────────────────────
# Registering a Windows service needs an administrator. On a machine where that
# is not available the same Supervisor runs as an ordinary background process
# owned by the signed-in user, so the tray's Start / Stop / Restart still do
# something real. It is NOT a Windows service and is never described as one:
# it does not survive a sign-out and does not start before the user logs in.

PID_FILE = LOG_DIR / 'local_server.pid'


def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW).stdout
        return str(pid) in out
    except Exception:
        return False


def local_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def local_status() -> str:
    """RUNNING / STOPPED for the per-user server."""
    if local_pid():
        return 'RUNNING'
    return 'STOPPED'


def local_start(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """Start the per-user server in the background. No elevation needed."""
    if local_pid():
        return True, 'TenderRadar is already running for this account.'
    pythonw = worker_python().with_name('pythonw.exe')
    if not pythonw.is_file():
        pythonw = worker_python()
    try:
        proc = subprocess.Popen(
            [str(pythonw), str(Path(__file__).resolve()), 'run',
             '--port', str(port)],
            cwd=str(ROOT), creationflags=CREATE_NO_WINDOW | 0x00000008,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
    except Exception as exc:
        return False, (f'TenderRadar could not be started.\n\nReason: {exc}\n\n'
                       f'Open Logs for the full detail.')
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid), encoding='utf-8')
    log(f'per-user server started (pid {proc.pid}) on port {port}')

    deadline = time.time() + WORKER_READY_TIMEOUT
    url = f'http://{HOST}:{port}/login/'
    while time.time() < deadline:
        if proc.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            return False, ('TenderRadar stopped while starting. Open Logs to '
                           'see the reason.')
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True, 'TenderRadar is running.'
        except urllib.error.HTTPError as exc:
            if exc.code in (200, 302):
                return True, 'TenderRadar is running.'
        except Exception:
            pass
        time.sleep(0.5)
    return False, ('TenderRadar did not become available in time. Open Logs to '
                   'see the reason.')


def local_stop() -> tuple[bool, str]:
    """Stop the per-user server and its worker. No elevation needed."""
    pid = local_pid()
    if not pid:
        PID_FILE.unlink(missing_ok=True)
        return True, 'TenderRadar was not running.'
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       capture_output=True, timeout=60,
                       creationflags=CREATE_NO_WINDOW)
    except Exception as exc:
        return False, f'TenderRadar could not be stopped.\n\nReason: {exc}'
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.5)
    PID_FILE.unlink(missing_ok=True)
    log(f'per-user server stopped (pid {pid})')
    return True, 'TenderRadar stopped.'


def local_restart(port: int = DEFAULT_PORT) -> tuple[bool, str]:
    ok, message = local_stop()
    if not ok:
        return ok, message
    time.sleep(1.0)
    return local_start(port)


def service_status() -> str:
    """RUNNING / STOPPED / NOT_INSTALLED / … — safe to call unelevated."""
    try:
        out = subprocess.run(['sc', 'query', SERVICE_NAME],
                             capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW).stdout.upper()
    except Exception:
        return 'UNKNOWN'
    if 'DOES NOT EXIST' in out or 'FAILED' in out:
        return 'NOT_INSTALLED'
    for state in ('RUNNING', 'START_PENDING', 'STOP_PENDING', 'STOPPED',
                  'PAUSED'):
        if state in out:
            return {'START_PENDING': 'STARTING',
                    'STOP_PENDING': 'STOPPING'}.get(state, state)
    return 'UNKNOWN'


def _privileged(action: str, verb: str, fn) -> int:
    if not is_elevated():
        print(ADMIN_HELP.format(action=action, verb=verb))
        return 5                                   # ERROR_ACCESS_DENIED
    try:
        fn()
        return 0
    except Exception as exc:
        message = str(exc)
        if 'Access is denied' in message or 'access denied' in message.lower():
            print(ADMIN_HELP.format(action=action, verb=verb))
            return 5
        print(f'What failed : {action} the TenderRadar service.\n'
              f'Reason      : {message}\n'
              f'What to do  : Open {LOG_FILE} for the full detail.')
        return 1


def cmd_install() -> int:
    def do():
        win32serviceutil.InstallService(
            f'{Path(__file__).stem}.TenderRadarService',
            SERVICE_NAME, SERVICE_DISPLAY,
            description=SERVICE_DESCRIPTION,
            startType=win32service.SERVICE_AUTO_START,
            exeName=str(worker_python()),
            exeArgs=f'"{Path(__file__).resolve()}"',
        )
        log('service installed, startup type Automatic')
        print('TenderRadar service installed and set to start with Windows.')
    return _privileged('installing', 'installed', do)


def cmd_remove() -> int:
    def do():
        try:
            win32serviceutil.StopService(SERVICE_NAME)
        except Exception:
            pass
        win32serviceutil.RemoveService(SERVICE_NAME)
        log('service removed')
        print('TenderRadar service removed. Your database and data are '
              'untouched.')
    return _privileged('removing', 'removed', do)


def cmd_start() -> int:
    return _privileged('starting', 'started',
                       lambda: win32serviceutil.StartService(SERVICE_NAME))


def cmd_stop() -> int:
    return _privileged('stopping', 'stopped',
                       lambda: win32serviceutil.StopService(SERVICE_NAME))


def cmd_restart() -> int:
    return _privileged('restarting', 'restarted',
                       lambda: win32serviceutil.RestartService(SERVICE_NAME))


def main(argv: list[str]) -> int:
    if not argv:
        # No arguments: Windows is starting us as a service.
        if HAVE_PYWIN32:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(TenderRadarService)
            servicemanager.StartServiceCtrlDispatcher()
            return 0
        print('pywin32 is required to run as a Windows service.')
        return 1

    command = argv[0].lower()
    if not HAVE_PYWIN32 and command != 'status':
        print('pywin32 is not installed, so the service cannot be managed.\n'
              'Install it with:  pip install pywin32')
        return 1

    if command == 'status':
        state = service_status()
        print(f'TenderRadar service: {state}')
        if state == 'NOT_INSTALLED':
            print('It has not been registered yet. Run, as administrator:\n'
                  f'    "{worker_python()}" "{Path(__file__).resolve()}" install')
        return 0
    if command in ('install', 'remove', 'start', 'stop', 'restart'):
        return {'install': cmd_install, 'remove': cmd_remove,
                'start': cmd_start, 'stop': cmd_stop,
                'restart': cmd_restart}[command]()
    if command == 'run':
        # The supervisor without Windows' service layer. Used two ways: to test
        # the service body without registering anything, and as the per-user
        # server on a machine with no administrator rights.
        port = DEFAULT_PORT
        if '--port' in argv:
            port = int(argv[argv.index('--port') + 1])
        sup = Supervisor(port)
        ok, message = sup.start_once()
        if not ok:
            log(f'per-user server could not start: {message}')
            print(message)
            PID_FILE.unlink(missing_ok=True)
            return 1
        print('running; Ctrl+C to stop')
        try:
            sup.supervise()
        except KeyboardInterrupt:
            pass
        finally:
            sup.stop()
            PID_FILE.unlink(missing_ok=True)
        return 0

    if command in ('local-start', 'local-stop', 'local-restart', 'local-status'):
        port = DEFAULT_PORT
        if '--port' in argv:
            port = int(argv[argv.index('--port') + 1])
        if command == 'local-status':
            print(f'TenderRadar (this account): {local_status()}')
            return 0
        fn = {'local-start': local_start, 'local-stop': lambda *_: local_stop(),
              'local-restart': local_restart}[command]
        ok, message = fn(port)
        print(message)
        return 0 if ok else 1

    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
