"""Service interfaces for the Scraper Factory.

Concrete implementations are resolved through `scraper_factory.providers`, so
the pipeline depends on abstractions only. The shipped code model is
a self-hosted Ollama server; the endpoint and model tag are configuration,
not code, so no part of the Factory is specialised for a particular model.

These are contracts plus framework-free data-transfer objects — no Django
imports — so the indexer, matcher and validator stay unit-testable.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Data transfer objects ─────────────────────────────────────────────────────

@dataclass
class SiteProfile:
    """What the analyzer learned about a candidate website."""
    url: str
    reachable: bool = False
    status_code: Optional[int] = None
    content_type: str = ""
    family: str = ""                      # HTML_CUSTOM | JSON_API | STEALTH_HTML | ...
    js_required: bool = False
    anti_bot: bool = False
    login_required: bool = False
    login_url: str = ""
    framework: str = ""                   # ASPNET | WORDPRESS | REACT | ...
    api_endpoints: list[str] = field(default_factory=list)
    rss_url: str = ""
    pagination: str = ""                  # QUERY_PARAM | PATH | NONE | INFINITE_SCROLL
    language: str = ""
    country_hint: str = ""
    site_signature: str = ""              # structural fingerprint (learning key)
    listing_sample: str = ""              # trimmed HTML/JSON of the listing region
    response_time_ms: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReferenceScraper:
    """An existing corpus module chosen as a style/logic reference."""
    module: str                            # e.g. 'nepco_tenders'
    class_name: str                        # e.g. 'NEPCOTendersScraper'
    family: str
    score: float
    reasons: list[str] = field(default_factory=list)
    source_code: str = ""
    line_count: int = 0


@dataclass
class StyleViolation:
    """One validator finding. `hard=True` means the candidate is rejected."""
    code: str                              # e.g. 'I1', 'C6', 'D-quotes'
    message: str
    line: Optional[int] = None
    hard: bool = False


@dataclass
class ValidationReport:
    """Outcome of the verification gates. The validator NEVER modifies code."""
    passed: bool = False
    style_score: int = 0
    violations: list[StyleViolation] = field(default_factory=list)
    gates: dict[str, bool] = field(default_factory=dict)

    @property
    def hard_violations(self) -> list[StyleViolation]:
        return [v for v in self.violations if v.hard]

    def explain(self) -> str:
        """Plain-language, line-anchored summary shown to the employee."""
        if self.passed:
            return f"All gates passed (style score {self.style_score}/100)."
        lines = []
        for v in self.violations:
            where = f" (line {v.line})" if v.line else ""
            kind = "REJECT" if v.hard else "style"
            lines.append(f"[{kind}] {v.code}{where}: {v.message}")
        return "\n".join(lines)


@dataclass
class TestOutcome:
    """Result of a sandboxed dry run of a candidate scraper."""
    ok: bool = False
    tenders_found: int = 0
    runtime_ms: int = 0
    sample_rows: list[dict] = field(default_factory=list)
    error_category: str = ""
    error_message: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""


# ── Interfaces ────────────────────────────────────────────────────────────────

class ICodeModel(abc.ABC):
    """A code-generating language model.

    Deliberately says nothing about which model or vendor. The concrete
    provider and its model tag are configuration; every caller in the Factory
    depends on this interface alone.
    """

    name: str = "unknown"

    @abc.abstractmethod
    def generate(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        """Return the model's raw completion text."""

    @abc.abstractmethod
    def available(self) -> tuple[bool, str]:
        """(reachable, detail) — lets the UI report 'model unreachable' clearly."""


class ICorpusIndexer(abc.ABC):
    """Builds the style index from tenders/scrapers/ — the ground truth."""

    @abc.abstractmethod
    def build(self, *, refresh: bool = False) -> Any:
        """Return a StyleIndex."""


class IWebsiteAnalyzer(abc.ABC):
    @abc.abstractmethod
    def analyze(self, url: str) -> SiteProfile: ...


class IInvestigator(abc.ABC):
    """Gathers evidence about a website. Generation is gated on its report."""

    @abc.abstractmethod
    def investigate(self, url: str, **kwargs) -> Any:
        """Return an InvestigationReport."""


class IPlanner(abc.ABC):
    """Chooses the generation strategy from investigation evidence."""

    @abc.abstractmethod
    def plan(self, report: Any) -> Any:
        """Return a GenerationPlan."""


class ISimilarityMatcher(abc.ABC):
    @abc.abstractmethod
    def match(self, profile: SiteProfile, *, limit: int = 3) -> list[ReferenceScraper]: ...


class ICodeValidator(abc.ABC):
    """VERIFY ONLY. Implementations must not rewrite or reformat code."""

    @abc.abstractmethod
    def validate(self, source_code: str, *, module_name: str,
                 class_name: str) -> ValidationReport: ...


class ISandbox(abc.ABC):
    @abc.abstractmethod
    def run(self, source_code: str, *, module_name: str, class_name: str,
            timeout_s: int = 180, country_code: str = "",
            list_url: str = "") -> TestOutcome: ...
