"""Post-installation health tracking for generated scrapers.

Policy, as specified: a run that collects nothing does **not** disable the
source. It records the failure, raises an alert, marks the scraper DEGRADED,
stores the investigation and diagnostic reports, and keeps monitoring. Only
consecutive failures past a configured threshold disable it.

The reasoning matters more than the mechanism: portals go down, get redesigned
for a day, and sit behind flaky proxies. A source that auto-disables on the
first bad night quietly stops delivering tenders, and nobody notices until
someone asks why a country went quiet. A DEGRADED source keeps trying and keeps
complaining, which is the behaviour that actually gets it fixed.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("scraper_factory")


class HealthMonitor:
    """Applies the degradation policy after each run of a generated scraper."""

    def __init__(self, *, disable_after: int | None = None):
        self.disable_after = int(
            disable_after if disable_after is not None
            else getattr(settings, "FACTORY_DISABLE_AFTER_FAILURES", 5))

    # ── public API ────────────────────────────────────────────────────────────

    def record_run(self, scraper, *, tenders_found: int, diagnosis=None,
                   tender_report=None, investigation_record=None,
                   error: str = ""):
        """Record one execution and return the updated health row."""
        from .models import HealthState, ScraperHealth

        health, _ = ScraperHealth.objects.get_or_create(scraper=scraper)
        health.total_runs += 1
        health.last_tenders_found = tenders_found

        succeeded = tenders_found > 0 and not error
        if tender_report is not None:
            # Rows that are not tenders are not a success, however many there are.
            succeeded = succeeded and tender_report.rows_valid > 0
            health.last_validation = {
                "rows_total": tender_report.rows_total,
                "rows_valid": tender_report.rows_valid,
                "stop_category": tender_report.stop_category,
                "confidence": tender_report.confidence,
                "notes": tender_report.notes[:4],
            }
        if diagnosis is not None:
            health.last_diagnosis = diagnosis.to_dict()
        if investigation_record is not None:
            health.last_investigation = investigation_record

        if succeeded:
            was_degraded = health.state == HealthState.DEGRADED
            health.consecutive_failures = 0
            health.last_ok_at = timezone.now()
            if health.state != HealthState.DISABLED:
                health.state = HealthState.HEALTHY
            health.save()
            if was_degraded:
                from . import alerting
                alerting.raise_alert(
                    rule=alerting.Rule.SCRAPER_RECOVERED, scraper=scraper,
                    message=f"collecting again ({tenders_found} tender(s))")
                # The earlier warnings described a state that no longer exists.
                alerting.close_open_alerts(
                    scraper, rules=[alerting.Rule.SCRAPER_DEGRADED,
                                    alerting.Rule.REPEATED_FAILURES])
                logger.info("[factory] %s recovered", scraper.class_name)
            return health

        # ── a failed run ─────────────────────────────────────────────────────
        health.consecutive_failures += 1
        health.total_failures += 1
        health.last_failure_at = timezone.now()

        reason = self._reason(diagnosis, tender_report, error)

        from . import alerting

        if health.consecutive_failures >= self.disable_after:
            health.state = HealthState.DISABLED
            health.disabled_at = timezone.now()
            health.disabled_reason = reason[:300]
            health.save()
            self._deactivate_source(scraper)
            alerting.raise_alert(
                rule=alerting.Rule.SCRAPER_DISABLED, scraper=scraper,
                message=(f"disabled after {health.consecutive_failures} "
                         f"consecutive failures: {reason}"),
                diagnosis=diagnosis,
                extra={"consecutive_failures": health.consecutive_failures,
                       "threshold": self.disable_after})
            logger.warning("[factory] %s DISABLED after %s consecutive failures",
                           scraper.class_name, health.consecutive_failures)
            return health

        health.state = HealthState.DEGRADED
        health.save()
        # One failure is noise; a run of them is a pattern. Escalate the rule so
        # the recommended action changes from "wait" to "go and look".
        rule = (alerting.Rule.REPEATED_FAILURES
                if health.consecutive_failures >= 2
                else alerting.Rule.SCRAPER_DEGRADED)
        alerting.raise_alert(
            rule=rule, scraper=scraper,
            message=(f"failure {health.consecutive_failures}/"
                     f"{self.disable_after}: {reason}"),
            diagnosis=diagnosis,
            extra={"consecutive_failures": health.consecutive_failures,
                   "threshold": self.disable_after})
        logger.warning("[factory] %s DEGRADED (%s/%s): %s", scraper.class_name,
                       health.consecutive_failures, self.disable_after, reason[:120])
        return health

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _reason(diagnosis, tender_report, error: str) -> str:
        if diagnosis is not None and diagnosis.root_cause is not None:
            return f"{diagnosis.subsystem}/{diagnosis.category} — " \
                   f"{diagnosis.root_cause.summary}"
        if tender_report is not None and tender_report.stop_category:
            return (f"{tender_report.stop_category} — none of the "
                    f"{tender_report.rows_total} extracted row(s) is a tender")
        if error:
            return error[:200]
        return "the run collected no tenders and no diagnosis was produced"

    @staticmethod
    def _deactivate_source(scraper) -> None:
        """Stop the scheduler running a scraper we have given up on.

        Only the ScraperSource row is touched. The generated file stays on disk
        and stays registered, so re-enabling is a one-field change once the
        portal is fixed — nothing has to be regenerated.
        """
        from tenders.models import ScraperSource
        try:
            updated = ScraperSource.objects.filter(
                scraper_class=scraper.class_name).update(is_active=False)
            logger.info("[factory] deactivated %s source row(s) for %s",
                        updated, scraper.class_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] could not deactivate source: %s", exc)
