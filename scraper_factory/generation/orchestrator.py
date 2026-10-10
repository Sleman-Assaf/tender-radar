"""F7 + F13 — the generation orchestrator.

Runs the pipeline and records everything:

    analyze (F8 snapshot) -> retrieve (F6) -> prompt (F5/F12) -> generate
        -> validate (A-D) -> [repair loop, max 3] -> sandbox (E) -> benchmark (F11)

Repair discipline (F7): a repair prompt contains ONLY the validation failures
plus the model's own previous file. We never regenerate blindly, because a blind
retry throws away the parts that were already correct and usually reintroduces
the same mistake.

Everything is written to a GenerationRun row (F13), including every attempt, so
a generation is reproducible and traceable after the fact.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

from .. import providers
from ..analysis.analyzer import WebsiteAnalyzer
from ..corpus.retriever import Retriever
from ..interfaces import (ICodeModel, ICodeValidator, IInvestigator, IPlanner,
                          ISandbox, SiteProfile, ValidationReport)
from ..investigation.adapter import to_site_profile
from ..models_ai.ollama_provider import ModelUnavailable, strip_code_fences
from ..prompting.builder import PromptBuilder
from .benchmark import Benchmarker, persist as persist_benchmark

logger = logging.getLogger("scraper_factory")


@dataclass
class GenerationResult:
    """Everything the caller (CLI or dashboard) needs to show the employee."""
    ok: bool = False
    source_code: str = ""
    module_name: str = ""
    class_name: str = ""
    profile: Optional[SiteProfile] = None
    investigation: Any = None
    plan: Any = None
    tender_report: Any = None
    field_report: Any = None
    diagnosis: Any = None
    report: Optional[ValidationReport] = None
    test_outcome: Any = None
    benchmark: Any = None
    generation_run: Any = None
    attempts: int = 0
    repair_attempts: int = 0
    reference_modules: list[str] = field(default_factory=list)
    outcome: str = ""
    message: str = ""

    def explain(self) -> str:
        """The reason this build ended as it did, most decisive cause first.

        A sandbox failure is reported before the validation summary: when the
        code is valid but extracts nothing, leading with "All gates passed"
        describes the least useful half of the truth.
        """
        parts = []
        sandbox_failed = (self.test_outcome is not None
                          and not getattr(self.test_outcome, "ok", False))
        if sandbox_failed:
            msg = getattr(self.test_outcome, "error_message", "")
            cat = getattr(self.test_outcome, "error_category", "")
            if msg:
                parts.append(f"[sandbox/{cat}] {msg}")
        if self.report is not None:
            parts.append(self.report.explain())
        return "\n".join(p for p in parts if p) or self.message


class GenerationOrchestrator:
    """Coordinates analysis, retrieval, prompting, generation and verification."""

    def __init__(self, *, model: Optional[ICodeModel] = None,
                 analyzer=None, retriever=None, builder=None,
                 validator=None, sandbox=None, investigator=None, planner=None):
        self.model = model
        self._analyzer = analyzer
        self._retriever = retriever or Retriever()
        self._builder = builder or PromptBuilder()
        self._validator = validator
        self._sandbox = sandbox
        self._investigator = investigator
        self._planner = planner
        self.max_repairs = int(getattr(settings, "FACTORY_MAX_REPAIR_TRIES", 3))

    # ── public API ────────────────────────────────────────────────────────────

    def generate(self, url: str, *, module_name: str, class_name: str,
                 country_code: str, source_name: str, user=None,
                 run_sandbox: bool = True, scraper=None) -> GenerationResult:
        from ..models import GenerationRun

        started = time.monotonic()
        # Per-run stage accumulator: the pipeline instance is reused across a
        # whole benchmark sweep, so this must not carry over between sites.
        self._validation_ms = 0
        result = GenerationResult(module_name=module_name, class_name=class_name)

        run = GenerationRun.objects.create(
            website_url=url[:500], created_by=user, scraper=scraper,
            outcome=GenerationRun.Outcome.RUNNING)
        result.generation_run = run

        try:
            # ── 1. investigate, then plan (Phase 1) ───────────────────────────
            # Generation is gated on both: no evidence, no code. Investigating
            # first is what stops the model being asked to guess selectors for a
            # page that was never fetched, or a listing that is not there.
            investigator = self._investigator or providers.get(IInvestigator)
            t_inv = time.monotonic()
            investigation = investigator.investigate(url, scraper=scraper)
            # Stage timing for the production benchmark: investigation was the
            # one phase whose cost never appeared anywhere. Stored on the result
            # and folded into BenchmarkReport.details (JSON — additive).
            result.investigation_ms = int((time.monotonic() - t_inv) * 1000)
            result.investigation = investigation

            planner = self._planner or providers.get(IPlanner)
            plan = planner.plan(investigation)
            result.plan = plan

            profile = to_site_profile(investigation, plan)
            result.profile = profile
            run.family = profile.family
            if investigation.snapshot_id:
                from ..models import HtmlSnapshot
                run.baseline_snapshot = HtmlSnapshot.objects.filter(
                    pk=investigation.snapshot_id).first()

            self._store_investigation(run, investigation, plan, scraper, user)

            root = investigation.root_cause()
            if root is not None:
                return self._finish_error(
                    run, result, GenerationRun.Outcome.ERROR,
                    f"Investigation stopped at {root.stage}: {root.failure}. "
                    + (root.evidence[0].render() if root.evidence else ""),
                    started)

            if not plan.viable:
                blockers = "; ".join(plan.blockers) or "no viable strategy"
                return self._finish_error(
                    run, result, GenerationRun.Outcome.ERROR,
                    f"No generation strategy could be selected: {blockers}",
                    started)

            logger.info("[factory] %s → %s via %s (%s%%)", url,
                        investigation.data_source, plan.strategy, plan.confidence)

            # ── 2. retrieve (F6) ─────────────────────────────────────────────
            bundle = self._retriever.retrieve(profile)
            result.reference_modules = bundle.reference_modules()
            run.reference_modules = [
                {"module": r.module, "score": r.score, "reasons": r.reasons}
                for r in bundle.references]
            if not bundle.references:
                logger.warning("[factory] no references retrieved for %s", url)

            # ── 3. build the prompt (F5/F12) ─────────────────────────────────
            prompt = self._builder.build(
                bundle, module_name=module_name, class_name=class_name,
                country_code=country_code, source_name=source_name,
                plan=plan, investigation=investigation)
            run.system_prompt = prompt.system
            run.user_prompt = prompt.user
            run.prompt_tokens_est = prompt.estimated_tokens
            run.prompt_version = self._prompt_version_row(prompt.version)

            model = self.model or providers.get(ICodeModel)
            run.model_name = getattr(model, "name", "unknown")
            run.model_params = {
                "num_ctx": int(getattr(settings, "FACTORY_MODEL_NUM_CTX",
                                    getattr(settings, "FACTORY_QWEN_NUM_CTX", 32768))),
                "temperature": 0,
                "fragments": prompt.fragment_ids,
                "strategy": plan.strategy,
                "data_source": investigation.data_source,
                "investigation_tier": investigation.tier,
            }
            run.save()

            # ── 4. generate, then validate/repair (F7) ───────────────────────
            code, report, gen_ms, attempts, repairs = self._generate_with_repair(
                model, prompt, run, module_name, class_name,
                investigation=investigation)
            result.source_code = code
            result.report = report
            result.attempts = attempts
            result.repair_attempts = repairs
            run.attempts = attempts
            run.repair_attempts = repairs
            run.generation_ms = gen_ms

            if report is None or not report.passed:
                run.outcome = GenerationRun.Outcome.FAILED_VALIDATION
                result.outcome = run.outcome
                result.message = ("The generated file did not pass validation "
                                  f"after {attempts} attempt(s).")
                self._benchmark(run, result, None, report, gen_ms, repairs)
                return self._finish(run, result, started)

            # ── 5+6. sandbox and tender validation, with runtime repair ───────
            # The repair loop above only ever saw the validator's failures, so a
            # file that passed every static gate but extracted zero rows was
            # final — diagnosed, queued for review, never repaired. Half of the
            # first corpus sweep failed exactly that way (SELECTOR_MISMATCH,
            # repairs=0). Runtime evidence is the best repair input the Factory
            # has: the diagnosis names the wrong selector and the proof, so the
            # model gets one more round with facts no static check can supply.
            outcome = None
            runtime_budget = int(getattr(settings, "FACTORY_RUNTIME_REPAIRS", 1))
            runtime_attempt = 0
            while True:
                outcome = None
                if run_sandbox:
                    sandbox = self._sandbox or providers.get(ISandbox)
                    outcome = sandbox.run(code, module_name=module_name,
                                          class_name=class_name,
                                          country_code=country_code,
                                          list_url=profile.url)
                    result.test_outcome = outcome
                    # Persist the extracted rows. Without this the only evidence
                    # of what the scraper actually found is a count, which is
                    # not enough to judge whether the generation was any good.
                    self._store_test_run(run, outcome)

                # Rows are not tenders until something says so. This gates the
                # date engine, classification and installation.
                if outcome is not None:
                    from ..validation.field_telemetry import FieldTelemetry
                    from ..validation.tender_validator import TenderValidator
                    rows = list(getattr(outcome, "sample_rows", []) or [])
                    result.tender_report = TenderValidator().validate(rows)
                    # Measured separately from tender validation: "are these
                    # tenders?" and "did the fields come out?" differ in repair.
                    result.field_report = FieldTelemetry().measure(
                        rows,
                        listing_sample=getattr(investigation, "listing_sample", ""))
                    run.model_params["tender_validation"] = {
                        "rows_valid": result.tender_report.rows_valid,
                        "rows_total": result.tender_report.rows_total,
                        "stop_category": result.tender_report.stop_category,
                        "confidence": result.tender_report.confidence,
                    }

                sandbox_failed = run_sandbox and not getattr(outcome, "ok", False)
                tenders_invalid = (result.tender_report is not None
                                   and not result.tender_report.passed)
                if not (sandbox_failed or tenders_invalid):
                    if runtime_attempt:
                        self._remember_repair(
                            result.diagnosis, True, investigation, run,
                            attempt=runtime_attempt, before=report, after=report)
                    break

                result.diagnosis = self._diagnose(result, install_error="")
                repairable = (result.diagnosis is not None
                              and result.diagnosis.root_cause is not None
                              and result.diagnosis.repairable)
                if runtime_attempt >= runtime_budget or not repairable:
                    if runtime_attempt and result.diagnosis is not None:
                        self._remember_repair(
                            result.diagnosis, False, investigation, run,
                            attempt=runtime_attempt, before=report, after=report)
                    self._benchmark(run, result, outcome, report, gen_ms, repairs)
                    run.outcome = GenerationRun.Outcome.FAILED_SANDBOX
                    result.outcome = run.outcome
                    result.message = self._failure_message(result)
                    self._learn_failure(result)
                    return self._finish(run, result, started)

                runtime_attempt += 1
                logger.info("[factory] runtime repair %s/%s: %s — %s",
                            runtime_attempt, runtime_budget,
                            result.diagnosis.subsystem,
                            result.diagnosis.root_cause.summary[:100])
                new_code, new_report, extra_ms = self._runtime_repair(
                    model, code, report, result, run, module_name, class_name)
                gen_ms += extra_ms
                if (new_code is None or new_report is None
                        or not new_report.passed):
                    # The repair broke validation: keep the old file and fail
                    # with the runtime diagnosis, which is still the truth.
                    self._remember_repair(
                        result.diagnosis, False, investigation, run,
                        attempt=runtime_attempt, before=report,
                        after=new_report or report, duration_ms=extra_ms)
                    self._benchmark(run, result, outcome, report, gen_ms, repairs)
                    run.outcome = GenerationRun.Outcome.FAILED_SANDBOX
                    result.outcome = run.outcome
                    result.message = self._failure_message(result)
                    self._learn_failure(result)
                    return self._finish(run, result, started)

                code, report = new_code, new_report
                result.source_code = code
                result.report = report
                repairs += 1
                run.repair_attempts = repairs
                result.repair_attempts = repairs
                run.generation_ms = gen_ms

            # ── 7. benchmark (F11) ───────────────────────────────────────────
            self._benchmark(run, result, outcome, report, gen_ms, repairs)

            run.outcome = GenerationRun.Outcome.PASSED
            result.ok = True
            result.outcome = run.outcome
            result.message = "Generated, validated and tested successfully."
            self._learn_success(result, outcome)
            return self._finish(run, result, started)

        except ModelUnavailable as exc:
            return self._finish_error(
                run, result, GenerationRun.Outcome.MODEL_UNREACHABLE,
                str(exc), started)
        except Exception as exc:  # noqa: BLE001 — always close the run
            logger.error("[factory] generation crashed: %s", exc, exc_info=True)
            return self._finish_error(
                run, result, GenerationRun.Outcome.ERROR,
                f"{type(exc).__name__}: {exc}", started)

    # ── the repair loop (F7) ──────────────────────────────────────────────────

    def _generate_with_repair(self, model, prompt, run, module_name: str,
                              class_name: str, investigation=None):
        """Generate, then repair using ONLY the validator's failures."""
        validator = self._validator or providers.get(ICodeValidator)
        attempt_log: list[dict] = []
        total_model_ms = 0
        code = ""
        report = None
        # Remembered so the outcome of each repair can be written back to
        # memory: a strategy that keeps failing must stop being suggested.
        pre_repair_diagnosis = None
        pre_repair_report = None
        pre_repair_prior_art = ""
        repair_started = 0.0

        for attempt in range(1, self.max_repairs + 2):   # 1 generation + N repairs
            kind = "generate" if attempt == 1 else "repair"
            t0 = time.monotonic()
            if attempt == 1:
                raw = model.generate(prompt.system, prompt.user)
            else:
                # A validation failure is its own diagnosis: the validator has
                # already named the exact violations, and scoping the repair to
                # them is what stops the model rewriting working code around the
                # fix. Non-validation failures are diagnosed before repairing.
                pre_repair_diagnosis = self._diagnose_validation(report)
                pre_repair_report = report
                repair_started = time.monotonic()
                guidance = self._repair_guidance(report, investigation)
                pre_repair_prior_art = ("PRIOR ART" if "PRIOR ART" in guidance
                                        else "")
                repair = self._builder.build_repair(
                    code, report, module_name=module_name,
                    class_name=class_name, extra_guidance=guidance)
                raw = model.generate(repair.system, repair.user)
            model_ms = int((time.monotonic() - t0) * 1000)
            total_model_ms += model_ms

            code = strip_code_fences(raw)
            code = self._pin_identity(code, class_name)
            if not code.strip():
                attempt_log.append({"attempt": attempt, "kind": kind,
                                    "duration_ms": model_ms,
                                    "error": "model returned no code"})
                continue

            t_val = time.monotonic()
            report = validator.validate(code, module_name=module_name,
                                        class_name=class_name)
            self._validation_ms = getattr(self, "_validation_ms", 0) \
                + int((time.monotonic() - t_val) * 1000)
            attempt_log.append({
                "attempt": attempt, "kind": kind, "duration_ms": model_ms,
                "style_score": report.style_score,
                "passed": report.passed,
                "hard_violations": [v.code for v in report.hard_violations],
                "violations": [
                    {"code": v.code, "line": v.line, "hard": v.hard,
                     "message": v.message[:200]}
                    for v in report.violations][:20],
            })

            logger.info("[factory] attempt %s (%s): passed=%s style=%s hard=%s",
                        attempt, kind, report.passed, report.style_score,
                        len(report.hard_violations))

            if pre_repair_diagnosis is not None:
                # Record whether the repair we just attempted actually worked,
                # so future runs get advice with a track record rather than a
                # plausible-sounding suggestion.
                self._remember_repair(
                    pre_repair_diagnosis, report.passed, investigation, run,
                    attempt=attempt - 1, before=pre_repair_report, after=report,
                    duration_ms=int((time.monotonic() - repair_started) * 1000),
                    prior_art=pre_repair_prior_art)
                pre_repair_diagnosis = None
                pre_repair_report = None
                pre_repair_prior_art = ""

            if report.passed:
                break

            # Some rejections are about the environment, not the code: a name
            # collision cannot be fixed by rewriting the file, because the
            # module and class names are assigned by us, not by the model.
            # Repairing those would burn the whole budget re-producing the same
            # rejection, so stop immediately and report the real cause.
            if self._unfixable_by_model(report):
                codes = sorted({v.code for v in report.hard_violations})
                logger.info("[factory] stopping repairs: %s cannot be fixed by "
                            "regenerating the file", codes)
                attempt_log[-1]["repair_skipped"] = (
                    "violation is not fixable by the model")
                break

        run.attempt_log = attempt_log
        repairs = max(0, len(attempt_log) - 1)
        return code, report, total_model_ms, len(attempt_log), repairs

    @staticmethod
    def _repair_guidance(report, investigation=None) -> str:
        """Structured constraints for a repair, plus any prior art.

        The validator reports code-level violations, so the diagnosis is
        immediate. Factory Memory is consulted first: if this exact diagnosis has
        been fixed before, the model is told how, rather than rediscovering it
        inside a three-attempt budget.
        """
        if report is None:
            return ""
        from ..diagnostics import DiagnosticEngine
        from ..diagnostics.repair import repair_instruction
        from ..learning import LearningStore
        try:
            diagnosis = DiagnosticEngine().diagnose(validation_report=report)
            known = ""
            try:
                known = LearningStore().known_repair_for(diagnosis, investigation)
            except Exception:  # noqa: BLE001 — memory is optional
                known = ""
            return repair_instruction(diagnosis, known_repair=known)
        except Exception:  # noqa: BLE001
            return ""

    def _runtime_repair(self, model, code, report, result, run,
                        module_name: str, class_name: str):
        """One repair round driven by runtime evidence.

        Returns (new_code, new_report, model_ms); (None, None, ms) when the
        model produced nothing usable. The repair prompt carries the diagnostic
        engine's structured instruction — root cause, allowed scope, forbidden
        scope — plus the sandbox facts, which is evidence no static validation
        pass could have offered.
        """
        validator = self._validator or providers.get(ICodeValidator)

        instruction = ""
        try:
            from ..diagnostics.repair import repair_instruction
            instruction = repair_instruction(result.diagnosis)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] no structured repair instruction: %s", exc)

        outcome = result.test_outcome
        tr = result.tender_report
        evidence = (
            "RUNTIME EVIDENCE (the file passed every static gate; the failure "
            "below happened against the live portal)\n"
            f"- rows extracted: {getattr(outcome, 'tenders_found', 0)}\n"
            f"- sandbox category: {getattr(outcome, 'error_category', '') or '-'}\n"
            f"- sandbox message: "
            f"{(getattr(outcome, 'error_message', '') or '-')[:300]}\n"
            + (f"- tender validation: {tr.rows_valid}/{tr.rows_total} valid "
               f"({tr.stop_category or 'no stop category'})\n" if tr else ""))

        t0 = time.monotonic()
        repair = self._builder.build_repair(
            code, report, module_name=module_name, class_name=class_name,
            extra_guidance=f"{instruction}\n\n{evidence}".strip())
        try:
            raw = model.generate(repair.system, repair.user)
        except ModelUnavailable as exc:
            logger.warning("[factory] runtime repair aborted: %s", exc)
            return None, None, int((time.monotonic() - t0) * 1000)
        ms = int((time.monotonic() - t0) * 1000)

        new_code = self._pin_identity(strip_code_fences(raw), class_name)
        if not new_code.strip():
            return None, None, ms
        new_report = validator.validate(new_code, module_name=module_name,
                                        class_name=class_name)
        return new_code, new_report, ms

    @staticmethod
    def _diagnose_validation(report):
        from ..diagnostics import DiagnosticEngine
        try:
            return DiagnosticEngine().diagnose(validation_report=report)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _remember_repair(diagnosis, worked: bool, investigation, run,
                         *, attempt: int = 1, before=None, after=None,
                         duration_ms: int = 0, prior_art: str = "") -> None:
        """Record the repair twice, for two different consumers.

        LearningStore keeps a prose lesson for the next prompt. RepairAttempt
        keeps a queryable row, so "which strategies actually work?" is a
        group-by rather than a JSON crawl over every run.
        """
        from ..learning import LearningStore
        from ..models import RepairAttempt

        try:
            LearningStore().record_repair(
                investigation=investigation, diagnosis=diagnosis, worked=worked,
                scraper=getattr(run, "scraper", None))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] could not record repair outcome: %s", exc)

        try:
            RepairAttempt.objects.create(
                scraper=getattr(run, "scraper", None), generation_run=run,
                attempt=attempt,
                subsystem=(getattr(diagnosis, "subsystem", "") or "")[:20],
                failure_category=(getattr(diagnosis, "category", "") or "")[:30],
                strategy=(getattr(diagnosis, "repair_strategy", "") or "")[:30],
                scope=list(getattr(diagnosis, "repair_scope", []) or []),
                worked=worked,
                style_score_before=getattr(before, "style_score", 0) or 0,
                style_score_after=getattr(after, "style_score", 0) or 0,
                violations_before=sorted({v.code for v in
                                          getattr(before, "violations", [])})[:12],
                violations_after=sorted({v.code for v in
                                         getattr(after, "violations", [])})[:12],
                duration_ms=duration_ms, prior_art_used=(prior_art or "")[:300])
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] could not store repair attempt: %s", exc)

    #: Violation codes the model cannot resolve by rewriting its own file.
    #: C8 is a module/class name collision, which the pipeline assigns.
    UNFIXABLE_CODES = frozenset({"C8"})

    @classmethod
    def _unfixable_by_model(cls, report) -> bool:
        hard = report.hard_violations
        return bool(hard) and all(v.code in cls.UNFIXABLE_CODES for v in hard)

    # ── diagnosis and learning (Phase 2) ──────────────────────────────────────

    @staticmethod
    def _diagnose(result: GenerationResult, *, install_error: str = ""):
        from ..diagnostics import DiagnosticEngine
        try:
            return DiagnosticEngine().diagnose(
                investigation=result.investigation, plan=result.plan,
                validation_report=result.report, test_outcome=result.test_outcome,
                tender_report=result.tender_report,
                field_report=result.field_report, install_error=install_error)
        except Exception as exc:  # noqa: BLE001 — a build must not die diagnosing
            logger.warning("[factory] diagnosis failed: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _pin_identity(code: str, class_name: str) -> str:
        """Pin the ScraperSource lookup string to the requested class name.

        The lookup string is not the model's to choose: the installer creates
        the source row under exactly the requested class name, so for a
        generated file any other value is wrong by definition. The first real
        AJi AI generations misspelled it two different ways ('NePco...',
        'Nebco...'), and at temperature 0 with a fixed seed the repair loop
        cannot converge on it — the model deterministically reproduces its own
        typo. A Factory-dictated constant is enforced mechanically, like the
        code-fence stripping: this is reading the answer into the contract,
        not repairing the model's code.
        """
        import re as _re

        if not class_name:
            return code

        def _fix(match):
            wrong = match.group(3)
            if wrong == class_name:
                return match.group(0)
            logger.info("[factory] pinned scraper_class=%r to %r "
                        "(Factory-dictated constant)", wrong, class_name)
            return f"{match.group(1)}{match.group(2)}{class_name}{match.group(2)}"

        return _re.sub(
            r"(scraper_class\s*=\s*)(['\"])([A-Za-z_][A-Za-z0-9_]*)\2",
            _fix, code)

    @staticmethod
    def _failure_message(result: GenerationResult) -> str:
        """Lead with the root cause, not with whichever symptom surfaced last."""
        d = result.diagnosis
        if d is not None and d.root_cause is not None:
            return (f"{d.subsystem}: {d.root_cause.summary}"
                    + (f" Suggested: {d.suggestions[0]}" if d.suggestions else ""))
        tr = result.tender_report
        if tr is not None and tr.stop_category:
            return (f"The scraper ran but none of its {tr.rows_total} row(s) are "
                    f"tenders ({tr.stop_category}).")
        return ("The file is valid Python in house style, but it did not extract "
                "tenders from the live site.")

    @staticmethod
    def _learn_success(result: GenerationResult, outcome) -> None:
        from ..learning import LearningStore
        try:
            LearningStore().record_success(
                investigation=result.investigation, plan=result.plan,
                scraper=getattr(result.generation_run, "scraper", None),
                tender_report=result.tender_report,
                rows_extracted=getattr(outcome, "tenders_found", 0))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] could not learn from success: %s", exc)

    @staticmethod
    def _learn_failure(result: GenerationResult) -> None:
        from ..learning import LearningStore
        try:
            LearningStore().record_failure(
                investigation=result.investigation, diagnosis=result.diagnosis,
                scraper=getattr(result.generation_run, "scraper", None))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] could not learn from failure: %s", exc)

    @staticmethod
    def _store_investigation(run, investigation, plan, scraper, user):
        """Persist the evidence. Never fail a build because logging failed."""
        from ..models import InvestigationRecord

        try:
            root = investigation.root_cause()
            return InvestigationRecord.objects.create(
                scraper=scraper, generation_run=run,
                snapshot=run.baseline_snapshot,
                url=investigation.url[:500],
                tier=investigation.tier,
                escalated=investigation.escalated,
                escalation_reason=investigation.escalation_reason[:300],
                data_source=investigation.data_source,
                data_source_confidence=investigation.data_source_confidence,
                overall_confidence=investigation.overall_confidence(),
                complete=investigation.complete,
                root_cause_stage=(root.stage if root else ""),
                failure_category=(root.failure if root else ""),
                stages={k: v.to_dict() for k, v in investigation.stages.items()},
                unknowns=investigation.all_unknowns(),
                strategy=plan.strategy,
                strategy_confidence=plan.confidence,
                plan_viable=plan.viable,
                plan=plan.to_dict(),
                site_signature=investigation.site_signature,
                language=investigation.language[:8],
                country_hint=investigation.country_hint[:4],
                duration_ms=investigation.duration_ms,
                created_by=user,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] could not store investigation: %s", exc)
            return None

    @staticmethod
    def _store_test_run(run, outcome, version=None):
        """Save the sandbox result, including the rows it extracted."""
        from ..models import FactoryTestRun, TestStatus

        if run.scraper is None:
            # FactoryTestRun.scraper is NOT NULL. Direct orchestrator calls (the
            # CLI, tests) have no record to attach to; skip cleanly instead of
            # raising an IntegrityError that then has to be swallowed.
            logger.debug("[factory] no scraper record; test run not persisted")
            return None
        try:
            status = TestStatus.PASSED if getattr(outcome, "ok", False) else (
                TestStatus.TIMEOUT
                if getattr(outcome, "error_category", "") == "TIMEOUT"
                else TestStatus.FAILED)
            return FactoryTestRun.objects.create(
                scraper=run.scraper, code_version=version, status=status,
                tenders_found=getattr(outcome, "tenders_found", 0),
                runtime_ms=getattr(outcome, "runtime_ms", 0),
                sample_rows=getattr(outcome, "sample_rows", []) or [],
                error_category=getattr(outcome, "error_category", "") or "",
                error_message=(getattr(outcome, "error_message", "") or "")[:4000],
                stdout_tail=(getattr(outcome, "stdout_tail", "") or "")[:4000],
                stderr_tail=(getattr(outcome, "stderr_tail", "") or "")[:4000],
                triggered_by=run.created_by,
            )
        except Exception as exc:  # noqa: BLE001 — never fail a build over this
            logger.warning("[factory] could not store test run: %s", exc)
            return None

    # ── bookkeeping ───────────────────────────────────────────────────────────

    def _benchmark(self, run, result, test_outcome, report, gen_ms, repairs):
        try:
            from ..interfaces import TestOutcome
            outcome = test_outcome or TestOutcome()
            bench = Benchmarker().evaluate(
                test_outcome=outcome,
                style_score=getattr(report, "style_score", 0),
                repair_iterations=repairs, generation_ms=gen_ms,
                previous_runtimes=self._previous_runtimes(run),
                validation_passed=bool(getattr(report, "passed", False)))
            # Stage timings for the production benchmark. Investigation and
            # validation costs were never persisted anywhere; end-to-end minus
            # generation minus sandbox left them indistinguishable from overhead.
            bench.details["investigation_ms"] = getattr(
                result, "investigation_ms", 0)
            bench.details["validation_ms"] = getattr(self, "_validation_ms", 0)
            result.benchmark = bench
            persist_benchmark(bench, generation_run=run, scraper=run.scraper)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] benchmarking failed: %s", exc)

    @staticmethod
    def _previous_runtimes(run) -> list[int]:
        try:
            from ..models import BenchmarkReport
            return list(
                BenchmarkReport.objects
                .filter(family=run.family, execution_success=True)
                .exclude(generation_run=run)
                .order_by("-created_at")
                .values_list("runtime_ms", flat=True)[:5])
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _prompt_version_row(version: str):
        try:
            from ..models import PromptVersion
            return PromptVersion.objects.filter(version=version).first()
        except Exception:  # noqa: BLE001
            return None

    def _finish(self, run, result: GenerationResult, started: float):
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.finished_at = timezone.now()
        run.save()
        result.outcome = run.outcome
        return result

    def _finish_error(self, run, result: GenerationResult, outcome: str,
                      message: str, started: float):
        run.outcome = outcome
        run.error_message = message[:4000]
        result.message = message
        result.outcome = outcome
        logger.warning("[factory] generation ended: %s — %s", outcome, message[:200])
        return self._finish(run, result, started)
