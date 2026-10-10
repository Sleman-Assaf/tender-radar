"""
TenderRadar_Setup.exe — the installation wizard.

A real Windows installer wizard (Welcome → Licence/Info → Directory →
Prerequisites → Installing → Finish) implemented as a compiled GUI program.

It performs exactly what a conventional installer does:
  * copies the application, the bundled Python runtime and the launcher
  * preserves an existing production.env instead of overwriting it
  * creates Start Menu and Desktop shortcuts
  * registers an uninstaller under Programs and Features (per-user hive, so it
    needs no administrator rights)
  * offers "Launch TenderRadar" on the final page
  * provisions MySQL — see provision_database()

On MySQL, the rule is *install when absent, never modify when present*:

  CASE A  a server is already there -> reuse it. The database is not recreated,
          the initial dump is refused unless the schema is empty, and nothing is
          changed at all unless the user supplies the MySQL root password.
  CASE B  no server at all          -> install the bundled Oracle MSI, initialise
          the data directory, register and start MySQL80. This needs elevation;
          if the user declines, the files are still installed and first_run.py
          finishes the job on the first launch.

Neither path drops, truncates or flushes anything — see mysql_setup.assert_safe().

Inno Setup would normally be used for this. It could not be: this network's
proxy replaces the jrsoftware.org download with an HTML block page, so the
toolchain is unobtainable here. desktop/TenderRadar.iss is provided so the same
package can be rebuilt with Inno Setup on an unrestricted machine — note that
the .iss only *warns* about a missing MySQL, it does not install one, so the two
build paths are not equivalent on a machine without MySQL.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

APP_NAME = 'TenderRadar'
PUBLISHER = 'AJi Group'
VERSION = '1.0.0'
APP_ID = 'TenderRadar'
EXE_NAME = 'TenderRadar.exe'
CREATE_NO_WINDOW = 0x08000000

# Files/folders never shipped to a target machine.
EXCLUDE_DIRS = {
    '.venv', 'venv', '__pycache__', '.git', '.cache', 'build', 'dist',
    'desktop/payload', 'desktop/build', 'desktop/dist', 'desktop/.cache',
    'staticfiles',                     # regenerated / served by WhiteNoise finders
    'templates_backup_20260722_095610',
    'backups',                         # local archives — not another site's data
    'logs',
}
EXCLUDE_FILES = {
    'db.sqlite3', 'db.sqlite3-journal', 'db.sqlite3-wal', 'db.sqlite3-shm',
    'TenderRadar_Setup.exe', 'ssl.zip',
}
# Directories that must exist and be writable after installation.
RUNTIME_DIRS = ['logs', 'backups', 'media', 'media/feedback_attachments',
                'staticfiles']


def payload_dir() -> Path:
    """Where the bundled application tree lives inside this EXE."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS'))
    return Path(__file__).resolve().parent / 'payload'


def default_install_dir() -> Path:
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return Path(base) / 'Programs' / APP_NAME


# ── Prerequisite checks (same logic the launcher uses) ───────────────────────

def service_state(name: str) -> str:
    try:
        out = subprocess.run(['sc', 'query', name], capture_output=True, text=True,
                             timeout=15, creationflags=CREATE_NO_WINDOW).stdout.upper()
    except Exception:
        return 'UNKNOWN'
    if 'DOES NOT EXIST' in out or 'FAILED' in out:
        return 'NOT_INSTALLED'
    return 'RUNNING' if 'RUNNING' in out else ('STOPPED' if 'STOPPED' in out else 'UNKNOWN')


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def find_mysql_tools() -> Path | None:
    for d in (r'C:\Program Files\MySQL\MySQL Server 8.0\bin',
              r'C:\Program Files\MySQL\MySQL Server 8.4\bin'):
        if (Path(d) / 'mysqldump.exe').is_file():
            return Path(d)
    found = shutil.which('mysqldump')
    return Path(found).parent if found else None


def check_prerequisites() -> list[tuple[str, bool, str]]:
    """
    (label, ok, detail). ok=False on the MySQL rows is a warning, not a block.

    A missing MySQL is not a failure: this package carries the Oracle MSI and
    installs it (CASE B). These rows therefore follow the same convention as the
    Visual C++ row — ok=True when the requirement is *satisfiable from the
    package*, with the detail text saying Setup will do it. Telling the user to
    go and install MySQL themselves would be wrong.
    """
    rows = []
    payload = payload_dir()
    msi = (payload / 'mysql' / 'mysql-server.msi').is_file()

    svc = service_state('MySQL80')
    reachable = port_open('127.0.0.1', 3306)
    if svc == 'RUNNING':
        rows.append(('MySQL 8.0 service', True, 'MySQL80 is installed and running'))
    elif svc == 'STOPPED':
        rows.append(('MySQL 8.0 service', False,
                     'MySQL80 is installed but STOPPED — Setup will start it'))
    elif svc == 'NOT_INSTALLED':
        rows.append(('MySQL 8.0 service', reachable or msi,
                     'MySQL80 service not found'
                     + (' — but a server answers on 127.0.0.1:3306' if reachable
                        else ' — Setup will install the bundled MySQL 8.0 '
                             '(asks for administrator approval)' if msi
                        else ' — install MySQL 8.0 or point DB_HOST at your server')))
    else:
        rows.append(('MySQL 8.0 service', reachable or msi, f'service state {svc.lower()}'))

    rows.append(('MySQL reachable', reachable or msi,
                 '127.0.0.1:3306 accepts connections' if reachable
                 else '127.0.0.1:3306 refused — Setup will install and start MySQL'
                      if msi else
                      '127.0.0.1:3306 refused — TenderRadar will not start until MySQL runs'))

    tools = find_mysql_tools()
    rows.append(('mysqldump / mysql', tools is not None or msi,
                 f'found in {tools}' if tools
                 else 'installed with the bundled MySQL — needed by Backup & Restore'
                      if msi else
                      'not found — Backup & Restore needs the MySQL client tools'))

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from desktop import mysql_setup as _M
        vc_present = _M.vcredist_ok()
    except Exception:
        vc_present = False
    rows.append(('Visual C++ runtime',
                 vc_present or (payload / 'mysql' / 'VC_redist.x64.exe').is_file(),
                 'already installed' if vc_present
                 else 'not installed — Setup will install the bundled copy '
                      '(MySQL requires it)'))
    rows.append(('Python runtime (bundled)', (payload / 'runtime' / 'python.exe').is_file(),
                 'included in this installer — no Python needed on this PC'))
    rows.append(('Application files', (payload / 'app' / 'manage.py').is_file(),
                 'included in this installer'))
    return rows


# ── Installation ─────────────────────────────────────────────────────────────

def long_path(p: Path) -> str:
    r"""
    Win32 extended-length form of an absolute path.

    Without this the install fails once install_dir plus the deepest file in the
    project exceeds MAX_PATH (260 characters) — easily reached by a user who
    installs under a long profile or a nested folder. The \\?\ prefix lifts the
    limit and is accepted by the CreateFile family that shutil ultimately calls.
    """
    s = str(p)
    if os.name == 'nt' and not s.startswith('\\\\?\\'):
        if s.startswith('\\\\'):                       # UNC: \\server\share
            return '\\\\?\\UNC\\' + s.lstrip('\\')
        return '\\\\?\\' + s
    return s


def copy_tree(src: Path, dst: Path, progress=None) -> int:
    files = [p for p in src.rglob('*') if p.is_file()]
    total = len(files)
    for i, f in enumerate(files, 1):
        rel = f.relative_to(src)
        target = dst / rel
        os.makedirs(long_path(target.parent), exist_ok=True)
        # Never clobber an existing configuration file.
        if rel.as_posix() in ('production.env', 'config/first_run.log') \
                and target.exists():
            continue
        shutil.copy2(long_path(f), long_path(target))
        if progress and (i % 40 == 0 or i == total):
            progress(i, total, rel.as_posix())
    return total


def make_shortcut(path: Path, target: Path, workdir: Path, icon: Path,
                  description: str) -> bool:
    """Create a .lnk via WScript.Shell — no third-party dependency."""
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');"
        f"$s.TargetPath='{target}';"
        f"$s.WorkingDirectory='{workdir}';"
        f"$s.IconLocation='{icon}';"
        f"$s.Description='{description}';"
        f"$s.Save()"
    )
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0 and path.exists()


def register_uninstaller(install_dir: Path, size_kb: int) -> bool:
    """Programs and Features entry in the per-user hive (no admin needed)."""
    try:
        key_path = rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}'
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_WRITE) as k:
            uninst = install_dir / 'Uninstall.exe'
            vals = {
                'DisplayName': f'{APP_NAME} {VERSION}',
                'DisplayVersion': VERSION,
                'Publisher': PUBLISHER,
                'InstallLocation': str(install_dir),
                'DisplayIcon': str(install_dir / EXE_NAME),
                'UninstallString': f'"{uninst}"',
                'QuietUninstallString': f'"{uninst}" /SILENT',
                'InstallDate': time.strftime('%Y%m%d'),
                'URLInfoAbout': 'https://www.ajigroup.com',
            }
            for name, value in vals.items():
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(k, 'EstimatedSize', 0, winreg.REG_DWORD, size_kb)
            winreg.SetValueEx(k, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, 'NoRepair', 0, winreg.REG_DWORD, 1)
        return True
    except OSError:
        return False


def existing_install() -> Path | None:
    """Detect a previous installation so an upgrade preserves its data."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf'Software\{PUBLISHER}\{APP_NAME}') as k:
            p = Path(winreg.QueryValueEx(k, 'InstallDir')[0])
            return p if (p / EXE_NAME).is_file() else None
    except OSError:
        return None


def stop_running_instance(install_dir: Path, log) -> None:
    """An upgrade must not copy over a running executable."""
    try:
        subprocess.run(['taskkill', '/IM', EXE_NAME, '/F', '/T'],
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
        pattern = str(install_dir / 'runtime') + '\\*'
        ps = ('Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath '
              f'-like "{pattern}" ' + '} | ForEach-Object '
              '{ Stop-Process -Id $_.ProcessId -Force }')
        subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
        log('   stopped the running instance')
        time.sleep(3)
    except Exception:
        pass


def _copy_problem(E, exc: OSError, what: str, install_dir: Path):
    """Turn a file-copy failure into something a non-technical user can act on."""
    err = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
    free_mb = E._free_disk_mb(install_dir)
    if err in (28, 112) or (0 <= free_mb < 120):          # ENOSPC / ERROR_DISK_FULL
        return E.not_enough_disk(REQUIRED_MB, max(0, free_mb), install_dir)
    if err in (5, 32):                                     # denied / in use
        return E.install_step_failed(
            f'Copying {what}',
            f'Windows would not let Setup write one of the files it needs to '
            f'install.',
            why='A file is open in another program, TenderRadar is still '
                'running, or your antivirus is blocking Setup.',
            technical=f'{type(exc).__name__} winerror={err}: {exc}',
            safety=E.DataSafety.NO_DATABASE_YET,
            extra_actions=['Close TenderRadar if it is running, then click '
                           'Retry.',
                           'If your antivirus warned about TenderRadar, allow '
                           'it and try again.'])
    return E.install_step_failed(
        f'Copying {what}',
        'Setup could not copy all of the files it needs.',
        why='The disk may be full, the folder may be protected, or the setup '
            'file itself may be damaged.',
        technical=f'{type(exc).__name__} winerror={err}: {exc}',
        safety=E.DataSafety.NO_DATABASE_YET)


def _db_problem(E, message: str, install_dir: Path):
    """Map provision_database()'s outcome onto the right user-facing problem."""
    low = (message or '').lower()
    if 'administrator' in low or 'elevat' in low:
        return E.needs_admin('install the database service')
    if 'root password was not accepted' in low or 'credential' in low:
        return E.mysql_bad_credentials('root', technical=message)
    if 'not accepting connections' in low or 'could not be started' in low:
        return E.mysql_service_failed(technical=message)
    if 'not installed' in low:
        return E.mysql_not_installed(will_install=False)
    if 'client tools' in low:
        return E.install_step_failed(
            'Database setup',
            'The MySQL command-line tools could not be found, so Setup cannot '
            'prepare the database.',
            why='MySQL is installed in an unusual location, or its installation '
                'is incomplete.',
            technical=message, safety=E.DataSafety.NOT_ATTEMPTED)
    return E.install_step_failed(
        'MySQL installation',
        'Windows could not install or configure the required database service.',
        why=message or 'The database step did not complete.',
        technical=message, safety=E.DataSafety.NOT_ATTEMPTED,
        extra_actions=[f'{APP_NAME} is already installed — you can also click '
                       f'Exit and start it from the shortcut, and it will offer '
                       f'to finish setting up the database.'])


class InstallStepError(Exception):
    """A named installation step failed. Carries a user-facing errors.Problem."""

    def __init__(self, problem):
        super().__init__(problem.title)
        self.problem = problem


def _errors():
    """desktop.errors, whether we are frozen or running from source."""
    if not getattr(sys, 'frozen', False):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from desktop import errors
    return errors


REQUIRED_MB = 700          # app + runtime + staged dataset, with headroom


def incomplete_install(install_dir: Path) -> str:
    """
    Describe an installation that was started but never finished.

    Setup is safe to re-run, so this is informational: it lets the wizard say
    "a previous installation did not finish" instead of silently overwriting and
    leaving the user wondering.
    """
    if not install_dir.is_dir():
        return ''
    markers = {
        'the application files': (install_dir / 'manage.py').is_file(),
        'the Python runtime': (install_dir / 'runtime' / 'python.exe').is_file(),
        'the launcher': (install_dir / 'TenderRadar.exe').is_file(),
    }
    if not any(markers.values()):
        return ''                                   # nothing there at all
    missing = [name for name, present in markers.items() if not present]
    if not missing:
        return ''                                   # complete
    return ('The following parts are missing: ' + ', '.join(missing) + '.')


def install(install_dir: Path, desktop_sc: bool, startmenu_sc: bool, log,
            root_password: str | None = None) -> bool:
    payload = payload_dir()
    E = _errors()

    # ── free space, before anything is written ───────────────────────────────
    free_mb = E._free_disk_mb(install_dir)
    if 0 <= free_mb < REQUIRED_MB:
        raise InstallStepError(E.not_enough_disk(REQUIRED_MB, free_mb, install_dir))
    log(f'Free space on {install_dir.drive or install_dir}: {free_mb:,} MB', ok=True)

    prior = existing_install()
    if prior and prior.resolve() == install_dir.resolve():
        log(f'Upgrading the existing installation at {install_dir}', ok=True)
        log('   production.env, media, backups and generated scrapers are preserved')
        stop_running_instance(install_dir, log)
    elif prior:
        log(f'Another installation exists at {prior} — installing separately')

    unfinished = incomplete_install(install_dir)
    if unfinished:
        log(f'A previous installation did not finish. {unfinished} '
            f'Setup will complete it; nothing will be deleted.', ok=True)

    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        probe = install_dir / '.write_test'
        probe.write_text('x', encoding='utf-8')
        probe.unlink()
    except OSError as exc:
        raise InstallStepError(E.install_step_failed(
            'Preparing the installation folder',
            f'Windows would not let Setup write to:\n        {install_dir}',
            why='The folder is protected, is on a read-only drive, or your '
                'Windows account does not have permission to write there.',
            technical=f'{type(exc).__name__}: {exc}',
            safety=E.DataSafety.NO_DATABASE_YET,
            extra_actions=['Choose a different folder on the previous page — '
                           'somewhere inside your own user folder always works.',
                           'Or run Setup as administrator.'])) from exc

    log(f'Installing to {install_dir}')

    log('Copying application files...')
    try:
        n1 = copy_tree(payload / 'app', install_dir,
                       lambda i, t, name: log(f'   {i}/{t}  {name}', replace=True))
    except OSError as exc:
        raise InstallStepError(_copy_problem(E, exc, 'the application files',
                                            install_dir)) from exc
    log(f'   {n1} application files copied', ok=True)

    log('Copying bundled Python runtime (no Python needed on this PC)...')
    try:
        n2 = copy_tree(payload / 'runtime', install_dir / 'runtime',
                       lambda i, t, name: log(f'   {i}/{t}  runtime\\{name}',
                                              replace=True))
    except OSError as exc:
        raise InstallStepError(_copy_problem(E, exc, 'the Python runtime',
                                            install_dir)) from exc
    log(f'   {n2} runtime files copied', ok=True)

    # First-run provisioning needs the dataset on disk, not only inside Setup.
    src_dump = payload / 'mysql' / 'initial_tenderradar.sql'
    if src_dump.is_file():
        cfg = install_dir / 'config'
        cfg.mkdir(parents=True, exist_ok=True)
        target_dump = cfg / 'initial_tenderradar.sql'
        if not target_dump.exists():
            shutil.copy2(long_path(src_dump), long_path(target_dump))
            log(f'   initial dataset staged ({target_dump.stat().st_size/1048576:.0f} MB)',
                ok=True)

    # An earlier build of this installer shipped production.env.pre-cutover,
    # which contained the build machine's MySQL password and SECRET_KEY. Remove
    # it from any install it reached. This is the only file Setup ever deletes,
    # it is a credential file rather than user data, and the live
    # production.env beside it is untouched.
    for stale in ('production.env.pre-cutover', 'production.env.bak'):
        victim = install_dir / stale
        if victim.is_file():
            try:
                victim.unlink()
                log(f'   removed {stale} (leaked credentials from an older '
                    f'version)', ok=True)
            except OSError:
                log(f'   could not remove {stale} — delete it by hand', ok=False)

    log('Creating writable runtime folders...')
    for d in RUNTIME_DIRS:
        os.makedirs(long_path(install_dir / d), exist_ok=True)
    log(f'   {len(RUNTIME_DIRS)} folders ready', ok=True)

    # Launcher
    src_exe = payload / EXE_NAME
    if src_exe.is_file():
        shutil.copy2(long_path(src_exe), long_path(install_dir / EXE_NAME))
        log(f'   {EXE_NAME} installed', ok=True)
    else:
        log(f'   {EXE_NAME} missing from the package', ok=False)
        return False

    # Uninstaller
    unins = payload / 'Uninstall.exe'
    if unins.is_file():
        shutil.copy2(long_path(unins), long_path(install_dir / 'Uninstall.exe'))
        log('   Uninstall.exe installed', ok=True)

    icon = install_dir / 'desktop' / 'TenderRadar.ico'
    if not icon.is_file():
        icon = install_dir / EXE_NAME

    if startmenu_sc:
        programs = Path(os.environ['APPDATA']) / r'Microsoft\Windows\Start Menu\Programs'
        folder = programs / APP_NAME
        folder.mkdir(parents=True, exist_ok=True)
        a = make_shortcut(folder / f'{APP_NAME}.lnk', install_dir / EXE_NAME,
                          install_dir, icon, f'Start {APP_NAME}')
        b = make_shortcut(folder / f'Uninstall {APP_NAME}.lnk',
                          install_dir / 'Uninstall.exe', install_dir, icon,
                          f'Remove {APP_NAME}')
        log(f'Start Menu shortcut{"s" if b else ""} created', ok=a)

    if desktop_sc:
        dsk = Path(os.path.join(os.environ['USERPROFILE'], 'Desktop'))
        ok = make_shortcut(dsk / f'{APP_NAME}.lnk', install_dir / EXE_NAME,
                           install_dir, icon, f'Start {APP_NAME}')
        log('Desktop shortcut created', ok=ok)

    size_kb = int(sum(f.stat().st_size for f in install_dir.rglob('*')
                      if f.is_file()) / 1024)
    log('Registering in Programs and Features...',
        ok=register_uninstaller(install_dir, size_kb))

    # Record where we installed, so the uninstaller can find it
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                rf'Software\{PUBLISHER}\{APP_NAME}', 0,
                                winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, 'InstallDir', 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(k, 'Version', 0, winreg.REG_SZ, VERSION)
    except OSError:
        pass

    log(f'Installed size: {size_kb/1024:.0f} MB', ok=True)

    # MySQL is configured after the files are in place, so the helper module and
    # the initial dump are available on disk.
    log('Configuring the database...')
    try:
        db_ok, db_msg = provision_database(install_dir, lambda m: log(f'   {m}'),
                                           root_password=root_password)
    except InstallStepError:
        raise
    except Exception as exc:
        raise InstallStepError(E.unexpected('configuring the database', exc,
                                           E.DataSafety.NOT_ATTEMPTED)) from exc
    log(db_msg, ok=db_ok)

    # A database failure does not abandon the installation: the files are already
    # in place and the launcher completes the setup on first run. But the user is
    # told, in plain language, exactly what is still outstanding.
    if not db_ok:
        raise InstallStepError(_db_problem(E, db_msg, install_dir))

    # If the database is not fully configured yet — no root password given, UAC
    # declined, MySQL absent — leave the MySQL installer on disk so the launcher
    # can finish the job on first run instead of dead-ending the user.
    if not (install_dir / 'production.env').is_file():
        src_msi = payload / 'mysql' / 'mysql-server.msi'
        if src_msi.is_file():
            tgt = install_dir / 'config' / 'mysql'
            tgt.mkdir(parents=True, exist_ok=True)
            if not (tgt / 'mysql-server.msi').exists():
                shutil.copy2(long_path(src_msi), long_path(tgt / 'mysql-server.msi'))
            # MySQL's MSI refuses to run without the VC++ 2019+ x64 runtime, so
            # the prerequisite has to travel with it.
            src_vc = payload / 'mysql' / 'VC_redist.x64.exe'
            if src_vc.is_file() and not (tgt / 'VC_redist.x64.exe').exists():
                shutil.copy2(long_path(src_vc), long_path(tgt / 'VC_redist.x64.exe'))
        log('Database setup will be completed the first time you start '
            'TenderRadar', ok=True)

    # ── Windows Service + tray ────────────────────────────────────────────
    install_windows_service(install_dir, log)
    install_tray_autostart(install_dir, log)
    return True


# ── Windows Service and tray registration ────────────────────────────────────

TRAY_RUN_VALUE = 'TenderRadar Tray'


def _runtime_python(install_dir: Path) -> Path:
    for rel in (r'runtime\pythonw.exe', r'runtime\python.exe',
                r'.venv\Scripts\python.exe'):
        p = install_dir / rel
        if p.is_file():
            return p
    return Path(sys.executable)


def install_windows_service(install_dir: Path, log) -> bool:
    """
    Register TenderRadar as an automatic-start Windows service.

    Never fatal. Registering a service requires administrator rights, and this
    installer is deliberately a per-user install that does not demand them — so
    when it is refused, say so in words and carry on. The application still
    works exactly as before via its shortcut; only the "starts with Windows"
    part is missing, and the user is told the one command that adds it.
    """
    script = install_dir / 'desktop' / 'service.py'
    python = install_dir / 'runtime' / 'python.exe'
    if not script.is_file():
        log('   service script not present — skipped', ok=False)
        return False
    if not python.is_file():
        python = _runtime_python(install_dir)

    if not is_elevated():
        log('Windows Service not registered — needs administrator rights', ok=False)
        log(f'   To start TenderRadar with Windows, run this once from an '
            f'administrator Command Prompt:')
        log(f'   "{python}" "{script}" install')
        return False

    try:
        r = subprocess.run([str(python), str(script), 'install'],
                           capture_output=True, text=True, timeout=120,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            detail = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
            log('Windows Service could not be registered', ok=False)
            for line in detail[:3]:
                log(f'   {line}')
            return False
        log('Windows Service registered (starts automatically with Windows)',
            ok=True)
    except Exception as exc:
        log(f'Windows Service could not be registered: {type(exc).__name__}',
            ok=False)
        return False

    try:
        r = subprocess.run([str(python), str(script), 'start'],
                           capture_output=True, text=True, timeout=180,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0:
            log('Windows Service started', ok=True)
        else:
            log('Service registered but did not start — open Logs for the '
                'reason', ok=False)
    except Exception:
        log('Service registered but did not start', ok=False)
    return True


def install_tray_autostart(install_dir: Path, log) -> bool:
    """
    Start the tray application when this user signs in.

    A per-user Run entry, so it needs no administrator rights and follows the
    account that installed TenderRadar. pythonw.exe is used so no console
    window flashes at sign-in.
    """
    script = install_dir / 'desktop' / 'tray.py'
    if not script.is_file():
        return False
    pyw = install_dir / 'runtime' / 'pythonw.exe'
    if not pyw.is_file():
        pyw = _runtime_python(install_dir)
    command = f'"{pyw}" "{script}"'
    try:
        with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Run') as k:
            winreg.SetValueEx(k, TRAY_RUN_VALUE, 0, winreg.REG_SZ, command)
        log('Tray application will start with Windows', ok=True)
        try:
            subprocess.Popen([str(pyw), str(script)], cwd=str(install_dir),
                             creationflags=CREATE_NO_WINDOW)
            log('Tray application started', ok=True)
        except Exception:
            pass
        return True
    except OSError as exc:
        log(f'Tray autostart could not be configured: {exc}', ok=False)
        return False



# ── MySQL provisioning ───────────────────────────────────────────────────────

def provision_database(install_dir: Path, log, root_password: str | None = None,
                       allow_install: bool = True) -> tuple[bool, str]:
    """
    CASE A  MySQL present  -> reuse it, preserve any existing database
    CASE B  MySQL absent   -> install from the bundled Oracle MSI (needs admin)

    Returns (ok, message). Nothing destructive happens on either path: the
    initial dump is imported only into a database that has zero tables.
    """
    sys.path.insert(0, str(install_dir))
    try:
        from desktop import mysql_setup as M
    except Exception as exc:
        return False, f'MySQL helper unavailable: {exc}'

    payload = payload_dir()
    msi = payload / 'mysql' / 'mysql-server.msi'
    dump = payload / 'mysql' / 'initial_tenderradar.sql'

    info = M.detect()
    log(f'MySQL service: {info["service"]}')
    if info['bin_dir']:
        log(f'MySQL tools:   {info["bin_dir"]}')

    # ---- CASE B: no server at all -----------------------------------------
    if info['service'] == 'NOT_INSTALLED' and not info['port_open']:
        if not allow_install:
            return False, ('MySQL 8 is not installed. Re-run Setup and allow it to '
                           'install MySQL, or point DB_HOST at an existing server.')
        if not is_elevated():
            return False, ('MySQL 8 must be installed, which requires administrator '
                           'rights. Right-click Setup and choose "Run as administrator".')
        base = Path(r'C:\Program Files\MySQL\MySQL Server 8.0')
        data = Path(r'C:\ProgramData\MySQL\MySQL Server 8.0\Data')
        if not M.install_server(msi, base, log,
                                vcredist=payload / 'mysql' / 'VC_redist.x64.exe'):
            return False, ('MySQL installation failed — see the installer log.')
        if not M.initialize_datadir(base, data, log):
            return False, 'MySQL data directory could not be initialised.'
        if not M.register_service(base, log):
            return False, 'The MySQL80 service could not be registered.'
        if not M.start_service():
            return False, 'The MySQL80 service could not be started.'
        root_password = root_password or M.generate_password()
        M.secure_root(base / 'bin', root_password, log)
        info = M.detect()

    # ---- service exists but is stopped ------------------------------------
    if info['service'] == 'STOPPED':
        log('MySQL is stopped — starting the service...')
        if not M.start_service():
            return False, ('The MySQL80 service is stopped and could not be started. '
                           'Start it from Services and run Setup again.')
        info = M.detect()

    bd = M.find_bin_dir()
    if not bd:
        return False, 'The MySQL client tools could not be located.'
    if not M.port_open():
        return False, 'MySQL is not accepting connections on 127.0.0.1:3306.'

    if not root_password:
        return True, ('MySQL is present and running. Setup did not change the '
                      'database. Provide the MySQL root password to let Setup '
                      'create the TenderRadar database and application user, or '
                      'configure production.env by hand.')

    if M.server_version(bd, 'root', root_password) is None:
        return False, 'The MySQL root password was not accepted.'

    ok, is_fresh = M.ensure_database(bd, root_password, log)
    if not ok:
        return False, 'The TenderRadar database could not be created or verified.'

    if is_fresh and dump.is_file():
        M.import_initial_dump(bd, root_password, dump, log)
    elif not is_fresh:
        log('Existing TenderRadar data detected — the initial dump was NOT imported')

    try:
        M.load_timezone_tables(bd, root_password, log)
    except Exception as exc:
        log(f'timezone tables skipped ({type(exc).__name__}) — not fatal')

    app_pw = M.generate_password()
    if not M.ensure_app_user(bd, root_password, M.APP_USER, app_pw, log):
        return False, 'The application database user could not be created.'
    if not M.verify_app_connection(bd, M.APP_USER, app_pw, log):
        return False, 'The application database user cannot reach the database.'

    M.write_production_env(install_dir / 'production.env', M.APP_USER, app_pw)
    log('production.env updated with the application credentials (not logged)')
    log(f'NO_PROXY set for internal services: {M.ensure_no_proxy()}')
    return True, 'MySQL is configured and reachable.'


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ── Wizard UI ────────────────────────────────────────────────────────────────

def run_wizard() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    BG, FG, ACCENT, MUTED, PANEL = '#0F0F0D', '#F6F6F3', '#FCD700', '#9c9b90', '#1a1a17'

    root = tk.Tk()
    root.title(f'{APP_NAME} {VERSION} Setup')
    root.geometry('720x520')
    root.resizable(False, False)
    root.configure(bg=BG)
    try:
        ico = payload_dir() / 'app' / 'desktop' / 'TenderRadar.ico'
        if ico.is_file():
            root.iconbitmap(str(ico))
    except Exception:
        pass

    state = {'dir': default_install_dir(), 'desktop': True, 'startmenu': True,
             'page': 0, 'done': False, 'launch': True}

    header = tk.Frame(root, bg=BG); header.pack(fill='x', pady=(18, 6))
    tk.Label(header, text=APP_NAME, bg=BG, fg=FG,
             font=('Segoe UI', 22, 'bold')).pack()
    tk.Label(header, text=f'{PUBLISHER} · Tender Intelligence · Version {VERSION}',
             bg=BG, fg=MUTED, font=('Segoe UI', 9)).pack()

    body = tk.Frame(root, bg=BG); body.pack(fill='both', expand=True, padx=26, pady=10)
    nav = tk.Frame(root, bg=BG); nav.pack(fill='x', padx=26, pady=(0, 18))

    back_btn = tk.Button(nav, text='< Back', bg='#33332e', fg=FG, relief='flat',
                         font=('Segoe UI', 10), padx=16, pady=7, cursor='hand2')
    next_btn = tk.Button(nav, text='Next >', bg=ACCENT, fg=BG, relief='flat',
                         font=('Segoe UI', 10, 'bold'), padx=20, pady=7, cursor='hand2')
    cancel_btn = tk.Button(nav, text='Cancel', bg='#33332e', fg=FG, relief='flat',
                           font=('Segoe UI', 10), padx=16, pady=7, cursor='hand2')
    cancel_btn.pack(side='right'); next_btn.pack(side='right', padx=8)
    back_btn.pack(side='right')

    def clear():
        for w in body.winfo_children():
            w.destroy()

    def h(text):
        tk.Label(body, text=text, bg=BG, fg=ACCENT,
                 font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(0, 8))

    def para(text):
        tk.Label(body, text=text, bg=BG, fg=FG, font=('Segoe UI', 10),
                 justify='left', wraplength=650).pack(anchor='w', pady=3)

    # page 0 — welcome
    def page_welcome():
        clear()
        h(f'Welcome to the {APP_NAME} Setup Wizard')
        para(f'This will install {APP_NAME} {VERSION} on your computer.')
        para('')
        para('TenderRadar monitors government and international procurement portals '
             'across the Middle East and Africa. After installation you start it by '
             'double-clicking the TenderRadar icon — no command prompt, no Python '
             'commands, and no manual server start.')
        para('')
        para('This package includes its own Python runtime, so Python does not need '
             'to be installed on this PC.')
        para('')
        para('MySQL 8.0 is included in this package. If MySQL is already installed '
             'Setup reuses it and your existing database is never modified. If it '
             'is not, Setup installs the bundled copy — that step asks for '
             'administrator approval, and if you decline, TenderRadar completes it '
             'the first time you start it. Setup checks on the next page.')
        back_btn.configure(state='disabled')
        next_btn.configure(text='Next >', state='normal')

    # page 1 — prerequisites
    def page_prereq():
        clear()
        h('Required components')
        para('Setup checked this computer:')
        frame = tk.Frame(body, bg=PANEL); frame.pack(fill='both', expand=True, pady=10)
        rows = check_prerequisites()
        blocking = False
        for label, ok, detail in rows:
            row = tk.Frame(frame, bg=PANEL); row.pack(fill='x', padx=12, pady=5)
            tk.Label(row, text='OK' if ok else '!', bg=PANEL,
                     fg='#1F9D55' if ok else '#C2410C',
                     font=('Consolas', 10, 'bold'), width=3).pack(side='left')
            tk.Label(row, text=label, bg=PANEL, fg=FG, font=('Segoe UI', 10, 'bold'),
                     width=24, anchor='w').pack(side='left')
            tk.Label(row, text=detail, bg=PANEL, fg=MUTED, font=('Segoe UI', 9),
                     anchor='w', wraplength=360, justify='left').pack(side='left')
            if not ok and label in ('Python runtime (bundled)', 'Application files'):
                blocking = True
        if any(not ok for _, ok, _ in rows):
            para('')
            para('Warnings above do not stop installation. Anything Setup can supply '
                 'itself — the Python runtime, the Visual C++ runtime, MySQL 8.0 — it '
                 'installs from this package; anything it cannot finish is completed '
                 'the first time you start TenderRadar.')
        back_btn.configure(state='normal')
        next_btn.configure(text='Next >', state='disabled' if blocking else 'normal')

    # page 2 — directory
    def page_dir():
        clear()
        h('Choose install location')
        para(f'Setup will install {APP_NAME} into the following folder.')
        para('')
        row = tk.Frame(body, bg=BG); row.pack(fill='x', pady=8)
        var = tk.StringVar(value=str(state['dir']))
        entry = tk.Entry(row, textvariable=var, bg=PANEL, fg=FG, relief='flat',
                         font=('Consolas', 10), insertbackground=FG)
        entry.pack(side='left', fill='x', expand=True, ipady=6)

        def browse():
            d = filedialog.askdirectory(initialdir=str(state['dir'].parent))
            if d:
                var.set(str(Path(d) / APP_NAME))

        tk.Button(row, text='Browse...', command=browse, bg='#33332e', fg=FG,
                  relief='flat', font=('Segoe UI', 9), padx=12,
                  cursor='hand2').pack(side='left', padx=(8, 0))

        para('')
        dv = tk.BooleanVar(value=state['desktop'])
        sv = tk.BooleanVar(value=state['startmenu'])
        for text, v in (('Create a Desktop shortcut', dv),
                        ('Create a Start Menu shortcut', sv)):
            tk.Checkbutton(body, text=text, variable=v, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=('Segoe UI', 10), anchor='w').pack(anchor='w')
        para('')
        para('Installation does not require administrator rights when installing into '
             'your user folder. The application folder must stay writable — the Scraper '
             'Factory generates Python modules there at runtime.')

        def commit():
            state['dir'] = Path(var.get()).expanduser()
            state['desktop'] = dv.get()
            state['startmenu'] = sv.get()
        state['commit_dir'] = commit
        back_btn.configure(state='normal')
        next_btn.configure(text='Install', state='normal')

    # page 3 — installing
    def page_install():
        clear()
        h('Installing')
        para(f'Please wait while {APP_NAME} is installed.')
        box = scrolledtext.ScrolledText(body, height=14, bg=PANEL, fg='#d9d8cf',
                                        font=('Consolas', 9), relief='flat', bd=0)
        box.pack(fill='both', expand=True, pady=10)
        box.configure(state='disabled')
        back_btn.configure(state='disabled')
        next_btn.configure(state='disabled')
        cancel_btn.configure(state='disabled')

        last_replace = {'idx': None}

        def log(msg, ok=None, replace=False):
            box.configure(state='normal')
            prefix = {True: '[OK]  ', False: '[FAIL] ', None: '      '}[ok]
            if replace and last_replace['idx']:
                box.delete(last_replace['idx'], 'end-1c')
                box.insert('end', prefix + msg)
            else:
                box.insert('end', ('\n' if box.index('end-1c') != '1.0' else '')
                           + prefix + msg)
                last_replace['idx'] = box.index('end-1c linestart') if replace else None
            box.see('end')
            box.configure(state='disabled')
            root.update_idletasks()

        def work(attempt: int = 1):
            """
            Run the installation. Any failure becomes a plain-language dialog
            with a Retry button rather than a line of red text the user cannot
            interpret. Setup is safe to re-run, so Retry simply starts again.
            """
            if attempt > 1:
                log(f'--- retrying the installation (attempt {attempt}) ---')
            problem = None
            ok = False
            try:
                ok = install(state['dir'], state['desktop'], state['startmenu'],
                             log, root_password=state.get('root_password'))
            except InstallStepError as exc:
                problem = exc.problem
                log(f'{problem.step}: {problem.title}', ok=False)
            except Exception as exc:
                try:
                    problem = _errors().unexpected('installing TenderRadar', exc)
                except Exception:
                    problem = None
                log('The installation could not be completed.', ok=False)

            if problem is not None:
                try:
                    E = _errors()
                    E.log_problem(problem, state['dir'])
                    choice = E.show_problem(problem, state['dir'], parent=root)
                except Exception:
                    messagebox.showerror(
                        'Setup',
                        'Installation could not be completed.\n\n'
                        f'Step that failed:\n    {getattr(problem, "step", "unknown")}\n\n'
                        'Please restart Windows and run the installer again.')
                    choice = 'exit'
                if choice == 'retry':
                    threading.Thread(target=lambda: work(attempt + 1),
                                     daemon=True).start()
                    return
                state['problem'] = problem

            state['done'] = ok
            root.after(600, page_finish)

        threading.Thread(target=work, daemon=True).start()

    # page 4 — finish
    def page_finish():
        clear()
        cancel_btn.configure(state='normal')
        if state['done']:
            h(f'{APP_NAME} has been installed')
            para(f'{APP_NAME} {VERSION} was installed to:')
            tk.Label(body, text=str(state['dir']), bg=BG, fg=ACCENT,
                     font=('Consolas', 10)).pack(anchor='w', pady=6)
            para('')
            para('To start it, double-click the TenderRadar shortcut. The server '
                 'starts automatically in the background and your browser opens '
                 'on it. TenderRadar uses port 8005 when it is free and moves to '
                 'the next free port if another program already has it, so the '
                 'address you see may differ.')
            para('')
            para('MySQL must be running. If it is not, TenderRadar will tell you '
                 'exactly what is missing instead of failing silently.')
            lv = tk.BooleanVar(value=True)
            tk.Checkbutton(body, text=f'Launch {APP_NAME} now', variable=lv, bg=BG,
                           fg=FG, selectcolor=PANEL, activebackground=BG,
                           activeforeground=FG,
                           font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=10)
            state['launch_var'] = lv
        else:
            problem = state.get('problem')
            h('Installation could not be completed')
            if problem is not None:
                para(problem.title)
                tk.Label(body, text='Step that failed:', bg=BG, fg=MUTED,
                         font=('Segoe UI', 9, 'bold')).pack(anchor='w',
                                                            pady=(12, 2))
                tk.Label(body, text=f'    {problem.step}', bg=BG, fg=ACCENT,
                         font=('Segoe UI', 10)).pack(anchor='w')
                tk.Label(body, text='Your data:', bg=BG, fg=MUTED,
                         font=('Segoe UI', 9, 'bold')).pack(anchor='w',
                                                            pady=(12, 2))
                tk.Label(body, text=problem.data_safety_message(), bg=BG, fg=FG,
                         wraplength=600, justify='left',
                         font=('Segoe UI', 10)).pack(anchor='w')
                if problem.actions:
                    tk.Label(body, text='What you can do:', bg=BG, fg=MUTED,
                             font=('Segoe UI', 9, 'bold')).pack(anchor='w',
                                                                pady=(12, 2))
                    for i, a in enumerate(problem.actions, 1):
                        tk.Label(body, text=f'  {i}. {a}', bg=BG, fg=FG,
                                 wraplength=600, justify='left',
                                 font=('Segoe UI', 10)).pack(anchor='w')

                def show_again():
                    try:
                        _errors().show_problem(problem, state['dir'], parent=root)
                    except Exception:
                        pass

                tk.Button(body, text='View Technical Details',
                          command=show_again, bg='#2a2a25', fg=MUTED,
                          relief='flat', padx=12, pady=6, cursor='hand2',
                          font=('Segoe UI', 9)).pack(anchor='w', pady=(14, 0))
            else:
                para('Setup could not finish installing TenderRadar.')
                para('')
                para('What you can do:')
                para('  1. Restart Windows and run the installer again.')
                para('  2. Make sure you have at least 700 MB of free disk space.')
                para('  3. If Windows asks for Administrator permission, click Yes.')
            state['launch_var'] = None
        back_btn.configure(state='disabled')
        next_btn.configure(text='Finish', state='normal')
        cancel_btn.configure(state='disabled')
        state['page'] = 4

    PAGES = [page_welcome, page_prereq, page_dir, page_install, page_finish]

    def go_next():
        p = state['page']
        if p == 2 and 'commit_dir' in state:
            state['commit_dir']()
            try:
                state['dir'].mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror('Setup', f'Cannot use that folder:\n\n{exc}')
                return
        if p == 4:
            lv = state.get('launch_var')
            if lv is not None and lv.get():
                exe = state['dir'] / EXE_NAME
                if exe.is_file():
                    subprocess.Popen([str(exe)], cwd=str(state['dir']),
                                     creationflags=0x00000008)
            root.destroy()
            return
        state['page'] = min(p + 1, 4)
        PAGES[state['page']]()

    def go_back():
        state['page'] = max(state['page'] - 1, 0)
        PAGES[state['page']]()

    def cancel():
        if state['page'] >= 4:
            root.destroy(); return
        if messagebox.askyesno('Setup', f'Cancel {APP_NAME} installation?'):
            root.destroy()

    next_btn.configure(command=go_next)
    back_btn.configure(command=go_back)
    cancel_btn.configure(command=cancel)
    root.protocol('WM_DELETE_WINDOW', cancel)

    page_welcome()
    root.mainloop()
    return 0


if __name__ == '__main__':
    # Silent mode for automated testing: /SILENT /DIR=<path>
    args = ' '.join(sys.argv[1:]).upper()
    if '/SILENT' in args or '/VERYSILENT' in args:
        target = default_install_dir()
        for a in sys.argv[1:]:
            if a.upper().startswith('/DIR='):
                target = Path(a.split('=', 1)[1].strip('"'))
        lines: list[str] = []

        def log(msg, ok=None, replace=False):
            if not replace:
                lines.append(f'{"[OK]  " if ok else "[FAIL]" if ok is False else "      "}{msg}')
        problem = None
        ok = False
        try:
            ok = install(target, desktop_sc=True, startmenu_sc=True, log=log)
        except InstallStepError as exc:
            problem = exc.problem
        except Exception as exc:
            try:
                problem = _errors().unexpected('installing TenderRadar', exc)
            except Exception:
                problem = None
        print('\n'.join(lines))
        if problem is not None:
            # Silent mode has no dialog, so the same explanation goes to stdout
            # and to the log file. Never a bare traceback.
            try:
                _errors().log_problem(problem, target)
            except Exception:
                pass
            print()
            print(problem.user_text())
        print('RESULT', 'SUCCESS' if ok else 'FAILED', target)
        sys.exit(0 if ok else 1)

    try:
        sys.exit(run_wizard())
    except Exception as exc:
        # The wizard itself failed. Still no traceback in the user's face.
        try:
            E = _errors()
            E.show_problem(E.unexpected('running Setup', exc,
                                        E.DataSafety.NOT_ATTEMPTED),
                           default_install_dir())
        except Exception:
            try:
                import tkinter.messagebox as mb
                mb.showerror('TenderRadar Setup',
                             'Setup could not start.\n\n'
                             'Please restart Windows and run the installer '
                             'again. Nothing on your computer was changed.')
            except Exception:
                pass
        sys.exit(1)
