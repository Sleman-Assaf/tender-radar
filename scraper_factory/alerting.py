"""Factory alerts — every alert must be actionable on its own.

An alert that says "scraper failed" costs a developer more time than it saves:
they have to go and find out what happened before they can decide whether to
care. So every alert here carries three things:

    ROOT CAUSE          what actually broke, from the diagnostic engine
    EVIDENCE            the observations that prove it
    RECOMMENDED ACTION  what a human should do next, including "nothing yet"

The recommended action is derived from the diagnosis, not written by hand per
call site, so the same failure always yields the same advice.

Storage reuses the existing FactoryAlert table — its `details` JSONField holds
the structured payload, so no production table changes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("scraper_factory")


class Rule:
    """The alert conditions Phase 4A requires."""

    REPEATED_FAILURES = "REPEATED_FAILURES"
    SCRAPER_DISABLED = "SCRAPER_DISABLED"
    SCRAPER_DEGRADED = "SCRAPER_DEGRADED"
    SCRAPER_RECOVERED = "SCRAPER_RECOVERED"
    INVESTIGATION_FAILED = "INVESTIGATION_FAILED"
    REPAIR_BUDGET_SPENT = "REPAIR_BUDGET_SPENT"
    API_UNAVAILABLE = "API_UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BENCHMARK_REGRESSION = "BENCHMARK_REGRESSION"
    SCHEDULER_FAILURE = "SCHEDULER_FAILURE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    REVIEW_QUEUED = "REVIEW_QUEUED"


#: rule → (severity, recommended action). The action is deliberately concrete:
#: "investigate" is not an action, "confirm the listing URL" is.
_ADVICE: dict[str, tuple[str, str]] = {
    Rule.REPEATED_FAILURES: (
        "WARNING",
        "Run `factory_investigate <url>` to see whether the portal changed. If it "
        "did, regenerate; if it is down, wait — the scraper will recover on its "
        "own once the site returns."),
    Rule.SCRAPER_DISABLED: (
        "CRITICAL",
        "This source is no longer collecting. Re-run the investigation, fix or "
        "regenerate the scraper, then set ScraperSource.is_active back to true. "
        "The generated file is untouched, so nothing has to be rebuilt from "
        "scratch."),
    Rule.SCRAPER_DEGRADED: (
        "WARNING",
        "No action needed yet — the scraper keeps running and may recover by "
        "itself. Act if it stays degraded across several cycles."),
    Rule.SCRAPER_RECOVERED: (
        "INFO", "None. Recorded so the earlier alerts can be closed."),
    Rule.INVESTIGATION_FAILED: (
        "WARNING",
        "Check the root cause first: a network or proxy failure needs "
        "infrastructure attention, not a new scraper. If the URL is a landing "
        "page, supply the actual listing URL instead."),
    Rule.REPAIR_BUDGET_SPENT: (
        "WARNING",
        "The automatic repair loop gave up. Open the manual review item — it "
        "already contains the investigation, the plan and the suggested repair "
        "scope."),
    Rule.API_UNAVAILABLE: (
        "WARNING",
        "The portal's data endpoint stopped answering. Confirm the endpoint still "
        "exists; if it moved, re-run the investigation so the new one is "
        "captured."),
    Rule.AUTH_REQUIRED: (
        "WARNING",
        "This portal now needs credentials. A developer must supply them; the "
        "Factory will not guess at a login."),
    Rule.BENCHMARK_REGRESSION: (
        "CRITICAL",
        "A change made the Factory worse on portals that previously passed. "
        "Review the regression list in the benchmark run before shipping "
        "anything else."),
    Rule.SCHEDULER_FAILURE: (
        "CRITICAL",
        "The scheduled run did not complete. Check the server log around the "
        "scheduled time; tenders are not being collected until this is fixed."),
    Rule.MODEL_UNAVAILABLE: (
        "WARNING",
        "The code model is unreachable, so no new scrapers can be generated. "
        "Existing scrapers keep running normally."),
    Rule.REVIEW_QUEUED: (
        "INFO",
        "A developer should pick this up from the manual review queue when "
        "convenient."),
}


def raise_alert(*, rule: str, scraper=None, message: str = "",
                diagnosis=None, evidence=None, extra=None, severity: str = ""):
    """Create one alert carrying root cause, evidence and recommended action.

    Returns the FactoryAlert row, or None if it could not be stored — alerting
    must never be the reason a run fails.
    """
    from .models import AlertSeverity, FactoryAlert

    default_severity, action = _ADVICE.get(rule, ("WARNING", "Review the run."))
    severity = severity or default_severity
    valid = {c[0] for c in AlertSeverity.choices}
    if severity not in valid:
        severity = "WARNING"

    root_cause = ""
    collected: list[str] = list(evidence or [])
    if diagnosis is not None and getattr(diagnosis, "root_cause", None) is not None:
        rc = diagnosis.root_cause
        root_cause = f"{rc.subsystem} → {rc.category}"
        if rc.summary:
            collected.insert(0, rc.summary)
        collected.extend(e.render() for e in rc.evidence[:4])
        if diagnosis.suggestions:
            # The diagnosis speaks to this failure, the rule speaks to the
            # pattern; both are useful, but they must read as two sentences.
            specific = diagnosis.suggestions[0].strip()
            if specific and specific[-1] not in ".!?":
                specific += "."
            action = f"{specific[0].upper()}{specific[1:]} {action}"

    headline = message or root_cause or rule.replace("_", " ").lower()

    details = {
        "rule": rule,
        "root_cause": root_cause,
        "evidence": collected[:8],
        "recommended_action": action,
        "raised_by": "alerting.raise_alert",
    }
    if diagnosis is not None:
        details["repair_strategy"] = getattr(diagnosis, "repair_strategy", "")
        details["repair_scope"] = list(getattr(diagnosis, "repair_scope", []) or [])
        details["repairable"] = bool(getattr(diagnosis, "repairable", False))
    if extra:
        details.update(extra)

    if scraper is None:
        # FactoryAlert.scraper is NOT NULL, so a Factory-wide condition (the
        # model being down, a scheduler failure) has nowhere to live in that
        # table. Log it prominently instead of silently dropping it.
        logger.error("[factory][ALERT %s] %s | action: %s", rule, headline, action)
        return None

    try:
        alert = FactoryAlert.objects.create(
            scraper=scraper, rule=rule[:30], severity=severity,
            message=headline[:300], details=details,
            is_open=severity != "INFO")
        logger.warning("[factory][ALERT %s/%s] %s", severity, rule, headline[:160])
        return alert
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] could not store alert %s: %s", rule, exc)
        return None


def close_open_alerts(scraper, *, rules=None) -> int:
    """Resolve open alerts for a scraper that is working again."""
    from django.utils import timezone

    from .models import FactoryAlert
    try:
        qs = FactoryAlert.objects.filter(scraper=scraper, is_open=True)
        if rules:
            qs = qs.filter(rule__in=list(rules))
        return qs.update(is_open=False, resolved_at=timezone.now())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] could not close alerts: %s", exc)
        return 0


def render(alert) -> str:
    """The three-part form, for a console or an e-mail."""
    d = alert.details or {}
    lines = [f"[{alert.severity}] {alert.rule} — {alert.message}"]
    if d.get("root_cause"):
        lines.append(f"  ROOT CAUSE : {d['root_cause']}")
    for e in (d.get("evidence") or [])[:5]:
        lines.append(f"  EVIDENCE   : {e}")
    if d.get("recommended_action"):
        lines.append(f"  ACTION     : {d['recommended_action']}")
    return "\n".join(lines)
