"""Manual review queue — the human-in-the-loop boundary.

When the Factory cannot finish a build it hands the work to a developer. The
handover is the whole point of this module: a queue entry that says "generation
failed" makes the developer redo the investigation by hand, which is the most
expensive part and the part the Factory has already done.

So each item carries the full case file — investigation, plan, evidence, failure
category, the strategy the Factory would have used, the closest existing
scrapers, and the repair scope it would have attempted.

Closing an item as FIXED writes the developer's resolution into Factory Memory,
which is how a human fix becomes an architectural lesson instead of a one-off.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("scraper_factory")


def queue_item(*, url: str, scraper=None, investigation=None, plan=None,
               diagnosis=None, generation_run=None,
               investigation_record=None, headline: str = "",
               references=None, notes: str = ""):
    """Create (or refresh) the review item for a build that could not continue."""
    from .models import ManualReviewItem, ReviewStatus

    evidence: list[str] = []
    failure_category = ""
    root_stage = ""

    if investigation is not None:
        root = investigation.root_cause()
        if root is not None:
            failure_category = root.failure
            root_stage = root.stage
            evidence.extend(e.render() for e in root.evidence[:4])
        evidence.extend(f"unknown: {u}" for u in investigation.all_unknowns()[:5])

    if diagnosis is not None and getattr(diagnosis, "root_cause", None):
        rc = diagnosis.root_cause
        failure_category = failure_category or rc.category
        root_stage = root_stage or rc.subsystem
        evidence.insert(0, rc.summary)
        evidence.extend(e.render() for e in rc.evidence[:4])

    if plan is not None:
        evidence.extend(f"blocker: {b}" for b in (plan.blockers or [])[:4])

    payload = {
        "url": url[:500],
        "scraper": scraper,
        "investigation": investigation_record,
        "generation_run": generation_run,
        "failure_category": (failure_category or "UNKNOWN")[:30],
        "root_cause_stage": root_stage[:20],
        "headline": (headline or _headline(investigation, plan, diagnosis))[:300],
        "planner_decision": plan.to_dict() if plan is not None else {},
        "evidence": evidence[:14],
        "suggested_strategy": (getattr(plan, "strategy", "") or "")[:20],
        "suggested_repair_scope": list(
            getattr(diagnosis, "repair_scope", []) or []),
        "similar_scrapers": list(references or []),
        "diagnostic": diagnosis.to_dict() if diagnosis is not None else {},
        "developer_notes": notes,
    }

    try:
        # One open item per URL: a portal that fails every night should not
        # produce a hundred identical tickets.
        existing = (ManualReviewItem.objects
                    .filter(url=payload["url"],
                            status__in=[ReviewStatus.OPEN,
                                        ReviewStatus.IN_PROGRESS])
                    .order_by("-created_at").first())
        if existing is not None:
            for key, value in payload.items():
                setattr(existing, key, value)
            existing.save()
            logger.info("[factory] refreshed review item #%s for %s",
                        existing.pk, url[:60])
            return existing

        item = ManualReviewItem.objects.create(**payload)
        logger.info("[factory] queued review item #%s (%s) for %s",
                    item.pk, item.failure_category, url[:60])
        _alert(item, scraper)
        return item
    except Exception as exc:  # noqa: BLE001 — never fail a build over the queue
        logger.warning("[factory] could not queue review item: %s", exc)
        return None


def resolve(item, *, resolution: str, user=None, status=None):
    """Close an item and turn the developer's fix into Factory Memory."""
    from django.utils import timezone

    from .models import ReviewStatus

    status = status or ReviewStatus.FIXED
    item.status = status
    item.resolution = resolution
    item.resolved_by = user
    item.resolved_at = timezone.now()
    item.save()

    if status == ReviewStatus.FIXED and resolution.strip():
        _remember(item, resolution)
    logger.info("[factory] review item #%s closed as %s", item.pk, status)
    return item


# ── internals ─────────────────────────────────────────────────────────────────

def _remember(item, resolution: str) -> None:
    """Store a human fix as an architectural lesson.

    Keyed on the structural signature of the investigation, so the lesson
    applies to the next portal built the same way rather than only to this URL.
    """
    from .learning import LearningStore
    from .models import LearningEntry

    record = item.investigation
    signature = getattr(record, "site_signature", "") or ""
    if not signature:
        logger.debug("[factory] review item #%s has no signature; not learning",
                     item.pk)
        return

    lesson = (f"{item.failure_category}: a developer fixed this by "
              f"{resolution.strip()}")
    try:
        row, created = LearningEntry.objects.get_or_create(
            site_signature=signature, lesson=lesson[:2000],
            defaults={"kind": LearningEntry.Kind.DEVELOPER_EDIT,
                      "family": (getattr(record, "data_source", "") or "")[:20],
                      "framework": "",
                      "scraper": item.scraper, "is_active": True})
        if not created:
            from django.db.models import F
            LearningEntry.objects.filter(pk=row.pk).update(
                times_applied=F("times_applied") + 1)
        logger.info("[factory] learned from review item #%s", item.pk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] could not learn from review item: %s", exc)


def _alert(item, scraper) -> None:
    from . import alerting
    alerting.raise_alert(
        rule=alerting.Rule.REVIEW_QUEUED, scraper=scraper,
        message=f"queued for developer review: {item.failure_category}",
        evidence=item.evidence[:4],
        extra={"review_item_id": item.pk, "url": item.url})


def _headline(investigation, plan, diagnosis) -> str:
    if diagnosis is not None and getattr(diagnosis, "root_cause", None):
        return diagnosis.root_cause.summary
    if plan is not None and plan.blockers:
        return plan.blockers[0]
    if investigation is not None:
        root = investigation.root_cause()
        if root is not None:
            return f"investigation stopped at {root.stage}: {root.failure}"
    return "the Factory could not complete this build"
