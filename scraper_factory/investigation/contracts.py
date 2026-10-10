"""Evidence contracts for the Investigation Engine.

Every stage answers the same shape: what it concluded, how sure it is, the
observations that justify it, and what it could not determine. A stage that
cannot prove its conclusion returns UNKNOWN — guessing is forbidden, because a
confident wrong answer sends the Planner down the wrong strategy and the repair
loop then burns its whole budget on a symptom.

Framework-free (no Django imports) so the stages stay unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

UNKNOWN = "UNKNOWN"


class Stage:
    """Investigation order. Also the root-cause order used by diagnostics."""

    HTTP = "HTTP"
    TECHNOLOGY = "TECHNOLOGY"
    AUTHENTICATION = "AUTHENTICATION"
    RENDERING = "RENDERING"
    API_DISCOVERY = "API_DISCOVERY"
    PAGINATION = "PAGINATION"
    CANDIDATE_ROWS = "CANDIDATE_ROWS"

    #: Earliest first. The first failing stage is the root cause; everything
    #: after it is a downstream symptom.
    ORDER = (HTTP, TECHNOLOGY, AUTHENTICATION, RENDERING, API_DISCOVERY,
             PAGINATION, CANDIDATE_ROWS)


class Failure:
    """Failure taxonomy. Generic errors are not permitted."""

    NETWORK_ERROR = "NETWORK_ERROR"
    SSL_ERROR = "SSL_ERROR"
    DNS_ERROR = "DNS_ERROR"
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"
    RATE_LIMIT = "RATE_LIMIT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    SESSION_REQUIRED = "SESSION_REQUIRED"
    COOKIE_REQUIRED = "COOKIE_REQUIRED"
    TOKEN_REQUIRED = "TOKEN_REQUIRED"
    CSRF_REQUIRED = "CSRF_REQUIRED"
    BOT_PROTECTION = "BOT_PROTECTION"
    JAVASCRIPT_RENDERING = "JAVASCRIPT_RENDERING"
    API_DISCOVERY_REQUIRED = "API_DISCOVERY_REQUIRED"
    GRAPHQL_REQUIRED = "GRAPHQL_REQUIRED"
    POST_REQUEST_REQUIRED = "POST_REQUEST_REQUIRED"
    PAGINATION_REQUIRED = "PAGINATION_REQUIRED"
    INFINITE_SCROLL_REQUIRED = "INFINITE_SCROLL_REQUIRED"
    REQUEST_CONFIGURATION = "REQUEST_CONFIGURATION"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    HTML_EMPTY = "HTML_EMPTY"
    LANDING_PAGE_CONTENT = "LANDING_PAGE_CONTENT"
    CATEGORY_PAGE = "CATEGORY_PAGE"
    SEARCH_PAGE = "SEARCH_PAGE"
    EMPTY_LISTING = "EMPTY_LISTING"
    INVALID_ROW_SELECTION = "INVALID_ROW_SELECTION"
    NON_TENDER_ELEMENTS_SELECTED = "NON_TENDER_ELEMENTS_SELECTED"
    FIELD_EXTRACTION_FAILED = "FIELD_EXTRACTION_FAILED"
    DATE_EXTRACTION_FAILED = "DATE_EXTRACTION_FAILED"
    CLASSIFICATION_FAILED = "CLASSIFICATION_FAILED"
    INSTALLATION_FAILED = "INSTALLATION_FAILED"
    #: The machine was saturated, so the work never ran. Says nothing about the
    #: code, and must never be treated as a defect in it.
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    UNKNOWN = UNKNOWN


@dataclass
class Evidence:
    """One observation. `where` lets a reviewer re-check the claim themselves."""

    what: str
    where: str = ""
    detail: str = ""

    def render(self) -> str:
        bits = [self.what]
        if self.where:
            bits.append(f"[{self.where}]")
        if self.detail:
            bits.append(f"— {self.detail}")
        return " ".join(bits)


@dataclass
class StageResult:
    """The uniform answer every investigation stage returns."""

    stage: str
    conclusion: str = UNKNOWN
    confidence: int = 0                       # 0-100
    evidence: list[Evidence] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    failure: str = ""                         # a Failure code, when this stage failed
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failure

    @property
    def proven(self) -> bool:
        """A conclusion is only usable when it is both known and evidenced."""
        return self.conclusion != UNKNOWN and bool(self.evidence)

    def add(self, what: str, where: str = "", detail: str = "") -> None:
        self.evidence.append(Evidence(what=what, where=where, detail=detail))

    def conclude(self, conclusion: str, confidence: int) -> None:
        self.conclusion = conclusion
        self.confidence = max(0, min(100, int(confidence)))

    def fail(self, category: str, detail: str = "") -> None:
        self.failure = category
        self.conclusion = UNKNOWN
        self.confidence = 0
        if detail:
            self.add(f"failed: {category}", detail=detail)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "conclusion": self.conclusion,
            "confidence": self.confidence,
            "evidence": [
                {"what": e.what, "where": e.where, "detail": e.detail}
                for e in self.evidence],
            "unknowns": list(self.unknowns),
            "failure": self.failure,
            "data": self.data,
        }


@dataclass
class InvestigationReport:
    """The structured report that must exist before generation may begin."""

    url: str
    tier: int = 1                             # 1 = HTTP only, 2 = browser used
    stages: dict[str, StageResult] = field(default_factory=dict)
    escalated: bool = False
    escalation_reason: str = ""
    data_source: str = UNKNOWN                # HTML | API | GRAPHQL | JS_STATE | UNKNOWN
    data_source_confidence: int = 0
    duration_ms: int = 0

    # Carried forward for the Planner, prompt and snapshot reuse.
    listing_sample: str = ""
    site_signature: str = ""
    language: str = ""
    country_hint: str = ""
    snapshot_id: Optional[int] = None

    def stage(self, name: str) -> StageResult:
        return self.stages.get(name) or StageResult(stage=name)

    def record(self, result: StageResult) -> StageResult:
        self.stages[result.stage] = result
        return result

    @property
    def complete(self) -> bool:
        """Every stage ran and none of them failed outright.

        Generation is gated on this. A stage concluding UNKNOWN does not block
        by itself — the Planner decides whether the unknowns are survivable —
        but a hard failure always does.
        """
        if not all(s in self.stages for s in Stage.ORDER):
            return False
        return not self.root_cause()

    def root_cause(self) -> Optional[StageResult]:
        """The earliest failing stage. Later failures are its symptoms."""
        for name in Stage.ORDER:
            result = self.stages.get(name)
            if result is not None and result.failure:
                return result
        return None

    @property
    def failure(self) -> str:
        rc = self.root_cause()
        return rc.failure if rc else ""

    #: Stages that always carry the plan, whatever the data source turns out
    #: to be. API_DISCOVERY and CANDIDATE_ROWS are conditional: which of the two
    #: matters depends on where the data actually lives.
    _ALWAYS_LOAD_BEARING = (Stage.HTTP, Stage.TECHNOLOGY, Stage.AUTHENTICATION,
                            Stage.RENDERING, Stage.PAGINATION)

    def _load_bearing(self) -> tuple[str, ...]:
        if self.data_source in ("API", "GRAPHQL", "JS_STATE"):
            return self._ALWAYS_LOAD_BEARING + (Stage.API_DISCOVERY,)
        return self._ALWAYS_LOAD_BEARING + (Stage.CANDIDATE_ROWS,)

    def overall_confidence(self) -> int:
        """Weakest link among the stages the plan actually rests on.

        Strictly weakest-link, but only over load-bearing stages: an API scan
        that confidently reports "no API here" is a correct answer, and letting
        it drag an otherwise solid HTML investigation down to its own score
        would make the number meaningless.
        """
        relevant = self._load_bearing()
        scored = [s.confidence for s in self.stages.values()
                  if s.stage in relevant]
        return min(scored) if scored else 0

    def all_unknowns(self) -> list[str]:
        out: list[str] = []
        for name in Stage.ORDER:
            result = self.stages.get(name)
            if result is None:
                out.append(f"{name}: stage did not run")
                continue
            out.extend(f"{name}: {u}" for u in result.unknowns)
        return out

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "tier": self.tier,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "data_source": self.data_source,
            "data_source_confidence": self.data_source_confidence,
            "overall_confidence": self.overall_confidence(),
            "complete": self.complete,
            "root_cause": (self.root_cause().stage if self.root_cause() else ""),
            "failure": self.failure,
            "duration_ms": self.duration_ms,
            "language": self.language,
            "country_hint": self.country_hint,
            "site_signature": self.site_signature,
            "snapshot_id": self.snapshot_id,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "unknowns": self.all_unknowns(),
        }

    def render(self) -> str:
        """Human-readable report — what a developer reads in the review queue."""
        lines = [
            f"INVESTIGATION REPORT — {self.url}",
            f"  tier            : {self.tier}"
            + (f" (escalated: {self.escalation_reason})" if self.escalated else ""),
            f"  data source     : {self.data_source} "
            f"({self.data_source_confidence}%)",
            f"  overall         : {self.overall_confidence()}% "
            f"({'complete' if self.complete else 'incomplete'})",
        ]
        rc = self.root_cause()
        if rc is not None:
            lines.append(f"  ROOT CAUSE      : {rc.stage} → {rc.failure}")
        lines.append("")
        for name in Stage.ORDER:
            result = self.stages.get(name)
            if result is None:
                lines.append(f"  {name:<15}: (did not run)")
                continue
            head = f"{result.conclusion} ({result.confidence}%)"
            if result.failure:
                head = f"FAILED → {result.failure}"
            lines.append(f"  {name:<15}: {head}")
            for e in result.evidence[:6]:
                lines.append(f"      · {e.render()}")
            for u in result.unknowns[:4]:
                lines.append(f"      ? {u}")
        return "\n".join(lines)
