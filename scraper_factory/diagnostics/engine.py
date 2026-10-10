"""Root-Cause Diagnostic Engine.

Generation is not intelligence; diagnosis is. When a build fails, the useful
question is never "what error came out at the end?" but "which subsystem failed
*first*?" — everything after the first failure is a consequence of it, and
repairing a consequence wastes the repair budget and usually reintroduces the
original fault.

So this engine walks one fixed order, earliest first, and stops at the first
stage it can prove failed. That stage is the root cause. Later failures are
recorded as symptoms, explicitly labelled, so a reviewer can see they were not
ignored — only deprioritised.

The SELECTOR_MISMATCH rule from the master prompt is enforced literally: that
verdict requires proof that the HTML contained rows, that the selector ran, and
that it matched nothing. Absent any of the three, the engine reports what it can
actually support, up to and including UNKNOWN.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..investigation.contracts import Evidence, Failure, Stage

logger = logging.getLogger("scraper_factory")


class Subsystem:
    """The full pipeline in failure order, per master prompt Part 4."""

    HTTP = "HTTP"
    AUTHENTICATION = "AUTHENTICATION"
    NETWORK = "NETWORK"
    RENDERING = "RENDERING"
    API_DISCOVERY = "API_DISCOVERY"
    PAGINATION = "PAGINATION"
    CANDIDATE_ROWS = "CANDIDATE_ROWS"
    TENDER_VALIDATION = "TENDER_VALIDATION"
    FIELD_EXTRACTION = "FIELD_EXTRACTION"
    DATE_EXTRACTION = "DATE_EXTRACTION"
    CODE_GENERATION = "CODE_GENERATION"
    CODE_VALIDATION = "CODE_VALIDATION"
    SANDBOX_EXECUTION = "SANDBOX_EXECUTION"
    CLASSIFICATION = "CLASSIFICATION"
    INSTALLATION = "INSTALLATION"

    #: Earliest first. The first proven failure here is the root cause.
    ORDER = (HTTP, AUTHENTICATION, NETWORK, RENDERING, API_DISCOVERY,
             PAGINATION, CANDIDATE_ROWS, TENDER_VALIDATION, FIELD_EXTRACTION,
             DATE_EXTRACTION, CODE_GENERATION, CODE_VALIDATION,
             SANDBOX_EXECUTION, CLASSIFICATION, INSTALLATION)


@dataclass
class Finding:
    """One subsystem's failure, with the evidence that proves it."""

    subsystem: str
    category: str
    summary: str
    confidence: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    is_symptom: bool = False
    symptom_of: str = ""

    def to_dict(self) -> dict:
        return {"subsystem": self.subsystem, "category": self.category,
                "summary": self.summary, "confidence": self.confidence,
                "is_symptom": self.is_symptom, "symptom_of": self.symptom_of,
                "evidence": [{"what": e.what, "where": e.where,
                              "detail": e.detail} for e in self.evidence]}

    def render(self) -> str:
        tag = f"SYMPTOM of {self.symptom_of}" if self.is_symptom else "ROOT CAUSE"
        lines = [f"  [{tag}] {self.subsystem} → {self.category} "
                 f"({self.confidence}%)", f"      {self.summary}"]
        for e in self.evidence[:4]:
            lines.append(f"      · {e.render()}")
        return "\n".join(lines)


@dataclass
class DiagnosticReport:
    """What failed, why, and what may be repaired."""

    root_cause: Optional[Finding] = None
    symptoms: list[Finding] = field(default_factory=list)
    repair_strategy: str = ""
    repair_scope: list[str] = field(default_factory=list)
    repairable: bool = False
    suggestions: list[str] = field(default_factory=list)
    learning: str = ""

    @property
    def category(self) -> str:
        return self.root_cause.category if self.root_cause else ""

    @property
    def subsystem(self) -> str:
        return self.root_cause.subsystem if self.root_cause else ""

    def to_dict(self) -> dict:
        return {
            "root_cause": self.root_cause.to_dict() if self.root_cause else None,
            "symptoms": [s.to_dict() for s in self.symptoms],
            "repair_strategy": self.repair_strategy,
            "repair_scope": list(self.repair_scope),
            "repairable": self.repairable,
            "suggestions": list(self.suggestions),
            "learning": self.learning,
        }

    def render(self) -> str:
        if self.root_cause is None:
            return "DIAGNOSIS: no failure detected."
        lines = ["DIAGNOSTIC REPORT", self.root_cause.render()]
        for s in self.symptoms:
            lines.append(s.render())
        lines.append(f"  repair strategy : {self.repair_strategy or 'none'}"
                     f" ({'repairable' if self.repairable else 'not repairable '
                        'by regenerating code'})")
        if self.repair_scope:
            lines.append(f"  repair scope    : {', '.join(self.repair_scope)}")
        for s in self.suggestions:
            lines.append(f"  suggestion      : {s}")
        return "\n".join(lines)


class DiagnosticEngine:
    """Determines the earliest failing subsystem from all available evidence."""

    def diagnose(self, *, investigation=None, plan=None, validation_report=None,
                 test_outcome=None, tender_report=None, field_report=None,
                 install_error: str = "") -> DiagnosticReport:
        """Build the report from whatever evidence the caller managed to collect.

        Every argument is optional on purpose: a build that died during
        investigation has no sandbox result, and demanding one would make the
        engine unusable exactly when it is most needed.
        """
        findings: list[Finding] = []

        findings.extend(self._from_investigation(investigation))
        findings.extend(self._from_plan(plan))
        findings.extend(self._from_tender_validation(tender_report))
        findings.extend(self._from_field_telemetry(field_report))
        findings.extend(self._from_code_validation(validation_report))
        findings.extend(self._from_sandbox(test_outcome, investigation,
                                           tender_report))
        if install_error:
            findings.append(Finding(
                subsystem=Subsystem.INSTALLATION,
                category=Failure.INSTALLATION_FAILED,
                summary=install_error[:300], confidence=95,
                evidence=[Evidence(what="installer raised", detail=install_error[:200])]))

        report = DiagnosticReport()
        if not findings:
            return report

        order = {name: i for i, name in enumerate(Subsystem.ORDER)}
        findings.sort(key=lambda f: order.get(f.subsystem, 99))

        report.root_cause = findings[0]
        for later in findings[1:]:
            later.is_symptom = True
            later.symptom_of = report.root_cause.subsystem
            report.symptoms.append(later)

        from .repair import map_repair
        map_repair(report)
        logger.info("[factory] diagnosis: %s → %s (repair=%s)",
                    report.subsystem, report.category, report.repair_strategy)
        return report

    # ── evidence sources ──────────────────────────────────────────────────────

    @staticmethod
    def _from_investigation(investigation) -> list[Finding]:
        if investigation is None:
            return []
        root = investigation.root_cause()
        if root is None:
            return []
        subsystem = {
            Stage.HTTP: Subsystem.HTTP,
            Stage.TECHNOLOGY: Subsystem.RENDERING,
            Stage.AUTHENTICATION: Subsystem.AUTHENTICATION,
            Stage.RENDERING: Subsystem.RENDERING,
            Stage.API_DISCOVERY: Subsystem.API_DISCOVERY,
            Stage.PAGINATION: Subsystem.PAGINATION,
            Stage.CANDIDATE_ROWS: Subsystem.CANDIDATE_ROWS,
        }.get(root.stage, Subsystem.HTTP)
        return [Finding(
            subsystem=subsystem, category=root.failure,
            summary=f"investigation stage {root.stage} could not complete",
            confidence=max(root.confidence, 80), evidence=list(root.evidence))]

    @staticmethod
    def _from_plan(plan) -> list[Finding]:
        if plan is None or plan.viable:
            return []
        # A non-viable plan is not itself a subsystem failure; it is the
        # consequence of missing evidence. Attribute it to the subsystem that
        # failed to supply that evidence.
        blockers = "; ".join(plan.blockers)
        low = blockers.lower()
        if "login" in low or "credential" in low:
            subsystem, category = Subsystem.AUTHENTICATION, Failure.LOGIN_REQUIRED
        elif "javascript" in low or "browser" in low:
            subsystem, category = Subsystem.RENDERING, Failure.JAVASCRIPT_RENDERING
        elif "landing" in low or "category page" in low:
            subsystem, category = Subsystem.CANDIDATE_ROWS, Failure.LANDING_PAGE_CONTENT
        else:
            subsystem, category = Subsystem.CANDIDATE_ROWS, Failure.UNKNOWN
        return [Finding(
            subsystem=subsystem, category=category,
            summary=blockers[:300] or "no viable generation strategy",
            confidence=plan.confidence or 60,
            evidence=[Evidence(what="planner refused", detail=b[:150])
                      for b in plan.blockers[:3]])]

    @staticmethod
    def _from_tender_validation(tender_report) -> list[Finding]:
        if tender_report is None or tender_report.stop_category == "":
            if tender_report is not None and not tender_report.passed \
                    and tender_report.rows_total:
                # Two very different failures reach here, and conflating them
                # sends a developer to the wrong place. If the rows themselves
                # are tenders but a field the corpus expects is absent, the row
                # selector is fine and the FIELD MAPPING is at fault.
                if (tender_report.rows_valid
                        and tender_report.valid_ratio >= 0.5
                        and tender_report.missing_required):
                    return [Finding(
                        subsystem=Subsystem.FIELD_EXTRACTION,
                        category=Failure.FIELD_EXTRACTION_FAILED,
                        summary=(f"the rows are genuine tenders "
                                 f"({tender_report.rows_valid}/"
                                 f"{tender_report.rows_total}), but "
                                 f"{', '.join(tender_report.missing_required)} "
                                 f"is missing from most of them"),
                        confidence=85,
                        evidence=[Evidence(
                            what=f"'{name}' present in "
                                 f"{tender_report.field_coverage.get(name, 0):.0%} "
                                 f"of rows",
                            where="tender validation")
                            for name in tender_report.missing_required[:4]])]
                return [Finding(
                    subsystem=Subsystem.TENDER_VALIDATION,
                    category=Failure.INVALID_ROW_SELECTION,
                    summary=(f"only {tender_report.rows_valid} of "
                             f"{tender_report.rows_total} rows are genuine tenders"),
                    confidence=80,
                    evidence=[Evidence(what=n) for n in tender_report.notes[:3]])]
            return []
        return [Finding(
            subsystem=Subsystem.TENDER_VALIDATION,
            category=tender_report.stop_category,
            summary=(f"no extracted row passed tender validation "
                     f"({tender_report.rows_total} row(s) examined)"),
            confidence=85,
            evidence=[Evidence(what=v.negatives[0], where=f"row {v.index}")
                      for v in tender_report.verdicts[:4] if v.negatives])]

    @staticmethod
    def _from_field_telemetry(field_report) -> list[Finding]:
        """Findings for FIELD_EXTRACTION and DATE_EXTRACTION.

        These two subsystems were declared from Phase 2 and never fired, because
        nothing measured whether fields came out. With telemetry they can, and
        the two are kept apart deliberately: a date found but not parsed needs
        the parser fixed, a date never read needs the mapping fixed, and they are
        different lines of the file.
        """
        if field_report is None:
            return []

        out: list[Finding] = []

        if field_report.field_extraction_failed:
            missing = field_report.missing_core
            if missing:
                summary = (f"{', '.join(missing)} absent from most of the "
                           f"{field_report.rows} extracted row(s); no healthy "
                           f"production source is ever missing these")
                evidence = [Evidence(
                    what=f"'{name}' present in "
                         f"{field_report.fields[name].coverage:.0%} of rows",
                    where="field telemetry",
                    detail=(f"examples: {field_report.fields[name].examples[:2]}"
                            if field_report.fields[name].examples else ""))
                    for name in missing[:4]]
            else:
                summary = (f"{field_report.bare_rows} of {field_report.rows} rows "
                           f"carry only title/url/id — the row selector worked "
                           f"but the per-field mapping did not run")
                evidence = [Evidence(
                    what=f"{field_report.bare_rows} bare row(s)",
                    where="field telemetry",
                    detail="every field outside the core three is empty")]
            out.append(Finding(
                subsystem=Subsystem.FIELD_EXTRACTION,
                category=Failure.FIELD_EXTRACTION_FAILED,
                summary=summary, confidence=85, evidence=evidence))

        elif field_report.malformed_fields:
            # Not fatal on its own, but a strong sign the mapping reads the wrong
            # column — a URL in the reference field, say.
            out.append(Finding(
                subsystem=Subsystem.FIELD_EXTRACTION,
                category=Failure.FIELD_EXTRACTION_FAILED,
                summary=(f"{', '.join(field_report.malformed_fields)} contain "
                         f"values of the wrong shape"),
                confidence=70,
                evidence=[Evidence(
                    what=f"'{name}' malformed in most rows",
                    where="field telemetry",
                    detail=f"examples: {field_report.fields[name].examples[:2]}")
                    for name in field_report.malformed_fields[:3]]))

        if field_report.dates_never_read:
            # Attributed to FIELD_EXTRACTION, not DATE_EXTRACTION: no value ever
            # reached the date fields, so the parser cannot be at fault. The
            # repair is the selector that feeds those fields.
            out.append(Finding(
                subsystem=Subsystem.FIELD_EXTRACTION,
                category=Failure.FIELD_EXTRACTION_FAILED,
                summary=(f"the listing publishes dates but no row carries one — "
                         f"the date fields are not being read"),
                confidence=75,
                evidence=[Evidence(
                    what=f"{field_report.dates_only_in_text} of "
                         f"{field_report.rows} rows contain date text",
                    where="date telemetry",
                    detail="every date field is empty across all rows")]))

        if field_report.date_extraction_failed:
            out.append(Finding(
                subsystem=Subsystem.DATE_EXTRACTION,
                category=Failure.DATE_EXTRACTION_FAILED,
                summary=(f"dates are published but only "
                         f"{field_report.date_parse_rate:.0%} were parsed into "
                         f"date objects"),
                confidence=80,
                evidence=[Evidence(
                    what=f"{field_report.dates_unparsed} value(s) stayed as text",
                    where="date telemetry",
                    detail=f"e.g. {field_report.date_strings_seen[:3]}")]
                + [Evidence(what=note, where="date telemetry")
                   for note in field_report.notes[:2]]))
        return out

    @staticmethod
    def _from_code_validation(validation_report) -> list[Finding]:
        if validation_report is None or validation_report.passed:
            return []
        hard = validation_report.hard_violations
        return [Finding(
            subsystem=Subsystem.CODE_VALIDATION,
            category="CODE_REJECTED",
            summary=(f"{len(hard)} blocking style/contract violation(s): "
                     f"{', '.join(sorted({v.code for v in hard}))}"),
            confidence=99,
            evidence=[Evidence(what=f"{v.code}: {v.message[:120]}",
                               where=f"line {v.line}" if v.line else "")
                      for v in hard[:5]])]

    def _from_sandbox(self, outcome, investigation, tender_report) -> list[Finding]:
        if outcome is None:
            return []

        category = getattr(outcome, "error_category", "") or ""
        message = getattr(outcome, "error_message", "") or ""
        found = getattr(outcome, "tenders_found", 0)

        # A run that completes without error but extracts nothing is still a
        # failure, and it is the most important one to diagnose properly. Exiting
        # early on ok=True let tender validation take the blame and report
        # EMPTY_LISTING — "the listing is empty, retry later" — for a page the
        # investigation had already proved contains rows.
        #
        # The sandbox's own verdict for this case is "SELECTOR", and its message
        # asserts the selectors are wrong. That is precisely the reflex the proof
        # rule exists to gate, so it is re-derived here from evidence rather than
        # accepted.
        if found == 0 and category.upper() in ("", "SELECTOR", "NO_TENDERS",
                                               "EMPTY"):
            return [self._diagnose_empty_extraction(investigation, message)]

        if getattr(outcome, "ok", False):
            return []

        mapped = {
            "TIMEOUT": (Subsystem.NETWORK, Failure.TIMEOUT),
            "NETWORK": (Subsystem.NETWORK, Failure.NETWORK_ERROR),
            "SSL": (Subsystem.NETWORK, Failure.SSL_ERROR),
            "BLOCKED": (Subsystem.HTTP, Failure.BOT_PROTECTION),
            "CONTRACT": (Subsystem.CODE_GENERATION, Failure.UNKNOWN),
            "CONFIG": (Subsystem.CODE_GENERATION, Failure.UNKNOWN),
            "ENGINE": (Subsystem.SANDBOX_EXECUTION, Failure.UNKNOWN),
            # The candidate never ran, so nothing about it can be concluded.
            # SANDBOX_EXECUTION maps to Repair.NONE, which is the point.
            "RESOURCE": (Subsystem.SANDBOX_EXECUTION, Failure.RESOURCE_EXHAUSTED),
            "IMPORT": (Subsystem.CODE_GENERATION, Failure.UNKNOWN),
            "SYNTAX": (Subsystem.CODE_GENERATION, Failure.UNKNOWN),
        }
        subsystem, failure = mapped.get(
            category.upper(), (Subsystem.SANDBOX_EXECUTION, category or Failure.UNKNOWN))
        return [Finding(
            subsystem=subsystem, category=failure,
            summary=message[:300] or f"sandbox failed with {category}",
            confidence=85,
            evidence=[Evidence(what=f"sandbox {category}", detail=message[:200])])]

    @staticmethod
    def _diagnose_empty_extraction(investigation, message: str) -> Finding:
        """Zero rows extracted. Name the real reason, never a reflex verdict.

        SELECTOR_MISMATCH is only permissible with all three proofs in hand.
        Without them the honest answer is the investigation's own finding, or
        UNKNOWN.
        """
        if investigation is None:
            return Finding(
                subsystem=Subsystem.SANDBOX_EXECUTION, category=Failure.UNKNOWN,
                summary="the scraper extracted nothing and no investigation "
                        "evidence is available to explain why",
                confidence=30,
                evidence=[Evidence(what="no investigation attached to this run")])

        rendering = investigation.stage(Stage.RENDERING)
        rows = investigation.stage(Stage.CANDIDATE_ROWS)
        api = investigation.stage(Stage.API_DISCOVERY)
        http = investigation.stage(Stage.HTTP)

        # 1. Was the site even reachable during the sandbox run?
        low = (message or "").lower()
        if any(w in low for w in ("proxy", "connection", "timeout", "refused",
                                  "not permitted", "unreachable")):
            return Finding(
                subsystem=Subsystem.NETWORK, category=Failure.NETWORK_ERROR,
                summary="the scraper could not reach the site during the run, so "
                        "the selectors were never exercised",
                confidence=90,
                evidence=[Evidence(what="sandbox error text", detail=message[:200]),
                          Evidence(what="investigation reached the site earlier",
                                   where=f"HTTP {http.data.get('status')}")])

        # 2. For a JSON source there is no DOM selector to blame. The generated
        #    request simply did not reproduce the one the investigation proved.
        data_source = getattr(investigation, "data_source", "")
        if data_source in ("API", "GRAPHQL", "JS_STATE"):
            endpoint = api.data.get("proven_endpoint", "")
            category = (Failure.GRAPHQL_REQUIRED if data_source == "GRAPHQL"
                        else Failure.API_DISCOVERY_REQUIRED)
            return Finding(
                subsystem=Subsystem.API_DISCOVERY, category=category,
                summary=("the tenders come from a JSON source, and the generated "
                         "request did not return them — the endpoint, its "
                         "parameters or the record path is wrong"),
                confidence=80,
                evidence=[
                    Evidence(what="investigation proved this data source",
                             where=endpoint or data_source,
                             detail=f"{rows.data.get('record_count', 0)} record(s) "
                                    f"observed"),
                    Evidence(what="generated scraper returned zero rows",
                             where="sandbox"),
                ])

        # 3. Does the data live somewhere the scraper is not looking?
        if rendering.conclusion == "JAVASCRIPT_RENDERED" and not api.data.get(
                "proven_endpoint"):
            return Finding(
                subsystem=Subsystem.RENDERING,
                category=Failure.JAVASCRIPT_RENDERING,
                summary="the listing is built by JavaScript and no reproducible "
                        "request was found, so a plain fetch returns no rows",
                confidence=88,
                evidence=list(rendering.evidence[:3]))

        # 4. SELECTOR_MISMATCH — all three proofs required.
        html_had_rows = rows.conclusion == "FOUND" and (
            rows.data.get("best") or {}).get("count", 0) > 0
        selector_known = bool((rows.data.get("best") or {}).get("selector"))
        if html_had_rows and selector_known:
            best = rows.data["best"]
            return Finding(
                subsystem=Subsystem.CANDIDATE_ROWS,
                category="SELECTOR_MISMATCH",
                summary=(f"the investigation found {best['count']} row(s) at "
                         f"{best['selector']} but the generated scraper matched "
                         f"none, so its selector is wrong"),
                confidence=85,
                evidence=[
                    Evidence(what=f"HTML provably contains rows",
                             where=best["selector"],
                             detail=f"{best['count']} matched during investigation"),
                    Evidence(what="scraper executed and returned zero rows",
                             where="sandbox"),
                ])

        # 5. Nothing proven — say so rather than guess.
        return Finding(
            subsystem=Subsystem.CANDIDATE_ROWS, category=Failure.UNKNOWN,
            summary=("the scraper extracted nothing and the evidence does not "
                     "prove why; SELECTOR_MISMATCH cannot be claimed because the "
                     "investigation never confirmed rows in the served HTML"),
            confidence=35,
            evidence=[Evidence(what=f"rows stage concluded {rows.conclusion}",
                               detail=f"{rows.confidence}% confidence"),
                      Evidence(what=f"rendering concluded {rendering.conclusion}")],
        )
