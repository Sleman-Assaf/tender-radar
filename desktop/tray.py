"""
TenderRadar system-tray application.

    pythonw desktop\\tray.py

Sits next to the Windows clock and drives the TenderRadar Windows Service:

    TenderRadar
    ────────────────
    ● Running
    Open TenderRadar
    Restart Service
    Stop Service
    Start Service
    Open Logs
    Open Installation Folder
    ────────────────
    Exit

Two things worth stating plainly, because both are easy to get wrong:

* **Exit closes the tray icon only.** The Windows Service keeps running and
  TenderRadar stays available. Stopping the server is a separate, explicit menu
  item.
* **Only one tray icon can exist.** A named Windows mutex is taken at startup;
  a second copy exits immediately rather than putting a duplicate icon in the
  notification area.

Start/Stop/Restart need administrator rights, because Windows only lets an
administrator control a service. When that is refused the tray says so in words
and offers to open the log — it never fails silently or shows a traceback.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from desktop import service as svc                     # noqa: E402

APP_NAME = 'TenderRadar'
MUTEX_NAME = 'Global\\TenderRadarTrayApplication'
HOST = '127.0.0.1'
PORT = int(os.environ.get('TENDERRADAR_PORT', '8005'))
URL = f'http://{HOST}:{PORT}/'
READY_URL = f'http://{HOST}:{PORT}/login/'
LOG_DIR = ROOT / 'logs'
TRAY_LOG = LOG_DIR / 'tray.log'
CREATE_NO_WINDOW = 0x08000000

POLL_SECONDS = 5
OPEN_WAIT_SECONDS = 60

#: status -> (bullet colour, label). Mirrors the states the service reports.
STATUS_COLOURS = {
    'RUNNING':       ((31, 157, 85), 'Running'),
    'STARTING':      ((168, 122, 0), 'Starting…'),
    'STOPPING':      ((168, 122, 0), 'Stopping…'),
    'RESTARTING':    ((168, 122, 0), 'Restarting…'),
    'STOPPED':       ((118, 117, 106), 'Stopped'),
    'NOT_INSTALLED': ((194, 65, 12), 'Not installed'),
    'UNKNOWN':       ((194, 65, 12), 'Error'),
}


def log(message: str) -> None:
    line = f'{datetime.now():%Y-%m-%d %H:%M:%S}  {message}'
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRAY_LOG, 'a', encoding='utf-8', errors='replace') as fh:
            fh.write(line + '\n')
    except OSError:
        pass


def single_instance() -> bool:
    """True if this is the only tray application running."""
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True                       # cannot tell — do not block
        ERROR_ALREADY_EXISTS = 183
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return False
        globals()['_mutex_handle'] = handle   # keep it alive for the process
        return True
    except Exception:
        return True


def server_responds(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(READY_URL, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 302)
    except Exception:
        return False


def control_mode() -> str:
    """
    'service' when a Windows service is registered, 'local' when it is not.

    Registering a service needs an administrator, which many development
    machines do not have. Rather than leaving Start/Stop/Restart permanently
    broken there, the tray falls back to driving the same supervisor as an
    ordinary per-user background process. That is never described as a service:
    the menu says "TenderRadar", the status says "this account", and nothing
    claims Windows will start it at boot — because it will not.
    """
    return 'local' if svc.service_status() == 'NOT_INSTALLED' else 'service'


def current_status() -> str:
    """What to show on the menu, for whichever mode is in effect."""
    state = svc.service_status()
    if state == 'NOT_INSTALLED':
        # per-user mode: the site answering is the truth, the pid file is a hint
        if server_responds():
            return 'RUNNING'
        return 'RUNNING' if svc.local_status() == 'RUNNING' else 'STOPPED'
    if state == 'RUNNING' and not server_responds():
        return 'STARTING'
    return state


# ── Messages ─────────────────────────────────────────────────────────────────

def notify(icon, title: str, message: str) -> None:
    """Balloon if the platform supports it, message box otherwise."""
    log(f'{title}: {message}')
    try:
        icon.notify(message, title)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, message, f'{APP_NAME} — {title}', 0x40)
    except Exception:
        pass


ADMIN_MESSAGE = (
    'TenderRadar Service could not be {done}.\n\n'
    'Reason: Windows denied permission — only an administrator may '
    '{verb} a service.\n\n'
    'Solution: right-click Command Prompt, choose "Run as administrator", '
    'then run:\n'
    '    "{python}" "{script}" {command}'
)


# ── Icon ─────────────────────────────────────────────────────────────────────

def make_image(status: str):
    """Draw the tray icon: the TenderRadar mark with a status bullet."""
    from PIL import Image, ImageDraw

    colour, _ = STATUS_COLOURS.get(status, STATUS_COLOURS['UNKNOWN'])
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # dark square with the brand yellow radar rings
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=10, fill=(15, 15, 13))
    d.ellipse([16, 16, 48, 48], outline=(252, 215, 0), width=3)
    d.ellipse([26, 26, 38, 38], fill=(252, 215, 0))
    # status bullet, bottom-right
    d.ellipse([size - 24, size - 24, size - 5, size - 5],
              fill=colour, outline=(255, 255, 255), width=2)
    return img


# ── Actions ──────────────────────────────────────────────────────────────────

class Tray:
    def __init__(self):
        self.icon = None
        self.status = 'UNKNOWN'
        self.busy = False

    # -- service control ---------------------------------------------------
    def _run_service_command(self, command: str, done: str, verb: str):
        """
        Ask service.py to do it, in a child process so the tray stays responsive
        and so the elevation check lives in exactly one place.
        """
        python = svc.worker_python()
        script = Path(svc.__file__).resolve()
        try:
            r = subprocess.run([str(python), str(script), command],
                               capture_output=True, text=True, timeout=180,
                               creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return False, f'{APP_NAME} did not respond within 3 minutes.'
        output = ((r.stdout or '') + (r.stderr or '')).strip()
        if r.returncode == 0:
            return True, output
        if r.returncode == 5 or 'administrator' in output.lower():
            return False, ADMIN_MESSAGE.format(
                done=done, verb=verb, python=python, script=script,
                command=command)
        return False, (output or
                       f'{APP_NAME} Service could not be {done}. '
                       f'Open Logs to see the reason.')

    def do_start(self, icon, _item=None):
        self._service_action(icon, 'start', 'started', 'start')

    def do_stop(self, icon, _item=None):
        self._service_action(icon, 'stop', 'stopped', 'stop')

    def do_restart(self, icon, _item=None):
        self._service_action(icon, 'restart', 'restarted', 'restart')

    def _service_action(self, icon, command, done, verb):
        if self.busy:
            return
        self.busy = True
        self.status = {'start': 'STARTING', 'stop': 'STOPPING',
                       'restart': 'RESTARTING'}[command]
        self.refresh()

        def work():
            if control_mode() == 'local':
                # No Windows service registered: drive the per-user server,
                # which needs no administrator rights at all.
                fn = {'start': svc.local_start,
                      'stop': lambda *_a: svc.local_stop(),
                      'restart': svc.local_restart}[command]
                try:
                    ok, message = fn(PORT)
                except Exception as exc:
                    ok, message = False, (
                        f'TenderRadar could not be {done}.\n\n'
                        f'Reason: {type(exc).__name__}: {exc}\n\n'
                        f'Choose "Open Logs" to see the detail.')
                notify(icon, APP_NAME if ok else 'Action failed', message)
                self.busy = False
                self.status = current_status()
                self.refresh()
                return

            ok, message = self._run_service_command(command, done, verb)
            if ok:
                # a service reports "running" before the site answers
                if command in ('start', 'restart'):
                    deadline = time.time() + OPEN_WAIT_SECONDS
                    while time.time() < deadline and not server_responds():
                        time.sleep(1)
                notify(icon, APP_NAME, f'Service {done}.')
            else:
                notify(icon, 'Action failed', message)
            self.busy = False
            self.refresh()

        threading.Thread(target=work, daemon=True).start()

    # -- open the site -----------------------------------------------------
    def do_open(self, icon, _item=None):
        """Only open the browser once the server actually answers."""
        def work():
            if server_responds():
                webbrowser.open(URL)
                log(f'opened {URL}')
                return
            state = current_status()
            if state in ('NOT_INSTALLED', 'STOPPED'):
                notify(icon, 'TenderRadar is not running',
                       'The service is not running. Choose "Start Service" '
                       'first, then open TenderRadar.')
                return
            notify(icon, APP_NAME, 'Waiting for TenderRadar to be ready…')
            deadline = time.time() + OPEN_WAIT_SECONDS
            while time.time() < deadline:
                if server_responds():
                    webbrowser.open(URL)
                    log(f'opened {URL} after waiting')
                    return
                time.sleep(1)
            notify(icon, 'TenderRadar is not responding',
                   f'TenderRadar did not become available within '
                   f'{OPEN_WAIT_SECONDS} seconds. Choose "Open Logs" to see '
                   f'why.')

        threading.Thread(target=work, daemon=True).start()

    # -- folders -----------------------------------------------------------
    def do_open_logs(self, icon, _item=None):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(LOG_DIR))
        except Exception as exc:
            notify(icon, 'Could not open the logs folder',
                   f'{LOG_DIR}\n\n{exc}')

    def do_open_folder(self, icon, _item=None):
        try:
            os.startfile(str(ROOT))
        except Exception as exc:
            notify(icon, 'Could not open the installation folder',
                   f'{ROOT}\n\n{exc}')

    # -- exit --------------------------------------------------------------
    def do_exit(self, icon, _item=None):
        """Closes the tray icon only — the service keeps running."""
        log('tray exited (the service was left running)')
        icon.visible = False
        icon.stop()

    # -- menu / refresh ----------------------------------------------------
    def status_text(self, _item=None) -> str:
        """
        The live status line at the top of the menu.

        Takes the menu item as an argument because pystray calls a callable
        `text` as text(item) — without it the status line raises TypeError and
        renders empty.

        In per-user mode it says so, so nobody reads "Running" as "installed as
        a Windows service and will come back after a reboot".
        """
        _, label = STATUS_COLOURS.get(self.status, STATUS_COLOURS['UNKNOWN'])
        if self.status == 'NOT_INSTALLED':
            return '● Not running'
        where = ' (this account)' if control_mode() == 'local' else ' (service)'
        return f'● {label}{where}'

    def _noun(self, _item=None) -> str:
        return 'TenderRadar' if control_mode() == 'local' else 'Service'

    def label_start(self, _item=None) -> str:
        return f'Start {self._noun()}'

    def label_stop(self, _item=None) -> str:
        return f'Stop {self._noun()}'

    def label_restart(self, _item=None) -> str:
        return f'Restart {self._noun()}'

    def build_menu(self):
        from pystray import Menu, MenuItem

        running = lambda _i=None: self.status == 'RUNNING'          # noqa: E731
        stopped = lambda _i=None: self.status in ('STOPPED', 'UNKNOWN')  # noqa: E731

        return Menu(
            MenuItem(self.status_text, None, enabled=False),
            Menu.SEPARATOR,
            MenuItem('Open TenderRadar', self.do_open, default=True),
            MenuItem(self.label_restart, self.do_restart, enabled=running),
            MenuItem(self.label_stop, self.do_stop, enabled=running),
            MenuItem(self.label_start, self.do_start, enabled=stopped),
            Menu.SEPARATOR,
            MenuItem('Open Logs', self.do_open_logs),
            MenuItem('Open Installation Folder', self.do_open_folder),
            Menu.SEPARATOR,
            MenuItem('Exit (leaves TenderRadar running)', self.do_exit),
        )

    def refresh(self):
        if not self.icon:
            return
        try:
            self.icon.icon = make_image(self.status)
            _, label = STATUS_COLOURS.get(self.status, STATUS_COLOURS['UNKNOWN'])
            self.icon.title = f'{APP_NAME} — {label}'
            self.icon.update_menu()
        except Exception:
            pass

    def poll(self):
        while True:
            if not self.busy:
                new = current_status()
                if new != self.status:
                    log(f'status {self.status} -> {new}')
                    self.status = new
                    self.refresh()
            time.sleep(POLL_SECONDS)

    def run(self) -> int:
        import pystray

        self.status = current_status()
        self.icon = pystray.Icon(
            APP_NAME, make_image(self.status),
            f'{APP_NAME} — {STATUS_COLOURS.get(self.status, ("", "Unknown"))[1]}',
            menu=self.build_menu())
        threading.Thread(target=self.poll, daemon=True).start()
        log(f'tray started (status {self.status})')
        self.icon.run()
        return 0


def main() -> int:
    if not single_instance():
        log('another tray application is already running — exiting')
        return 0
    try:
        import PIL          # noqa: F401
        import pystray      # noqa: F401
    except ImportError:
        message = ('The TenderRadar tray application needs pystray and Pillow.\n'
                   'Install them with:  pip install pystray Pillow')
        print(message)
        try:
            ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
        except Exception:
            pass
        return 1
    return Tray().run()


if __name__ == '__main__':
    sys.exit(main())
