"""Scheduler Engine — queue, leases, retries and execution history.

This does not replace the project's APScheduler loop. That loop owns *when* the
77 production sources run, it works, and it is not the Factory's to change. What
it cannot do is retry one failing source without re-running all of them, or stop
two workers colliding on the same scraper.

So the Factory keeps its own queue for the scrapers it authored:

    ScheduledJob   one unit of work, with a lease so it runs once at a time
    RetryPolicy    how many attempts, how far apart, for which failures
    ExecutionHistory  what the scheduler decided each time, and why

The lease matters more than it looks. Without one, a slow run still holding a
connection can be started again by the next tick, and two copies of the same
scraper write the same tenders concurrently. A lease with an expiry also survives
a worker being killed: the job returns to the queue when the lease lapses,
instead of being stuck as "running" forever.

Retry decisions are taken from the diagnostic category, not from the fact that
something failed. A wrong selector fails identically however many times it runs;
retrying it three times wastes twenty minutes and teaches nobody anything. A
proxy timeout is worth another attempt in five minutes.
"""

from __future__ import annotations

import logging
import os
import random
import socket
import time
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("scraper_factory")

#: How long a worker may hold a job before it is considered dead.
DEFAULT_LEASE_SECONDS = 1800


def worker_id() -> str:
    """host:pid — enough to tell two workers apart and to read in a log."""
    return f"{socket.gethostname()}:{os.getpid()}"


# ── policies ──────────────────────────────────────────────────────────────────

def default_policy():
    """The default RetryPolicy, created on first use.

    Values chosen to be conservative: three attempts, five minutes apart and
    doubling, retrying only transport-shaped failures. A portal that is briefly
    unreachable recovers on its own; one whose markup changed does not, and
    should reach a human quickly rather than after an hour of retries.
    """
    from .models import RetryPolicy

    policy, created = RetryPolicy.objects.get_or_create(
        is_default=True,
        defaults={
            "name": "default",
            "max_attempts": 3,
            "base_delay_seconds": 300,
            "backoff_multiplier": 2.0,
            "max_delay_seconds": 7200,
            "jitter_seconds": 30,
            "retry_categories": list(RetryPolicy.TRANSIENT),
        })
    if created:
        logger.info("[factory] created default retry policy")
    return policy


# ── enqueueing ────────────────────────────────────────────────────────────────

def enqueue(scraper, *, when=None, priority: int = 0, reason: str = "",
            policy=None, attempt: int = 0):
    """Queue one scraper, unless it is already queued.

    Idempotent on purpose: a scheduler tick that runs twice, or an operator
    clicking twice, must not create two jobs for the same scraper.
    """
    from .models import QueueState, ScheduledJob

    when = when or timezone.now()
    existing = (ScheduledJob.objects
                .filter(scraper=scraper,
                        state__in=[QueueState.PENDING, QueueState.LEASED])
                .order_by("next_run_at").first())
    if existing is not None:
        # Raise priority and pull the time forward if this request is more urgent.
        changed = []
        if priority > existing.priority:
            existing.priority = priority
            changed.append("priority")
        if when < existing.next_run_at:
            existing.next_run_at = when
            changed.append("next_run_at")
        if changed:
            existing.save(update_fields=changed + ["updated_at"])
        return existing

    return ScheduledJob.objects.create(
        scraper=scraper, policy=policy or default_policy(),
        state=QueueState.PENDING, priority=priority, attempt=attempt,
        next_run_at=when, reason=reason[:120])


def enqueue_due(*, limit: int = 200) -> int:
    """Queue every installed generated scraper whose turn it is.

    Degraded scrapers are given a higher priority than healthy ones: a source
    that is currently collecting nothing is the one worth attending to first.
    """
    from tenders.models import ScraperSource

    from .models import FactoryStatus, GeneratedScraper, HealthState, ScraperHealth

    active = set(ScraperSource.objects.filter(is_active=True)
                 .values_list("scraper_class", flat=True))
    scrapers = list(GeneratedScraper.objects
                    .filter(status=FactoryStatus.INSTALLED)[:limit])
    health = {h.scraper_id: h for h in
              ScraperHealth.objects.filter(scraper__in=scrapers)}

    queued = 0
    for scraper in scrapers:
        if scraper.class_name not in active:
            continue                    # disabled source: not ours to revive
        h = health.get(scraper.pk)
        priority = 10 if (h and h.state == HealthState.DEGRADED) else 0
        reason = "degraded, retrying sooner" if priority else "scheduled sweep"
        enqueue(scraper, priority=priority, reason=reason)
        queued += 1
    logger.info("[factory] enqueue_due queued %s job(s)", queued)
    return queued


# ── claiming ──────────────────────────────────────────────────────────────────

def claim(*, lease_seconds: int = DEFAULT_LEASE_SECONDS, worker: str = ""):
    """Atomically take the next due job, or None.

    The update is guarded by the row's own state so two workers racing cannot
    both win: whichever UPDATE matches first flips the state, the other matches
    nothing and moves on. Selecting then saving would let both through.
    """
    from .models import QueueState, ScheduledJob

    worker = worker or worker_id()
    now = timezone.now()

    release_expired_leases()

    with transaction.atomic():
        job = (ScheduledJob.objects
               .filter(state=QueueState.PENDING, next_run_at__lte=now)
               .order_by("-priority", "next_run_at")
               .first())
        if job is None:
            return None
        claimed = (ScheduledJob.objects
                   .filter(pk=job.pk, state=QueueState.PENDING)
                   .update(state=QueueState.LEASED, leased_at=now,
                           lease_expires_at=now + timedelta(seconds=lease_seconds),
                           leased_by=worker[:120]))
        if not claimed:
            return None                 # another worker got there first
    job.refresh_from_db()
    logger.info("[factory] %s claimed job #%s (%s)", worker, job.pk,
                job.scraper.class_name)
    return job


def release_expired_leases() -> int:
    """Return jobs whose worker died to the queue."""
    from .models import QueueState, ScheduledJob

    now = timezone.now()
    stale = ScheduledJob.objects.filter(state=QueueState.LEASED,
                                        lease_expires_at__lt=now)
    count = stale.count()
    if count:
        for job in stale:
            logger.warning("[factory] lease expired on job #%s (%s, held by %s) "
                           "— returning it to the queue", job.pk,
                           job.scraper.class_name, job.leased_by or "?")
        stale.update(state=QueueState.PENDING, leased_at=None,
                     lease_expires_at=None, leased_by="")
    return count


# ── completion ────────────────────────────────────────────────────────────────

def complete(job, *, succeeded: bool, tenders_found: int = 0,
             duration_ms: int = 0, failure_category: str = "",
             error: str = "", metric=None, health_state: str = "",
             correlation_id: str = ""):
    """Close a job: record history, then retry, fail or finish it.

    Returns the ExecutionHistory row, whose `decision` field states in words what
    the scheduler chose. That sentence is the thing an operator reads when asking
    "why has this not run again?".
    """
    from .models import ExecutionHistory, QueueState

    policy = job.policy or default_policy()
    attempt = job.attempt + 1
    now = timezone.now()

    retry_at = None
    if succeeded:
        decision = "succeeded; job closed"
        job.state = QueueState.DONE
    elif policy.should_retry(attempt=attempt, category=failure_category):
        delay = policy.delay_for(attempt)
        jitter = random.randint(0, policy.jitter_seconds) if policy.jitter_seconds else 0
        retry_at = now + timedelta(seconds=delay + jitter)
        decision = (f"{failure_category or 'failure'} is retryable; attempt "
                    f"{attempt + 1}/{policy.max_attempts} in {delay + jitter}s")
        job.state = QueueState.PENDING
        job.next_run_at = retry_at
    else:
        if attempt >= policy.max_attempts:
            decision = (f"no attempts left ({attempt}/{policy.max_attempts}); "
                        f"handing over to health monitoring")
        else:
            decision = (f"{failure_category or 'failure'} is not retryable — "
                        f"another attempt would fail identically")
        job.state = QueueState.FAILED

    job.attempt = attempt
    job.last_category = (failure_category or "")[:30]
    job.last_error = (error or "")[:300]
    job.leased_at = None
    job.lease_expires_at = None
    job.leased_by = ""
    job.save()

    history = ExecutionHistory.objects.create(
        scraper=job.scraper, job=job, metric=metric, attempt=attempt,
        succeeded=succeeded, tenders_found=tenders_found,
        duration_ms=duration_ms, failure_category=(failure_category or "")[:30],
        health_state_after=(health_state or "")[:10],
        retry_scheduled=retry_at is not None, retry_at=retry_at,
        decision=decision[:200], correlation_id=(correlation_id or "")[:40])

    logger.info("[factory] job #%s attempt %s: %s", job.pk, attempt, decision)
    return history


def cancel(job, *, reason: str = "") -> None:
    from .models import QueueState

    job.state = QueueState.CANCELLED
    job.reason = (reason or job.reason)[:120]
    job.save(update_fields=["state", "reason", "updated_at"])
    logger.info("[factory] job #%s cancelled: %s", job.pk, reason)


# ── running ───────────────────────────────────────────────────────────────────

def run_job(job, *, correlation_id: str = ""):
    """Execute one claimed job through the project's own runner.

    The Factory does not re-implement running a scraper. It calls
    `tenders.apps._run_single_scraper`, so tenders are saved by the existing
    pipeline with its dedup, mirrors, AI enrichment and ScrapeLog behaviour —
    and the health observer then reacts to the ScrapeLog exactly as it does for a
    scheduled run. There is one code path for executing a scraper, not two.
    """
    from tenders.models import ScraperSource

    from .logging_utils import event
    from .models import HealthState, ScraperHealth

    started = time.monotonic()
    source = (ScraperSource.objects
              .filter(scraper_class=job.scraper.class_name, is_active=True)
              .first())
    if source is None:
        return complete(job, succeeded=False,
                        failure_category="INSTALLATION_FAILED",
                        error="no active ScraperSource for this scraper",
                        correlation_id=correlation_id)

    event("job.start", correlation_id=correlation_id, job=job.pk,
          scraper=job.scraper.class_name, attempt=job.attempt + 1)

    error = ""
    try:
        from tenders.apps import _run_single_scraper
        _run_single_scraper(source.pk)
    except Exception as exc:  # noqa: BLE001 — a crash is an outcome, not a stop
        error = f"{type(exc).__name__}: {exc}"[:300]
        logger.error("[factory] job #%s crashed: %s", job.pk, error, exc_info=True)

    duration_ms = int((time.monotonic() - started) * 1000)

    # The observer has already evaluated the run and updated health by now, so
    # read the outcome from there rather than guessing at it.
    health = ScraperHealth.objects.filter(scraper=job.scraper).first()
    metric = (job.scraper.metrics.order_by("-created_at").first()
              if hasattr(job.scraper, "metrics") else None)

    succeeded = bool(metric and metric.succeeded) and not error
    category = (metric.failure_category if metric else "") or (
        "UNKNOWN" if error else "")
    found = metric.tenders_total if metric else 0

    history = complete(
        job, succeeded=succeeded, tenders_found=found, duration_ms=duration_ms,
        failure_category=category, error=error, metric=metric,
        health_state=(health.state if health else HealthState.HEALTHY),
        correlation_id=correlation_id)

    event("job.finish", correlation_id=correlation_id, job=job.pk,
          scraper=job.scraper.class_name, succeeded=succeeded,
          tenders=found, duration_ms=duration_ms, category=category,
          decision=history.decision)
    return history


def drain(*, max_jobs: int = 10, lease_seconds: int = DEFAULT_LEASE_SECONDS):
    """Claim and run up to `max_jobs` due jobs. Returns the histories."""
    from .logging_utils import new_correlation_id

    done = []
    for _ in range(max_jobs):
        job = claim(lease_seconds=lease_seconds)
        if job is None:
            break
        done.append(run_job(job, correlation_id=new_correlation_id()))
    return done


# ── introspection ─────────────────────────────────────────────────────────────

def queue_summary() -> dict:
    """Counts by state, plus what is due now and what is overdue."""
    from django.db.models import Count

    from .models import QueueState, ScheduledJob

    now = timezone.now()
    by_state = {row["state"]: row["n"] for row in
                ScheduledJob.objects.values("state").annotate(n=Count("id"))}
    return {
        "by_state": by_state,
        "pending": by_state.get(QueueState.PENDING, 0),
        "leased": by_state.get(QueueState.LEASED, 0),
        "due_now": ScheduledJob.objects.filter(
            state=QueueState.PENDING, next_run_at__lte=now).count(),
        "overdue_1h": ScheduledJob.objects.filter(
            state=QueueState.PENDING,
            next_run_at__lt=now - timedelta(hours=1)).count(),
        "retrying": ScheduledJob.objects.filter(
            state=QueueState.PENDING, attempt__gt=0).count(),
        "expired_leases": ScheduledJob.objects.filter(
            state=QueueState.LEASED, lease_expires_at__lt=now).count(),
    }
