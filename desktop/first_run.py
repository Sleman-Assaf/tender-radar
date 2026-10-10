"""
First-run provisioning, driven by the launcher.

TenderRadar_Setup.exe provisions MySQL during installation whenever it can. It
cannot always: the MySQL *server* install needs administrator rights, and a user
may decline the UAC prompt, or install while disconnected from the database
server. Rather than leave the application permanently broken in that case, the
launcher re-checks on every start and finishes the job on the first successful
run. That is what makes "double-click the icon and it works" true rather than
"true only if setup went perfectly".

Division of labour
------------------
  * unelevated, done in-process by the launcher
        start the MySQL service, create the database, import the initial dump
        into an EMPTY database, create the least-privilege application user,
        load the timezone tables, generate production.env
  * elevated, done by re-launching this program through UAC
        install the bundled MySQL server, write my.ini, initialise the data
        directory, register the MySQL80 service

Safety
------
Nothing here drops, truncates or flushes anything. The dump import is guarded
twice: once by ensure_database() reporting whether the schema was newly created,
and again inside mysql_setup.import_initial_dump(), which re-counts the tables
immediately before writing and refuses if the count is not zero.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop import mysql_setup as M                              # noqa: E402

PLACEHOLDER = '__GENERATED_ON_FIRST_RUN__'
CREATE_NO_WINDOW = 0x08000000

# What the launcher needs to know before it starts Django.
READY = 'ready'                     # configured and the app user can connect
NEEDS_PROVISION = 'needs_provision'  # MySQL is there; database/user/env are not
NEEDS_SERVER = 'needs_server'       # no MySQL server at all
UNREACHABLE = 'unreachable'         # configured, but the server is not answering
SQLITE = 'sqlite'                   # deliberately not using MySQL


def app_root() -> Path:
    """The installed application directory (holds manage.py)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def read_env(path: Path) -> dict:
    cfg = {}
    if not path.is_file():
        return cfg
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


# ── State detection ──────────────────────────────────────────────────────────

def classify(root: Path | None = None) -> tuple[str, dict]:
    """Decide what, if anything, first-run setup has to do.

    Returns (state, info). Cheap: at most one short-lived MySQL connection.
    """
    root = root or app_root()
    env_path = root / 'production.env'
    cfg = read_env(env_path)
    info = {'env_path': env_path, 'cfg': cfg}

    engine = cfg.get('DB_ENGINE') or os.environ.get('DB_ENGINE', '')
    if 'mysql' not in engine:
        # No production.env at all also lands here on a fresh install, but the
        # shipped template does set DB_ENGINE, so treat a missing file as
        # "needs provisioning" rather than silently running on SQLite.
        if not env_path.is_file() and (root / 'production.env.template').is_file():
            return _classify_mysql(root, info, password=None)
        return SQLITE, info

    password = cfg.get('DB_PASSWORD', '')
    if not password or password == PLACEHOLDER:
        return _classify_mysql(root, info, password=None)
    return _classify_mysql(root, info, password=password)


def _classify_mysql(root: Path, info: dict, password: str | None) -> tuple[str, dict]:
    cfg = info['cfg']
    host = cfg.get('DB_HOST', '127.0.0.1')
    port = int(cfg.get('DB_PORT', '3306') or 3306)
    user = cfg.get('DB_USER', M.APP_USER)

    state = M.service_state()
    info['service'] = state
    info['bin_dir'] = M.find_bin_dir()

    if state == 'STOPPED':
        M.start_service()
        info['service'] = M.service_state()

    reachable = M.port_open(host, port)
    info['reachable'] = reachable

    if not reachable:
        # No service and nothing answering means there is no server here, even if
        # client tools are left over from a previous MySQL installation. Saying
        # "not installed" is both more accurate and more useful than "cannot
        # connect", because it leads to a different fix.
        if state == 'NOT_INSTALLED':
            return NEEDS_SERVER, info
        return UNREACHABLE, info

    if password and info['bin_dir']:
        if M.verify_app_connection(info['bin_dir'], user, password, lambda _m: None):
            return READY, info
    elif password:
        # No client tools to test with; let Django be the judge.
        return READY, info

    return NEEDS_PROVISION, info


# ── Elevation ────────────────────────────────────────────────────────────────

def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def bundled_msi(root: Path) -> Path | None:
    """The MySQL installer, staged by Setup when it could not install it."""
    d = root / 'config' / 'mysql'
    if d.is_dir():
        for p in sorted(d.glob('*.msi')):
            return p
    return None


def initial_dump(root: Path) -> Path | None:
    for cand in (root / 'config' / 'initial_tenderradar.sql',
                 root / 'config' / 'mysql' / 'initial_tenderradar.sql'):
        if cand.is_file():
            return cand
    return None


def elevate_for_server_install(root: Path) -> tuple[bool, str]:
    """Re-launch ourselves through UAC to install the MySQL server.

    Windows shows its standard consent dialog. The elevated copy writes its
    progress to config/first_run.log and exits; we wait for it here.
    """
    msi = bundled_msi(root)
    if msi is None:
        return False, ('The MySQL server is not installed and this copy of '
                       'TenderRadar does not include the MySQL installer.\n\n'
                       'Run TenderRadar-Setup.exe again and accept the '
                       'administrator prompt, or ask IT to install MySQL 8.0.')

    exe = sys.executable if getattr(sys, 'frozen', False) else sys.executable
    args = '--provision-mysql' if getattr(sys, 'frozen', False) \
        else f'"{Path(__file__).resolve()}" --provision-mysql'

    marker = root / 'config' / 'first_run.log'
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('starting\n', encoding='utf-8')
    except OSError:
        pass

    rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, args,
                                             str(root), 1)
    if rc <= 32:
        return False, ('Administrator permission is required to install MySQL '
                       'and the request was declined.\n\nRight-click '
                       'TenderRadar and choose "Run as administrator", or ask '
                       'IT to install MySQL 8.0.')

    # Wait for the elevated copy to finish (installing MySQL takes minutes).
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(2)
        try:
            text = marker.read_text(encoding='utf-8')
        except OSError:
            continue
        if 'PROVISION_OK' in text:
            return True, 'MySQL was installed and configured.'
        if 'PROVISION_FAILED' in text:
            tail = '\n'.join(text.strip().splitlines()[-6:])
            return False, f'The MySQL installation did not complete:\n\n{tail}'
    return False, 'The MySQL installation timed out after 15 minutes.'


# ── Provisioning ─────────────────────────────────────────────────────────────

def provision(root: Path, root_password: str, log) -> tuple[bool, str]:
    """Create the database, import the dump if empty, create the app user,
    and write production.env. Never destructive."""
    bin_dir = M.find_bin_dir()
    if bin_dir is None:
        return False, ('The MySQL client tools were not found, so TenderRadar '
                       'cannot set up its database.')

    ok, is_fresh = M.ensure_database(bin_dir, root_password, log)
    if not ok:
        return False, ('Could not create the TenderRadar database. The MySQL '
                       'root password may be wrong.')

    if is_fresh:
        dump = initial_dump(root)
        if dump is not None:
            log('The database is empty — importing the initial TenderRadar data.')
            # import_initial_dump re-verifies emptiness before writing anything.
            M.import_initial_dump(bin_dir, root_password, dump, log)
        else:
            log('The database is empty and no initial dataset is bundled; '
                'Django migrations will build an empty schema.')
    else:
        log('An existing TenderRadar database was found and left untouched.')

    # Important but not fatal. Without these rows MySQL's CONVERT_TZ returns NULL
    # and analytics date-grouping degrades, but the database, the application user
    # and the configuration are all valid — so a failure here must not throw away
    # a working installation. It is reported, not raised.
    try:
        if not M.load_timezone_tables(bin_dir, root_password, log):
            log('Timezone tables could not be loaded. TenderRadar will work; '
                'date grouping in Analytics may be affected.')
    except Exception as exc:
        log(f'Timezone tables skipped ({type(exc).__name__}). TenderRadar will '
            f'work; date grouping in Analytics may be affected.')

    app_pw = M.generate_password(32)
    if not M.ensure_app_user(bin_dir, root_password, M.APP_USER, app_pw, log):
        return False, 'Could not create the TenderRadar database user.'
    if not M.verify_app_connection(bin_dir, M.APP_USER, app_pw, log):
        return False, 'The TenderRadar database user was created but cannot connect.'

    M.write_production_env(root / 'production.env', M.APP_USER, app_pw)
    M.ensure_no_proxy()
    log('Configuration written. TenderRadar is ready.')
    return True, 'Database configured.'


def provision_server_elevated() -> int:
    """The elevated half: install + initialise + register the MySQL service."""
    root = app_root()
    marker = root / 'config' / 'first_run.log'
    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(str(msg))
        try:
            marker.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        except OSError:
            pass

    try:
        if not is_elevated():
            log('PROVISION_FAILED: not running with administrator rights')
            return 1

        msi = bundled_msi(root)
        if msi is None:
            log('PROVISION_FAILED: the bundled MySQL installer is missing')
            return 1

        base = Path(r'C:\Program Files\MySQL\MySQL Server 8.0')
        data = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'MySQL' / 'MySQL Server 8.0' / 'Data'

        if M.service_state() == 'NOT_INSTALLED':
            log('Installing the MySQL server...')
            if not M.install_server(
                    msi, base, log,
                    vcredist=msi.parent / 'VC_redist.x64.exe'):
                log('PROVISION_FAILED: the MySQL server installation failed')
                return 1
            log('Writing my.ini (utf8mb4, STRICT_TRANS_TABLES)...')
            M.write_my_ini(base, data)
            if not data.exists() or not any(data.iterdir()):
                log('Initialising the data directory...')
                if not M.initialize_datadir(base, data, log):
                    log('PROVISION_FAILED: could not initialise the data directory')
                    return 1
            log('Registering the MySQL80 service (automatic start)...')
            if not M.register_service(base, log):
                log('PROVISION_FAILED: could not register the MySQL80 service')
                return 1

        if M.service_state() != 'RUNNING':
            log('Starting the MySQL80 service...')
            if not M.start_service():
                log('PROVISION_FAILED: the MySQL80 service would not start')
                return 1

        bin_dir = M.find_bin_dir() or (base / 'bin')
        root_pw = M.generate_password(28)
        if M.secure_root(bin_dir, root_pw, log):
            cred = root / 'config' / 'mysql_root_password.txt'
            cred.write_text(
                'MySQL administrator (root) password generated for this machine.\n'
                'Store it in your password manager, then DELETE this file.\n'
                'TenderRadar itself does not need it: the application uses the\n'
                'restricted account tenderradar_app.\n\n'
                f'{root_pw}\n', encoding='utf-8')
            subprocess.run(['icacls', str(cred), '/inheritance:r',
                            '/grant:r', f'{os.environ.get("USERNAME", "")}:R'],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
            log(f'Root password saved to {cred.name} — move it to a password '
                f'manager and delete the file.')

        ok, msg = provision(root, root_pw, log)
        log('PROVISION_OK' if ok else f'PROVISION_FAILED: {msg}')
        return 0 if ok else 1
    except Exception as exc:                                  # never hang the parent
        log(f'PROVISION_FAILED: {exc.__class__.__name__}: {exc}')
        return 1


if __name__ == '__main__':
    if '--provision-mysql' in sys.argv:
        sys.exit(provision_server_elevated())
    st, nfo = classify()
    print('state:', st)
    for k, v in nfo.items():
        if k != 'cfg':
            print(f'  {k}: {v}')
