"""Tier 2 — browser-assisted investigation.

Only runs when Tier 1 could not prove where the tender data comes from. A
browser costs seconds and memory per site, so the escalation rule is strict and
lives in `engine.py`; this module just does the work when asked.

What the browser adds that static analysis cannot:
  * the DOM *after* hydration, so rows built by JavaScript become visible
  * the network log, so the XHR/fetch that fetched the rows is captured with
    its real headers and parameters — this is what turns a JS portal into a
    plain API scraper and removes the browser from the generated code
  * scroll and load-more probing, to prove whether more pages exist

Absent Playwright the module degrades honestly: it reports that the browser was
unavailable and leaves the unknowns in place rather than inventing a conclusion.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

from django.conf import settings

from .contracts import Evidence, Stage, StageResult

logger = logging.getLogger("scraper_factory")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_ASSET_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|css|js|woff2?|ttf|ico|webp|mp4|map)(?:\?|$)", re.I)


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed, or no browser binary is present."""


class Tier2Investigator:
    """Renders the page and records what the browser actually did."""

    def __init__(self, timeout_ms: int | None = None):
        self.timeout_ms = int(
            timeout_ms or getattr(settings, "FACTORY_TIER2_TIMEOUT_MS", 45000))
        self.settle_ms = int(getattr(settings, "FACTORY_TIER2_SETTLE_MS", 2500))
        self.max_scrolls = int(getattr(settings, "FACTORY_TIER2_MAX_SCROLLS", 3))

    # ── availability ──────────────────────────────────────────────────────────

    @staticmethod
    def available() -> tuple[bool, str]:
        """(usable, detail) — import check only.

        Deliberately does not start the driver: spinning one up merely to read
        the executable path leaves a cancelled task behind and prints a
        TargetClosedError on teardown. A missing browser binary is detected
        where it actually matters, at launch, and reported with the fix.
        """
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, ("playwright is not installed — "
                           "pip install playwright && playwright install chromium")
        return True, "playwright is importable"

    # ── main entry ────────────────────────────────────────────────────────────

    def run(self, url: str) -> dict:
        """Render `url` and return observations. Raises BrowserUnavailable.

        Bounded by the 'browser' resource slot: a chromium instance costs far
        more than an HTTP probe, and the scheduler can ask for several
        investigations at once. Exhaustion is surfaced as BrowserUnavailable
        because that is the signal Tier 2 callers already degrade on — the
        investigation continues on Tier 1 evidence instead of failing outright.
        """
        ok, detail = self.available()
        if not ok:
            raise BrowserUnavailable(detail)

        from ..maintenance import ResourceBusy, limited

        try:
            with limited("browser"):
                return self._run_browser(url)
        except ResourceBusy as exc:
            raise BrowserUnavailable(
                f"no browser slot available: {exc} Concurrency is capped by "
                f"FACTORY_MAX_CONCURRENT_BROWSER.") from exc

    @staticmethod
    def _proxy_for(url: str) -> dict | None:
        """The proxy chromium needs for this host, from the environment.

        The HTTP rungs reach external portals through the corporate proxy
        because `requests` honours HTTP_PROXY; chromium does not, so Tier 2
        launched proxy-less and died with ERR_NAME_NOT_RESOLVED on the same
        hosts Tier 1 had just probed. Same rules as the rest of the machine:
        env proxy for external hosts, direct for anything in NO_PROXY.
        """
        import os
        from urllib.parse import urlparse

        proxy = (os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
                 or "").strip()
        if not proxy:
            return None
        host = (urlparse(url).hostname or "").lower()
        no_proxy = (os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
                    or "")
        for entry in (e.strip().lower() for e in no_proxy.split(",") if e.strip()):
            if host == entry or (entry.startswith(".") and host.endswith(entry)):
                return None
        return {"server": proxy}

    def _run_browser(self, url: str) -> dict:
        from playwright.sync_api import sync_playwright

        observations: dict = {
            "rendered_html": "", "requests": [], "xhr": [], "console_errors": [],
            "row_growth": [], "final_url": url, "status": None,
            "load_more_clicked": False, "scrolled": 0, "duration_ms": 0,
        }
        started = time.monotonic()
        host = (urlparse(url).hostname or "").lower()

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=True, proxy=self._proxy_for(url),
                    args=["--disable-dev-shm-usage", "--no-sandbox"])
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if "executable doesn" in message.lower() or "install" in message.lower():
                    raise BrowserUnavailable(
                        "chromium is not installed — run: playwright install "
                        "chromium") from exc
                raise BrowserUnavailable(
                    f"chromium failed to launch: {message[:200]}") from exc
            try:
                context = browser.new_context(
                    user_agent=_UA, locale="en-US",
                    viewport={"width": 1440, "height": 900},
                    ignore_https_errors=True)
                page = context.new_page()

                def on_response(response) -> None:
                    try:
                        req = response.request
                        if _ASSET_RE.search(req.url):
                            return
                        entry = {
                            "url": req.url[:500],
                            "method": req.method,
                            "status": response.status,
                            "resource_type": req.resource_type,
                            "content_type": (
                                response.headers.get("content-type") or "")[:80],
                            "same_host": (urlparse(req.url).hostname or "").lower() == host,
                        }
                        observations["requests"].append(entry)
                        if req.resource_type in ("xhr", "fetch"):
                            body = self._json_body(response)
                            if body is not None:
                                entry = dict(entry)
                                entry["post_data"] = (req.post_data or "")[:500]
                                entry["headers"] = {
                                    k: v for k, v in (req.headers or {}).items()
                                    if k.lower() in (
                                        "accept", "content-type", "x-requested-with",
                                        "authorization", "referer")}
                                entry.update(body)
                                observations["xhr"].append(entry)
                    except Exception:  # noqa: BLE001 — never break the crawl
                        pass

                page.on("response", on_response)
                page.on("console", lambda m: (
                    observations["console_errors"].append(m.text[:200])
                    if m.type == "error" else None))

                response = page.goto(url, timeout=self.timeout_ms,
                                     wait_until="domcontentloaded")
                observations["status"] = getattr(response, "status", None)
                self._settle(page)

                observations["row_growth"].append(self._row_count(page))
                observations["rendered_html"] = page.content()
                observations["final_url"] = page.url

                self._probe_more(page, observations)

                observations["rendered_html"] = page.content()
            finally:
                browser.close()

        observations["duration_ms"] = int((time.monotonic() - started) * 1000)
        return observations

    # ── page interaction ──────────────────────────────────────────────────────

    def _settle(self, page) -> None:
        """Wait for the network to go quiet, but never hang on a chatty page."""
        try:
            page.wait_for_load_state("networkidle", timeout=self.settle_ms * 2)
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(self.settle_ms)

    def _probe_more(self, page, observations: dict) -> None:
        """Scroll and click load-more to see whether the row count grows."""
        before = observations["row_growth"][0] if observations["row_growth"] else 0

        for i in range(self.max_scrolls):
            try:
                page.mouse.wheel(0, 20000)
                page.wait_for_timeout(1200)
            except Exception:  # noqa: BLE001
                break
            count = self._row_count(page)
            observations["row_growth"].append(count)
            observations["scrolled"] = i + 1
            if count <= before:
                break
            before = count

        for label in ("load more", "show more", "view more", "المزيد"):
            try:
                button = page.get_by_text(re.compile(label, re.I)).first
                if button and button.is_visible(timeout=800):
                    button.click(timeout=2500)
                    page.wait_for_timeout(1500)
                    observations["load_more_clicked"] = True
                    observations["row_growth"].append(self._row_count(page))
                    break
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _row_count(page) -> int:
        """Cheap structural row count, evaluated inside the page."""
        try:
            return int(page.evaluate(
                """() => {
                    let best = 0;
                    document.querySelectorAll('table').forEach(t => {
                        const n = t.querySelectorAll('tr td').length
                            ? t.querySelectorAll('tr').length : 0;
                        if (n > best) best = n;
                    });
                    const counts = {};
                    document.querySelectorAll('[class]').forEach(el => {
                        el.classList.forEach(c => {
                            counts[c] = (counts[c] || 0) + 1;
                        });
                    });
                    Object.entries(counts).forEach(([c, n]) => {
                        if (n >= 3 && /card|item|row|result|tender|notice|listing/i.test(c)
                            && n > best) best = n;
                    });
                    return best;
                }"""))
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _json_body(response) -> dict | None:
        """Describe an XHR payload if it carries a record array."""
        ctype = (response.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            return None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        from .discovery import ApiDiscovery
        shape = ApiDiscovery._shape(payload)
        return {
            "kind": "JSON",
            "records": shape["record_count"],
            "items_path": shape["items_path"],
            "fields": shape["fields"],
            "tender_like": shape["tender_like"],
        }


def apply_tier2(report, observations: dict) -> None:
    """Fold browser observations back into the Tier 1 stage results.

    Tier 2 corrects Tier 1 rather than replacing it: the earlier conclusions
    stay in the report with their evidence so the escalation itself is auditable.
    """
    from .discovery import RenderingAnalyzer
    from .rows import CandidateRowDetector

    rendered = observations.get("rendered_html") or ""
    # Same-host preferred, but not required: modern portals routinely serve
    # their listing data from a search-vendor host (SearchStax, Algolia,
    # solr-as-a-service) — the ADB corpus scraper intercepts exactly such a
    # call. Requiring same_host made Tier 2 blind to the very request it was
    # sent to capture, so a tender-like response wins regardless of host, with
    # same-host ones ranked first when both exist.
    xhr = [x for x in observations.get("xhr", []) if x.get("same_host")]
    any_host_data = [x for x in observations.get("xhr", [])
                     if x.get("tender_like") and x.get("records", 0) >= 3]
    data_xhr = ([x for x in any_host_data if x.get("same_host")]
                or any_host_data)

    # ── API discovery: a captured XHR is proof, not a candidate ──────────────
    api = report.stage(Stage.API_DISCOVERY)
    if data_xhr:
        best = max(data_xhr, key=lambda x: x.get("records", 0))
        api.conclude("PROVEN", 95)
        api.evidence.append(Evidence(
            what=f"browser captured {best['method']} {best['url']}",
            where="network log",
            detail=f"{best['records']} record(s) at {best['items_path'] or 'root'}"))
        api.data["proven_endpoint"] = best["url"]
        api.data["proven_via"] = "tier2_network"
        api.data["request_headers"] = best.get("headers", {})
        api.data["post_data"] = best.get("post_data", "")
        api.data["endpoints"] = (api.data.get("endpoints") or []) + data_xhr
        api.unknowns = [u for u in api.unknowns if "runtime" not in u]
        report.record(api)

        # The DOM no longer matters: the records come from a request we can
        # reproduce. Leaving "rows require Tier 2 rendering" behind would read
        # as an outstanding question that Tier 2 has in fact answered.
        rows_stage = report.stage(Stage.CANDIDATE_ROWS)
        if rows_stage.conclusion in ("NOT_IN_HTML", "NONE"):
            rows_stage.conclude("API_RECORDS", 90)
            rows_stage.evidence.append(Evidence(
                what=f"{best['records']} record(s) from the captured endpoint",
                where=best.get("items_path") or "response root",
                detail="the rendered DOM is irrelevant once the request is known"))
            rows_stage.unknowns = []
            rows_stage.data["record_count"] = best.get("records", 0)
            rows_stage.data["fields"] = best.get("fields", [])
            report.record(rows_stage)
    elif xhr:
        api.data["endpoints"] = (api.data.get("endpoints") or []) + xhr
        api.evidence.append(Evidence(
            what=f"browser saw {len(xhr)} same-host XHR/fetch call(s), "
                 f"none returning tender-like records",
            where="network log"))
        report.record(api)

    # ── rendering: judge the hydrated DOM ────────────────────────────────────
    rendering = report.stage(Stage.RENDERING)
    if rendered:
        structure = RenderingAnalyzer._structure(rendered)
        rendering.data["rendered_structure"] = structure
        rows = max(structure["rows"], structure["cards"], structure["list_items"])
        if rows >= 3:
            rendering.conclude("JAVASCRIPT_RENDERED", 95)
            rendering.evidence.append(Evidence(
                what=f"{rows} row(s) appeared only after JavaScript ran",
                where="rendered DOM",
                detail="the served HTML had none"))
            rendering.data["needs_browser"] = not bool(data_xhr)
            rendering.unknowns = []
        else:
            rendering.evidence.append(Evidence(
                what="no listing rows even after rendering",
                where="rendered DOM",
                detail=f"{structure['text_length']} chars of text"))
            rendering.unknowns.append(
                "URL may not be the listing page, or content needs interaction")
        report.record(rendering)

    # ── pagination: growth under scroll is decisive ──────────────────────────
    growth = observations.get("row_growth") or []
    pagination = report.stage(Stage.PAGINATION)
    if len(growth) > 1 and growth[-1] > growth[0]:
        style = "LOAD_MORE" if observations.get("load_more_clicked") else "INFINITE_SCROLL"
        pagination.conclude(style, 90)
        pagination.evidence.append(Evidence(
            what=f"row count grew {growth[0]} → {growth[-1]}",
            where="browser interaction",
            detail=(f"after clicking load-more" if style == "LOAD_MORE"
                    else f"after {observations.get('scrolled', 0)} scroll(s)")))
        pagination.data["style"] = style.lower()
        pagination.data["row_growth"] = growth
        pagination.unknowns = []
        report.record(pagination)
    elif len(growth) > 1 and growth[-1] == growth[0] and growth[0] > 0:
        if pagination.conclusion in ("LOAD_MORE", "INFINITE_SCROLL", "UNKNOWN"):
            pagination.conclude("NONE", 75)
            pagination.evidence.append(Evidence(
                what=f"row count stayed at {growth[0]} through "
                     f"{observations.get('scrolled', 0)} scroll(s)",
                where="browser interaction",
                detail="single page of results"))
            pagination.data["row_growth"] = growth
            report.record(pagination)

    # ── candidate rows from the hydrated DOM ─────────────────────────────────
    if rendered and not data_xhr:
        http_like = StageResult(stage=Stage.HTTP)
        http_like.data["body"] = rendered
        detector = CandidateRowDetector()
        fresh = detector.run(report.url, http_like, rendering, api)
        if fresh.conclusion == "FOUND":
            fresh.evidence.insert(0, Evidence(
                what="rows located in the rendered DOM, not the served HTML",
                where="Tier 2"))
            report.record(fresh)
