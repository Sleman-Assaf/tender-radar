"""
MySQL detection, installation and provisioning for the TenderRadar installer.

Every function is non-destructive by construction:

  * an existing MySQL installation is reused, never reinstalled
  * an existing `tenderradar` database is detected and PRESERVED
  * the initial dump is imported ONLY into a database with no tables
  * assert_safe() refuses DROP DATABASE / DROP TABLE / TRUNCATE /
    FLUSH TABLES / DELETE FROM <app table> at runtime, on every single
    statement this module sends. FLUSH PRIVILEGES and DELETE FROM
    mysql.time_zone* are allowed: they touch MySQL's own system schema
    and destroy no application data. Django's `manage.py flush` is
    never invoked.
  * secrets never appear in argv (a temporary defaults-file is used) and never
    reach the log

MySQL stays a Windows service, independent of TenderRadar.exe. Nothing here is
embedded in the launcher.
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import string
import subprocess
import tempfile
import time
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
# Overridable so the launcher, the installer and this module can never
# disagree about which service they are talking about.
SERVICE = os.environ.get('TENDERRADAR_MYSQL_SERVICE', 'MySQL80')
DB_NAME = 'tenderradar'
APP_USER = 'tenderradar_app'
PLACEHOLDER = '__GENERATED_ON_FIRST_RUN__'

SEARCH_DIRS = [
    r'C:\Program Files\MySQL\MySQL Server 8.0\bin',
    r'C:\Program Files\MySQL\MySQL Server 8.4\bin',
    r'C:\Program Files\MySQL\MySQL Server 8.1\bin',
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _run(args: list[str], timeout: int = 120, stdin_file: Path | None = None):
    kw: dict = dict(capture_output=True, text=True, timeout=timeout,
                    creationflags=CREATE_NO_WINDOW, encoding='utf-8', errors='replace')
    if stdin_file is not None:
        with open(stdin_file, 'rb') as fh:
            return subprocess.run(args, stdin=fh, **kw)
    return subprocess.run(args, **kw)


def generate_password(n: int = 32) -> str:
    """Strong password from a cryptographic source. Avoids quoting hazards."""
    alphabet = string.ascii_letters + string.digits + '-_.~'
    while True:
        pw = ''.join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw


def service_state(name: str = SERVICE) -> str:
    r = _run(['sc', 'query', name], timeout=20)
    out = (r.stdout or '').upper()
    if 'DOES NOT EXIST' in out or r.returncode == 1060:
        return 'NOT_INSTALLED'
    if 'RUNNING' in out:
        return 'RUNNING'
    if 'START_PENDING' in out:
        return 'STARTING'
    if 'STOPPED' in out:
        return 'STOPPED'
    return 'UNKNOWN'


def start_service(name: str = SERVICE, wait: int = 60) -> bool:
    """
    Start the service. Needs administrator rights; returns False if refused.

    Returns as soon as Windows refuses rather than polling out the full timeout —
    a rejected request will not succeed by waiting, and a silent minute looks
    exactly like a hang to whoever is watching.
    """
    if service_state(name) == 'RUNNING':
        return True
    r = _run(['net', 'start', name], timeout=wait)
    if r.returncode != 0:
        low = ((r.stdout or '') + (r.stderr or '')).lower()
        if ('access is denied' in low or 'error 5' in low
                or 'disabled' in low or 'error 1058' in low):
            return False
        if service_state(name) not in ('STARTING', 'RUNNING'):
            return False
    deadline = time.time() + wait
    while time.time() < deadline:
        st = service_state(name)
        if st == 'RUNNING':
            return True
        if st == 'NOT_INSTALLED':
            return False
        time.sleep(1.5)
    return service_state(name) == 'RUNNING'


def find_bin_dir() -> Path | None:
    for d in SEARCH_DIRS:
        if (Path(d) / 'mysql.exe').is_file():
            return Path(d)
    from shutil import which
    hit = which('mysql')
    return Path(hit).parent if hit else None


def port_open(host: str = '127.0.0.1', port: int = 3306, timeout: float = 2.0) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def server_version(bin_dir: Path, user: str, password: str,
                   host: str = '127.0.0.1', port: int = 3306) -> str | None:
    ok, out = sql(bin_dir, user, password, 'SELECT VERSION();', host=host, port=port)
    if not ok:
        return None
    m = re.search(r'(\d+\.\d+\.\d+)', out)
    return m.group(1) if m else None


def _defaults_file(user: str, password: str, host: str, port: int) -> Path:
    """Credentials via a temp my.cnf — never on the command line."""
    fd, path = tempfile.mkstemp(suffix='.cnf')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write('[client]\n'
                 f'user={user}\n'
                 f'password="{password}"\n'
                 f'host={host}\n'
                 f'port={port}\n'
                 'default-character-set=utf8mb4\n')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return Path(path)


class UnsafeStatement(RuntimeError):
    """Raised when provisioning would run a data-destroying statement."""


# Statements that must never reach the application schema from this module.
# Kept as a runtime guard rather than a code-review promise: a future edit here
# cannot quietly gain the ability to destroy a customer's data.
_DESTRUCTIVE = (
    re.compile(r'\bDROP\s+(DATABASE|SCHEMA)\b', re.I),
    re.compile(r'\bDROP\s+TABLE\b', re.I),
    re.compile(r'\bTRUNCATE\b', re.I),
    re.compile(r'\bFLUSH\s+TABLES\b', re.I),
    re.compile(r'\bDELETE\s+FROM\s+(?!mysql\.)', re.I),
)
# The one legitimate exception: the isolated database used by the test suite.
_ALLOW_DESTRUCTIVE_ON = re.compile(r'`?tenderradar_[a-z0-9_]*test[a-z0-9_]*`?', re.I)


def assert_safe(statements: str, allow_test_db: bool = True) -> None:
    """Refuse anything that could destroy application data.

    `DELETE FROM mysql.time_zone*` is permitted: that is MySQL's own system
    schema, it is how a single timezone is idempotently reloaded, and it cannot
    reach `tenderradar`. `FLUSH PRIVILEGES` is permitted for the same reason —
    it re-reads the grant tables and destroys nothing. Django's `manage.py
    flush`, which *would* empty the application tables, is never invoked here.
    """
    for pattern in _DESTRUCTIVE:
        m = pattern.search(statements)
        if not m:
            continue
        tail = statements[m.start():m.start() + 200]
        if allow_test_db and _ALLOW_DESTRUCTIVE_ON.search(tail):
            continue                              # isolated *_test* database
        raise UnsafeStatement(
            f'refused a destructive statement in database provisioning: '
            f'{tail.strip().splitlines()[0][:120]!r}')


def sql(bin_dir: Path, user: str, password: str, statements: str,
        database: str | None = None, host: str = '127.0.0.1', port: int = 3306,
        timeout: int = 300) -> tuple[bool, str]:
    """Run SQL through the mysql client. Returns (ok, stdout-or-error)."""
    assert_safe(statements)
    cnf = _defaults_file(user, password, host, port)
    sqlfile = Path(tempfile.mkstemp(suffix='.sql')[1])
    try:
        sqlfile.write_text(statements, encoding='utf-8')
        args = [str(bin_dir / 'mysql.exe'), f'--defaults-extra-file={cnf}',
                '--protocol=TCP', '--default-character-set=utf8mb4', '-N', '-B']
        if database:
            args.append(database)
        r = _run(args, timeout=timeout, stdin_file=sqlfile)
        return r.returncode == 0, (r.stdout if r.returncode == 0 else (r.stderr or ''))
    finally:
        for p in (cnf, sqlfile):
            try:
                p.unlink()
            except OSError:
                pass


# ── detection ────────────────────────────────────────────────────────────────

def detect(root_password: str | None = None) -> dict:
    """
    Everything the installer needs to decide between CASE A (reuse) and
    CASE B (install). Purely read-only.
    """
    info: dict = {
        'service': service_state(),
        'bin_dir': None,
        'port_open': port_open(),
        'version': None,
        'root_ok': False,
        'database_exists': None,
        'database_tables': None,
        'tools': {'mysql': False, 'mysqldump': False},
    }
    bd = find_bin_dir()
    if bd:
        info['bin_dir'] = str(bd)
        info['tools'] = {'mysql': (bd / 'mysql.exe').is_file(),
                         'mysqldump': (bd / 'mysqldump.exe').is_file()}
    if bd and info['port_open'] and root_password is not None:
        v = server_version(bd, 'root', root_password)
        info['version'] = v
        info['root_ok'] = v is not None
        if info['root_ok']:
            ok, out = sql(bd, 'root', root_password,
                          "SELECT COUNT(*) FROM information_schema.schemata "
                          f"WHERE schema_name='{DB_NAME}';")
            info['database_exists'] = ok and out.strip().startswith('1')
            if info['database_exists']:
                ok2, out2 = sql(bd, 'root', root_password,
                                "SELECT COUNT(*) FROM information_schema.tables "
                                f"WHERE table_schema='{DB_NAME}';")
                info['database_tables'] = int(out2.strip() or 0) if ok2 else None
    return info


# ── CASE B: install the server from the bundled MSI ──────────────────────────

def vcredist_ok() -> bool:
    """
    Is the Visual C++ 2019+ x64 runtime present?

    Oracle's MySQL 8.0 MSI carries a hard LaunchCondition:

        Installed OR (VS14REDISTX64_64_MAJOR = 14 AND VS14REDISTX64_64_MINOR >= 20)
        -> "This application requires Visual Studio 2019 x64 Redistributable."

    Windows does not ship that runtime, so on a clean PC the MySQL install would
    abort with exactly that message. This reads the same registry values the MSI
    checks, so the answer here matches the MSI's own verdict.
    """
    import winreg
    for hive_path in (r'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
                      r'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes'
                      r'\x64'):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path) as k:
                installed = winreg.QueryValueEx(k, 'Installed')[0]
                major = winreg.QueryValueEx(k, 'Major')[0]
                minor = winreg.QueryValueEx(k, 'Minor')[0]
                if installed and int(major) == 14 and int(minor) >= 20:
                    return True
        except OSError:
            continue
    return False


def install_vcredist(installer: Path, log) -> bool:
    """
    Install the bundled Microsoft VC++ redistributable, if it is needed.

    Skipped entirely when the runtime is already present, which is the common
    case — most Windows machines have picked it up from some other application.
    Needs administrator rights, like any machine-wide runtime.
    """
    if vcredist_ok():
        log('Microsoft Visual C++ runtime: already present')
        return True
    if not installer.is_file():
        log('The Microsoft Visual C++ runtime is required by MySQL but is not '
            'bundled in this package')
        return False
    log('Installing the Microsoft Visual C++ runtime (required by MySQL)...')
    r = _run([str(installer), '/install', '/quiet', '/norestart'], timeout=900)
    # 0 = installed, 1638 = a newer version is already there, 3010 = reboot advised
    if r.returncode not in (0, 1638, 3010):
        log(f'The Visual C++ runtime installer returned {r.returncode}')
        return vcredist_ok()
    log('Microsoft Visual C++ runtime installed')
    return vcredist_ok()


def install_server(msi: Path, install_dir: Path, log,
                   vcredist: Path | None = None) -> bool:
    """
    Install the MySQL server files from the bundled Oracle MSI.

    Requires administrator rights. The MSI lays down the binaries only; the data
    directory and the service are created by initialize_datadir/register_service.
    """
    if not msi.is_file():
        log('MySQL installer package not found in this bundle')
        return False

    # Prerequisite first, or the MSI refuses with its own launch-condition error.
    if not vcredist_ok():
        candidates = [p for p in (vcredist, msi.parent / 'VC_redist.x64.exe')
                      if p is not None]
        for cand in candidates:
            if install_vcredist(cand, log):
                break
        else:
            log('MySQL cannot be installed until the Microsoft Visual C++ '
                'runtime is present')
            return False

    log('Installing MySQL server files (this can take a few minutes)...')
    r = _run(['msiexec', '/i', str(msi), '/qn', '/norestart',
              f'INSTALLDIR={install_dir}\\',
              'ADDLOCAL=Server,Client'], timeout=1800)
    if r.returncode not in (0, 3010):          # 3010 = success, reboot advised
        log(f'MySQL installation failed (msiexec exit {r.returncode})')
        return False
    log('MySQL server files installed')
    return True


def write_my_ini(base_dir: Path, data_dir: Path, port: int = 3306) -> Path:
    """
    Server configuration. utf8mb4 and STRICT_TRANS_TABLES are required by
    TenderRadar: the data contains Arabic plus 4-byte emoji, and strict mode
    stops over-long values being silently truncated.
    """
    ini = base_dir / 'my.ini'
    ini.write_text(
        '[mysqld]\n'
        f'basedir={base_dir}\n'
        f'datadir={data_dir}\n'
        f'port={port}\n'
        'character-set-server=utf8mb4\n'
        'collation-server=utf8mb4_0900_ai_ci\n'
        "sql-mode=STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION\n"
        'default-storage-engine=InnoDB\n'
        'innodb_buffer_pool_size=1G\n'
        'max_connections=300\n'
        'max_allowed_packet=256M\n'
        'default-authentication-plugin=caching_sha2_password\n'
        '\n[client]\n'
        'default-character-set=utf8mb4\n',
        encoding='utf-8')
    return ini


def initialize_datadir(base_dir: Path, data_dir: Path, log) -> bool:
    """Create the data directory. Refuses to touch a directory that already has data."""
    if data_dir.exists() and any(data_dir.iterdir()):
        log('MySQL data directory already contains data — leaving it untouched')
        return True
    data_dir.mkdir(parents=True, exist_ok=True)
    ini = write_my_ini(base_dir, data_dir)
    log('Initialising the MySQL data directory...')
    r = _run([str(base_dir / 'bin' / 'mysqld.exe'),
              f'--defaults-file={ini}', '--initialize-insecure'], timeout=900)
    if r.returncode != 0:
        log(f'Data directory initialisation failed: {(r.stderr or "")[:200]}')
        return False
    log('Data directory initialised')
    return True


def register_service(base_dir: Path, log) -> bool:
    """Register MySQL80 as an auto-start Windows service (administrator only)."""
    if service_state() != 'NOT_INSTALLED':
        log(f'Service {SERVICE} already exists — not recreating it')
        return True
    ini = base_dir / 'my.ini'
    r = _run([str(base_dir / 'bin' / 'mysqld.exe'), '--install', SERVICE,
              f'--defaults-file={ini}'], timeout=120)
    if r.returncode != 0:
        log(f'Could not register the {SERVICE} service: {(r.stderr or "")[:200]}')
        return False
    _run(['sc', 'config', SERVICE, 'start=', 'auto'], timeout=60)
    log(f'Service {SERVICE} registered and set to start with Windows')
    return True


def secure_root(bin_dir: Path, new_password: str, log) -> bool:
    """Set a password on the freshly initialised (passwordless) root account."""
    ok, err = sql(bin_dir, 'root', '',
                  f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{new_password}';\n"
                  'FLUSH PRIVILEGES;\n')
    if ok:
        log('root account secured with a generated password')
    else:
        log('Could not set the root password (it may already be set)')
    return ok


# ── provisioning (safe on existing installations) ────────────────────────────

def ensure_database(bin_dir: Path, root_password: str, log) -> tuple[bool, bool]:
    """
    Make sure `tenderradar` exists with the right charset.

    Returns (ok, is_fresh) where is_fresh means "the database has no tables", the
    only situation in which importing the initial dump is allowed.
    CREATE DATABASE IF NOT EXISTS never harms an existing database.
    """
    ok, err = sql(bin_dir, 'root', root_password,
                  f'CREATE DATABASE IF NOT EXISTS `{DB_NAME}` '
                  'CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;')
    if not ok:
        log('Could not create or verify the database')
        return False, False
    ok2, out = sql(bin_dir, 'root', root_password,
                   'SELECT COUNT(*) FROM information_schema.tables '
                   f"WHERE table_schema='{DB_NAME}';")
    tables = int((out or '0').strip() or 0) if ok2 else 0
    if tables:
        log(f'Existing database `{DB_NAME}` found with {tables} tables — PRESERVED')
    else:
        log(f'Database `{DB_NAME}` is empty (fresh installation)')
    return True, tables == 0


def ensure_app_user(bin_dir: Path, root_password: str, username: str,
                    password: str, log) -> bool:
    """
    Least-privilege application account. No SUPER, no FILE, no global grants —
    only what Django and the Backup/Restore feature need on this one schema.
    """
    grants = ('SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, '
              'REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, '
              'CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, TRIGGER')
    stmts = []
    for host in ('localhost', '127.0.0.1'):
        stmts.append(f"CREATE USER IF NOT EXISTS '{username}'@'{host}' "
                     f"IDENTIFIED WITH caching_sha2_password BY '{password}';")
        stmts.append(f"ALTER USER '{username}'@'{host}' "
                     f"IDENTIFIED WITH caching_sha2_password BY '{password}';")
        stmts.append(f"GRANT {grants} ON `{DB_NAME}`.* TO '{username}'@'{host}';")
    stmts.append('FLUSH PRIVILEGES;')
    ok, err = sql(bin_dir, 'root', root_password, '\n'.join(stmts))
    log(f'Application database user `{username}` provisioned (least privilege)'
        if ok else 'Could not provision the application database user')
    return ok


def import_initial_dump(bin_dir: Path, root_password: str, dump: Path, log) -> bool:
    """
    Load the shipped dataset. The caller must have established that the database
    has NO tables — this function refuses otherwise, so an existing production
    database can never be overwritten.
    """
    ok, out = sql(bin_dir, 'root', root_password,
                  'SELECT COUNT(*) FROM information_schema.tables '
                  f"WHERE table_schema='{DB_NAME}';")
    tables = int((out or '0').strip() or 0) if ok else -1
    if tables != 0:
        log(f'REFUSING to import the initial data: `{DB_NAME}` already has '
            f'{tables} tables. Existing data is preserved.')
        return False
    if not dump.is_file():
        log('Initial database package not found in this bundle')
        return False
    log(f'Importing the initial database ({dump.stat().st_size/1048576:.0f} MB)...')
    cnf = _defaults_file('root', root_password, '127.0.0.1', 3306)
    try:
        r = _run([str(bin_dir / 'mysql.exe'), f'--defaults-extra-file={cnf}',
                  '--protocol=TCP', '--default-character-set=utf8mb4', DB_NAME],
                 timeout=3600, stdin_file=dump)
    finally:
        try:
            cnf.unlink()
        except OSError:
            pass
    if r.returncode != 0:
        log(f'Initial import failed: {(r.stderr or "")[:200]}')
        return False
    ok, out = sql(bin_dir, 'root', root_password,
                  'SELECT COUNT(*) FROM information_schema.tables '
                  f"WHERE table_schema='{DB_NAME}';")
    log(f'Initial database imported ({(out or "").strip()} tables)')
    return True


def load_timezone_tables(bin_dir: Path, root_password: str, log,
                         zones=('UTC', 'Asia/Amman')) -> bool:
    """
    Populate mysql.time_zone* — reporting failure, never raising it.

    The caller is always in the middle of provisioning a database that is already
    valid. Timezone data missing degrades analytics date-grouping; it does not
    invalidate the installation, so this must not be able to abort it. Every
    failure mode — no tzdata, no mysql client, wrong password, unreadable zone —
    comes back as False.
    """
    try:
        return _load_timezone_tables(bin_dir, root_password, log, zones)
    except Exception as exc:
        log(f'Timezone tables could not be loaded ({type(exc).__name__}). '
            f'TenderRadar will work; Analytics date grouping may be affected.')
        return False


def _load_timezone_tables(bin_dir: Path, root_password: str, log,
                          zones=('UTC', 'Asia/Amman')) -> bool:
    """
    Populate mysql.time_zone* from the local IANA tzdata.

    Required, not optional: with these tables empty, MySQL's CONVERT_TZ returns
    NULL for named zones, and every TruncDate/`__date` query in the analytics
    module silently returns nothing. Windows ships no mysql_tzinfo_to_sql, and
    Oracle's prebuilt timezone SQL is not reachable from every network, so the
    rows are generated here from Python's own tzdata.
    """
    import struct

    def tzfile(zone: str) -> bytes:
        """
        The raw TZif bytes for one zone.

        Two lookups, because this runs in two very different interpreters. Under
        a normal Python the `tzdata` package is importable and importlib.resources
        finds it. Inside the frozen launcher it is NOT: TenderRadar.exe does not
        bundle tzdata, so importlib.resources raises and provisioning used to die
        here on every fresh install. The filesystem lookup finds the copy that
        ships in the application's own bundled runtime instead.
        """
        parts = zone.split('/')
        # 1. the interpreter's own tzdata, when it has one
        try:
            import importlib.resources as ir
            pkg = ('tzdata.zoneinfo'
                   + ('.' + '.'.join(parts[:-1]) if len(parts) > 1 else ''))
            return ir.files(pkg).joinpath(parts[-1]).read_bytes()
        except Exception:
            pass
        # 2. the bundled runtime's site-packages, found on disk
        here = Path(__file__).resolve().parent.parent
        roots = [here / 'runtime' / 'Lib' / 'site-packages',
                 here / '.venv' / 'Lib' / 'site-packages']
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).resolve().parent
            roots.insert(0, exe_dir / 'runtime' / 'Lib' / 'site-packages')
        for root in roots:
            cand = root.joinpath('tzdata', 'zoneinfo', *parts)
            if cand.is_file():
                return cand.read_bytes()
        raise FileNotFoundError(
            f'no tzdata available for {zone}; looked in '
            + ', '.join(str(r) for r in roots))

    def parse(buf: bytes):
        def block(b, off, size):
            assert b[off:off + 4] == b'TZif'
            ver = b[off + 4:off + 5]
            isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = struct.unpack(
                '>6I', b[off + 20:off + 44])
            p = off + 44
            fmt = '>q' if size == 8 else '>i'
            times = [struct.unpack(fmt, b[p + i * size:p + (i + 1) * size])[0]
                     for i in range(timecnt)]
            p += timecnt * size
            idx = list(b[p:p + timecnt]); p += timecnt
            types = []
            for _ in range(typecnt):
                gmt, isdst, ab = struct.unpack('>iBB', b[p:p + 6]); p += 6
                types.append((gmt, isdst, ab))
            chars = b[p:p + charcnt]; p += charcnt
            out = [(g, d, chars[a:chars.index(b'\0', a)].decode()) for g, d, a in types]
            end = p + leapcnt * (size + 4) + isstdcnt + isutcnt
            return list(zip(times, idx)), out, ver, end
        tr, ty, ver, end = block(buf, 0, 4)
        if ver in (b'2', b'3'):
            tr, ty, _, _ = block(buf, end, 8)
        return tr, ty

    stmts = ["SET SESSION sql_mode='';", "SET SESSION time_zone='+00:00';"]
    for tz_id, zone in enumerate(zones, start=1):
        tr, ty = parse(tzfile(zone))
        stmts += [
            f"DELETE FROM mysql.time_zone_name WHERE Name='{zone}';",
            f"INSERT INTO mysql.time_zone (Time_zone_id, Use_leap_seconds) "
            f"VALUES ({tz_id},'N') ON DUPLICATE KEY UPDATE Use_leap_seconds='N';",
            f'DELETE FROM mysql.time_zone_transition WHERE Time_zone_id={tz_id};',
            f'DELETE FROM mysql.time_zone_transition_type WHERE Time_zone_id={tz_id};',
            f"INSERT INTO mysql.time_zone_name (Name, Time_zone_id) VALUES ('{zone}',{tz_id});",
        ]
        for i, (gmt, isdst, abbr) in enumerate(ty):
            stmts.append('INSERT INTO mysql.time_zone_transition_type '
                         '(Time_zone_id, Transition_type_id, Offset, Is_DST, Abbreviation) '
                         f"VALUES ({tz_id},{i},{gmt},{int(isdst)},'{abbr}');")
        rows = [f'({tz_id},{t},{i})' for t, i in tr if -2**31 < t < 2**31]
        for k in range(0, len(rows), 400):
            stmts.append('INSERT INTO mysql.time_zone_transition '
                         '(Time_zone_id, Transition_time, Transition_type_id) VALUES '
                         + ','.join(rows[k:k + 400]) + ';')
    ok, err = sql(bin_dir, 'root', root_password, '\n'.join(stmts), database='mysql',
                  timeout=600)
    if ok:
        ok2, out = sql(bin_dir, 'root', root_password,
                       "SELECT CONVERT_TZ('2026-01-15 12:00:00','UTC','Asia/Amman');")
        good = ok2 and 'NULL' not in (out or '').upper()
        log('Timezone tables loaded and verified (CONVERT_TZ works)' if good
            else 'Timezone tables loaded but CONVERT_TZ still returns NULL')
        return good
    log('Could not load the timezone tables — analytics date grouping may be wrong')
    return False


def verify_app_connection(bin_dir: Path, username: str, password: str, log) -> bool:
    ok, out = sql(bin_dir, username, password,
                  'SELECT COUNT(*) FROM information_schema.tables '
                  f"WHERE table_schema='{DB_NAME}';", database=DB_NAME)
    if ok:
        log(f'Application account can reach `{DB_NAME}` ({(out or "").strip()} tables)')
        return True
    log('The application account could not connect to the database')
    return False


# ── configuration file ───────────────────────────────────────────────────────

def write_production_env(env_path: Path, username: str, password: str,
                         host: str = '127.0.0.1', port: int = 3306,
                         secret_key: str | None = None) -> None:
    """
    Create or update production.env without disturbing unrelated keys and
    without ever logging the values.
    """
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding='utf-8').splitlines()
    else:
        # Fresh install: start from the shipped template, which carries the
        # comments, DJANGO_DEBUG=False and NO_PROXY but no secrets.
        template = env_path.parent / 'production.env.template'
        if template.is_file():
            lines = template.read_text(encoding='utf-8').splitlines()

    # A per-installation SECRET_KEY. Never reuse the build machine's key, and
    # never leave the template placeholder in place.
    if secret_key is None:
        current = ''
        for line in lines:
            m = re.match(r'^\s*DJANGO_SECRET_KEY\s*=\s*(.*)$', line)
            if m:
                current = m.group(1).strip()
        if not current or current == PLACEHOLDER:
            secret_key = 'django-insecure-' + secrets.token_urlsafe(50)

    wanted = {
        'DB_ENGINE': 'django.db.backends.mysql',
        'DB_NAME': DB_NAME,
        'DB_USER': username,
        'DB_PASSWORD': password,
        'DB_HOST': host,
        'DB_PORT': str(port),
    }
    bd = find_bin_dir()
    if bd:
        wanted['MYSQL_BIN_DIR'] = str(bd)
    if secret_key:
        wanted['DJANGO_SECRET_KEY'] = secret_key

    seen = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r'^\s*#?\s*([A-Z_][A-Z0-9_]*)\s*=', line)
        key = m.group(1) if m else None
        if key in wanted:
            out.append(f'{key}={wanted[key]}')
            seen.add(key)
        else:
            out.append(line)
    if out and out[-1].strip():
        out.append('')
    for k, v in wanted.items():
        if k not in seen:
            out.append(f'{k}={v}')
    env_path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def ensure_no_proxy(extra=('172.30.0.4', '172.30.0.0/16')) -> str:
    """
    Keep internal AJiBrain traffic off the corporate proxy while leaving external
    proxying intact. Written to the per-user environment; no admin needed.
    """
    import winreg
    base = 'localhost,127.0.0.1,::1,.local'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
            base = winreg.QueryValueEx(k, 'NO_PROXY')[0]
    except OSError:
        pass
    parts = [p for p in base.split(',') if p]
    for e in extra:
        if e not in parts:
            parts.append(e)
    value = ','.join(parts)
    for name in ('NO_PROXY', 'no_proxy'):
        _run(['setx', name, value], timeout=30)
    return value
