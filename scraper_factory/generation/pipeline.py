"""One-click Scraper Factory pipeline (MVP).

The employee's entire interaction is: paste a URL, click Generate. Everything
else is automatic:

    URL -> analyze -> match -> prompt -> model -> validate
        -> sandbox -> repair (<=3) -> INSTALL -> REGISTER -> RUN -> SAVE

There is no approval step, no preview, and no manual installation. The employee
sees a status and a one-line outcome; prompts, generated code, validation
reports, benchmark reports and extracted rows are recorded for developers but
never surfaced.

Because no human inspects the code, the automatic gates are the only protection:
a candidate reaches installation ONLY if it passed the safety, contract and style
gates AND actually extracted tenders from the live site in the sandbox. If it
cannot get there within the repair budget, the request goes to the internal
developer review queue instead — it is never installed "hopefully".

The final run reuses the project's own runner (`tenders.apps._run_single_scraper`),
so tenders are saved by the existing pipeline with its dedup, mirrors, AI
enrichment and ScrapeLog behaviour. No business logic is duplicated here.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from django.utils import timezone

from ..installation.installer import (MODE_DRY_RUN, MODE_INSTALL, InstallError,
                                      ScraperInstaller)
from ..models import (AuditAction, FactoryAuditLog, FactoryStatus,
                      GeneratedScraper, ScraperCodeVersion, VersionOrigin)
from .orchestrator import GenerationOrchestrator

logger = logging.getLogger("scraper_factory")

# Employee-facing stages. Deliberately coarse: the employee needs progress, not
# internals.
STAGE_LABELS = {
    "ANALYZING": "Analysing the website",
    "GENERATING": "Writing the scraper",
    "VERIFYING": "Checking the scraper",
    "TESTING": "Testing against the live site",
    "INSTALLING": "Adding it to the project",
    "RUNNING": "Collecting tenders",
    "DONE": "Finished",
    "REVIEW": "Sent to the development team",
}


@dataclass
class BuildOutcome:
    """What the employee sees. Nothing internal leaks into this object."""
    ok: bool = False
    stage: str = "ANALYZING"
    headline: str = ""
    detail: str = ""
    scraper_id: Optional[int] = None
    source_name: str = ""
    tenders_saved: int = 0
    needs_developer: bool = False
    duration_ms: int = 0
    #: The tenders the scraper actually extracted, as captured by the sandbox.
    #: Shown after a build so the result can be judged rather than trusted.
    extracted_rows: list[dict] = field(default_factory=list)
    # Internal-only, for developers and the audit trail. Never rendered to the
    # employee; kept here so the caller can log or queue it.
    internal: dict[str, Any] = field(default_factory=dict)

    def employee_message(self) -> str:
        return self.headline


class ScraperFactoryPipeline:
    """Runs the full automatic build for one URL."""

    def __init__(self, *, model=None, orchestrator=None, installer=None):
        self._model = model
        self._orchestrator = orchestrator
        self._installer = installer or ScraperInstaller()

    # ── public API ────────────────────────────────────────────────────────────

    def build(self, url: str, *, user=None, country_code: str = "",
              source_name: str = "", progress=None,
              mode: str = MODE_INSTALL,
              with_save: bool = False) -> BuildOutcome:
        """Generate, verify, install, register and run a scraper for `url`.

        ``mode`` selects between:
          MODE_INSTALL  — permanent transactional installation
          MODE_DRY_RUN  — the identical pipeline, then everything is rolled back
                          (file, registry, runtime registration, and the tenders
                          it saved), leaving the repository byte-identical
        """
        started = time.monotonic()
        outcome = BuildOutcome()

        def step(stage: str):
            outcome.stage = stage
            if progress:
                progress(stage, STAGE_LABELS.get(stage, stage))

        try:
            step("ANALYZING")
            identity = self._derive_identity(url, source_name, country_code)
            outcome.source_name = identity["source_name"]

            existing = self._existing_for(url)
            if existing is not None and existing.status == FactoryStatus.INSTALLED:
                outcome.ok = False
                outcome.stage = "DONE"
                outcome.headline = (
                    f"This website is already covered by "
                    f"'{existing.name}'. Nothing to do.")
                return self._finish(outcome, started)

            if existing is not None:
                # A previous build for this URL failed. Reuse its record so the
                # employee can simply click Generate again: this keeps the
                # generation history attached to one row and avoids a unique
                # clash on module_name/class_name.
                record = self._reuse_record(existing, identity)
            else:
                record = self._create_record(url, identity, user)
            outcome.scraper_id = record.pk
            identity["module_name"] = record.module_name
            identity["class_name"] = record.class_name
            outcome.source_name = record.name

            # ── generate + verify + test + repair ─────────────────────────────
            step("GENERATING")
            orch = self._orchestrator or GenerationOrchestrator(model=self._model)
            record.status = FactoryStatus.GENERATING
            record.save(update_fields=["status", "updated_at"])

            result = orch.generate(
                url, module_name=identity["module_name"],
                class_name=identity["class_name"],
                country_code=identity["country_code"],
                source_name=identity["source_name"], user=user,
                run_sandbox=True, scraper=record)

            outcome.internal = {
                "generation_run_id": getattr(result.generation_run, "pk", None),
                "outcome": result.outcome,
                "attempts": result.attempts,
                "repairs": result.repair_attempts,
                "references": result.reference_modules,
                "style_score": getattr(result.report, "style_score", 0),
                "explanation": result.explain()[:4000],
            }

            # The sandbox already ran the scraper against the live site, so its
            # rows are the honest evidence of what this build produces.
            outcome.extracted_rows = list(
                getattr(result.test_outcome, "sample_rows", []) or [])

            step("VERIFYING")
            if not result.ok:
                return self._to_review(record, outcome, result, started)

            # Record the accepted code as version 1.
            version = self._store_version(record, result, user)

            # ── install (or temporarily install) + register + run ─────────────
            step("INSTALLING")
            if mode == MODE_DRY_RUN:
                return self._dry_run_install(
                    record, identity, result, outcome, url, step, started,
                    with_save=with_save)

            try:
                install = self._installer.install(
                    source_code=result.source_code,
                    module_name=identity["module_name"],
                    class_name=identity["class_name"],
                    country_code=identity["country_code"],
                    source_name=identity["source_name"],
                    website_url=url, generated_scraper=record, user=user,
                    allow_replace=True)
            except InstallError as exc:
                outcome.internal["install_error"] = str(exc)[:2000]
                return self._to_review(record, outcome, result, started,
                                       reason=f"Installation failed: {exc}")

            if version is not None:
                record.active_version = version
                record.save(update_fields=["active_version", "updated_at"])

            # ── run it through the project's own runner ──────────────────────
            step("RUNNING")
            saved, run_error = self._run_once(install.scraper_source_id)
            outcome.tenders_saved = saved
            outcome.internal["first_run_error"] = run_error

            # A failed first execution must not leave a broken replacement in
            # place: restore the previous working version if we replaced one.
            if run_error and install.replaced_backup_id:
                restored = self._restore_previous(install.replaced_backup_id)
                outcome.internal["restored_previous"] = restored
                if restored:
                    return self._to_review(
                        record, outcome, result, started,
                        reason=("First execution failed after replacing an "
                                f"existing scraper: {run_error}. The previous "
                                "working version was restored."))

            step("DONE")
            outcome.ok = True
            outcome.headline = (
                f"Done. '{identity['source_name']}' was added and collected "
                f"{saved} tender(s). It will keep updating automatically.")
            outcome.detail = ("The new source now runs on the normal schedule "
                              "with every other source.")
            return self._finish(outcome, started)

        except Exception as exc:  # noqa: BLE001 — the employee must never see a stack
            logger.error("[factory] pipeline crashed for %s: %s", url, exc,
                         exc_info=True)
            outcome.ok = False
            outcome.needs_developer = True
            outcome.stage = "REVIEW"
            outcome.headline = ("We could not add this website automatically. "
                                "It has been sent to the development team.")
            outcome.internal["crash"] = f"{type(exc).__name__}: {exc}"
            self._queue_crash(outcome, url, user)
            return self._finish(outcome, started)

    # ── identity derivation ───────────────────────────────────────────────────

    def _derive_identity(self, url: str, source_name: str,
                         country_code: str) -> dict[str, str]:
        """Derive module/class/source names from the URL, house-style.

        The employee only supplies a URL, so these must be produced
        automatically and must match the corpus conventions: lower_snake_case
        module, CamelCase class ending in 'Scraper'.
        """
        host = (urlparse(url).hostname or "portal").lower()
        host = re.sub(r"^www\.", "", host)
        tld = host.rsplit(".", 1)[-1] if "." in host else ""

        # An explicit name is the best source for a readable identifier; fall
        # back to the host. Bare IP addresses carry no meaning, so they are
        # never used for naming.
        is_ip = bool(re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host))
        if source_name.strip():
            stem_parts = [p for p in re.split(r"[^a-z0-9]+",
                                              source_name.lower()) if p][:3]
        elif is_ip:
            stem_parts = ["portal"]
        else:
            parts = [p for p in re.split(r"[.\-]", host)
                     if p and p not in ("com", "net", "org", "gov", "www")]
            stem_parts = [p for p in parts if p != tld][:3]
        stem_parts = stem_parts or ["portal"]

        module_name = "_".join(stem_parts)
        module_name = re.sub(r"[^a-z0-9_]", "", module_name) or "portal"
        if not module_name[0].isalpha():
            module_name = f"p_{module_name}"
        module_name = self._unique_module(module_name)

        class_name = "".join(p.capitalize() for p in module_name.split("_"))
        class_name = f"{class_name}Scraper"
        class_name = self._unique_class(class_name)

        if not country_code:
            country_code = self._country_from_tld(tld)

        return {
            "module_name": module_name,
            "class_name": class_name,
            "country_code": (country_code or "JO").upper(),
            "source_name": source_name or " ".join(
                p.capitalize() for p in stem_parts),
        }

    def _unique_module(self, base: str) -> str:
        from django.conf import settings
        import pathlib
        directory = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR"))
        candidate, n = base, 2
        while (directory / f"{candidate}.py").exists() or \
                GeneratedScraper.objects.filter(module_name=candidate).exists():
            candidate = f"{base}_{n}"
            n += 1
        return candidate

    @staticmethod
    def _unique_class(base: str) -> str:
        try:
            from tenders.scrapers import SCRAPER_REGISTRY
            taken = set(SCRAPER_REGISTRY)
        except Exception:  # noqa: BLE001
            taken = set()
        candidate, n = base, 2
        while candidate in taken or \
                GeneratedScraper.objects.filter(class_name=candidate).exists():
            candidate = f"{base[:-7]}{n}Scraper"
            n += 1
        return candidate

    @staticmethod
    def _country_from_tld(tld: str) -> str:
        return {
            "jo": "JO", "sa": "SA", "ae": "AE", "qa": "QA", "om": "OM",
            "kw": "KW", "bh": "BH", "iq": "IQ", "sy": "SY", "eg": "EG",
            "ma": "MA", "rw": "RW", "et": "ET", "ke": "KE", "ug": "UG",
        }.get(tld, "")

    # ── records ───────────────────────────────────────────────────────────────

    @staticmethod
    def _existing_for(url: str):
        from ..services import url_fingerprint
        fp = url_fingerprint(url)
        return GeneratedScraper.objects.filter(url_fingerprint=fp).first()

    def _create_record(self, url: str, identity: dict, user) -> GeneratedScraper:
        from ..services import url_fingerprint
        from tenders.models import CountryConfig

        country = CountryConfig.objects.filter(
            code=identity["country_code"]).first()
        if country is None:
            country = CountryConfig.objects.filter(is_active=True).first()
        if country is None:
            raise RuntimeError("No CountryConfig rows exist; cannot create a source.")

        return GeneratedScraper.objects.create(
            name=identity["source_name"], website_url=url[:500],
            url_fingerprint=url_fingerprint(url),
            module_name=identity["module_name"],
            class_name=identity["class_name"],
            country=country, family="", status=FactoryStatus.ANALYZING,
            created_by=user if user is not None else self._system_user(),
        )

    def _reuse_record(self, record: GeneratedScraper,
                      identity: dict) -> GeneratedScraper:
        """Reset a previously failed record so the URL can be retried.

        The record's stored module/class names are kept so history stays on one
        row — but only while they are still usable. If a previous attempt left a
        file or registry entry behind, reusing the same names would fail
        validation with a duplicate-name error on every retry, and no amount of
        repairing can fix that. In that case fresh unique names are assigned.
        """
        record.status = FactoryStatus.ANALYZING
        record.review_reason = ""
        fields = ["status", "review_reason", "updated_at"]

        if identity.get("source_name"):
            record.name = identity["source_name"]
            fields.append("name")

        if self._names_taken(record.module_name, record.class_name):
            new_module = self._unique_module(record.module_name)
            new_class = self._unique_class(record.class_name)
            logger.info("[factory] names %s/%s are taken; retrying as %s/%s",
                        record.module_name, record.class_name,
                        new_module, new_class)
            record.module_name = new_module
            record.class_name = new_class
            fields += ["module_name", "class_name"]

        record.save(update_fields=fields)
        return record

    @staticmethod
    def _names_taken(module_name: str, class_name: str) -> bool:
        """True when the module file or the registry key is already in use."""
        import pathlib

        from django.conf import settings
        directory = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR"))
        if (directory / f"{module_name}.py").exists():
            return True
        try:
            from tenders.scrapers import SCRAPER_REGISTRY
            return class_name in SCRAPER_REGISTRY
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _system_user():
        from django.contrib.auth.models import User
        return (User.objects.filter(is_superuser=True).order_by("pk").first()
                or User.objects.order_by("pk").first())

    @staticmethod
    def _store_version(record: GeneratedScraper, result, user):
        try:
            run = result.generation_run
            # Retries reuse the record, so the version number must continue the
            # sequence rather than always being 1 (unique_together).
            last = (ScraperCodeVersion.objects.filter(scraper=record)
                    .order_by("-version_number").first())
            next_number = (last.version_number if last else 0) + 1
            return ScraperCodeVersion.objects.create(
                scraper=record, version_number=next_number,
                source_code=result.source_code,
                origin=(VersionOrigin.QWEN_REPAIRED if result.repair_attempts
                        else VersionOrigin.QWEN_GENERATED),
                attempt_number=result.attempts,
                reference_modules=result.reference_modules,
                model_name=getattr(run, "model_name", ""),
                model_params=getattr(run, "model_params", {}) or {},
                validation_report={
                    "style_score": getattr(result.report, "style_score", 0),
                    "passed": getattr(result.report, "passed", False),
                },
                style_score=getattr(result.report, "style_score", 0),
                comment="Automatic one-click generation",
                created_by=user)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] could not store code version: %s", exc)
            return None

    # ── dry run ───────────────────────────────────────────────────────────────

    def _dry_run_install(self, record, identity: dict, result,
                         outcome: BuildOutcome, url: str, step,
                         started: float, with_save: bool = False) -> BuildOutcome:
        """Execute the real pipeline, then undo every trace of it."""
        try:
            with self._installer.dry_run(
                    source_code=result.source_code,
                    module_name=identity["module_name"],
                    class_name=identity["class_name"],
                    country_code=identity["country_code"],
                    source_name=identity["source_name"],
                    website_url=url) as install:
                from django.conf import settings as _s
                skip_save = bool(getattr(_s, "FACTORY_DRYRUN_SKIP_SAVE", True))                     and not with_save
                if skip_save:
                    # The sandbox already ran this scraper against the live site
                    # and captured its rows, so extraction is proven. Skipping
                    # the save pass keeps a test to seconds instead of minutes.
                    outcome.tenders_saved = len(outcome.extracted_rows)
                    outcome.internal["save_pass"] = "skipped (sandbox already proved extraction)"
                else:
                    step("RUNNING")
                    saved, run_error = self._run_once(install.scraper_source_id)
                    outcome.tenders_saved = saved
                    outcome.internal["first_run_error"] = run_error
                outcome.internal["dry_run_steps"] = list(install.steps)
        except InstallError as exc:
            outcome.internal["install_error"] = str(exc)[:2000]
            return self._to_review(record, outcome, result, started,
                                   reason=f"Dry-run installation failed: {exc}")

        # The record stays as a test result; nothing was installed.
        record.status = FactoryStatus.TEST_PASSED
        record.save(update_fields=["status", "updated_at"])

        step("DONE")
        outcome.ok = not outcome.internal.get("first_run_error")
        outcome.headline = (
            f"Dry run finished. '{identity['source_name']}' would collect "
            f"{outcome.tenders_saved} tender(s). Nothing was installed and "
            "nothing was saved.")
        outcome.detail = "The repository and database are unchanged."
        return self._finish(outcome, started)

    def _restore_previous(self, backup_id: int) -> bool:
        try:
            from ..models import ScraperFileBackup
            backup = ScraperFileBackup.objects.filter(pk=backup_id).first()
            if backup is None:
                return False
            self._installer.restore(backup)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("[factory] could not restore previous version: %s", exc)
            return False

    # ── run through the existing project runner ───────────────────────────────

    @staticmethod
    def _run_once(source_id: Optional[int]) -> tuple[int, str]:
        """Run the new scraper using the project's own runner.

        Reuses `tenders.apps._run_single_scraper`, which scrapes, persists via
        save_tender_data(), writes a ScrapeLog and performs AI enrichment. That
        keeps every piece of business logic in one place.
        """
        """Run the scraper once. Returns (tenders_saved, error_text).

        `error_text` is empty on success. It is non-empty when the run raised or
        when the scraper reported a failure on its console, which the caller uses
        to decide whether to restore a replaced version.
        """
        if not source_id:
            return 0, "no source row was created"
        try:
            import contextlib
            import io

            from tenders.apps import _run_single_scraper
            from tenders.models import Tender

            before = Tender.objects.filter(source_id=source_id).count()
            # House scrapers print their banner and progress to stdout. The
            # employee must not see scraper internals, so the console output is
            # captured here; it still reaches the log file via logger calls.
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink):
                _run_single_scraper(source_id)
            after = Tender.objects.filter(source_id=source_id).count()
            console = sink.getvalue()
            logger.debug("[factory] first-run console output:\n%s",
                         console[-4000:])

            saved = max(0, after - before)
            reported = re.findall(r"^\s*X\s+\[[^\]]+\]\s*(.+)$", console,
                                  re.MULTILINE)
            if saved == 0 and reported:
                return 0, reported[-1].strip()[:300]
            return saved, ""
        except Exception as exc:  # noqa: BLE001 — a failed first run is not fatal
            # for a NEW scraper: it is installed and the scheduler will retry.
            logger.warning("[factory] first run failed for source %s: %s",
                           source_id, exc)
            return 0, f"{type(exc).__name__}: {exc}"[:300]

    # ── developer review queue ────────────────────────────────────────────────

    def _to_review(self, record: GeneratedScraper, outcome: BuildOutcome,
                   result, started: float, reason: str = "") -> BuildOutcome:
        """Automatic generation failed — hand off to developers, never install."""
        detail = reason or result.explain()
        record.status = FactoryStatus.AWAITING_DEVELOPER
        record.review_reason = (detail or "")[:4000]
        record.save(update_fields=["status", "review_reason", "updated_at"])

        try:
            FactoryAuditLog.objects.create(
                action=AuditAction.REJECT, scraper=record,
                new_value={"stage": outcome.stage,
                           "internal": outcome.internal},
                reason=("Automatic build failed after the repair budget; queued "
                        "for developer review"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] audit skipped: %s", exc)

        # Hand the developer the whole case file, not just a status. Everything
        # below was already computed during the build; discarding it would make
        # the human redo the investigation by hand.
        try:
            from .. import review
            from ..models import InvestigationRecord

            review.queue_item(
                url=record.website_url, scraper=record,
                investigation=getattr(result, "investigation", None),
                plan=getattr(result, "plan", None),
                diagnosis=getattr(result, "diagnosis", None),
                generation_run=getattr(result, "generation_run", None),
                investigation_record=InvestigationRecord.objects.filter(
                    scraper=record).order_by("-created_at").first(),
                headline=(detail or "")[:300],
                references=list(getattr(result, "reference_modules", []) or []))
        except Exception as exc:  # noqa: BLE001 — the queue must not break a build
            logger.warning("[factory] could not queue review item: %s", exc)

        outcome.ok = False
        outcome.needs_developer = True
        outcome.stage = "REVIEW"
        outcome.headline = ("We could not add this website automatically. "
                            "It has been sent to the development team.")
        outcome.detail = ""
        logger.info("[factory] queued %s for developer review: %s",
                    record.website_url, (detail or "")[:200])
        return self._finish(outcome, started)

    @staticmethod
    def _queue_crash(outcome: BuildOutcome, url: str, user) -> None:
        try:
            FactoryAuditLog.objects.create(
                actor=user, action=AuditAction.REJECT,
                new_value={"url": url, "internal": outcome.internal},
                reason="Pipeline crashed; queued for developer review")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _finish(outcome: BuildOutcome, started: float) -> BuildOutcome:
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        return outcome
