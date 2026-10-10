"""The Planner — turns an InvestigationReport into a generation strategy.

This is the layer that stops the Factory being one generic pipeline. A JSON API
and an ASP.NET postback grid need different code, different prompt fragments and
different repairs; deciding that *once*, from evidence, is what keeps the rest of
the system honest.

Rules the Planner obeys:
  * a strategy is only selected when the evidence supports it — otherwise the
    plan is not viable and generation must not start
  * the cheapest strategy that fits wins; a browser-assisted scraper is a last
    resort because it is slow and fragile in production
  * every decision carries its reasons, so a rejected plan can be explained to
    the employee and argued with by a developer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..investigation.contracts import InvestigationReport, Stage, UNKNOWN

logger = logging.getLogger("scraper_factory")


class Strategy:
    """The scraper shapes the Factory knows how to generate."""

    STATIC_HTML = "STATIC_HTML"
    API = "API"
    GRAPHQL = "GRAPHQL"
    JAVASCRIPT = "JAVASCRIPT"              # data in page state, no extra request
    BROWSER_ASSISTED = "BROWSER_ASSISTED"
    ASPNET_POSTBACK = "ASPNET_POSTBACK"
    LOGIN_SESSION = "LOGIN_SESSION"
    HYBRID = "HYBRID"                      # listing one way, detail another
    NONE = "NONE"

    #: Cheapest and most robust first. Used only for reporting the ranking.
    PREFERENCE = (API, GRAPHQL, STATIC_HTML, JAVASCRIPT, ASPNET_POSTBACK,
                  LOGIN_SESSION, HYBRID, BROWSER_ASSISTED)


#: Corpus families the installed scrapers already use. The strategy maps onto
#: one of these so retrieval keeps returning real reference files.
_FAMILY_FOR = {
    Strategy.API: "JSON_API",
    Strategy.GRAPHQL: "JSON_API",
    Strategy.JAVASCRIPT: "JSON_API",
    Strategy.STATIC_HTML: "HTML_CUSTOM",
    Strategy.ASPNET_POSTBACK: "HTML_CUSTOM",
    Strategy.LOGIN_SESSION: "HTML_CUSTOM",
    Strategy.HYBRID: "HTML_CUSTOM",
    Strategy.BROWSER_ASSISTED: "PLAYWRIGHT",
}


@dataclass
class GenerationPlan:
    """What to build, why, and what the generator must be told."""

    strategy: str = Strategy.NONE
    family: str = ""
    confidence: int = 0
    viable: bool = False
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    # Concrete facts the prompt builder injects.
    directives: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy, "family": self.family,
            "confidence": self.confidence, "viable": self.viable,
            "reasons": list(self.reasons), "blockers": list(self.blockers),
            "risks": list(self.risks), "directives": dict(self.directives),
        }

    def render(self) -> str:
        lines = [f"PLAN: {self.strategy} ({self.confidence}%) "
                 f"— {'viable' if self.viable else 'NOT VIABLE'}"]
        for r in self.reasons:
            lines.append(f"  + {r}")
        for r in self.risks:
            lines.append(f"  ! {r}")
        for b in self.blockers:
            lines.append(f"  x {b}")
        return "\n".join(lines)


class Planner:
    """Selects the generation strategy from investigation evidence."""

    #: Below this the plan is reported but not acted on.
    MIN_CONFIDENCE = 50

    def plan(self, report: InvestigationReport) -> GenerationPlan:
        plan = GenerationPlan()

        root = report.root_cause()
        if root is not None:
            plan.blockers.append(
                f"investigation failed at {root.stage}: {root.failure}")
            plan.strategy = Strategy.NONE
            return plan

        auth = report.stage(Stage.AUTHENTICATION)
        tech = report.stage(Stage.TECHNOLOGY)
        api = report.stage(Stage.API_DISCOVERY)
        rendering = report.stage(Stage.RENDERING)
        pagination = report.stage(Stage.PAGINATION)
        rows = report.stage(Stage.CANDIDATE_ROWS)

        # ── gating conditions come first: they override the data source ──────
        if auth.data.get("gated") or auth.conclusion == "LOGIN_REQUIRED":
            plan.strategy = Strategy.LOGIN_SESSION
            plan.confidence = auth.confidence
            plan.reasons.append("the listing is behind a login")
            plan.blockers.append(
                "no credentials are configured for this portal — a developer "
                "must supply them before a scraper can be generated")
            plan.family = _FAMILY_FOR[Strategy.LOGIN_SESSION]
            return plan

        # ── the data source decides the rest ─────────────────────────────────
        source = report.data_source
        if source == "GRAPHQL":
            self._plan_graphql(plan, report, api)
        elif source == "API":
            self._plan_api(plan, report, api, pagination)
        elif source == "JS_STATE":
            self._plan_js_state(plan, report, api)
        elif source == "HTML":
            self._plan_html(plan, report, tech, auth, rows, pagination)
        else:
            self._plan_unknown(plan, report, rendering, rows)

        # ── cross-cutting adjustments ────────────────────────────────────────
        self._apply_fetch_rung(plan, report)
        self._apply_pagination(plan, pagination)
        self._apply_unknowns(plan, report)

        plan.family = _FAMILY_FOR.get(plan.strategy, "HTML_CUSTOM")
        plan.viable = (plan.strategy != Strategy.NONE
                       and not plan.blockers
                       and plan.confidence >= self.MIN_CONFIDENCE)
        if not plan.viable and not plan.blockers:
            plan.blockers.append(
                f"confidence {plan.confidence}% is below the {self.MIN_CONFIDENCE}% "
                f"threshold for automatic generation")
        logger.info("[factory] plan: %s (%s%%) viable=%s",
                    plan.strategy, plan.confidence, plan.viable)
        return plan

    # ── strategy builders ─────────────────────────────────────────────────────

    @staticmethod
    def _plan_api(plan: GenerationPlan, report, api, pagination) -> None:
        endpoint = api.data.get("proven_endpoint", "")
        info = next((e for e in api.data.get("endpoints", [])
                     if e.get("url") == endpoint), {})
        plan.strategy = Strategy.API
        plan.confidence = api.confidence
        plan.reasons.append(f"a JSON endpoint returns {info.get('records', 0)} "
                            f"record(s) directly")
        if api.data.get("proven_via") == "tier2_network":
            plan.reasons.append("endpoint was captured from the browser network log, "
                                "so the generated scraper needs no browser")
        plan.directives.update({
            "endpoint": endpoint,
            "method": info.get("method", "GET"),
            "items_path": info.get("items_path", ""),
            "fields": info.get("fields", []),
            "request_headers": api.data.get("request_headers", {}),
            "post_data": api.data.get("post_data", ""),
        })
        if info.get("method") == "POST" or api.data.get("post_data"):
            plan.risks.append("the endpoint needs a POST body; reproduce it exactly")

    @staticmethod
    def _plan_graphql(plan: GenerationPlan, report, api) -> None:
        plan.strategy = Strategy.GRAPHQL
        plan.confidence = max(api.confidence, 60)
        plan.reasons.append("the portal serves its data over GraphQL")
        plan.directives.update({
            "endpoint": api.data.get("proven_endpoint", ""),
            "post_data": api.data.get("post_data", ""),
            "request_headers": api.data.get("request_headers", {}),
        })
        if not api.data.get("post_data"):
            plan.risks.append(
                "the GraphQL query document was not captured; it must be "
                "reconstructed before this can work")
            plan.confidence = min(plan.confidence, 55)

    @staticmethod
    def _plan_js_state(plan: GenerationPlan, report, api) -> None:
        embedded = api.data.get("embedded_source") or {}
        plan.strategy = Strategy.JAVASCRIPT
        plan.confidence = api.confidence
        plan.reasons.append(
            f"the page ships its data inline as {embedded.get('source', 'JSON')} "
            f"({embedded.get('records', 0)} records) — parse the blob, no browser")
        plan.directives.update({
            "embedded_source": embedded.get("source", ""),
            "items_path": embedded.get("items_path", ""),
            "fields": embedded.get("fields", []),
        })

    @staticmethod
    def _plan_html(plan: GenerationPlan, report, tech, auth, rows,
                   pagination) -> None:
        best = rows.data.get("best") or {}
        # ASP.NET only forces postback when paging cannot be done with a GET —
        # measured, not assumed. A __VIEWSTATE page whose rows come back on a
        # plain GET with ?page= links is an ordinary static scraper.
        needs_postback = auth.data.get("has_viewstate") and (
            pagination.conclusion == "POSTBACK"
            or (auth.data.get("has_dopostback")
                and pagination.conclusion in ("UNKNOWN", "LOAD_MORE")))

        if needs_postback:
            plan.strategy = Strategy.ASPNET_POSTBACK
            plan.confidence = min(rows.confidence, 75)
            plan.reasons.append(
                "ASP.NET grid whose paging goes through __doPostBack, so page two "
                "requires a POST carrying __VIEWSTATE")
            plan.directives["postback"] = True
            plan.directives["event_targets"] = pagination.data.get(
                "event_targets", [])
        else:
            plan.strategy = Strategy.STATIC_HTML
            plan.confidence = rows.confidence
            plan.reasons.append(
                f"{best.get('count', 0)} tender row(s) are already in the served "
                f"HTML at {best.get('selector', '?')}")
            if auth.data.get("has_viewstate"):
                plan.reasons.append(
                    "the page carries __VIEWSTATE but the rows come back on a "
                    "plain GET, so no postback is needed")

        plan.directives.update({
            # The row-level selector, not the container's — see rows._row_css.
            "row_selector": best.get("css") or best.get("selector", ""),
            "row_container_selector": best.get("selector", ""),
            "row_count": best.get("count", 0),
            "row_container": best.get("where", ""),
            "sample_text": best.get("first_text", ""),
        })
        if not best.get("with_dates"):
            plan.risks.append(
                "no dates were visible in the candidate rows; the deadline may "
                "only exist on the detail page")
            plan.directives["dates_missing"] = True

    @staticmethod
    def _plan_unknown(plan: GenerationPlan, report, rendering, rows) -> None:
        if rendering.conclusion == "JAVASCRIPT_RENDERED":
            if report.escalated:
                plan.strategy = Strategy.BROWSER_ASSISTED
                plan.confidence = min(rows.confidence, 65) if rows.conclusion == "FOUND" else 40
                plan.reasons.append(
                    "rows only exist after JavaScript runs and no reproducible "
                    "request was found, so the scraper must drive a browser")
                plan.risks.append(
                    "browser scrapers are the slowest and most fragile family; "
                    "prefer a developer finding the underlying request")
                if rows.conclusion != "FOUND":
                    plan.blockers.append(
                        "even after rendering, no candidate rows were located")
                return
            plan.strategy = Strategy.NONE
            plan.blockers.append(
                "the listing needs JavaScript and the browser tier was not "
                "available to investigate it")
            return

        plan.strategy = Strategy.NONE
        plan.blockers.append(
            "the investigation could not prove where the tender data comes from")
        if rows.conclusion == "NONE":
            plan.blockers.append(
                "this URL may be a landing or category page rather than the "
                "tender listing itself")

    # ── cross-cutting ─────────────────────────────────────────────────────────

    @staticmethod
    def _apply_fetch_rung(plan: GenerationPlan, report) -> None:
        """A portal that needed a stealth client in probing needs one in prod."""
        rung = report.stage(Stage.HTTP).data.get("rung", "requests")
        plan.directives["http_client"] = rung
        if rung != "requests":
            plan.reasons.append(
                f"plain requests was blocked; the scraper must fetch with {rung}")

    @staticmethod
    def _apply_pagination(plan: GenerationPlan, pagination) -> None:
        plan.directives["pagination"] = {
            "strategy": pagination.conclusion,
            "confidence": pagination.confidence,
            **{k: v for k, v in pagination.data.items() if k != "row_growth"},
        }
        if pagination.conclusion == UNKNOWN and plan.strategy != Strategy.NONE:
            plan.risks.append(
                "pagination strategy is unproven; the scraper may only ever "
                "return page one")
            plan.confidence = max(0, plan.confidence - 10)
        if pagination.conclusion in ("INFINITE_SCROLL", "LOAD_MORE") and \
                plan.strategy == Strategy.STATIC_HTML:
            plan.risks.append(
                "paging is script-driven; without the underlying request the "
                "scraper is limited to the first page")

    @staticmethod
    def _apply_unknowns(plan: GenerationPlan, report) -> None:
        unknowns = report.all_unknowns()
        plan.directives["unknown_count"] = len(unknowns)
        # Unknowns are not fatal on their own — they lower confidence, which is
        # what the viability threshold then judges.
        if len(unknowns) >= 6:
            plan.confidence = max(0, plan.confidence - 10)
            plan.risks.append(
                f"{len(unknowns)} unresolved unknowns across the investigation")
