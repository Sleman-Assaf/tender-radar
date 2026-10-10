"""Scraper Factory web surface — UI page plus a small JSON API.

Two execution modes are exposed, matching the CLI:

    Generate & Test     (dry_run) — full pipeline, then everything rolled back
    Generate & Install  (install) — real transactional installation

The employee-facing response carries only a status and a one-line message.
Prompts, generated code, validation reports, benchmark reports and extracted
rows are never returned to a non-staff caller; developers see them in the
review queue instead.

Builds run synchronously: a generation takes tens of seconds, which is
acceptable for a single click and keeps the MVP free of a task queue. If it ever
needs to be asynchronous, only this module changes.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .generation.pipeline import ScraperFactoryPipeline
from .installation.installer import MODE_DRY_RUN, MODE_INSTALL
from .models import FactoryStatus, GeneratedScraper

logger = logging.getLogger("scraper_factory")

MODES = {"dry_run": MODE_DRY_RUN, "install": MODE_INSTALL}


def _is_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_staff)


# ── UI ────────────────────────────────────────────────────────────────────────

@login_required
@require_GET
def factory_home(request):
    """The one-click page: paste a URL, choose a mode, click."""
    recent = (GeneratedScraper.objects
              .select_related("country")
              .order_by("-created_at")[:15])

    # Tell the user plainly when generation is simulated, so a placeholder
    # scraper is never mistaken for the real model's work.
    simulated = False
    try:
        from .models_ai.auto import active_model_summary
        simulated = bool(active_model_summary().get("simulated"))
    except Exception as exc:  # noqa: BLE001 — the page must render regardless
        logger.debug("[factory] could not read model status: %s", exc)

    return render(request, "factory/home.html", {
        "recent": recent,
        "is_staff": _is_staff(request.user),
        "simulated": simulated,
        "queue_count": GeneratedScraper.objects.filter(
            status=FactoryStatus.AWAITING_DEVELOPER).count(),
    })


@login_required
@require_GET
def operations_dashboard(request):
    """Operational view of every installed generated scraper.

    Recomputed on each request from the health, metric and investigation tables,
    so it is current by construction — there is no cache to invalidate and no
    "last updated" caveat to explain.

    Staff only: it exposes diagnostic categories and recommended actions, which
    are developer material, not employee material.
    """
    if not _is_staff(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    from . import operations

    window = _int_param(request, "days", default=7, low=1, high=365)
    return render(request, "factory/operations.html", {
        "overviews": operations.scraper_overviews(),
        "summary": operations.fleet_summary(days=window),
        "strategies": operations.strategy_performance(days=max(window, 30)),
        "window_days": window,
        "is_staff": True,
    })


@login_required
@require_GET
def review_queue(request):
    """Manual review queue — everything a developer needs, in one place."""
    if not _is_staff(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    from .models import ManualReviewItem, ReviewStatus

    status = request.GET.get("status") or ReviewStatus.OPEN
    valid = {c[0] for c in ReviewStatus.choices}
    items = (ManualReviewItem.objects
             .select_related("scraper", "investigation")
             .order_by("-created_at"))
    if status in valid:
        items = items.filter(status=status)

    counts = {choice: ManualReviewItem.objects.filter(status=choice).count()
              for choice, _ in ReviewStatus.choices}
    return render(request, "factory/review_queue.html", {
        "items": items[:100],
        "status": status,
        "counts": counts,
        "statuses": ReviewStatus.choices,
        "is_staff": True,
    })


@login_required
@require_POST
def review_resolve(request, pk: int):
    """Move a review item along, and write the fix into Factory Memory.

    The queue was read-only until now, so closing an item meant a shell call —
    which in practice meant the lesson never got written and each portal of the
    same shape was rediagnosed from scratch.

    Status transitions are restricted rather than free-form. Reopening a closed
    item is allowed (a fix that did not hold is common); what is refused is
    closing as FIXED with no description of the fix, because that is precisely
    the case that produces an empty lesson and teaches the Factory nothing.
    """
    if not _is_staff(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    from django.contrib import messages
    from django.shortcuts import redirect, get_object_or_404

    from . import review
    from .models import ManualReviewItem, ReviewStatus

    item = get_object_or_404(ManualReviewItem, pk=pk)
    status = (request.POST.get("status") or "").strip()
    resolution = (request.POST.get("resolution") or "").strip()
    notes = request.POST.get("developer_notes")

    valid = {c[0] for c in ReviewStatus.choices}
    if status and status not in valid:
        messages.error(request, f"'{status}' is not a review status.")
        return redirect(_back(request))

    # Notes are independent of the status: a developer recording progress
    # mid-investigation should not have to close the item to save it.
    if notes is not None and notes.strip() != (item.developer_notes or "").strip():
        item.developer_notes = notes[:20000]
        item.save(update_fields=["developer_notes", "updated_at"])

    if not status or status == item.status:
        if notes is not None:
            messages.success(request, f"Notes saved on review item #{item.pk}.")
        return redirect(_back(request))

    closing = status in (ReviewStatus.FIXED, ReviewStatus.WONT_FIX)
    if closing and not resolution:
        messages.error(
            request,
            "Describe what you changed before closing this item — the text is "
            "what becomes the Factory's lesson for the next portal of this "
            "shape, and an empty one teaches it nothing.")
        return redirect(_back(request))

    if closing:
        # resolve() owns the transition, the timestamps and the Factory Memory
        # write, so the UI and the shell cannot drift apart.
        review.resolve(item, resolution=resolution, user=request.user,
                       status=status)
        learned = (status == ReviewStatus.FIXED
                   and bool(getattr(item.investigation, "site_signature", "")))
        messages.success(
            request,
            f"Review item #{item.pk} closed as {item.get_status_display()}."
            + (" The fix was recorded in Factory Memory." if learned else
               " No investigation signature is attached, so no lesson could be"
               " keyed to a site shape."))
    else:
        item.status = status
        # Reopening must clear the closure, or the item shows as open while
        # still claiming it was resolved last Tuesday.
        if status in (ReviewStatus.OPEN, ReviewStatus.IN_PROGRESS):
            item.resolved_at = None
            item.resolved_by = None
        if status == ReviewStatus.IN_PROGRESS and item.assigned_to is None:
            item.assigned_to = request.user
        item.save()
        messages.success(request,
                         f"Review item #{item.pk} is now "
                         f"{item.get_status_display()}.")

    logger.info("[factory] review item #%s set to %s by %s",
                item.pk, status, request.user)
    return redirect(_back(request))


def _back(request) -> str:
    """Return to the filtered queue the developer came from."""
    from django.urls import reverse

    nxt = request.POST.get("next") or ""
    # Only same-site relative paths, so a crafted form cannot bounce a staff
    # user off to another host.
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return reverse("factory:review_queue")


def _int_param(request, name: str, *, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(request.GET.get(name, default))))
    except (TypeError, ValueError):
        return default


# ── API ───────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def api_build(request):
    """POST {url, mode, name?, country?} -> the employee-facing outcome."""
    payload = _payload(request)
    url = (payload.get("url") or "").strip()
    mode_key = (payload.get("mode") or "dry_run").strip()

    if not url:
        return JsonResponse({"ok": False,
                             "message": "Please enter a website address."},
                            status=400)
    if not url.startswith(("http://", "https://")):
        return JsonResponse(
            {"ok": False,
             "message": "The address must start with http:// or https://."},
            status=400)
    if mode_key not in MODES:
        return JsonResponse({"ok": False, "message": "Unknown mode."},
                            status=400)

    # Installing writes code into the project and activates a new source, so it
    # is staff-only even though no approval step remains in the flow.
    if mode_key == "install" and not _is_staff(request.user):
        return JsonResponse(
            {"ok": False,
             "message": "You can test a website, but only staff can install."},
            status=403)

    pipeline = ScraperFactoryPipeline()
    try:
        outcome = pipeline.build(
            url, user=request.user, mode=MODES[mode_key],
            country_code=(payload.get("country") or "").strip(),
            source_name=(payload.get("name") or "").strip(),
            with_save=bool(payload.get("with_save")))
    except Exception as exc:  # noqa: BLE001 — never leak a traceback to the UI
        logger.error("[factory] api_build failed for %s: %s", url, exc,
                     exc_info=True)
        return JsonResponse(
            {"ok": False, "needs_developer": True,
             "message": ("We could not add this website automatically. "
                         "It has been sent to the development team.")},
            status=200)

    body = {
        "ok": outcome.ok,
        "mode": mode_key,
        "stage": outcome.stage,
        "message": outcome.headline,
        "detail": outcome.detail,
        "tenders": outcome.tenders_saved,
        "needs_developer": outcome.needs_developer,
        "duration_ms": outcome.duration_ms,
        "scraper_id": outcome.scraper_id,
        # The extracted tenders, so the result can be inspected instead of
        # taken on trust. These are tender listings, not internal diagnostics.
        "rows": outcome.extracted_rows,
        "field_completeness": _completeness(outcome.extracted_rows),
    }
    # Internals are for developers only.
    if _is_staff(request.user):
        body["internal"] = outcome.internal
    return JsonResponse(body)


@login_required
@require_GET
def api_status(request):
    """Lightweight status feed for the UI's recent-activity list."""
    rows = []
    for rec in GeneratedScraper.objects.order_by("-created_at")[:20]:
        rows.append({
            "id": rec.pk,
            "name": rec.name,
            "url": rec.website_url,
            "status": rec.status,
            "status_label": rec.get_status_display(),
            "family": rec.family,
            "installed": rec.status == FactoryStatus.INSTALLED,
            "created_at": rec.created_at.isoformat(),
        })
    return JsonResponse({"ok": True, "results": rows})


# ── helpers ───────────────────────────────────────────────────────────────────

def _completeness(rows: list[dict]) -> dict:
    """Per-field fill rate across the extracted rows — a quick quality read."""
    if not rows:
        return {}
    fields = ("title", "source_url", "reference_no", "submission_deadline",
              "entity", "publication_date")
    out = {}
    for name in fields:
        filled = sum(1 for r in rows if str(r.get(name) or "").strip())
        out[name] = round(filled * 100 / len(rows))
    return out


def _payload(request) -> dict:
    if request.content_type and "json" in request.content_type:
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return {}
    return request.POST.dict()
