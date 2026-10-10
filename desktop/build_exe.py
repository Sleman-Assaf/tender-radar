"""
Build TenderRadar.exe from desktop/launcher.py.

Only the launcher is frozen — Django, the scrapers and the data stay on disk
(see the rationale in launcher.py). That keeps the EXE small and, critically,
leaves tenders/scrapers/ writable so the Scraper Factory keeps working.

    .venv\\Scripts\\python.exe desktop\\build_exe.py

Output: TenderRadar.exe in the project root (next to manage.py).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / 'desktop'
BUILD = DESKTOP / 'build'
DIST = DESKTOP / 'dist'
ICON = DESKTOP / 'TenderRadar.ico'


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


def main() -> int:
    ensure_icon()
    try:
        import PyInstaller                                   # noqa: F401
    except ImportError:
        print('PyInstaller is not installed. Run:')
        print(r'   .venv\Scripts\python.exe -m pip install pyinstaller')
        return 1

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--onefile',
        '--windowed',                 # no console window (requirement 8)
        '--name', 'TenderRadar',
        '--distpath', str(DIST),
        '--workpath', str(BUILD),
        '--specpath', str(BUILD),
        # tkinter ships with Python; psutil gives us a clean process-tree kill
        '--hidden-import', 'psutil',
        '--exclude-module', 'django',        # deliberately NOT bundled
        '--exclude-module', 'numpy',
        '--exclude-module', 'PIL',
        '--exclude-module', 'lxml',
        '--exclude-module', 'playwright',
    ]
    if ICON.is_file():
        cmd += ['--icon', str(ICON)]
    cmd.append(str(DESKTOP / 'launcher.py'))

    print('building:', ' '.join(cmd[:8]), '...')
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode:
        return r.returncode

    built = DIST / 'TenderRadar.exe'
    if not built.is_file():
        print('build reported success but the EXE is missing')
        return 1

    # Place it next to manage.py — the launcher locates the app from its own folder
    target = ROOT / 'TenderRadar.exe'
    shutil.copy2(built, target)
    print(f'\n  {target}  ({target.stat().st_size / 1048576:.1f} MB)')
    print('  double-click it, or ship the whole project folder.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
