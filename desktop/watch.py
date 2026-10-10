"""
Watching the application for code changes — shared by devserver.py and the
launcher's "apply changes live" mode.

One policy, in one place: what is worth watching, how long to wait for an editor
to finish writing, and how to tell what changed. Both supervisors restart the
same Waitress worker in the same way; only the way in differs.

Nothing here runs in production. The launcher starts the watcher only when the
user turns live reload on, and the Windows service never does.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

#: Never worth watching: dependencies, build output, caches and data. `runtime`
#: matters most — an installed copy keeps the bundled interpreter there, and its
#: ~9,000 library files would make every scan expensive and every pip install
#: look like a code change.
IGNORED_DIRS = {
    '.venv', 'venv', 'runtime', 'Lib', 'Scripts', 'site-packages',
    '__pycache__', '.git', '.idea', '.pytest_cache', 'node_modules',
    'staticfiles', 'backups', 'logs', 'media', 'docs', 'payload',
    'build', 'dist', '.cache', 'snapshots', 'mysql', 'browser-profile',
    'templates_backup_20260722_095610',
}

#: Only Python needs a restart — templates and static files are already live.
WATCHED_SUFFIXES = {'.py'}

#: Editors write a file several times in a row; wait for them to settle.
DEBOUNCE_SECONDS = 0.6


def iter_watched_files(root: Path):
    for path in root.rglob('*'):
        if path.suffix not in WATCHED_SUFFIXES or not path.is_file():
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in parts):
            continue
        yield path


def snapshot(root: Path) -> dict[Path, float]:
    out: dict[Path, float] = {}
    for path in iter_watched_files(root):
        try:
            out[path] = path.stat().st_mtime
        except OSError:
            pass
    return out


def changed_files(before: dict[Path, float],
                  after: dict[Path, float]) -> list[Path]:
    changes = [p for p, m in after.items() if before.get(p) != m]
    changes += [p for p in before if p not in after]          # deletions
    return changes


class Watcher:
    """
    A background thread that calls `on_change(paths)` when Python source changes.

    It only reports; restarting is the caller's business, so the launcher can
    marshal that onto its UI thread and the devserver can do it inline. Stopping
    is always safe, including before it ever started.
    """

    def __init__(self, root: Path, on_change, interval: float = 1.0) -> None:
        self.root = Path(root)
        self.on_change = on_change
        self.interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.file_count = 0

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> int:
        if self.is_running():
            return self.file_count
        self._stop.clear()
        self.file_count = len(snapshot(self.root))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.file_count

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        watched = snapshot(self.root)
        while not self._stop.wait(self.interval):
            current = snapshot(self.root)
            changes = changed_files(watched, current)
            if not changes:
                continue

            # Let the editor finish, then look again — a half-written file
            # would restart the worker straight into a SyntaxError.
            if self._stop.wait(DEBOUNCE_SECONDS):
                return
            current = snapshot(self.root)
            changes = changed_files(watched, current)
            watched = current
            self.file_count = len(current)
            if not changes or self._stop.is_set():
                continue
            try:
                self.on_change(changes)
            except Exception:                 # a UI error must not kill watching
                pass
