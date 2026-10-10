"""
Build a self-contained, relocatable Python runtime for the TenderRadar installer.

Uses CPython's official *embeddable* distribution, which is designed to be
copied anywhere and resolves sys.prefix from the executable's own folder — so it
works from C:\\Program Files\\TenderRadar without touching the registry and
without the user installing Python.

    .venv\\Scripts\\python.exe desktop\\build_runtime.py

Result: desktop/payload/runtime/   (python.exe + stdlib + every dependency)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / 'desktop' / 'payload'
RUNTIME = PAYLOAD / 'runtime'
CACHE = ROOT / 'desktop' / '.cache'

PY_VER = f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'
TAG = f'cp{sys.version_info.major}{sys.version_info.minor}'
EMBED_URL = f'https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip'

# Needed only to build/package — never at runtime.
BUILD_ONLY = {
    'pip', 'setuptools', 'wheel', 'pkg_resources', '_distutils_hack',
    'PyInstaller', 'pyinstaller_hooks_contrib', 'altgraph', 'pefile',
    'pyflakes',
}


def fetch_embeddable() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    zpath = CACHE / f'python-{PY_VER}-embed-amd64.zip'
    if zpath.is_file() and zpath.stat().st_size > 5_000_000:
        print(f'  using cached {zpath.name}')
        return zpath
    print(f'  downloading {EMBED_URL}')
    req = urllib.request.Request(EMBED_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=300) as r, open(zpath, 'wb') as out:
        shutil.copyfileobj(r, out)
    size = zpath.stat().st_size
    if size < 5_000_000:
        raise RuntimeError(f'download looks wrong ({size} bytes) — proxy may be blocking it')
    print(f'  downloaded {size/1048576:.1f} MB')
    return zpath


STRAY_PTH = ('distutils-precedence.pth',)


def clean_runtime(runtime: Path) -> None:
    """Drop files that only produce noise in the embeddable runtime."""
    sp = runtime / 'Lib' / 'site-packages'
    for name in STRAY_PTH:
        f = sp / name
        if f.is_file():
            f.unlink()
            print(f'   removed {name} (imports _distutils_hack, which the '
                  f'embeddable runtime does not ship)')


def main() -> int:
    print(f'Building runtime for Python {PY_VER}')
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)

    # 1. embeddable interpreter
    with zipfile.ZipFile(fetch_embeddable()) as zf:
        zf.extractall(RUNTIME)
    print(f'  extracted embeddable interpreter -> {RUNTIME.name}/')

    # 2. enable `site` so Lib/site-packages is importable, and add the app folder.
    #    The embeddable build ships pythonXY._pth with `import site` commented out.
    pth = next(RUNTIME.glob('python*._pth'), None)
    if pth is None:
        raise RuntimeError('python._pth not found in the embeddable zip')
    lines = pth.read_text(encoding='utf-8').splitlines()
    out: list[str] = []
    for line in lines:
        out.append('import site' if line.strip() in ('#import site', '# import site') else line)
    if 'import site' not in out:
        out.append('import site')
    for extra in ('Lib\\site-packages', '..'):          # '..' = the app folder
        if extra not in out:
            out.insert(0, extra)
    pth.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(f'  patched {pth.name}: site enabled, Lib\\site-packages and app folder on sys.path')

    # 3. copy every runtime dependency out of the working virtualenv
    src = ROOT / '.venv' / 'Lib' / 'site-packages'
    dst = RUNTIME / 'Lib' / 'site-packages'
    dst.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for item in sorted(src.iterdir()):
        base = item.name.split('-')[0].replace('.dist-info', '').replace('.egg-info', '')
        if base in BUILD_ONLY or item.name in BUILD_ONLY:
            skipped += 1
            continue
        if item.name in ('__pycache__',):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests', 'test'))
        else:
            shutil.copy2(item, target)
        copied += 1
    print(f'  copied {copied} packages ({skipped} build-only packages skipped)')

    # 4. the Scripts folder is not needed, but waitress/django-admin entry points are
    #    imported as modules by the launcher, so nothing else to do.

    total = sum(f.stat().st_size for f in RUNTIME.rglob('*') if f.is_file())
    print(f'\n  runtime: {RUNTIME}  ({total/1048576:.0f} MB)')

    # 5. prove it works standalone: import Django and the MySQL driver
    exe = RUNTIME / 'python.exe'
    # The Windows-service and tray imports are in here on purpose. pywin32 loads
    # pythoncom/pywintypes DLLs from Lib/site-packages/pywin32_system32 via
    # pywin32.pth, which only runs because this runtime enables `site` in step 2.
    # If that ever breaks, the build must fail here rather than ship a runtime
    # whose service and tray cannot start.
    probe = (
        'import sys, django, MySQLdb, waitress, apscheduler, celery, requests, bs4;'
        'import win32serviceutil, win32service, servicemanager, pythoncom;'
        'import pystray, PIL;'
        'from PIL import Image, ImageDraw;'
        'print("PY", sys.version.split()[0]);'
        'print("DJANGO", django.get_version());'
        'print("MYSQLDB", MySQLdb.version_info);'
        'print("PYWIN32", win32service.__file__.split("site-packages")[-1]);'
        'print("PYSTRAY", pystray.__version__ if hasattr(pystray, "__version__") else "ok");'
        'print("PILLOW", PIL.__version__);'
        'print("PREFIX", sys.prefix)'
    )
    r = subprocess.run([str(exe), '-c', probe], capture_output=True, text=True, cwd=str(RUNTIME))
    print('  self-test:')
    for line in (r.stdout or '').strip().splitlines():
        print('    ' + line)
    if r.returncode:
        print('    FAILED:\n' + (r.stderr or '')[-1500:])
        return 1
    clean_runtime(RUNTIME)
    return 0


if __name__ == '__main__':
    sys.exit(main())
