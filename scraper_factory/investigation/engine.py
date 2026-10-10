"""The Investigation Engine — runs the stages and owns tier escalation.

Order is fixed (contracts.Stage.ORDER) because it is also the root-cause order:
the first stage that fails is the real problem and everything after it is a
symptom. When a stage fails hard the engine stops; continuing would produce
confident nonsense about a page nobody could even fetch.

Escalation to Tier 2 is deliberately narrow. A browser is only launched when
Tier 1 finished without proving where the tender data comes from — never
"because the site looks modern". The reason is always recorded, so an expensive
run can be justified after the fact.
"""

from __future__ import annotations

import hashlib
import logging
import time

from django.conf import settings

from .authentication import AuthenticationDetector
from .contracts import Failure, InvestigationReport, Stage, StageResult, UNKNOWN
from .discovery import ApiDiscovery, EmbeddedJsonFinder, RenderingAnalyzer
from .http_probe import HttpProbe
from .pagination import PaginationDetector
from .rows import CandidateRowDetector
from .technology import LocaleDetector, TechnologyDetector

logger = logging.getLogger("scraper_factory")


class InvestigationEngine:
    """Produces the InvestigationReport that gates all generation."""

    def __init__(self, *, http=None, technology=None, auth=None, rendering=None,
                 api=None, pagination=None, rows=None, tier2=None,
                 snapshot_store=None):
        self.http = http or HttpProbe()
        self.technology = technology or TechnologyDetector()
        self.auth = auth or AuthenticationDetector()
        self.rendering = rendering or RenderingAnalyzer()
        self.api = api or ApiDiscovery()
        self.pagination = pagination or PaginationDetector()
        self.rows = rows or CandidateRowDetector(
            sample_chars=int(getattr(settings, "FACTORY_HTML_SAMPLE_CHARS", 14000)))
        self.embedded = EmbeddedJsonFinder()
        self._tier2 = tier2
        self._snapshots = snapshot_store
        self.locale = LocaleDetector()

    # ── public API ────────────────────────────────────────────────────────────

    def investigate(self, url: str, *, allow_tier2: bool | None = None,
                    capture_snapshot: bool = True,
                    scraper=None) -> InvestigationReport:
        started = time.monotonic()
        report = InvestigationReport(url=url)

        if allow_tier2 is None:
            allow_tier2 = bool(getattr(settings, "FACTORY_TIER2_ENABLED", True))

        # ── Stage 1: HTTP ────────────────────────────────────────────────────
        http = report.record(self.http.run(url))
        if http.failure == Failure.BOT_PROTECTION and allow_tier2:
            # Bot protection is the one HTTP failure a browser routinely
            # passes: the production ADB scraper works nightly against a portal
            # that 403s every HTTP client rung. Returning here — as this used
            # to — meant the tiered design never got to prove that. apply_tier2
            # can conclude the whole investigation from the browser run alone
            # (captured XHR becomes a proven API; the hydrated DOM yields rows).
            self._escalate(report, "every HTTP client is blocked (bot "
                                   "protection); trying a real browser")
            got_content = bool(
                report.stage(Stage.API_DISCOVERY).data.get("proven_endpoint")
                or report.stage(Stage.RENDERING).data.get("rendered_structure"))
            if report.escalated and got_content:
                # The block is a fact about HTTP clients, not about the site.
                # Left as a failure it stays the root cause and the planner
                # refuses a portal the browser just read.
                http.failure = ""
                http.conclude("REACHABLE_VIA_BROWSER", 80)
                http.add("blocked for every HTTP client, served to a real "
                         "browser", where="tier 2",
                         detail="generated code must use the browser or the "
                                "captured request, never a plain GET")
                report.record(http)
                self._collect_sample(report)
                report.site_signature = self._signature(report)
                if capture_snapshot:
                    self._snapshot(report, http, scraper)
            return self._finish(report, started)
        if http.failure:
            logger.info("[factory] investigation stopped at HTTP: %s", http.failure)
            return self._finish(report, started)

        body = http.data.get("body") or ""

        # ── Stage 2: technology ──────────────────────────────────────────────
        tech = report.record(self.technology.run(url, http))
        report.language, report.country_hint = self.locale.detect(url, body)

        # ── Stage 3: authentication ──────────────────────────────────────────
        auth = report.record(self.auth.run(url, http, tech))
        if auth.failure:
            logger.info("[factory] investigation stopped at AUTH: %s", auth.failure)
            return self._finish(report, started)

        # ── Stage 4: rendering ───────────────────────────────────────────────
        rendering = report.record(self.rendering.run(url, http, tech))

        # ── Stage 5: API discovery (static) ──────────────────────────────────
        api = report.record(self.api.run(url, http, tech))
        embedded = self.embedded.find(body)
        if embedded:
            api.data["embedded_json"] = embedded
            usable = [e for e in embedded if e["tender_like"] and e["records"] >= 3]
            if usable and not api.data.get("proven_endpoint"):
                best = max(usable, key=lambda e: e["records"])
                api.add(f"{best['records']} record(s) embedded in the page as "
                        f"{best['source']}",
                        where=best["items_path"] or "inline JSON",
                        detail="no extra request needed")
                api.data["embedded_source"] = best
                if api.conclusion in ("NONE_FOUND", "NONE_REACHABLE",
                                      "CANDIDATES_UNCONFIRMED"):
                    api.conclude("EMBEDDED", 88)
            report.record(api)

        # ── Stage 6: pagination ──────────────────────────────────────────────
        report.record(self.pagination.run(url, http, api, rendering))

        # ── Stage 7: candidate rows ──────────────────────────────────────────
        report.record(self.rows.run(url, http, rendering, api))

        # ── decide the data source, then escalate only if still unproven ─────
        self._resolve_data_source(report)

        reason = self._escalation_reason(report)
        if reason and allow_tier2:
            self._escalate(report, reason)
        elif reason:
            report.escalation_reason = f"{reason} (Tier 2 disabled by settings)"
            report.stage(Stage.RENDERING).unknowns.append(
                "browser investigation was not permitted")

        self._collect_sample(report)
        report.site_signature = self._signature(report)

        if capture_snapshot:
            self._snapshot(report, http, scraper)

        return self._finish(report, started)

    # ── data source resolution ────────────────────────────────────────────────

    @staticmethod
    def _resolve_data_source(report: InvestigationReport) -> None:
        """Name the single place the tenders come from, or admit UNKNOWN."""
        api = report.stage(Stage.API_DISCOVERY)
        rendering = report.stage(Stage.RENDERING)
        rows = report.stage(Stage.CANDIDATE_ROWS)

        if api.data.get("proven_endpoint"):
            report.data_source = "GRAPHQL" if api.data.get("graphql") else "API"
            report.data_source_confidence = api.confidence
            return
        if api.conclusion == "EMBEDDED":
            report.data_source = "JS_STATE"
            report.data_source_confidence = api.confidence
            return
        if rows.conclusion == "FOUND" and rendering.conclusion in (
                "SERVER_RENDERED", "SERVER_RENDERED_NO_LISTING"):
            report.data_source = "HTML"
            report.data_source_confidence = min(rows.confidence, rendering.confidence)
            return
        if rows.conclusion == "API_RECORDS":
            report.data_source = "API"
            report.data_source_confidence = rows.confidence
            return
        report.data_source = UNKNOWN
        report.data_source_confidence = 0

    @staticmethod
    def _escalation_reason(report: InvestigationReport) -> str:
        """Why a browser is needed — empty string means Tier 1 was sufficient."""
        if report.data_source in ("GRAPHQL", "JS_STATE"):
            return ""                       # a request we can reproduce: no browser
        if report.data_source == "API":
            # "API" alone is not reproducibility. tenderjo's endpoint answers a
            # bare GET with its *categories*; the tenders need fifteen query
            # parameters that only the page's own traffic reveals — the
            # hand-written scraper's author read them out of the browser, and
            # Tier 2's network capture sees exactly the same thing. Skipping
            # the browser is only safe once the endpoint is proven, params and
            # all.
            if report.stage(Stage.API_DISCOVERY).data.get("proven_endpoint"):
                return ""
            return ("an API serves the data but its real request was never "
                    "captured — the browser must observe the parameters the "
                    "page itself sends")
        rendering = report.stage(Stage.RENDERING)
        rows = report.stage(Stage.CANDIDATE_ROWS)
        pagination = report.stage(Stage.PAGINATION)

        if report.data_source == "HTML":
            # Rows are in the HTML. Only escalate if paging is genuinely opaque,
            # because otherwise page two is unreachable.
            if pagination.conclusion in ("LOAD_MORE", "INFINITE_SCROLL"):
                return ("rows are in the HTML but paging is script-driven; "
                        "the follow-up request must be captured")
            return ""
        if rendering.conclusion == "JAVASCRIPT_RENDERED":
            return "listing is rendered by JavaScript and no API was proven"
        if rows.conclusion in ("NONE", "NOT_IN_HTML"):
            return "no candidate rows in the served HTML and no API was proven"
        return "Tier 1 could not prove where the tender data comes from"

    def _escalate(self, report: InvestigationReport, reason: str) -> None:
        from .tier2 import BrowserUnavailable, Tier2Investigator, apply_tier2

        investigator = self._tier2 or Tier2Investigator()
        report.escalation_reason = reason
        logger.info("[factory] escalating to Tier 2: %s", reason)

        try:
            observations = investigator.run(report.url)
        except BrowserUnavailable as exc:
            report.escalated = False
            report.escalation_reason = f"{reason} — browser unavailable: {exc}"
            rendering = report.stage(Stage.RENDERING)
            rendering.unknowns.append(f"Tier 2 unavailable: {exc}")
            report.record(rendering)
            if report.data_source == UNKNOWN:
                rows = report.stage(Stage.CANDIDATE_ROWS)
                if not rows.failure:
                    rows.fail(Failure.JAVASCRIPT_RENDERING,
                              "listing needs a browser and none is installed")
                    report.record(rows)
            logger.warning("[factory] Tier 2 unavailable: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001 — a browser crash is evidence
            report.escalated = False
            report.escalation_reason = f"{reason} — browser run failed: {exc}"
            rendering = report.stage(Stage.RENDERING)
            rendering.unknowns.append(f"Tier 2 crashed: {type(exc).__name__}: {exc}")
            report.record(rendering)
            logger.warning("[factory] Tier 2 crashed: %s", exc, exc_info=True)
            return

        report.escalated = True
        report.tier = 2
        apply_tier2(report, observations)
        report.stages[Stage.RENDERING].data["tier2_duration_ms"] = (
            observations.get("duration_ms", 0))
        self._resolve_data_source(report)

        if report.data_source == UNKNOWN:
            rows = report.stage(Stage.CANDIDATE_ROWS)
            if not rows.failure and rows.conclusion != "FOUND":
                rows.fail(Failure.JAVASCRIPT_RENDERING,
                          "no rows and no data request even after rendering")
                report.record(rows)

    # ── sample, signature, snapshot ───────────────────────────────────────────

    @staticmethod
    def _collect_sample(report: InvestigationReport) -> None:
        """The markup or payload the prompt will derive selectors from."""
        rows = report.stage(Stage.CANDIDATE_ROWS)
        sample = rows.data.get("sample") or ""
        if not sample:
            best = rows.data.get("best") or {}
            sample = best.get("sample") or ""
        if not sample:
            api = report.stage(Stage.API_DISCOVERY)
            embedded = api.data.get("embedded_source")
            if embedded:
                sample = (f"// embedded {embedded['source']} · "
                          f"{embedded['records']} record(s) at "
                          f"{embedded['items_path']}\n"
                          f"// fields: {', '.join(embedded['fields'][:20])}")
        report.listing_sample = sample

    @staticmethod
    def _signature(report: InvestigationReport) -> str:
        """Structural key for learning and retrieval — URL deliberately excluded."""
        tech = report.stage(Stage.TECHNOLOGY)
        auth = report.stage(Stage.AUTHENTICATION)
        rendering = report.stage(Stage.RENDERING)
        pagination = report.stage(Stage.PAGINATION)
        rows = report.stage(Stage.CANDIDATE_ROWS)
        best = rows.data.get("best") or {}
        parts = [
            report.data_source, tech.conclusion, auth.conclusion,
            rendering.conclusion, pagination.conclusion,
            str(auth.data.get("has_viewstate", False)),
            best.get("selector", ""), best.get("where", ""),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _snapshot(self, report: InvestigationReport, http: StageResult,
                  scraper) -> None:
        try:
            from ..analysis.snapshot import SnapshotStore
            store = self._snapshots or SnapshotStore()
            content_type = (http.data.get("content_type") or "").lower()
            kind = ("JSON_PAYLOAD" if "json" in content_type else "RAW_HTML")
            snap = store.capture(
                url=report.url, body=http.data.get("body") or "", kind=kind,
                status_code=http.data.get("status"),
                headers=http.data.get("headers") or {},
                cookies={c: "" for c in http.data.get("cookies") or []},
                fetched_with=http.data.get("rung", "requests"), scraper=scraper)
            report.snapshot_id = snap.pk
        except Exception as exc:  # noqa: BLE001 — never fail an investigation on this
            logger.warning("[factory] snapshot capture failed: %s", exc)

    @staticmethod
    def _finish(report: InvestigationReport, started: float) -> InvestigationReport:
        report.duration_ms = int((time.monotonic() - started) * 1000)
        rc = report.root_cause()
        logger.info(
            "[factory] investigation %s → source=%s tier=%s conf=%s%s",
            report.url[:60], report.data_source, report.tier,
            report.overall_confidence(),
            f" ROOT_CAUSE={rc.stage}/{rc.failure}" if rc else "")
        return report
