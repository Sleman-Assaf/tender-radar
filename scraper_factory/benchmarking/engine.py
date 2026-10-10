"""Golden Benchmark — measure the Factory, not one website.

What is being scored is the *investigation and planning*, not the code model.
That is deliberate: those two stages decide everything downstream, they are
deterministic, and they can be measured without a single LLM call. So the
benchmark is meaningful today with the fake provider and will mean exactly the
same thing once a real model is connected.

Each case declares what a correct answer looks like — the data source, the
strategy, the technology, a minimum row count. A run compares reality against
those expectations and returns one of:

    PASS      every declared expectation matched
    FAIL      an expectation was contradicted
    UNKNOWN   nothing was contradicted, but something could not be determined
    ERROR     the case could not be evaluated at all

UNKNOWN exists so that an unreachable portal never masquerades as a regression.
A network outage would otherwise turn a whole suite red and hide the one real
failure inside it.

Regression comparison is the point of storing runs: a change that lifts the
average while breaking four portals that used to work is not an improvement.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("scraper_factory")

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
ERROR = "ERROR"


@dataclass
class CaseResult:
    """One portal's outcome, with the reason it scored that way."""

    case_id: int
    label: str
    url: str
    verdict: str = UNKNOWN
    duration_ms: int = 0
    attempts: int = 1

    data_source: str = ""
    strategy: str = ""
    technology: str = ""
    rows: int = 0
    tier: int = 1
    confidence: int = 0
    viable: bool = False

    root_cause: str = ""
    mismatches: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "label": self.label, "url": self.url,
            "verdict": self.verdict, "duration_ms": self.duration_ms,
            "attempts": self.attempts, "data_source": self.data_source,
            "strategy": self.strategy, "technology": self.technology,
            "rows": self.rows, "tier": self.tier, "confidence": self.confidence,
            "viable": self.viable, "root_cause": self.root_cause,
            "mismatches": self.mismatches, "unknowns": self.unknowns[:4],
            "error": self.error,
        }


class BenchmarkEngine:
    """Runs a suite in parallel and scores it against expectations."""

    def __init__(self, *, workers: int | None = None, retries: int | None = None,
                 allow_tier2: bool | None = None, timeout_s: int | None = None):
        self.workers = int(workers or getattr(settings,
                                              "FACTORY_BENCHMARK_WORKERS", 6))
        self.retries = int(retries if retries is not None
                           else getattr(settings, "FACTORY_BENCHMARK_RETRIES", 1))
        self.allow_tier2 = allow_tier2
        self.timeout_s = int(timeout_s or getattr(
            settings, "FACTORY_BENCHMARK_CASE_TIMEOUT", 120))

    # ── public API ────────────────────────────────────────────────────────────

    def run(self, suite, *, limit: int | None = None, user=None,
            baseline=None, mark_baseline: bool = False, notes: str = ""):
        """Execute every active case in `suite` and persist a BenchmarkRun."""
        from ..models import BenchmarkCase, BenchmarkRun

        cases = list(BenchmarkCase.objects.filter(suite=suite, is_active=True)
                     .order_by("label"))
        if limit:
            cases = cases[:limit]
        if not cases:
            logger.warning("[factory] benchmark suite '%s' has no active cases",
                           suite.name)

        run = BenchmarkRun.objects.create(
            suite=suite, baseline=baseline or self._latest_baseline(suite),
            total=len(cases), triggered_by=user, notes=notes[:300])

        started = time.monotonic()
        results: list[CaseResult] = []

        # Threads, not processes: every case is dominated by network wait, and a
        # thread pool keeps the Django connection handling simple.
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._run_case_safely, c): c for c in cases}
            for future in as_completed(futures):
                case = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    results.append(CaseResult(
                        case_id=case.pk, label=case.label, url=case.url,
                        verdict=ERROR, error=f"{type(exc).__name__}: {exc}"[:300]))

        results.sort(key=lambda r: r.label.lower())
        self._summarise(run, results, started)
        if mark_baseline:
            self.promote_baseline(run)
        logger.info("[factory] benchmark '%s': %s/%s passed (%.0f%%)",
                    suite.name, run.passed, run.total, run.success_rate * 100)
        return run

    def promote_baseline(self, run) -> None:
        """Make this run the comparison point for future runs."""
        from ..models import BenchmarkRun

        BenchmarkRun.objects.filter(suite=run.suite, is_baseline=True).update(
            is_baseline=False)
        run.is_baseline = True
        run.save(update_fields=["is_baseline"])
        logger.info("[factory] benchmark run #%s is now the baseline", run.pk)

    # ── one case ──────────────────────────────────────────────────────────────

    def _run_case_safely(self, case) -> CaseResult:
        """Retry a case before believing it failed.

        A single timeout is usually the network, not the Factory. Retrying once
        is the difference between a benchmark that reports regressions and one
        that reports weather.
        """
        from django.db import close_old_connections

        last: Optional[CaseResult] = None
        for attempt in range(1, self.retries + 2):
            try:
                result = self._run_case(case)
            except Exception as exc:  # noqa: BLE001
                result = CaseResult(case_id=case.pk, label=case.label,
                                    url=case.url, verdict=ERROR,
                                    error=f"{type(exc).__name__}: {exc}"[:300])
            result.attempts = attempt
            last = result
            if result.verdict in (PASS, FAIL):
                break                    # a decided verdict needs no retry
        close_old_connections()
        return last

    def _run_case(self, case) -> CaseResult:
        from ..investigation.contracts import Stage
        from ..investigation.engine import InvestigationEngine
        from ..planning.planner import Planner

        result = CaseResult(case_id=case.pk, label=case.label, url=case.url)
        started = time.monotonic()

        report = InvestigationEngine().investigate(
            case.url, allow_tier2=self.allow_tier2, capture_snapshot=False)
        plan = Planner().plan(report)
        result.duration_ms = int((time.monotonic() - started) * 1000)

        rows_stage = report.stage(Stage.CANDIDATE_ROWS)
        best = (rows_stage.data.get("best") or {})
        result.data_source = report.data_source
        result.strategy = plan.strategy
        result.technology = report.stage(Stage.TECHNOLOGY).conclusion
        result.rows = int(best.get("count") or rows_stage.data.get("record_count") or 0)
        result.tier = report.tier
        result.confidence = report.overall_confidence()
        result.viable = plan.viable
        result.unknowns = report.all_unknowns()

        root = report.root_cause()
        if root is not None:
            result.root_cause = f"{root.stage}/{root.failure}"

        result.verdict = self._score(case, result, report)
        return result

    @staticmethod
    def _score(case, result: CaseResult, report) -> str:
        """Compare against declared expectations. Contradiction beats silence."""
        mismatches = result.mismatches

        # An unreachable portal is not a Factory regression.
        if result.root_cause.startswith("HTTP/"):
            failure = result.root_cause.split("/", 1)[1]
            if failure in ("NETWORK_ERROR", "TIMEOUT", "DNS_ERROR", "SSL_ERROR",
                           "RATE_LIMIT"):
                result.unknowns.insert(0, f"portal unreachable: {failure}")
                return UNKNOWN
            # BLOCKED / BOT_PROTECTION is a real finding about the portal.
            if case.expected_data_source:
                mismatches.append(f"blocked before the data source could be "
                                  f"confirmed ({failure})")
                return FAIL
            return UNKNOWN

        if case.expected_data_source:
            if result.data_source == "UNKNOWN":
                result.unknowns.insert(0, "data source could not be determined")
                return UNKNOWN
            if result.data_source != case.expected_data_source:
                mismatches.append(
                    f"data source {result.data_source}, expected "
                    f"{case.expected_data_source}")

        if case.expected_strategy and result.strategy != case.expected_strategy:
            mismatches.append(f"strategy {result.strategy}, expected "
                              f"{case.expected_strategy}")

        if case.expected_technology and result.technology != case.expected_technology:
            mismatches.append(f"technology {result.technology}, expected "
                              f"{case.expected_technology}")

        if case.min_rows and result.rows < case.min_rows:
            mismatches.append(f"{result.rows} row(s), expected at least "
                              f"{case.min_rows}")

        if mismatches:
            return FAIL
        if not (case.expected_data_source or case.expected_strategy
                or case.expected_technology or case.min_rows):
            # Nothing declared, so nothing was checked. This used to return PASS
            # for any plannable portal, which made all 144 undeclared cases read
            # as green — a suite reporting success while unable to catch a single
            # regression, and indistinguishable from a healthy one. PASS means
            # "every declared expectation matched"; with none declared the honest
            # verdict is UNKNOWN, which `_summarise` already excludes from the
            # pass rate. Declare expectations with `factory_benchmark_capture`.
            return UNKNOWN
        return PASS

    # ── aggregation ───────────────────────────────────────────────────────────

    def _summarise(self, run, results: list[CaseResult], started: float) -> None:
        counts = {PASS: 0, FAIL: 0, UNKNOWN: 0, ERROR: 0}
        root_causes: dict[str, int] = {}
        strategies: dict[str, int] = {}
        total_ms = 0
        tier2 = 0

        for r in results:
            counts[r.verdict] = counts.get(r.verdict, 0) + 1
            total_ms += r.duration_ms
            if r.tier == 2:
                tier2 += 1
            if r.root_cause:
                root_causes[r.root_cause] = root_causes.get(r.root_cause, 0) + 1
            if r.strategy:
                strategies[r.strategy] = strategies.get(r.strategy, 0) + 1

        decided = counts[PASS] + counts[FAIL]
        run.passed = counts[PASS]
        run.failed = counts[FAIL]
        run.unknown = counts[UNKNOWN]
        run.errored = counts[ERROR]
        # Rate over decided cases only: unreachable portals must not dilute it.
        run.success_rate = (counts[PASS] / decided) if decided else 0.0
        run.total_ms = int((time.monotonic() - started) * 1000)
        run.average_ms = int(total_ms / len(results)) if results else 0
        run.tier2_count = tier2
        run.root_causes = dict(sorted(root_causes.items(), key=lambda kv: -kv[1]))
        run.strategy_counts = dict(sorted(strategies.items(), key=lambda kv: -kv[1]))
        run.results = [r.to_dict() for r in results]
        run.regressions, run.improvements = self._compare(run, results)
        run.finished_at = timezone.now()
        run.save()

        if run.regressions:
            self._alert_regression(run)

    @staticmethod
    def _compare(run, results: list[CaseResult]) -> tuple[list, list]:
        """Cases whose verdict moved relative to the baseline."""
        baseline = run.baseline
        if baseline is None:
            return [], []
        was = {r.get("url"): r.get("verdict") for r in (baseline.results or [])}
        regressions, improvements = [], []
        for r in results:
            previous = was.get(r.url)
            if previous is None:
                continue
            if previous == PASS and r.verdict == FAIL:
                regressions.append({"label": r.label, "url": r.url,
                                    "was": previous, "now": r.verdict,
                                    "why": r.mismatches[:3]})
            elif previous == FAIL and r.verdict == PASS:
                improvements.append({"label": r.label, "url": r.url,
                                     "was": previous, "now": r.verdict})
        return regressions, improvements

    @staticmethod
    def _alert_regression(run) -> None:
        from .. import alerting
        alerting.raise_alert(
            rule=alerting.Rule.BENCHMARK_REGRESSION, scraper=None,
            message=(f"{len(run.regressions)} portal(s) regressed in benchmark "
                     f"run #{run.pk} of '{run.suite.name}'"),
            evidence=[f"{r['label']}: {r['was']} → {r['now']}"
                      for r in run.regressions[:6]],
            extra={"benchmark_run_id": run.pk})

    @staticmethod
    def _latest_baseline(suite):
        from ..models import BenchmarkRun
        return (BenchmarkRun.objects.filter(suite=suite, is_baseline=True)
                .order_by("-created_at").first())


# ── reporting ─────────────────────────────────────────────────────────────────

def render_run(run) -> str:
    """The report the brief asks for, as text."""
    lines = [
        f"BENCHMARK — {run.suite.name}  (run #{run.pk})",
        f"  cases        : {run.total}",
        f"  PASS         : {run.passed}",
        f"  FAIL         : {run.failed}",
        f"  UNKNOWN      : {run.unknown}",
        f"  ERROR        : {run.errored}",
        f"  success rate : {run.success_rate:.0%}  (of decided cases)",
        f"  average time : {run.average_ms} ms per case",
        f"  wall clock   : {run.total_ms} ms",
        f"  browser tier : {run.tier2_count} case(s) escalated",
    ]
    if run.baseline_id:
        lines.append(f"  baseline     : run #{run.baseline_id}")
        lines.append(f"  regressions  : {len(run.regressions)}")
        lines.append(f"  improvements : {len(run.improvements)}")

    if run.regressions:
        lines.append("")
        lines.append("  REGRESSIONS — these passed before and fail now:")
        for r in run.regressions:
            lines.append(f"    ! {r['label']}: {'; '.join(r.get('why') or []) or 'no detail'}")

    if run.root_causes:
        lines.append("")
        lines.append("  root causes:")
        for cause, n in list(run.root_causes.items())[:8]:
            lines.append(f"    {n:>3}  {cause}")

    if run.strategy_counts:
        lines.append("")
        lines.append("  strategies chosen:")
        for strategy, n in run.strategy_counts.items():
            lines.append(f"    {n:>3}  {strategy}")

    failures = [r for r in (run.results or []) if r.get("verdict") == "FAIL"]
    if failures:
        lines.append("")
        lines.append("  failing cases:")
        for r in failures[:15]:
            why = "; ".join(r.get("mismatches") or []) or r.get("error") or "?"
            lines.append(f"    x {r['label']}: {why[:100]}")
    return "\n".join(lines)
