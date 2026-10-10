"""Gate E — sandboxed dry execution of a candidate scraper.

Generated code is executed, so it runs in a SEPARATE process with:

  * a hard timeout (a hung portal must not hang the web app)
  * `save_tender_data` monkey-blocked, so even misbehaving code cannot persist
  * no installation: the candidate is imported from a temporary module inside
    tenders/scrapers/ (so its `from .base import ...` resolves exactly as it
    will after install) and removed again in a finally block

The child prints one JSON line, which the parent parses. Communicating over
stdout keeps the parent immune to anything the child does to its own state.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid

from django.conf import settings

from ..interfaces import ISandbox, TestOutcome

logger = logging.getLogger("scraper_factory")

# Runs inside the child process.
CHILD_SCRIPT = r'''
import json, os, sys, time, traceback

def main():
    module_name = os.environ["FACTORY_TEST_MODULE"]
    class_name = os.environ["FACTORY_TEST_CLASS"]
    real_class = os.environ.get("FACTORY_REAL_CLASS") or class_name
    country_code = os.environ.get("FACTORY_TEST_COUNTRY") or ""
    list_url = os.environ.get("FACTORY_TEST_URL") or ""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tender_radar.settings")

    import django
    django.setup()

    # Block persistence before the candidate can reach it.
    import tenders.services as services

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "save_tender_data() is blocked during sandbox testing")

    services.save_tender_data = _blocked

    from django.db import transaction
    from importlib import import_module

    results = []
    runtime_ms = 0

    # The house pattern looks its own ScraperSource up by scraper_class and
    # returns [] when there is none. A candidate is not installed yet, so we
    # provision a temporary source inside a transaction and ALWAYS roll it back:
    # without this every test would report zero tenders for the wrong reason.
    with transaction.atomic():
        from tenders.models import CountryConfig, ScraperSource, TenderingURL

        country = None
        if country_code:
            country = CountryConfig.objects.filter(code=country_code).first()
        if country is None:
            country = CountryConfig.objects.filter(is_active=True).first()

        source = None
        if country is not None:
            source = ScraperSource.objects.create(
                name=f"[sandbox] {real_class}", country=country,
                base_url=(list_url or "http://sandbox.invalid")[:500],
                scraper_class=real_class, is_active=True,
                description="Temporary sandbox source — rolled back after the test.",
            )
            if list_url:
                TenderingURL.objects.create(
                    source=source, label="sandbox listing", url=list_url[:1000],
                    max_pages=2, is_active=True)

        started = time.monotonic()
        module = import_module(f"tenders.scrapers.{module_name}")
        cls = getattr(module, class_name, None)
        if cls is None:
            # The generated class name may differ from the temp module name.
            candidates = [v for k, v in vars(module).items()
                          if k.endswith("Scraper") and isinstance(v, type)]
            if not candidates:
                raise RuntimeError(
                    f"No scraper class found in module {module_name}")
            cls = candidates[0]

        scraper = cls()
        if source is not None:
            scraper.source = source

        try:
            results = scraper.scrape() or []
        finally:
            runtime_ms = int((time.monotonic() - started) * 1000)
            # Nothing the sandbox created may survive.
            transaction.set_rollback(True)

    rows = []
    for td in results[:25]:
        rows.append({
            "title": str(getattr(td, "title", ""))[:300],
            "source_url": str(getattr(td, "source_url", ""))[:500],
            "external_id": str(getattr(td, "external_id", ""))[:120],
            "reference_no": str(getattr(td, "reference_no", ""))[:100],
            "entity": str(getattr(td, "entity", ""))[:200],
            "status": str(getattr(td, "status", "")),
            "submission_deadline": str(getattr(td, "submission_deadline", "") or ""),
            "publication_date": str(getattr(td, "publication_date", "") or ""),
        })

    print("__FACTORY_RESULT__" + json.dumps({
        "ok": True, "count": len(results), "runtime_ms": runtime_ms,
        "rows": rows,
    }))

try:
    main()
except Exception as exc:
    print("__FACTORY_RESULT__" + json.dumps({
        "ok": False, "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc()[-3000:],
    }))
'''


class SubprocessSandbox(ISandbox):
    """Executes a candidate scraper in an isolated child process."""

    def __init__(self):
        self.scrapers_dir = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR"))
        self.project_root = self.scrapers_dir.parent.parent
        self.default_timeout = int(getattr(settings, "FACTORY_SANDBOX_TIMEOUT", 180))

    def run(self, source_code: str, *, module_name: str, class_name: str,
            timeout_s: int | None = None, country_code: str = "",
            list_url: str = "") -> TestOutcome:
        """Execute a candidate. `country_code`/`list_url` provision the temporary
        ScraperSource the house pattern expects (rolled back afterwards)."""
        timeout_s = timeout_s or self.default_timeout
        outcome = TestOutcome()

        # Temporary module name so a failed test never collides with a real file.
        temp_module = f"_factory_tmp_{uuid.uuid4().hex[:8]}"
        temp_path = self.scrapers_dir / f"{temp_module}.py"
        runner_path = self.project_root / f"_factory_runner_{temp_module}.py"

        # Everything below is wrapped so a crash can NEVER leave the temporary
        # module behind: a leftover file in tenders/scrapers/ would be picked up
        # as part of the corpus and corrupt the coding standard itself.
        try:
            return self._run_guarded(source_code, temp_module, temp_path,
                                     runner_path, class_name, country_code,
                                     list_url, timeout_s, outcome)
        except Exception as exc:  # noqa: BLE001
            logger.error("[factory] sandbox failed: %s", exc, exc_info=True)
            outcome.error_category = "ENGINE"
            outcome.error_message = f"Sandbox error: {type(exc).__name__}: {exc}"
            return outcome
        finally:
            self._cleanup(temp_path, runner_path)

    def _run_guarded(self, source_code: str, temp_module: str,
                     temp_path: pathlib.Path, runner_path: pathlib.Path,
                     class_name: str, country_code: str, list_url: str,
                     timeout_s: int, outcome: TestOutcome) -> TestOutcome:
        try:
            temp_path.write_text(source_code, encoding="utf-8")
            runner_path.write_text(CHILD_SCRIPT, encoding="utf-8")
        except OSError as exc:
            outcome.error_category = "ENGINE"
            outcome.error_message = f"Could not stage candidate for testing: {exc}"
            return outcome

        env = dict(os.environ)
        env["FACTORY_TEST_MODULE"] = temp_module
        env["FACTORY_TEST_CLASS"] = class_name
        env["FACTORY_REAL_CLASS"] = class_name
        env["FACTORY_TEST_COUNTRY"] = country_code or ""
        env["FACTORY_TEST_URL"] = list_url or ""
        env["DJANGO_SETTINGS_MODULE"] = "tender_radar.settings"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(self.project_root)
        # Loopback targets must not be sent to the corporate proxy — that is how
        # a local fixture portal is tested. NO_PROXY alone would not work here
        # because BaseScraper sets session.trust_env = False whenever
        # SCRAPER_PROXY is configured, so we clear the setting for this child
        # only. External hosts are unaffected and still use the proxy.
        if re.search(r"//(127\.0\.0\.1|localhost|\[::1\])", list_url or ""):
            env["SCRAPER_PROXY"] = ""
            env["NO_PROXY"] = "127.0.0.1,localhost,::1"
            env["no_proxy"] = env["NO_PROXY"]

        from ..maintenance import ResourceBusy, limited

        started = time.monotonic()
        try:
            # The slot covers the child process only. Holding it across staging
            # and cleanup would mean a slow disk shrank the concurrency budget,
            # and cleanup must never be blocked waiting for a slot — a leftover
            # module in tenders/scrapers/ would corrupt the corpus itself.
            with limited("sandbox"):
                proc = subprocess.run(
                    [sys.executable, str(runner_path)],
                    cwd=str(self.project_root), env=env, timeout=timeout_s,
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace",
                )
            stdout, stderr = proc.stdout or "", proc.stderr or ""
        except ResourceBusy as exc:
            # Not a defect in the candidate: the machine is saturated. Reported
            # under its own category so diagnosis attributes it to the harness
            # (SANDBOX_EXECUTION, repairable=False) instead of rewriting code
            # that was never executed.
            outcome.error_category = "RESOURCE"
            outcome.error_message = (
                f"The candidate was not executed: {exc} Concurrency is capped by "
                f"FACTORY_MAX_CONCURRENT_SANDBOX; retry when a slot frees up.")
            outcome.runtime_ms = int((time.monotonic() - started) * 1000)
            return outcome
        except subprocess.TimeoutExpired:
            outcome.error_category = "TIMEOUT"
            outcome.error_message = (
                f"The scraper did not finish within {timeout_s}s. The portal may "
                "be very slow, or the pagination loop may not terminate.")
            outcome.runtime_ms = int((time.monotonic() - started) * 1000)
            return outcome
        except Exception as exc:  # noqa: BLE001
            outcome.error_category = "ENGINE"
            outcome.error_message = f"Sandbox failed to start: {exc}"
            return outcome
        # Cleanup is handled by the caller's finally block, which also covers
        # failures that happen before the subprocess is even started.

        outcome.stdout_tail = stdout[-4000:]
        outcome.stderr_tail = stderr[-4000:]
        outcome.runtime_ms = int((time.monotonic() - started) * 1000)

        payload = self._extract_payload(stdout)
        if payload is None:
            outcome.error_category = "ENGINE"
            outcome.error_message = (
                "The sandbox produced no result. Child stderr: "
                + (stderr[-600:] or "(empty)"))
            return outcome

        if not payload.get("ok"):
            outcome.error_message = payload.get("error", "unknown error")
            outcome.error_category = self._categorize(outcome.error_message)
            outcome.stderr_tail = (payload.get("traceback") or
                                   outcome.stderr_tail)[-4000:]
            return outcome

        outcome.tenders_found = int(payload.get("count", 0))
        outcome.sample_rows = payload.get("rows") or []
        outcome.runtime_ms = int(payload.get("runtime_ms", outcome.runtime_ms))
        outcome.ok = outcome.tenders_found > 0
        if not outcome.ok:
            # House scrapers swallow fetch failures (print + logger + return []),
            # so "0 rows" alone does not mean the selectors are wrong. Read the
            # console dialect the corpus prints to report the true cause.
            category, message = self._diagnose_empty(stdout)
            outcome.error_category = category
            outcome.error_message = message
        return outcome

    @staticmethod
    def _diagnose_empty(stdout: str) -> tuple[str, str]:
        """Distinguish a fetch failure from a genuine selector miss."""
        reported = re.findall(r"^\s*X\s+\[[^\]]+\]\s*(.+)$", stdout or "",
                              re.MULTILINE)
        if reported:
            detail = reported[-1].strip()
            low = detail.lower()
            if any(k in low for k in ("operation not permitted", "proxy",
                                      "connection", "timed out", "timeout",
                                      "resolve", "unreachable", "refused")):
                return "NETWORK", (
                    "The scraper could not fetch the page, so it returned no "
                    f"tenders. It reported: {detail}. Check network/proxy access "
                    "from the machine running the factory.")
            if any(k in low for k in ("403", "401", "blocked", "forbidden")):
                return "BLOCKED", (
                    f"The portal refused the request: {detail}. This site likely "
                    "needs the stealth fetch ladder (curl_cffi/cloudscraper).")
            if "no active scrapersource" in low:
                return "CONTRACT", (
                    "The scraper could not find its ScraperSource row. The "
                    "sandbox provisions one automatically, so this usually means "
                    "the scraper_class string inside the file does not match the "
                    f"class name. It reported: {detail}")
            if "not installed" in low:
                return "CONFIG", f"A required library is missing: {detail}"
            return "ENGINE", f"The scraper reported a failure: {detail}"

        return "SELECTOR", (
            "The scraper ran and fetched the page without error but extracted 0 "
            "tenders, so its selectors do not match the live markup.")

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_payload(stdout: str):
        marker = "__FACTORY_RESULT__"
        idx = stdout.rfind(marker)
        if idx < 0:
            return None
        try:
            return json.loads(stdout[idx + len(marker):].strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _categorize(message: str) -> str:
        low = (message or "").lower()
        if "save_tender_data" in low:
            return "CONTRACT"
        if "timeout" in low or "timed out" in low:
            return "TIMEOUT"
        if "connection" in low or "dns" in low or "resolve" in low:
            return "OFFLINE"
        if "403" in low or "blocked" in low or "challenge" in low:
            return "BLOCKED"
        if "json" in low or "expecting value" in low:
            return "PARSE"
        if any(k in low for k in ("attributeerror", "typeerror", "keyerror",
                                 "indexerror", "nonetype")):
            return "PARSE"
        return "ENGINE"

    @staticmethod
    def _cleanup(*paths: pathlib.Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        # Remove any stale bytecode for the temporary module.
        for path in paths:
            cache = path.parent / "__pycache__"
            if not cache.exists():
                continue
            for pyc in cache.glob(f"{path.stem}*.pyc"):
                try:
                    pyc.unlink(missing_ok=True)
                except OSError:
                    pass
