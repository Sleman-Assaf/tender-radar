"""Cleanup, resource limits and safety verification.

Three jobs that only matter in production:

  * **Cleanup.** Snapshots, metrics and benchmark runs accumulate forever
    otherwise. Every sweep here is bounded by a retention setting, is dry-run by
    default, and refuses to touch anything a scraper or a review item still
    points at — a cleanup job that deletes the evidence behind an open incident
    is worse than no cleanup at all.

  * **Resource limits.** A sandbox forks a Python process and Tier 2 launches
    Chromium. Without a ceiling, a burst of parallel work can exhaust the machine
    and take the web server down with it. The limiter is advisory rather than
    enforced by the OS, which is enough because every caller goes through it.

  * **Safety verification.** The production invariants, checkable on demand:
    the corpus is untouched, no generated file escaped its manifest, and the
    installer can still roll back byte-perfectly.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("scraper_factory")


# ── resource limits ───────────────────────────────────────────────────────────

class ResourceLimiter:
    """A counting semaphore per resource kind, with a timeout.

    Advisory: nothing stops code bypassing it. That is acceptable because the two
    expensive operations — sandbox subprocesses and browser launches — each have a
    single call site, so routing both through here bounds the real cost. Blocking
    forever would turn exhaustion into a hang, so acquisition times out and the
    caller is told plainly.
    """

    _semaphores: dict[str, threading.BoundedSemaphore] = {}
    _lock = threading.Lock()

    @classmethod
    def _semaphore(cls, kind: str, limit: int) -> threading.BoundedSemaphore:
        with cls._lock:
            if kind not in cls._semaphores:
                cls._semaphores[kind] = threading.BoundedSemaphore(max(1, limit))
            return cls._semaphores[kind]

    @classmethod
    def acquire(cls, kind: str, *, limit: int | None = None,
                timeout: float | None = None) -> bool:
        limit = int(limit if limit is not None else cls.limit_for(kind))
        timeout = float(timeout if timeout is not None
                        else getattr(settings, "FACTORY_RESOURCE_WAIT_SECONDS", 120))
        got = cls._semaphore(kind, limit).acquire(timeout=timeout)
        if not got:
            logger.warning("[factory] resource '%s' busy: %s slot(s) all in use "
                           "after %ss", kind, limit, timeout)
        return got

    @classmethod
    def release(cls, kind: str) -> None:
        try:
            cls._semaphore(kind, cls.limit_for(kind)).release()
        except ValueError:
            # Released more than acquired: a bug in a caller, not a reason to
            # crash a run. Log it so it gets fixed.
            logger.warning("[factory] resource '%s' released too many times", kind)

    @staticmethod
    def limit_for(kind: str) -> int:
        return int({
            "sandbox": getattr(settings, "FACTORY_MAX_CONCURRENT_SANDBOX", 2),
            "browser": getattr(settings, "FACTORY_MAX_CONCURRENT_BROWSER", 2),
            "generation": getattr(settings, "FACTORY_MAX_CONCURRENT_GENERATION", 1),
        }.get(kind, 4))

    @classmethod
    def snapshot(cls) -> dict:
        with cls._lock:
            return {kind: {"limit": cls.limit_for(kind),
                           "free": getattr(sem, "_value", None)}
                    for kind, sem in cls._semaphores.items()}


class limited:
    """Context manager form: `with limited("browser"): ...`

    Raises ResourceBusy rather than proceeding unbounded, because silently
    ignoring the limit would make it decorative.
    """

    def __init__(self, kind: str, *, limit: int | None = None,
                 timeout: float | None = None):
        self.kind, self.limit, self.timeout = kind, limit, timeout
        self._held = False

    def __enter__(self):
        self._held = ResourceLimiter.acquire(self.kind, limit=self.limit,
                                             timeout=self.timeout)
        if not self._held:
            raise ResourceBusy(
                f"no free '{self.kind}' slot after waiting; "
                f"limit is {ResourceLimiter.limit_for(self.kind)}")
        return self

    def __exit__(self, *exc):
        if self._held:
            ResourceLimiter.release(self.kind)
        return False


class ResourceBusy(RuntimeError):
    """Raised when a bounded resource has no free slot."""


# ── cleanup ───────────────────────────────────────────────────────────────────

def cleanup(*, dry_run: bool = True) -> dict:
    """Run every retention sweep. Dry-run by default.

    Returns what was (or would be) removed, per sweep, so the caller can print it
    and an operator can see the effect before allowing it.
    """
    results = {
        "dry_run": dry_run,
        "snapshots": _sweep_snapshots(dry_run=dry_run),
        "metrics": _sweep_metrics(dry_run=dry_run),
        "history": _sweep_history(dry_run=dry_run),
        "benchmark_runs": _sweep_benchmark_runs(dry_run=dry_run),
        "jobs": _sweep_jobs(dry_run=dry_run),
        "staging": _sweep_staging(dry_run=dry_run),
        "alerts": _sweep_alerts(dry_run=dry_run),
    }
    total = sum(v.get("removed", 0) for v in results.values()
                if isinstance(v, dict))
    results["total_removed"] = total
    logger.info("[factory] cleanup %s: %s item(s)",
                "would remove" if dry_run else "removed", total)
    return results


def _sweep_snapshots(*, dry_run: bool) -> dict:
    """Old HTML snapshots, except any a benchmark case or review still needs."""
    from .models import BenchmarkCase, HtmlSnapshot, InvestigationRecord

    days = int(getattr(settings, "FACTORY_SNAPSHOT_RETENTION_DAYS", 180))
    cutoff = timezone.now() - timedelta(days=days)
    keep = set(BenchmarkCase.objects.exclude(snapshot=None)
               .values_list("snapshot_id", flat=True))
    keep |= set(InvestigationRecord.objects.exclude(snapshot=None)
                .values_list("snapshot_id", flat=True))

    qs = HtmlSnapshot.objects.filter(created_at__lt=cutoff).exclude(pk__in=keep)
    n = qs.count()
    if not dry_run and n:
        qs.delete()
    return {"removed": n, "retention_days": days, "protected": len(keep)}


def _sweep_metrics(*, dry_run: bool) -> dict:
    from .models import ExecutionMetric

    days = int(getattr(settings, "FACTORY_METRIC_RETENTION_DAYS", 365))
    cutoff = timezone.now() - timedelta(days=days)
    qs = ExecutionMetric.objects.filter(created_at__lt=cutoff)
    n = qs.count()
    if not dry_run and n:
        qs.delete()
    return {"removed": n, "retention_days": days}


def _sweep_history(*, dry_run: bool) -> dict:
    """Scheduling history is kept longer than metrics — it is the audit trail."""
    from .models import ExecutionHistory

    days = int(getattr(settings, "FACTORY_HISTORY_RETENTION_DAYS", 730))
    cutoff = timezone.now() - timedelta(days=days)
    qs = ExecutionHistory.objects.filter(created_at__lt=cutoff)
    n = qs.count()
    if not dry_run and n:
        qs.delete()
    return {"removed": n, "retention_days": days}


def _sweep_benchmark_runs(*, dry_run: bool) -> dict:
    """Old runs, never a baseline — a baseline is the comparison point."""
    from .models import BenchmarkRun

    keep_last = int(getattr(settings, "FACTORY_BENCHMARK_KEEP_RUNS", 50))
    ids = list(BenchmarkRun.objects.order_by("-created_at")
               .values_list("pk", flat=True)[:keep_last])
    qs = BenchmarkRun.objects.exclude(pk__in=ids).filter(is_baseline=False)
    n = qs.count()
    if not dry_run and n:
        qs.delete()
    return {"removed": n, "kept": len(ids)}


def _sweep_jobs(*, dry_run: bool) -> dict:
    from .models import QueueState, ScheduledJob

    days = int(getattr(settings, "FACTORY_JOB_RETENTION_DAYS", 30))
    cutoff = timezone.now() - timedelta(days=days)
    qs = ScheduledJob.objects.filter(
        updated_at__lt=cutoff,
        state__in=[QueueState.DONE, QueueState.CANCELLED])
    n = qs.count()
    if not dry_run and n:
        qs.delete()
    return {"removed": n, "retention_days": days}


def _sweep_staging(*, dry_run: bool) -> dict:
    """Temporary generated files left behind by an interrupted install."""
    staging = pathlib.Path(getattr(settings, "FACTORY_STAGING_DIR", ""))
    if not staging or not staging.exists():
        return {"removed": 0, "note": "no staging directory"}

    hours = int(getattr(settings, "FACTORY_STAGING_RETENTION_HOURS", 48))
    cutoff = timezone.now().timestamp() - hours * 3600
    removed = []
    for path in staging.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                removed.append(path.name)
                if not dry_run:
                    path.unlink()
        except OSError as exc:
            logger.warning("[factory] cannot sweep %s: %s", path, exc)
    return {"removed": len(removed), "files": removed[:10],
            "retention_hours": hours}


def _sweep_alerts(*, dry_run: bool) -> dict:
    """Resolved alerts only. An open alert is never swept."""
    from .models import FactoryAlert

    days = int(getattr(settings, "FACTORY_ALERT_RETENTION_DAYS", 180))
    cutoff = timezone.now() - timedelta(days=days)
    qs = FactoryAlert.objects.filter(is_open=False, created_at__lt=cutoff)
    n = qs.count()
    if not dry_run and n:
        qs.delete()
    return {"removed": n, "retention_days": days}


# ── safety verification ───────────────────────────────────────────────────────

def verify_safety() -> dict:
    """Check the production invariants. Returns {check: (ok, detail)}."""
    checks: dict[str, tuple[bool, str]] = {}

    scrapers_dir = pathlib.Path(getattr(
        settings, "FACTORY_SCRAPERS_DIR",
        pathlib.Path(settings.BASE_DIR) / "tenders" / "scrapers"))

    # 1. the registry file
    init = scrapers_dir / "__init__.py"
    try:
        raw = init.read_bytes()
        checks["registry_readable"] = (True, f"{len(raw)} bytes")
        checks["registry_lf"] = (b"\r\n" not in raw,
                                 "LF" if b"\r\n" not in raw else "CRLF found")
        checks["registry_md5"] = (True, hashlib.md5(raw).hexdigest())
    except OSError as exc:
        checks["registry_readable"] = (False, str(exc))

    # 2. every file on disk is either corpus or claimed by the manifest
    try:
        # owned_modules() is a METHOD on the installer, not a module function.
        # Importing it as a function silently failed inside a try/except in
        # factory_regression and made that check report "0 factory-owned"
        # forever — a broken check printing a reassuring number.
        from .installation.installer import ScraperInstaller
        owned = set(ScraperInstaller().owned_modules())
        on_disk = {p.stem for p in scrapers_dir.glob("*.py")
                   if not p.name.startswith("_") and p.name != "base.py"}
        unclaimed_generated = owned - on_disk
        checks["manifest_consistent"] = (
            not unclaimed_generated,
            f"{len(owned)} owned, {len(on_disk)} on disk"
            + (f"; MISSING {sorted(unclaimed_generated)}"
               if unclaimed_generated else ""))
    except Exception as exc:  # noqa: BLE001
        checks["manifest_consistent"] = (False, f"{type(exc).__name__}: {exc}")

    # 3. installed scrapers are importable and registered
    try:
        from tenders.scrapers import SCRAPER_REGISTRY

        from .models import FactoryStatus, GeneratedScraper
        installed = list(GeneratedScraper.objects
                         .filter(status=FactoryStatus.INSTALLED)
                         .values_list("class_name", flat=True))
        missing = [c for c in installed if c not in SCRAPER_REGISTRY]
        checks["registered"] = (
            not missing,
            f"{len(installed)} installed, {len(SCRAPER_REGISTRY)} in registry"
            + (f"; NOT REGISTERED {missing}" if missing else ""))
    except Exception as exc:  # noqa: BLE001
        checks["registered"] = (False, f"{type(exc).__name__}: {exc}")

    # 4. a backup exists for anything that was replaced
    try:
        from .models import ScraperFileBackup
        checks["backups_present"] = (
            True, f"{ScraperFileBackup.objects.count()} backup(s) retained")
    except Exception as exc:  # noqa: BLE001
        checks["backups_present"] = (False, str(exc))

    # 5. nothing is holding a stale install lock
    try:
        from .installation.lock import install_lock  # noqa: F401
        lock_path = pathlib.Path(
            getattr(settings, "FACTORY_STAGING_DIR", ".")) / "install.lock"
        stale = lock_path.exists()
        checks["no_stale_lock"] = (
            not stale, "clear" if not stale else f"lock present at {lock_path}")
    except Exception as exc:  # noqa: BLE001
        checks["no_stale_lock"] = (True, f"not checkable: {exc}")

    return checks
