"""
Assemble the payload and build TenderRadar_Setup.exe.

    .venv\\Scripts\\python.exe desktop\\build_runtime.py     (once, ~194 MB)
    .venv\\Scripts\\python.exe desktop\\build_exe.py         (the launcher)
    .venv\\Scripts\\python.exe desktop\\build_installer.py   (this file)

Layout inside the installer:

    payload/app/        the Django project, exactly as it runs today
    payload/runtime/    self-contained CPython + every dependency
    payload/TenderRadar.exe
    payload/Uninstall.exe
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / 'desktop'
PAYLOAD = DESKTOP / 'payload'
APP = PAYLOAD / 'app'
RUNTIME = PAYLOAD / 'runtime'
BUILD = DESKTOP / 'build'
DIST = DESKTOP / 'dist'
ICON = DESKTOP / 'TenderRadar.ico'

# Never shipped. Mirrors installer.EXCLUDE_* and adds build artefacts.
SKIP_DIRS = {
    '.venv', 'venv', '__pycache__', '.git', '.idea', '.pytest_cache',
    'staticfiles', 'backups', 'logs',
    'templates_backup_20260722_095610',
    'payload', 'build', 'dist', '.cache',
}
SKIP_FILES = {
    'db.sqlite3', 'db.sqlite3-journal', 'db.sqlite3-wal', 'db.sqlite3-shm',
    'TenderRadar.exe', 'TenderRadar_Setup.exe', 'ssl.zip',
    # SECURITY: the real production.env holds this machine's MySQL password and
    # SECRET_KEY. It is NEVER shipped. production.env.template goes instead, and
    # the launcher generates the real file with fresh credentials on first run.
    'production.env',
}
SKIP_SUFFIX = {'.pyc', '.pyo', '.log', '.pre-restore', '.pre-cutover', '.bak'}

# Anything that could carry a credential. Pattern-based, because an exact
# filename list already missed production.env.pre-cutover once: it held both the
# MySQL password and the Django SECRET_KEY and it shipped.
SECRET_PATTERNS = (
    re.compile(r'^production\.env(?!\.template$)', re.I),   # .pre-cutover, .bak, .old…
    re.compile(r'^\.env', re.I),
    re.compile(r'\.(pem|key|pfx|p12|jks|keystore)$', re.I),
    re.compile(r'(secret|credential|password)', re.I),
    re.compile(r'^my\.cnf$', re.I),
)


def is_junk_file(rel: Path) -> bool:
    """
    Rubbish that must never reach a customer's machine.

    Filenames containing braces come from an f-string that was used as a path by
    mistake — shell redirection then created a real file with the literal
    expression as its name. Two of these ("{cert_path.resolve()}')" and
    "{key_path.resolve()}')", 33 bytes each) had been shipping inside the
    installer. Matching the pattern rather than the two names stops the next one.
    """
    name = rel.name
    if '{' in name or '}' in name:
        return True
    if name.startswith('~') or name.endswith('~'):
        return True
    if name in ('Thumbs.db', 'desktop.ini', '.DS_Store'):
        return True
    return False


def is_secret_file(rel: Path) -> bool:
    """True if this file must never be shipped to another machine."""
    if rel.name.lower() == 'production.env.template':
        return False                                  # the safe, secret-free one
    if rel.name.lower() in ('gen_secret_key.py', 'check_settings.py'):
        return False                                  # generators, not secrets
    return any(p.search(rel.name) for p in SECRET_PATTERNS)


def local_secrets() -> list[tuple[str, str]]:
    """(label, value) for every secret on THIS machine, for the content scan."""
    out: list[tuple[str, str]] = []
    env = ROOT / 'production.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, _, v = line.partition('=')
                k, v = k.strip(), v.strip()
                if len(v) >= 12 and any(t in k for t in
                                        ('PASSWORD', 'SECRET', 'KEY', 'TOKEN')):
                    out.append((k, v))
    return out


def assert_dump_sanitized() -> None:
    """The shipped dataset must carry no credentials.

    assert_no_secrets() only ever looked at payload/app, so the MySQL dump was
    never examined — and it shipped with the live Microsoft Graph client secret,
    the mailbox password, an access token and 162 live session rows in it. The
    dump is a mysqldump of a working system, so it will pick those up again every
    time it is regenerated: this has to run on every build, not once.
    """
    dump = PAYLOAD / 'mysql' / 'initial_tenderradar.sql'
    if not dump.is_file():
        print('   no initial dataset in the payload — nothing to sanitize')
        return
    sys.path.insert(0, str(ROOT))
    from desktop import sanitize_dump as S
    S.ensure_clean(dump, backup_dir=DESKTOP / '.cache')


def assert_no_secrets(tree: Path) -> None:
    """Abort the build if any staged file contains a local secret value.

    A filename blacklist can only block what it was told about; this reads the
    bytes, so a new secret-bearing file fails the build instead of shipping.
    """
    wanted = local_secrets()
    if not wanted:
        print('   WARNING: no local secrets found to scan for')
        return
    hits = []
    for f in tree.rglob('*'):
        if not f.is_file():
            continue
        try:
            blob = f.read_bytes()
        except OSError:
            continue
        for label, val in wanted:
            if val.encode() in blob:
                hits.append((f.relative_to(tree).as_posix(), label))
    if hits:
        for rel, label in hits:
            print(f'   LEAK: {rel} contains {label}')
        raise SystemExit(f'SECURITY: {len(hits)} secret(s) staged for shipping — '
                         f'build aborted')
    print(f'   scanned for {len(wanted)} local secret(s): none present')
# media holds this machine's uploads; ship the structure, not the content
SKIP_TREE_CONTENT = {'media', 'ssl'}


def ensure_icon() -> None:
    """Regenerate TenderRadar.ico if the generator is newer than the icon.

    Keeps the multi-size icon in step with desktop/make_icon.py automatically, so
    a build can never quietly ship the old 16x16-only file. Pillow is a build-time
    tool only; if it is absent the existing icon is used unchanged.
    """
    gen = DESKTOP / 'make_icon.py'
    if not gen.is_file():
        return
    if ICON.is_file() and ICON.stat().st_mtime >= gen.stat().st_mtime:
        return
    r = subprocess.run([sys.executable, str(gen)], cwd=str(ROOT),
                       capture_output=True, text=True)
    if r.returncode:
        print('   icon generation skipped:',
              (r.stderr or r.stdout or '').strip().splitlines()[-1:] or '?')
    else:
        print(f'   icon rebuilt: {ICON.name} ({ICON.stat().st_size:,} bytes)')


def stage_app() -> int:
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)
    n = 0
    for src in ROOT.rglob('*'):
        rel = src.relative_to(ROOT)
        parts = rel.parts
        if any(p in SKIP_DIRS for p in parts):
            continue
        if src.is_dir():
            continue
        if src.name in SKIP_FILES or src.suffix in SKIP_SUFFIX:
            continue
        if is_secret_file(rel) or is_junk_file(rel):
            continue
        # keep the folder, drop this machine's contents
        if parts[0] in SKIP_TREE_CONTENT and src.name != '.gitkeep':
            continue
        dst = APP / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    # make sure the writable folders exist in the package
    for d in ('media/feedback_attachments', 'media/dewa_ads', 'logs', 'backups',
              'staticfiles'):
        (APP / d).mkdir(parents=True, exist_ok=True)
        (APP / d / '.gitkeep').touch(exist_ok=True)
    return n


def build_child(script: Path, name: str, extra: list[str] | None = None) -> Path:
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--onefile', '--windowed', '--name', name,
           '--distpath', str(DIST), '--workpath', str(BUILD), '--specpath', str(BUILD),
           '--hidden-import', 'psutil']
    if ICON.is_file():
        cmd += ['--icon', str(ICON)]
    cmd += (extra or [])
    cmd.append(str(script))
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode:
        print((r.stdout or '')[-2500:])
        print((r.stderr or '')[-1500:])
        raise SystemExit(f'{name} build failed')
    out = DIST / f'{name}.exe'
    print(f'  {name}.exe  {out.stat().st_size/1048576:.1f} MB')
    return out


def main() -> int:
    try:
        import PyInstaller                                   # noqa: F401
    except ImportError:
        raise SystemExit(r'pip install pyinstaller first')

    if not (RUNTIME / 'python.exe').is_file():
        raise SystemExit('runtime missing — run desktop\\build_runtime.py first')

    if not (ROOT / 'production.env.template').is_file():
        raise SystemExit('production.env.template is missing — refusing to build '
                         'an installer with no configuration mechanism')

    ensure_icon()
    print('1. staging the application...')
    n = stage_app()
    print(f'   {n} files -> payload/app')
    if (APP / 'production.env').exists():
        raise SystemExit('SECURITY: production.env leaked into the payload')
    print('   production.env excluded; production.env.template shipped instead')
    print('   scanning the staged tree for credentials...')
    assert_no_secrets(APP)
    print('   scanning the initial dataset for credentials...')
    assert_dump_sanitized()

    print('2. building the launcher and uninstaller...')
    launcher = build_child(DESKTOP / 'launcher.py', 'TenderRadar')
    shutil.copy2(launcher, ROOT / 'TenderRadar.exe')      # dev convenience
    shutil.copy2(launcher, PAYLOAD / 'TenderRadar.exe')
    unins = build_child(DESKTOP / 'uninstaller.py', 'Uninstall')
    shutil.copy2(unins, PAYLOAD / 'Uninstall.exe')

    rt = sum(f.stat().st_size for f in RUNTIME.rglob('*') if f.is_file())
    ap = sum(f.stat().st_size for f in APP.rglob('*') if f.is_file())
    my = PAYLOAD / 'mysql'
    ms = sum(f.stat().st_size for f in my.rglob('*') if f.is_file()) if my.exists() else 0
    print(f'   payload: app {ap/1048576:.0f} MB + runtime {rt/1048576:.0f} MB'
          f' + mysql {ms/1048576:.0f} MB')

    print('3. building TenderRadar_Setup.exe (this bundles the whole payload)...')
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--onefile', '--windowed', '--name', 'TenderRadar_Setup',
           '--distpath', str(DIST), '--workpath', str(BUILD), '--specpath', str(BUILD),
           '--add-data', f'{APP}{";"}app',
           '--add-data', f'{RUNTIME}{";"}runtime',
           '--add-data', f'{PAYLOAD / "TenderRadar.exe"}{";"}.',
           '--add-data', f'{PAYLOAD / "Uninstall.exe"}{";"}.',
           # bundled MySQL server + the verified initial dataset
           '--add-data', f'{PAYLOAD / "mysql"}{";"}mysql',
           '--hidden-import', 'psutil']
    if ICON.is_file():
        cmd += ['--icon', str(ICON)]
    cmd.append(str(DESKTOP / 'installer.py'))
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode:
        print((r.stdout or '')[-3000:])
        raise SystemExit('installer build failed')

    setup = DIST / 'TenderRadar_Setup.exe'
    final = ROOT / 'TenderRadar_Setup.exe'
    shutil.copy2(setup, final)
    print(f'\n  {final}')
    print(f'  {final.stat().st_size/1048576:.0f} MB  — give this one file to the user')
    return 0


if __name__ == '__main__':
    sys.exit(main())
