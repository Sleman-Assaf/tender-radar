"""Cross-process lock guarding installation of scrapers.

Installing (or dry-run installing) mutates two shared resources: files inside
`tenders/scrapers/` and `tenders/scrapers/__init__.py`. Two builds running at the
same time corrupt each other:

    A backs up the clean registry, patches it
    B backs up the registry — which now contains A's entry — and patches it
    A finishes and restores ITS backup  -> B's entry is lost
    B finishes and restores ITS backup  -> A's entry comes back from the dead

Both then leave orphaned files that block every future build for those names.
Two clicks in the UI, or two employees, are enough to trigger it.

The lock serialises the whole install/dry-run/uninstall/restore critical section.
It is a file lock created with O_EXCL so it works across processes (the web
server, a management command and the benchmark can all be running), and it
records the owning pid plus a timestamp so a crashed holder can be detected and
broken rather than deadlocking the factory forever.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import pathlib
import time

from django.conf import settings

logger = logging.getLogger("scraper_factory")

#: A lock older than this is assumed to belong to a crashed process.
STALE_AFTER_SECONDS = 900

#: How long to wait for another build to finish before giving up.
DEFAULT_TIMEOUT_SECONDS = 600


class InstallLockTimeout(RuntimeError):
    """Raised when another installation holds the lock for too long."""


def _lock_path() -> pathlib.Path:
    base = pathlib.Path(getattr(settings, "FACTORY_BACKUP_DIR")).parent
    return base / "install.lock"


def _read_holder(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _is_stale(info: dict) -> bool:
    started = info.get("started_at", 0)
    try:
        age = time.time() - float(started)
    except (TypeError, ValueError):
        return True
    if age > STALE_AFTER_SECONDS:
        return True
    # A holder whose process is gone is stale regardless of age.
    pid = info.get("pid")
    if isinstance(pid, int) and pid > 0 and not _pid_alive(pid):
        return True
    return False


def _pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        # PermissionError means it exists but belongs to someone else.
        return isinstance(_pid_alive, object) and True
    except Exception:  # noqa: BLE001
        return False


@contextlib.contextmanager
def install_lock(*, purpose: str = "install",
                 timeout: int = DEFAULT_TIMEOUT_SECONDS):
    """Hold the installation lock for the duration of the block."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False

    while not acquired:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "purpose": purpose,
                           "started_at": time.time()}, fh)
            acquired = True
        except FileExistsError:
            info = _read_holder(path)
            if _is_stale(info):
                logger.warning(
                    "[factory] breaking stale install lock held by pid %s "
                    "(purpose=%s)", info.get("pid"), info.get("purpose"))
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise InstallLockTimeout(
                    "Another scraper is being installed right now "
                    f"(pid {info.get('pid')}, {info.get('purpose')}). "
                    "Please try again in a moment.")
            time.sleep(1.0)

    try:
        yield
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("[factory] could not release install lock: %s", exc)
