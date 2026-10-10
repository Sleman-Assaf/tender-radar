# tenders/backup.py
"""
Full-system backup & restore for TenderRadar.

A backup is a single .zip under settings.BACKUP_ROOT containing up to five
parts, each optional:

    manifest.json        what this archive holds, who made it, when
    database/            a consistent copy of the database
    media/               everything under MEDIA_ROOT (uploaded files)
    config/              production.env, requirements.txt, web.config
    code/                the application source tree (see CODE_EXCLUDE_*)
    docs/                docs/ — developer documentation + user guide PDFs

The database part is captured with whatever is native to the engine in use:

    SQLite   SQLite's own online-backup API, so a backup taken while the site
             is serving traffic is consistent and does not trip Windows locks.
    MySQL    mysqldump --single-transaction, which is likewise consistent and
             non-blocking on InnoDB; restore feeds the dump back through mysql.
    other    `dumpdata` JSON as a last resort.

Every entry point returns model rows (SystemBackup / BackupRestore) so the
System Dashboard has a full history without the views knowing any of this.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import connections
from django.utils import timezone

if TYPE_CHECKING:                       # models are imported lazily at runtime
    from .models import BackupRestore, SystemBackup

logger = logging.getLogger('tenders')

#: Files treated as "system configuration". Paths are relative to BASE_DIR and
#: are never allowed to escape it.
CONFIG_FILES = ['production.env', 'requirements.txt', 'web.config']

#: Directory names never included in the `code/` part, wherever they appear.
#: `backups` matters twice over: BACKUP_ROOT usually lives inside BASE_DIR, and
#: archiving earlier archives would make every backup swallow all previous ones.
CODE_EXCLUDE_DIRS = {
    '.venv', 'venv', '__pycache__', '.git', '.idea', '.claude', 'node_modules',
    'logs', 'media', 'staticfiles', 'backups', 'snapshots', 'staging',
}

#: File names / suffixes never included in the `code/` part. The database and
#: media have their own parts; compiled and log files are regenerated.
CODE_EXCLUDE_SUFFIXES = ('.pyc', '.pyo', '.log', '.sqlite3', '.sqlite3-journal',
                         '.sqlite3-wal', '.sqlite3-shm', '.zip', '.bak')

DB_MEMBER_SQLITE = 'database/db.sqlite3'
DB_MEMBER_MYSQL  = 'database/dump.sql'
DB_MEMBER_JSON   = 'database/dumpdata.json'

#: Where to look for mysqldump/mysql when they are not on PATH. Override with
#: the MYSQL_BIN_DIR environment variable (see settings.MYSQL_BIN_DIR).
_MYSQL_FALLBACK_DIRS = [
    r'C:\Program Files\MySQL\MySQL Server 8.0\bin',
    r'C:\Program Files\MySQL\MySQL Server 8.4\bin',
    '/usr/bin', '/usr/local/bin', '/usr/local/mysql/bin',
]


# ── Paths ─────────────────────────────────────────────────────────────────────

def backup_root() -> Path:
    root = Path(getattr(settings, 'BACKUP_ROOT', Path(settings.BASE_DIR) / 'backups'))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_sqlite(alias: str = 'default') -> bool:
    return 'sqlite' in settings.DATABASES[alias]['ENGINE']


def _is_mysql(alias: str = 'default') -> bool:
    return 'mysql' in settings.DATABASES[alias]['ENGINE']


def _db_path(alias: str = 'default') -> Path:
    return Path(str(settings.DATABASES[alias]['NAME']))


# ── MySQL helpers ─────────────────────────────────────────────────────────────

def mysql_tool(name: str) -> str | None:
    """
    Absolute path to a MySQL client binary (`mysqldump` / `mysql`).

    Looks at settings.MYSQL_BIN_DIR first, then PATH, then the usual install
    locations. Returns None when the tool is not installed, so callers can fail
    with a clear message instead of a confusing OSError.
    """
    exe = f'{name}.exe' if os.name == 'nt' else name

    configured = str(getattr(settings, 'MYSQL_BIN_DIR', '') or '').strip()
    if configured:
        candidate = Path(configured) / exe
        if candidate.is_file():
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    for d in _MYSQL_FALLBACK_DIRS:
        candidate = Path(d) / exe
        if candidate.is_file():
            return str(candidate)
    return None


def _mysql_defaults_file(alias: str = 'default') -> str:
    """
    Write a throwaway my.cnf holding the credentials.

    Passing --password= on the command line would expose it in the process list
    to every user on the machine; a defaults file avoids that. The caller must
    delete the file (see _run_mysql_tool).
    """
    cfg = settings.DATABASES[alias]
    fd, path = tempfile.mkstemp(suffix='.cnf')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(
            '[client]\n'
            f'user={cfg.get("USER", "")}\n'
            f'password="{cfg.get("PASSWORD", "")}"\n'
            f'host={cfg.get("HOST") or "127.0.0.1"}\n'
            f'port={cfg.get("PORT") or 3306}\n'
            'default-character-set=utf8mb4\n'
        )
    try:                                   # best-effort lock-down on POSIX
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _run_mysql_tool(tool: str, args: list[str], *, stdout=None, stdin=None,
                    alias: str = 'default') -> None:
    """Run mysqldump/mysql with credentials supplied through a defaults file."""
    binary = mysql_tool(tool)
    if not binary:
        raise RuntimeError(
            f'{tool} was not found. Install the MySQL client tools or set '
            f'MYSQL_BIN_DIR to the folder containing {tool}.'
        )
    cnf = _mysql_defaults_file(alias)
    try:
        proc = subprocess.run(
            [binary, f'--defaults-extra-file={cnf}', *args],
            stdout=stdout, stdin=stdin, stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b'').decode('utf-8', 'replace').strip()
            raise RuntimeError(f'{tool} failed (exit {proc.returncode}): {err[:500]}')
        # mysqldump writes warnings to stderr even on success — surface them.
        warn = (proc.stderr or b'').decode('utf-8', 'replace').strip()
        if warn:
            logger.warning(f'[backup] {tool}: {warn[:300]}')
    finally:
        try:
            os.remove(cnf)
        except OSError:
            pass


def _dump_mysql(dest: Path, alias: str = 'default') -> None:
    """
    Consistent logical dump of the MySQL database into `dest`.

    --single-transaction gives InnoDB a consistent snapshot without locking the
    tables, so this is safe to run while the site is serving traffic.
    --no-tablespaces avoids requiring the global PROCESS privilege, which the
    application's own database account does not need to hold.
    """
    cfg = settings.DATABASES[alias]
    with open(dest, 'wb') as out:
        _run_mysql_tool('mysqldump', [
            '--single-transaction',
            '--no-tablespaces',
            '--routines', '--triggers', '--events',
            '--add-drop-table',
            '--hex-blob',
            '--default-character-set=utf8mb4',
            '--set-charset',
            cfg['NAME'],
        ], stdout=out, alias=alias)


def _restore_mysql(zf: zipfile.ZipFile, alias: str = 'default') -> None:
    """Feed the archived SQL dump back through the mysql client."""
    cfg = settings.DATABASES[alias]
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / 'dump.sql'
        with zf.open(DB_MEMBER_MYSQL) as src, open(staged, 'wb') as out:
            shutil.copyfileobj(src, out)

        connections.close_all()
        with open(staged, 'rb') as fh:
            _run_mysql_tool('mysql', [
                '--default-character-set=utf8mb4',
                cfg['NAME'],
            ], stdin=fh, alias=alias)
        connections.close_all()


def disk_usage() -> dict:
    """Total size and count of the archives currently on disk."""
    total = count = 0
    for f in backup_root().glob('*.zip'):
        try:
            total += f.stat().st_size
            count += 1
        except OSError:
            continue
    return {'count': count, 'bytes': total}


# ── Archive helpers ───────────────────────────────────────────────────────────

def _dump_sqlite(dest: Path) -> None:
    """Consistent online copy of the live SQLite database into `dest`."""
    connections.close_all()
    src = sqlite3.connect(str(_db_path()))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _dump_json(dest: Path) -> None:
    """Last-resort fallback for engines with no native dump tool."""
    from django.core.management import call_command
    with open(dest, 'w', encoding='utf-8') as fh:
        call_command(
            'dumpdata',
            exclude=['contenttypes', 'auth.permission', 'sessions.session'],
            natural_foreign=True, natural_primary=True,
            stdout=fh,
        )


def _add_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str) -> int:
    """Add every file under `root` to the archive. Returns the file count."""
    if not root.exists():
        return 0
    added = 0
    for path in root.rglob('*'):
        if path.is_file():
            zf.write(path, f'{arc_prefix}/{path.relative_to(root).as_posix()}')
            added += 1
    return added


def _add_code_tree(zf: zipfile.ZipFile) -> int:
    """
    Add the application source tree under `code/`. Returns the file count.

    Prefix-prunes excluded directories (rather than filtering files afterwards)
    so a .venv of tens of thousands of files is never even walked. Backup and
    media roots are also excluded by resolved path, in case they are configured
    to live inside BASE_DIR under different names.
    """
    base = Path(settings.BASE_DIR).resolve()
    skip_roots = set()
    for special in (backup_root(), Path(settings.MEDIA_ROOT)):
        try:
            skip_roots.add(special.resolve())
        except OSError:
            pass
    # templates_backup_* snapshots are working copies, not source.
    added = 0

    def walk(folder: Path):
        nonlocal added
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            return
        for path in entries:
            name = path.name
            if path.is_dir():
                if (name in CODE_EXCLUDE_DIRS or name.startswith('templates_backup')
                        or path.resolve() in skip_roots):
                    continue
                walk(path)
            elif path.is_file():
                if name.lower().endswith(CODE_EXCLUDE_SUFFIXES):
                    continue
                try:
                    zf.write(path, f'code/{path.relative_to(base).as_posix()}')
                    added += 1
                except OSError as exc:                    # a locked file must not kill the backup
                    logger.warning(f'[backup] skipped unreadable {path}: {exc}')

    walk(base)
    return added


def _add_docs_tree(zf: zipfile.ZipFile) -> int:
    """Add docs/ (developer documentation, user guide, architecture notes)."""
    return _add_tree(zf, Path(settings.BASE_DIR) / 'docs', 'docs')


def _safe_extract(zf: zipfile.ZipFile, member: str, dest_dir: Path,
                  rel_path: str | None = None) -> Path:
    """
    Extract archive entry `member` to `dest_dir/rel_path`, refusing absolute
    paths and `..` traversal (zip-slip). Returns the extracted path.
    """
    dest_dir = dest_dir.resolve()
    target   = (dest_dir / (rel_path or member)).resolve()
    if not str(target).startswith(str(dest_dir)):
        raise ValueError(f'Unsafe path in archive: {member}')
    target.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(target, 'wb') as out:
        shutil.copyfileobj(src, out)
    return target


# ── Create ────────────────────────────────────────────────────────────────────

def create_backup(user=None, *, trigger=None, include_database=True,
                  include_media=True, include_config=True,
                  include_code=False, include_docs=False, note='',
                  prune=True) -> 'SystemBackup':
    """
    Build one archive and return its SystemBackup row.

    Never raises: a failure is recorded on the row with status=FAILED so the
    dashboard can show what went wrong.

    On success the folder is trimmed to the configured retention limit, so
    manual and scheduled backups are held to the same ceiling — pruning used to
    happen only in the scheduled task, which let manual archives pile up
    without limit. Pass prune=False for the safety copy taken before a restore:
    that one exists precisely because the operator is about to change the
    system, and it must not trigger deletions of its own.
    """
    from .models import SystemBackup

    trigger = trigger or SystemBackup.Trigger.MANUAL
    if not (include_database or include_media or include_config
            or include_code or include_docs):
        include_database = True

    # Second-granularity stamps collide when two backups run back to back
    # (e.g. the safety copy taken immediately before a restore), and a
    # collision would silently overwrite the earlier archive.
    #
    # A free name means free on disk *and* unknown to the catalogue. Checking
    # only the disk let a name be reused once pruning had deleted its archive,
    # which pointed two rows at one path — and pruning the older of those rows
    # would then delete the newer backup's file.
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
    root  = backup_root()

    def _taken(name: str) -> bool:
        return ((root / name).exists()
                or SystemBackup.objects.filter(filename=name).exists())

    filename = f'tenderradar_backup_{stamp}.zip'
    suffix   = 1
    while _taken(filename):
        suffix += 1
        filename = f'tenderradar_backup_{stamp}_{suffix}.zip'
    path = root / filename

    record = SystemBackup.objects.create(
        filename=filename,
        file_path=str(path),
        status=SystemBackup.Status.RUNNING,
        trigger=trigger,
        created_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
        includes_database=include_database,
        includes_media=include_media,
        includes_config=include_config,
        includes_code=include_code,
        includes_docs=include_docs,
        note=note[:255],
    )

    try:
        manifest = {
            'created_at':  timezone.now().isoformat(),
            'created_by':  getattr(user, 'username', 'system'),
            'trigger':     trigger,
            'app':         'TenderRadar',
            'db_engine':   settings.DATABASES['default']['ENGINE'],
            'contents':    [],
            'media_files': 0,
        }

        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            if include_database:
                with tempfile.TemporaryDirectory() as tmp:
                    if _is_sqlite():
                        snap = Path(tmp) / 'db.sqlite3'
                        _dump_sqlite(snap)
                        zf.write(snap, DB_MEMBER_SQLITE)
                        manifest['contents'].append('database:sqlite')
                    elif _is_mysql():
                        snap = Path(tmp) / 'dump.sql'
                        _dump_mysql(snap)
                        zf.write(snap, DB_MEMBER_MYSQL)
                        manifest['contents'].append('database:mysql')
                        manifest['db_name'] = settings.DATABASES['default']['NAME']
                    else:
                        snap = Path(tmp) / 'dumpdata.json'
                        _dump_json(snap)
                        zf.write(snap, DB_MEMBER_JSON)
                        manifest['contents'].append('database:json')

            if include_media:
                count = _add_tree(zf, Path(settings.MEDIA_ROOT), 'media')
                manifest['media_files'] = count
                manifest['contents'].append('media')

            if include_config:
                base = Path(settings.BASE_DIR)
                for name in CONFIG_FILES:
                    src = base / name
                    if src.is_file():
                        zf.write(src, f'config/{name}')
                manifest['contents'].append('config')

            if include_code:
                manifest['code_files'] = _add_code_tree(zf)
                manifest['contents'].append('code')

            if include_docs:
                manifest['doc_files'] = _add_docs_tree(zf)
                manifest['contents'].append('docs')

            zf.writestr('manifest.json', json.dumps(manifest, indent=2))

            # A plain, human-readable companion to manifest.json: project name
            # and date up front, so someone looking at the extracted files (or
            # a stack of archives) can identify the backup without a JSON reader.
            contents_label = ', '.join(c.split(':')[0].title()
                                       for c in manifest['contents']) or '—'
            local_now = timezone.localtime()
            version_txt = (
                'TenderRadar Backup\n'
                '==================\n'
                f'Project : TenderRadar\n'
                f'Date    : {local_now:%Y-%m-%d %H:%M:%S} ({settings.TIME_ZONE})\n'
                f'Created : {manifest["created_by"]}\n'
                f'Trigger : {dict(SystemBackup.Trigger.choices).get(trigger, trigger)}\n'
                f'Contents: {contents_label}\n'
                f'Archive : {filename}\n'
            )
            zf.writestr('version.txt', version_txt)

        record.size_bytes  = path.stat().st_size
        record.status      = SystemBackup.Status.SUCCESS
        record.finished_at = timezone.now()
        record.save(update_fields=['size_bytes', 'status', 'finished_at'])
        logger.info(f'[backup] created {filename} ({record.size_display})')

        if prune:
            # Trim inside the success branch: a failed run must never delete a
            # good archive to make room for one that does not exist.
            prune_backups(retention_limit())

    except Exception as exc:
        logger.exception('[backup] failed')
        record.status        = SystemBackup.Status.FAILED
        record.error_message = str(exc)
        record.finished_at   = timezone.now()
        record.save(update_fields=['status', 'error_message', 'finished_at'])
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    return record


# ── Restore ───────────────────────────────────────────────────────────────────

def restore_backup(backup, user=None, *, restore_database=True,
                   restore_media=True, restore_config=False,
                   restore_code=False, restore_docs=False) -> 'BackupRestore':
    """
    Restore `backup` in place.

    A safety backup of the current state is always taken first, so a restore is
    itself reversible. Returns the BackupRestore audit row; inspect `.status`
    and `.error_message` for the outcome.

    The running process keeps serving from cached state for some things
    (e.g. anything read at start-up) — the caller should tell the operator to
    restart the app server afterwards.
    """
    from .models import BackupRestore, SystemBackup

    # The safety copy must cover at least everything about to be overwritten —
    # code and docs are included whenever this restore touches them, so a code
    # restore is reversible the same way a database restore is.
    safety = create_backup(
        user, trigger=SystemBackup.Trigger.PRE_RESTORE,
        include_database=True, include_media=True, include_config=True,
        include_code=restore_code, include_docs=restore_docs,
        note=f'Automatic safety copy before restoring {backup.filename}',
        prune=False,
    )

    # Restoring the database replaces every table, including this catalogue —
    # so identify the rows by filename now and re-resolve them afterwards.
    backup_filename = backup.filename
    safety_filename = safety.filename if safety.status == SystemBackup.Status.SUCCESS else None
    actor_username  = getattr(user, 'username', None) if (
        user and getattr(user, 'is_authenticated', False)) else None

    audit = BackupRestore()
    restored_db = False

    try:
        if not backup.exists_on_disk:
            raise FileNotFoundError(f'Archive missing on disk: {backup.file_path}')

        with zipfile.ZipFile(backup.file_path) as zf:
            names = set(zf.namelist())

            if restore_database:
                # Refuse to cross engines: a SQLite file cannot be poured into
                # MySQL (or the reverse) and silently doing so would corrupt the
                # live database.
                if DB_MEMBER_MYSQL in names:
                    if not _is_mysql():
                        raise ValueError(
                            'This archive holds a MySQL dump but the application '
                            'is not running on MySQL. Restore it on a MySQL install.'
                        )
                    _restore_mysql(zf)
                elif DB_MEMBER_SQLITE in names:
                    if not _is_sqlite():
                        raise ValueError(
                            'This archive holds a SQLite database but the '
                            'application is not running on SQLite. Restore it on '
                            'a SQLite install.'
                        )
                    _restore_sqlite(zf)
                elif DB_MEMBER_JSON in names:
                    _restore_json(zf)
                else:
                    raise ValueError('This archive contains no database.')
                audit.restored_database = restored_db = True

            if restore_media:
                media_members = [n for n in names if n.startswith('media/') and not n.endswith('/')]
                if media_members:
                    dest = Path(settings.MEDIA_ROOT)
                    dest.mkdir(parents=True, exist_ok=True)
                    for name in media_members:
                        _safe_extract(zf, name, dest, rel_path=name[len('media/'):])
                    audit.restored_media = True

            if restore_config:
                base = Path(settings.BASE_DIR)
                for name in [n for n in names if n.startswith('config/') and not n.endswith('/')]:
                    leaf = Path(name).name
                    if leaf not in CONFIG_FILES:
                        continue                       # only known config files
                    live = base / leaf
                    if live.is_file():
                        shutil.copy2(live, base / f'{leaf}.pre-restore')
                    with zf.open(name) as src, open(live, 'wb') as out:
                        shutil.copyfileobj(src, out)
                    audit.restored_config = True

            if restore_code:
                # The safety copy above already archived the current tree.
                # Files are written in place; the running process keeps
                # executing the old code until restarted — the caller must say
                # so to the operator.
                code_members = [n for n in names
                                if n.startswith('code/') and not n.endswith('/')]
                if code_members:
                    base = Path(settings.BASE_DIR)
                    for name in code_members:
                        _safe_extract(zf, name, base, rel_path=name[len('code/'):])
                    audit.restored_code = True
                    logger.info(f'[restore] wrote {len(code_members)} code file(s)')

            if restore_docs:
                docs_members = [n for n in names
                                if n.startswith('docs/') and not n.endswith('/')]
                if docs_members:
                    dest = Path(settings.BASE_DIR) / 'docs'
                    dest.mkdir(parents=True, exist_ok=True)
                    for name in docs_members:
                        _safe_extract(zf, name, dest, rel_path=name[len('docs/'):])
                    audit.restored_docs = True

        audit.status = BackupRestore.Status.SUCCESS
        logger.info(f'[restore] {backup_filename} restored by {actor_username or "system"}')

    except Exception as exc:
        logger.exception('[restore] failed')
        audit.status        = BackupRestore.Status.FAILED
        audit.error_message = str(exc)

    if restored_db:
        # The catalogue was replaced along with everything else. Re-index the
        # archives that are physically on disk so nothing is orphaned, then
        # re-point this audit row at the rows that now exist.
        reconcile_catalogue()

    audit.backup        = _find_or_index(backup_filename)
    audit.safety_backup = _find_or_index(safety_filename) if safety_filename else None
    audit.restored_by   = _find_user(actor_username)
    audit.save()
    return audit


def _find_user(username):
    if not username:
        return None
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(username=username).first()


def _find_or_index(filename: str):
    """The SystemBackup row for `filename`, re-creating it from disk if needed."""
    from .models import SystemBackup

    row = SystemBackup.objects.filter(filename=filename).order_by('-created_at').first()
    if row:
        return row
    path = backup_root() / filename
    if not path.is_file():
        return None
    return SystemBackup.objects.create(
        filename=filename, file_path=str(path), size_bytes=path.stat().st_size,
        status=SystemBackup.Status.SUCCESS, trigger=SystemBackup.Trigger.MANUAL,
        finished_at=timezone.now(),
        note='Re-indexed from disk after a restore',
    )


def import_backup(uploaded_file, user=None):
    """
    Validate an uploaded archive and add it to the backup catalogue.

    Returns (SystemBackup row, '') on success or (None, reason) on rejection.
    Importing changes nothing on the live system — restore is a separate,
    explicitly confirmed step that runs through restore_backup() like any
    other archive (safety copy included).

    Validation, in order:
      * streams to a temp file first — never trusted in place, never in memory
      * must be a real zip whose every member CRC verifies (testzip)
      * every member name must be relative and free of '..' (zip-slip)
      * must contain a manifest.json naming this application
      * only then is it moved under BACKUP_ROOT and catalogued
    """
    from .models import SystemBackup

    root  = backup_root()
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')

    def _taken(name: str) -> bool:
        return ((root / name).exists()
                or SystemBackup.objects.filter(filename=name).exists())

    filename = f'tenderradar_uploaded_{stamp}.zip'
    suffix   = 1
    while _taken(filename):
        suffix += 1
        filename = f'tenderradar_uploaded_{stamp}_{suffix}.zip'

    staged = root / f'.incoming_{filename}'
    try:
        with open(staged, 'wb') as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)

        try:
            zf = zipfile.ZipFile(staged)
        except zipfile.BadZipFile:
            return None, 'not a valid .zip archive.'

        with zf:
            names = zf.namelist()
            if not names:
                return None, 'the archive is empty.'

            for name in names:
                norm = name.replace('\\', '/')
                if norm.startswith('/') or norm[1:3] == ':/' or '..' in norm.split('/'):
                    return None, f'unsafe path inside the archive: {name!r}'

            bad = zf.testzip()
            if bad is not None:
                return None, f'corrupt archive — CRC check failed at {bad!r}.'

            if 'manifest.json' not in names:
                return None, ('no manifest.json — this is not a TenderRadar '
                              'backup archive.')
            try:
                manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                return None, 'manifest.json is not valid JSON.'
            if manifest.get('app') != 'TenderRadar':
                return None, ('the manifest does not identify a TenderRadar '
                              'backup (app != "TenderRadar").')

            name_set = set(names)
            has_db   = bool({DB_MEMBER_SQLITE, DB_MEMBER_MYSQL, DB_MEMBER_JSON}
                            & name_set)
            has = lambda prefix: any(n.startswith(prefix) for n in names)

        os.replace(staged, root / filename)

        record = SystemBackup.objects.create(
            filename=filename,
            file_path=str(root / filename),
            size_bytes=(root / filename).stat().st_size,
            status=SystemBackup.Status.SUCCESS,
            trigger=SystemBackup.Trigger.UPLOADED,
            created_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
            finished_at=timezone.now(),
            includes_database=has_db,
            includes_media=has('media/'),
            includes_config=has('config/'),
            includes_code=has('code/'),
            includes_docs=has('docs/'),
            note=(f'Uploaded archive (originally {uploaded_file.name}, '
                  f'created {manifest.get("created_at", "?")[:16]} '
                  f'by {manifest.get("created_by", "?")})')[:255],
        )
        logger.info(f'[backup] imported uploaded archive as {filename} '
                    f'({record.size_display})')
        return record, ''

    except Exception as exc:
        logger.exception('[backup] upload import failed')
        return None, str(exc)
    finally:
        try:
            if staged.exists():
                staged.unlink()
        except OSError:
            pass


def reconcile_catalogue() -> int:
    """
    Add a catalogue row for every archive on disk that the database does not
    know about. Called after a database restore, when the catalogue itself has
    been rolled back. Returns how many rows were added.
    """
    from .models import SystemBackup

    known = set(SystemBackup.objects.values_list('filename', flat=True))
    added = 0
    for path in sorted(backup_root().glob('*.zip')):
        if path.name in known:
            continue
        try:
            SystemBackup.objects.create(
                filename=path.name, file_path=str(path),
                size_bytes=path.stat().st_size,
                status=SystemBackup.Status.SUCCESS,
                trigger=SystemBackup.Trigger.MANUAL,
                finished_at=timezone.now(),
                note='Re-indexed from disk after a restore',
            )
            added += 1
        except Exception:                                    # pragma: no cover
            logger.exception(f'[backup] could not re-index {path.name}')
    if added:
        logger.info(f'[backup] re-indexed {added} archive(s) after restore')
    return added


def _restore_sqlite(zf: zipfile.ZipFile) -> None:
    """
    Push the archived database into the live SQLite file through SQLite's own
    backup API — no file replacement, so this is safe on Windows while the
    server holds the database open.
    """
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / 'restore.sqlite3'
        with zf.open(DB_MEMBER_SQLITE) as src, open(staged, 'wb') as out:
            shutil.copyfileobj(src, out)

        connections.close_all()
        source = sqlite3.connect(str(staged))
        try:
            live = sqlite3.connect(str(_db_path()))
            try:
                source.backup(live)
            finally:
                live.close()
        finally:
            source.close()
        connections.close_all()


def _restore_json(zf: zipfile.ZipFile) -> None:
    """Last-resort fixture restore for engines with no native tool (flush, then loaddata)."""
    from django.core.management import call_command

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / 'dumpdata.json'
        with zf.open(DB_MEMBER_JSON) as src, open(staged, 'wb') as out:
            shutil.copyfileobj(src, out)
        call_command('flush', '--noinput')
        call_command('loaddata', str(staged))


# ── Housekeeping ──────────────────────────────────────────────────────────────

def delete_backup(backup) -> None:
    """Remove the archive from disk and drop its catalogue row."""
    try:
        if backup.file_path and os.path.isfile(backup.file_path):
            os.remove(backup.file_path)
    except OSError as exc:
        logger.warning(f'[backup] could not delete {backup.file_path}: {exc}')
    backup.delete()


def retention_limit() -> int:
    """
    How many archives to keep, from the Backup & Restore schedule settings.

    The value is shared by manual and scheduled backups so the folder has one
    ceiling rather than one per trigger. Falls back to the model default if the
    settings row cannot be read, so a backup is never blocked by a config read.
    """
    from .models import BackupSchedule

    try:
        return int(BackupSchedule.get().retention_count)
    except Exception:                                        # pragma: no cover
        logger.exception('[backup] could not read retention setting; using 10')
        return 10


def prune_backups(keep: int) -> int:
    """
    Keep the newest `keep` successful automatic/manual backups, delete the rest.
    Safety copies taken before a restore are never pruned. Returns how many
    archives were removed. `keep <= 0` disables pruning.
    """
    from .models import SystemBackup

    if keep <= 0:
        return 0
    prunable = (SystemBackup.objects
                .filter(status=SystemBackup.Status.SUCCESS)
                .exclude(trigger=SystemBackup.Trigger.PRE_RESTORE)
                .order_by('-created_at'))
    removed = 0
    for old in list(prunable[keep:]):
        delete_backup(old)
        removed += 1
    if removed:
        logger.info(f'[backup] pruned {removed} old backup(s)')
    return removed
