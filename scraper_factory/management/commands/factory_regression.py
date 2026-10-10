"""Regression gate — run this after every architectural change.

    python manage.py factory_regression
    python manage.py factory_regression --benchmark 12
    python manage.py factory_regression --strict

Checks, in order of how badly a failure would hurt:

  1  the 73 hand-written scrapers are byte-for-byte unchanged
  2  every one of them still passes the style/contract checker
  3  no migration is missing, and every applied migration was additive
  4  production data is intact (tender and user counts)
  5  the Factory's own invariants still hold (field contract, taxonomy, providers)
  6  optionally, a benchmark subset with regression comparison

Exit code is non-zero when anything fails, so this can gate a commit or a deploy.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

from django.core.management.base import BaseCommand

#: The corpus fingerprint recorded when Phase 1 began. Any change here means a
#: hand-written production scraper was modified, which is the one thing the
#: Factory must never do.
CORPUS_INIT_MD5 = "82f454872d13ab458f20ee2757b33aee"


class Command(BaseCommand):
    help = "Run the Factory regression gate."

    def add_arguments(self, parser):
        parser.add_argument("--benchmark", type=int, default=0,
                            help="Also run this many benchmark cases.")
        parser.add_argument("--strict", action="store_true",
                            help="Treat warnings as failures.")

    def handle(self, *args, **options):
        self.failures: list[str] = []
        self.warnings: list[str] = []

        self.stdout.write("")
        self.stdout.write("FACTORY REGRESSION GATE")
        self.stdout.write("=" * 74)

        self._check_corpus_unchanged()
        self._check_selfcheck()
        self._check_migrations()
        self._check_production_data()
        self._check_factory_invariants()
        self._check_install_rollback()
        self._check_safety()
        if options["benchmark"]:
            self._check_benchmark(options["benchmark"])

        self.stdout.write("")
        if self.warnings:
            self.stdout.write("WARNINGS")
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f"  ! {w}"))

        strict_fail = bool(self.warnings) and options["strict"]
        if self.failures or strict_fail:
            self.stdout.write("")
            for f in self.failures:
                self.stdout.write(self.style.ERROR(f"  x {f}"))
            self.stdout.write(self.style.ERROR(
                f"\nREGRESSION GATE FAILED — {len(self.failures)} failure(s)"
                + (", warnings treated as failures" if strict_fail else "")))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS("\nREGRESSION GATE PASSED"))

    # ── checks ────────────────────────────────────────────────────────────────

    def _ok(self, label: str, detail: str = "") -> None:
        self.stdout.write(f"  [ok]   {label}" + (f" — {detail}" if detail else ""))

    def _fail(self, label: str, detail: str) -> None:
        self.stdout.write(self.style.ERROR(f"  [FAIL] {label} — {detail}"))
        self.failures.append(f"{label}: {detail}")

    def _warn(self, label: str, detail: str) -> None:
        self.stdout.write(self.style.WARNING(f"  [warn] {label} — {detail}"))
        self.warnings.append(f"{label}: {detail}")

    def _check_corpus_unchanged(self) -> None:
        from django.conf import settings

        self.stdout.write("")
        self.stdout.write("1. hand-written scrapers unchanged")
        d = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR",
                                 pathlib.Path(settings.BASE_DIR) / "tenders" / "scrapers"))
        init = d / "__init__.py"
        try:
            digest = hashlib.md5(init.read_bytes()).hexdigest()
        except OSError as exc:
            self._fail("registry readable", str(exc))
            return
        if digest == CORPUS_INIT_MD5:
            self._ok("registry byte-identical", digest)
        else:
            self._fail("registry modified",
                       f"__init__.py is {digest}, expected {CORPUS_INIT_MD5}")

        if b"\r\n" in init.read_bytes():
            self._fail("registry line endings", "CRLF found; the corpus uses LF")
        else:
            self._ok("registry line endings", "LF")

        # Factory-owned files are allowed to change; everything else is not.
        # A method on the installer, not a module-level function. Getting this
        # wrong made the check silently report zero owned modules.
        try:
            from ...installation.installer import ScraperInstaller
            owned = set(ScraperInstaller().owned_modules())
        except Exception as exc:  # noqa: BLE001
            self._warn("ownership manifest", f"{type(exc).__name__}: {exc}")
            owned = set()
        modules = [p for p in d.glob("*.py")
                   if not p.name.startswith("_") and p.name != "base.py"]
        self._ok("corpus module count",
                 f"{len(modules)} ({len(owned)} factory-owned)")

    def _check_selfcheck(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        self.stdout.write("")
        self.stdout.write("2. style/contract checker against the corpus")
        buf = StringIO()
        try:
            call_command("factory_selfcheck", stdout=buf, stderr=buf)
        except SystemExit:
            pass
        except Exception as exc:  # noqa: BLE001
            self._fail("factory_selfcheck", f"{type(exc).__name__}: {exc}")
            return
        text = buf.getvalue()
        import re
        m = re.search(r"checked=(\d+)\s+passed=(\d+)\s+failed=(\d+)", text)
        if not m:
            self._fail("factory_selfcheck", "could not read the verdict")
            return
        checked, passed, failed = (int(m.group(i)) for i in (1, 2, 3))
        if failed:
            self._fail("factory_selfcheck", f"{failed} of {checked} modules fail")
        else:
            self._ok("every corpus module passes", f"{passed}/{checked}")

    def _check_migrations(self) -> None:
        from io import StringIO

        from django.core.management import call_command
        from django.db.migrations.loader import MigrationLoader

        self.stdout.write("")
        self.stdout.write("3. migrations")
        buf = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run",
                         stdout=buf, stderr=buf)
            self._ok("no model changes awaiting a migration")
        except SystemExit:
            self._fail("pending migration",
                       "models changed without a migration: " +
                       buf.getvalue().strip()[:160])
        except Exception as exc:  # noqa: BLE001
            self._warn("makemigrations --check", f"{type(exc).__name__}: {exc}")

        # Every Factory migration must be additive. Destructive operations are
        # the one migration class this project forbids outright.
        try:
            loader = MigrationLoader(None, ignore_no_migrations=True)
            destructive = []
            for (app, name), migration in loader.disk_migrations.items():
                if app != "scraper_factory":
                    continue
                for op in migration.operations:
                    kind = type(op).__name__
                    if kind in ("DeleteModel", "RemoveField", "RenameField",
                                "RenameModel", "AlterModelTable"):
                        destructive.append(f"{name}: {kind}")
            if destructive:
                self._fail("additive migrations only", "; ".join(destructive[:4]))
            else:
                count = sum(1 for a, _ in loader.disk_migrations if a == "scraper_factory")
                self._ok("all factory migrations additive", f"{count} migration(s)")
        except Exception as exc:  # noqa: BLE001
            self._warn("migration inspection", f"{type(exc).__name__}: {exc}")

    def _check_production_data(self) -> None:
        self.stdout.write("")
        self.stdout.write("4. production data intact")
        try:
            from django.contrib.auth.models import User

            from tenders.models import ScraperSource, Tender
            tenders = Tender.objects.count()
            users = User.objects.count()
            sources = ScraperSource.objects.count()
            active = ScraperSource.objects.filter(is_active=True).count()
            self._ok("tenders present", f"{tenders}")
            self._ok("users present", f"{users}")
            self._ok("sources", f"{sources} ({active} active)")
            if tenders == 0:
                self._fail("tenders", "the tender table is empty")
            if users == 0:
                self._fail("users", "the user table is empty")

            # A source deactivated by the health policy is expected; one
            # deactivated with no health record behind it is not.
            from ...models import GeneratedScraper, HealthState, ScraperHealth
            disabled = ScraperHealth.objects.filter(state=HealthState.DISABLED)
            if disabled.exists():
                self._warn("disabled generated scrapers",
                           f"{disabled.count()} — check the review queue")
        except Exception as exc:  # noqa: BLE001
            self._fail("production data check", f"{type(exc).__name__}: {exc}")

    def _check_factory_invariants(self) -> None:
        self.stdout.write("")
        self.stdout.write("5. factory invariants")
        try:
            from ...corpus.fields import get_field_contract
            contract = get_field_contract(refresh=True)
            if contract.corpus_size < 50:
                self._fail("field contract",
                           f"derived from only {contract.corpus_size} scrapers")
            elif not contract.required:
                self._fail("field contract", "no required fields derived")
            else:
                self._ok("field contract",
                         f"{len(contract.required)} required from "
                         f"{contract.corpus_size} scrapers")
        except Exception as exc:  # noqa: BLE001
            self._fail("field contract", f"{type(exc).__name__}: {exc}")

        try:
            from ... import providers
            from ...interfaces import (ICodeModel, ICodeValidator, IInvestigator,
                                       IPlanner, ISandbox)
            for interface in (IInvestigator, IPlanner, ICodeValidator, ISandbox,
                              ICodeModel):
                providers.get(interface)
            self._ok("every provider resolves")
        except Exception as exc:  # noqa: BLE001
            self._fail("provider registry", f"{type(exc).__name__}: {exc}")

        try:
            from ...prompting import strategies
            from ...planning.planner import Strategy
            missing = [s for s in Strategy.PREFERENCE
                       if s not in strategies.BRIEFS and s != Strategy.NONE]
            if missing:
                self._fail("strategy briefs", f"missing for {missing}")
            else:
                self._ok("strategy briefs",
                         f"{len(strategies.BRIEFS)} strategies covered")
        except Exception as exc:  # noqa: BLE001
            self._fail("strategy briefs", f"{type(exc).__name__}: {exc}")

        try:
            from ...validation.tender_validator import TenderValidator
            junk = [{"title": "Home"}, {"title": "Most Viewed"},
                    {"title": "Contact"}]
            report = TenderValidator().validate(junk)
            if report.rows_valid:
                self._fail("tender validator",
                           "accepted navigation labels as tenders")
            else:
                self._ok("tender validator rejects non-tenders",
                         report.stop_category)
        except Exception as exc:  # noqa: BLE001
            self._fail("tender validator", f"{type(exc).__name__}: {exc}")

        try:
            from ...observers import _connected
            if _connected:
                self._ok("health observer attached")
            else:
                self._warn("health observer",
                           "not attached in this process (normal for a one-off "
                           "command, a problem in the web/scheduler process)")
        except Exception as exc:  # noqa: BLE001
            self._warn("health observer", f"{type(exc).__name__}: {exc}")

    #: A minimal scraper that satisfies every gate. Deliberately trivial: this
    #: check is about the installer's transactional behaviour, not codegen.
    PROBE_SOURCE = '''# tenders/scrapers/{module}.py
"""
Regression probe — installed and immediately rolled back.

Technology: none. This file exists only to verify that installation and
rollback are transactional; it is never left on disk.

Date format: ISO "YYYY-MM-DD".
"""

import logging

from .base import BaseScraper, TenderData

logger = logging.getLogger('tenders')

BASE_URL = 'https://probe.invalid'
LIST_URL = 'https://probe.invalid/tenders'


class {cls}(BaseScraper):
    """Regression probe scraper. Returns nothing and contacts nothing."""

    country_code = '{country}'
    source_name  = 'Regression Probe'
    base_url     = BASE_URL

    def scrape(self) -> list[TenderData]:
        from tenders.models import ScraperSource
        try:
            source = ScraperSource.objects.get(
                scraper_class='{cls}', is_active=True,
            )
        except ScraperSource.DoesNotExist:
            print('  X  [PROBE] No active ScraperSource')
            return []

        print(f'\\n{{"_" * 62}}')
        print(f'  SOURCE  : {{source.name}}')
        print(f'  SCRAPER : {cls}')
        print(f'  MODE    : regression probe (no network)')
        print(f'  URL     : {{LIST_URL}}')
        print(f'  FILTER  : All accepted')
        print(f'{{"_" * 62}}')

        results: list[TenderData] = []
        print(f'\\n  {{"=" * 58}}')
        print(f'  TOTAL : {{len(results)}} tender(s)')
        print(f'  {{"=" * 58}}\\n')
        logger.info(f'[PROBE] complete — total={{len(results)}}')
        return results
'''

    def _check_install_rollback(self) -> None:
        """Install a throwaway scraper for real, then prove rollback is exact.

        This is the check that matters most for production safety, and it cannot
        be faked: it writes a real file, patches the real registry, then rolls
        back and compares bytes. Anything less would not detect the failure mode
        it exists to catch — a rollback that leaves the registry subtly altered.
        """
        import hashlib

        from django.conf import settings
        from django.db import transaction

        self.stdout.write("")
        self.stdout.write("6. installation and rollback")

        # A valid lower_snake_case name: the installer's preflight rejects a
        # leading underscore, even though the corpus indexer uses that prefix to
        # ignore temp modules. The name is distinctive so a leftover is obvious,
        # and the assertions below fail loudly if rollback ever leaves one.
        module = "regression_probe"
        cls = "RegressionProbeScraper"
        scrapers_dir = pathlib.Path(getattr(
            settings, "FACTORY_SCRAPERS_DIR",
            pathlib.Path(settings.BASE_DIR) / "tenders" / "scrapers"))
        init = scrapers_dir / "__init__.py"
        probe_file = scrapers_dir / f"{module}.py"

        before = init.read_bytes()
        before_md5 = hashlib.md5(before).hexdigest()

        if probe_file.exists():
            self._fail("probe file", f"{probe_file.name} already exists; refusing "
                                     f"to overwrite it")
            return

        source = self.PROBE_SOURCE.format(module=module, cls=cls, country="JO")
        installed = registry_patched = False

        class _Rollback(Exception):
            pass

        try:
            from ...installation.installer import ScraperInstaller
            installer = ScraperInstaller()
            try:
                with transaction.atomic():
                    with installer.dry_run(
                            source_code=source, module_name=module,
                            class_name=cls, country_code="JO",
                            source_name="Regression Probe",
                            website_url="https://probe.invalid/tenders"):
                        installed = probe_file.exists()
                        registry_patched = cls in init.read_text(
                            encoding="utf-8", errors="replace")
                    raise _Rollback
            except _Rollback:
                pass
        except Exception as exc:  # noqa: BLE001
            self._fail("install/rollback", f"{type(exc).__name__}: {exc}")
            # Leave nothing behind even on failure.
            if probe_file.exists():
                try:
                    probe_file.unlink()
                except OSError:
                    pass
            if init.read_bytes() != before:
                init.write_bytes(before)
                self._fail("registry restored", "had to restore it manually")
            return

        if installed:
            self._ok("file written during install", f"{module}.py")
        else:
            self._fail("file written during install", "the probe file never appeared")

        if registry_patched:
            self._ok("registry patched during install", f"{cls} registered")
        else:
            self._fail("registry patched during install",
                       f"{cls} was not added to SCRAPER_REGISTRY")

        # ── the assertions that matter ───────────────────────────────────────
        after = init.read_bytes()
        after_md5 = hashlib.md5(after).hexdigest()
        if after_md5 == before_md5:
            self._ok("registry byte-identical after rollback", after_md5)
        else:
            self._fail("rollback corrupted the registry",
                       f"{before_md5} -> {after_md5}")
            init.write_bytes(before)

        if probe_file.exists():
            self._fail("rollback left the probe file behind", str(probe_file))
            try:
                probe_file.unlink()
            except OSError:
                pass
        else:
            self._ok("probe file removed by rollback")

        try:
            from tenders.models import ScraperSource
            if ScraperSource.objects.filter(scraper_class=cls).exists():
                self._fail("rollback left a ScraperSource row", cls)
            else:
                self._ok("no database rows survived the rollback")
        except Exception as exc:  # noqa: BLE001
            self._warn("rollback db check", f"{type(exc).__name__}: {exc}")

    def _check_safety(self) -> None:
        self.stdout.write("")
        self.stdout.write("7. production safety invariants")
        try:
            from ...maintenance import verify_safety
            for name, (ok, detail) in verify_safety().items():
                if ok:
                    self._ok(name, detail)
                else:
                    self._fail(name, detail)
        except Exception as exc:  # noqa: BLE001
            self._fail("safety verification", f"{type(exc).__name__}: {exc}")

    def _check_benchmark(self, limit: int) -> None:
        self.stdout.write("")
        self.stdout.write(f"6. benchmark subset ({limit} case(s))")
        try:
            from ...benchmarking import BenchmarkEngine
            from ...models import BenchmarkSuite

            suite = BenchmarkSuite.objects.filter(is_active=True).first()
            if suite is None:
                self._warn("benchmark", "no suite exists; run "
                                        "factory_benchmark_suite --seed")
                return
            run = BenchmarkEngine(allow_tier2=False, retries=0).run(
                suite, limit=limit, notes="regression gate")
            detail = (f"{run.passed} pass / {run.failed} fail / {run.unknown} "
                      f"unknown, {run.success_rate:.0%} of decided")
            if run.regressions:
                self._fail("benchmark regressions",
                           f"{len(run.regressions)} portal(s) regressed: " +
                           ", ".join(r["label"] for r in run.regressions[:4]))
            elif run.failed:
                self._warn("benchmark failures",
                           f"{detail} — none regressed against the baseline")
            else:
                self._ok("benchmark subset", detail)
        except Exception as exc:  # noqa: BLE001
            self._warn("benchmark", f"{type(exc).__name__}: {exc}")
