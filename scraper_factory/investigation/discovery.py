"""Stages 4 & 5 — rendering analysis and API discovery.

Together these answer the single most important question in the whole
investigation: *where does the tender data actually come from?* Everything the
Planner decides follows from that answer.

Rendering analysis is deliberately conservative. Declaring JAVASCRIPT_RENDERING
is what triggers an expensive Tier 2 browser run, so the stage only says it when
the HTML genuinely lacks a listing. Conversely it never blames selectors — that
verdict is reserved for the diagnostic engine, which can prove the selector ran.

API discovery reads endpoints out of the static assets. Anything it finds is a
*candidate* until probed; a candidate that returns a JSON array of records is
promoted to a proven data source, and that promotion is what lets most JS-heavy
portals skip the browser entirely.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

from django.conf import settings

from .contracts import Failure, Stage, StageResult

logger = logging.getLogger("scraper_factory")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: Endpoint-looking strings inside markup or JS.
_ENDPOINT_PATTERNS = (
    r"""["'](/[^"'\s]*?(?:api|json|ashx|handler|async|service|data|search|
        list|tender|bid|notice|procurement)[^"'\s]*?)["']""",
    r"""["'](https?://[^"'\s]*?/(?:api|json|graphql)[^"'\s]*?)["']""",
    r"""(?:fetch|axios(?:\.\w+)?)\s*\(\s*["']([^"'\s]+)["']""",
    r"""\.open\s*\(\s*["'](?:GET|POST)["']\s*,\s*["']([^"'\s]+)["']""",
    r"""url\s*:\s*["']([^"'\s]+)["']""",
)

#: Embedded JSON blobs that already carry the data.
_EMBEDDED_JSON = (
    ("__NEXT_DATA__", r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>'),
    ("window.__NUXT__", r'window\.__NUXT__\s*=\s*(\{.*?\});?\s*</script>'),
    ("window.__INITIAL_STATE__",
     r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>'),
    ("JSON-LD", r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>'),
    ("application/json script",
     r'<script[^>]+type="application/json"[^>]*>(.*?)</script>'),
)

#: Words that suggest a record array really is procurement data.
_TENDER_KEYS = (
    "tender", "bid", "notice", "procurement", "rfq", "rfp", "solicitation",
    "contract", "award", "deadline", "closing", "reference", "entity",
    # Development-bank portals list procurement as "projects" and
    # "opportunities" (ADB's search API among them, whose corpus scraper
    # treats exactly those records as tenders). Not "title"/"date": every
    # news feed has those, and a false tender-like verdict sends generation
    # after an API that serves articles.
    "project", "opportunity", "publication",
    "مناقص", "عطاء", "منافس", "إعلان",
)


class RenderingAnalyzer:
    """Decides whether the listing is in the HTML or produced by JavaScript."""

    def run(self, url: str, http: StageResult, tech: StageResult) -> StageResult:
        result = StageResult(stage=Stage.RENDERING)
        body = http.data.get("body") or ""
        content_type = (http.data.get("content_type") or "").lower()

        if not body:
            result.fail(http.failure or Failure.EMPTY_RESPONSE, "no body")
            return result

        # A JSON document served directly needs no rendering discussion.
        if "json" in content_type or body.lstrip()[:1] in "[{":
            result.conclude("NOT_HTML", 95)
            result.add("response is a JSON document, not a page",
                       where=f"Content-Type: {content_type or 'sniffed'}")
            result.data["structure"] = {}
            return result

        structure = self._structure(body)
        result.data["structure"] = structure

        # max, not sum: the three counts overlap — one <tr> can also carry a
        # card-ish class — and adding them reports more rows than the page has.
        rows = max(structure["rows"], structure["cards"], structure["list_items"])
        text_len = structure["text_length"]
        script_len = structure["script_length"]

        # Rows in the served HTML settle it. The text-length floor only guards
        # against three-item navigation menus — a dense table of short cells is
        # a perfectly normal listing and must not be pushed to the browser tier.
        if rows >= 3 and text_len >= 150:
            if rows >= 8 and text_len > 400:
                confidence = 92
            elif text_len > 400:
                confidence = 85
            else:
                confidence = 78
            result.conclude("SERVER_RENDERED", confidence)
            result.add(f"{rows} repeating row/card element(s) already in the HTML",
                       where="parsed DOM",
                       detail=f"{structure['tables']} table(s), "
                              f"{structure['cards']} card-like block(s)")
            result.add(f"{text_len} characters of visible text", where="parsed DOM")
            return result

        # Nothing listing-shaped. Decide *why* before blaming JavaScript.
        if text_len < 200 and script_len > 2000:
            result.conclude("JAVASCRIPT_RENDERED", 88)
            result.add("almost no visible text but substantial script payload",
                       where="parsed DOM",
                       detail=f"text={text_len} chars, script={script_len} chars")
            result.data["needs_browser"] = True
            return result

        if tech.data.get("is_spa"):
            result.conclude("JAVASCRIPT_RENDERED", 82)
            result.add(f"{tech.conclusion} app shell with no rows in the served HTML",
                       where="framework markers + parsed DOM")
            result.data["needs_browser"] = True
            return result

        if rows == 0 and text_len >= 400:
            # There is a real page here; it just does not look like a listing.
            result.conclude("SERVER_RENDERED_NO_LISTING", 60)
            result.add("page has content but no repeating row structure",
                       where="parsed DOM",
                       detail=f"{text_len} chars of text, {rows} repeating blocks")
            result.unknowns.append(
                "this URL may be a landing/category page rather than the listing")
            result.data["needs_browser"] = True
            return result

        result.conclude("UNKNOWN", 30)
        result.unknowns.append(
            f"cannot tell where the listing comes from: {rows} rows, "
            f"{text_len} chars text, {script_len} chars script")
        result.data["needs_browser"] = True
        return result

    @staticmethod
    def _structure(body: str) -> dict:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return {"rows": 0, "cards": 0, "list_items": 0, "tables": 0,
                    "text_length": len(re.sub(r"<[^>]+>", " ", body)),
                    "script_length": 0, "repeating_classes": {}}

        soup = BeautifulSoup(body, "html.parser")
        script_length = sum(len(s.get_text() or "") for s in soup.find_all("script"))
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        tables = soup.find_all("table")
        rows = 0
        for table in tables:
            body_rows = [r for r in table.find_all("tr") if r.find_all("td")]
            rows = max(rows, len(body_rows))

        counts: dict[str, int] = {}
        for el in soup.find_all(class_=True):
            for cls in el.get("class") or []:
                counts[cls] = counts.get(cls, 0) + 1
        repeating = {k: v for k, v in counts.items() if v >= 3}

        card_like = 0
        for cls, n in repeating.items():
            if re.search(r"card|item|row|result|tender|notice|listing|post|entry",
                         cls, re.I):
                card_like = max(card_like, n)

        list_items = 0
        for ul in soup.find_all(["ul", "ol"]):
            kids = ul.find_all("li", recursive=False)
            if len(kids) >= 3:
                list_items = max(list_items, len(kids))

        return {
            "rows": rows,
            "cards": card_like,
            "list_items": list_items,
            "tables": len(tables),
            "text_length": len(soup.get_text(" ", strip=True)),
            "script_length": script_length,
            "repeating_classes": dict(sorted(
                repeating.items(), key=lambda kv: -kv[1])[:15]),
        }


class ApiDiscovery:
    """Finds and probes candidate data endpoints without a browser."""

    def __init__(self, timeout: int | None = None, max_probes: int | None = None):
        self.timeout = int(timeout or getattr(settings,
                                              "SCRAPER_REQUEST_TIMEOUT", 45))
        self.max_probes = int(
            max_probes if max_probes is not None
            else getattr(settings, "FACTORY_INVESTIGATION_MAX_PROBES", 6))

    def run(self, url: str, http: StageResult, tech: StageResult) -> StageResult:
        result = StageResult(stage=Stage.API_DISCOVERY)
        body = http.data.get("body") or ""
        content_type = (http.data.get("content_type") or "").lower()

        # The URL the employee pasted may itself be the API.
        if "json" in content_type or body.lstrip()[:1] in "[{":
            payload = self._parse(body)
            if payload is not None:
                shape = self._shape(payload)
                result.conclude("SELF", 95)
                result.add("the requested URL returns JSON directly",
                           where=f"Content-Type: {content_type or 'sniffed'}",
                           detail=f"record path {shape['items_path'] or 'unclear'}")
                result.data.update({"endpoints": [{
                    "url": url, "method": "GET", "status": http.data.get("status"),
                    "kind": "JSON", "records": shape["record_count"],
                    "items_path": shape["items_path"],
                    "fields": shape["fields"], "tender_like": shape["tender_like"],
                    "proven": True}],
                    "proven_endpoint": url, "graphql": False})
                return result

        candidates = self._candidates(url, body)
        result.data["candidate_count"] = len(candidates)

        graphql = [c for c in candidates if "graphql" in c.lower()]
        if graphql:
            result.data["graphql"] = True
            result.add(f"GraphQL endpoint referenced: {graphql[0]}",
                       where="static assets")

        if not candidates:
            result.conclude("NONE_FOUND", 55)
            result.add("no API-shaped URLs referenced in the markup or inline JS",
                       where="static analysis")
            result.unknowns.append(
                "endpoints built at runtime by bundled JS would not be visible here")
            result.data["endpoints"] = []
            return result

        probed = []
        proven = None
        for candidate in candidates[:self.max_probes]:
            info = self._probe(candidate)
            probed.append(info)
            if info.get("tender_like") and info.get("records", 0) >= 3:
                proven = info
                break

        result.data["endpoints"] = probed

        if proven is not None:
            result.conclude("PROVEN", 92)
            result.add(f"{proven['url']} returned {proven['records']} record(s)",
                       where=f"HTTP {proven['status']}",
                       detail=f"fields: {', '.join(proven['fields'][:8])}")
            result.data["proven_endpoint"] = proven["url"]
            return result

        reachable = [p for p in probed if p.get("status") and p["status"] < 400]
        if reachable:
            result.conclude("CANDIDATES_UNCONFIRMED", 45)
            result.add(f"{len(reachable)} endpoint(s) responded but none returned "
                       f"anything recognisable as tender records",
                       where="probe results")
            result.unknowns.append(
                "endpoints may need parameters, headers or a session we do not have")
            return result

        result.conclude("NONE_REACHABLE", 40)
        result.add(f"{len(probed)} candidate(s) probed, none returned usable data",
                   where="probe results")
        result.unknowns.append("candidate endpoints may require POST or auth")
        return result

    # ── candidate extraction ──────────────────────────────────────────────────

    def _candidates(self, url: str, body: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()

        def add(raw: str) -> None:
            raw = (raw or "").strip()
            if not raw or raw.startswith(("#", "data:", "javascript:", "mailto:")):
                return
            if re.search(r"\.(?:png|jpe?g|gif|svg|css|woff2?|ttf|ico|webp|mp4)$",
                         raw, re.I):
                return
            absolute = urljoin(url, raw)
            if not absolute.lower().startswith(("http://", "https://")):
                return
            # Stay on the portal; third-party analytics endpoints are noise.
            if urlparse(absolute).hostname != urlparse(url).hostname:
                return
            if absolute in seen:
                return
            seen.add(absolute)
            found.append(absolute)

        for pattern in _ENDPOINT_PATTERNS:
            for m in re.finditer(pattern, body, re.I | re.X):
                add(m.group(1))
                if len(found) >= 40:
                    break

        # Rank: explicit API paths and tender words first.
        def rank(candidate: str) -> tuple:
            low = candidate.lower()
            return (
                0 if re.search(r"/api/|/graphql|\.json|/odata/", low) else 1,
                0 if any(w in low for w in
                         ("tender", "bid", "notice", "procure", "search", "list")) else 1,
                len(candidate),
            )

        found.sort(key=rank)
        return found

    def _probe(self, endpoint: str) -> dict:
        """GET a candidate and describe what came back. Read-only, never POST."""
        info = {"url": endpoint, "method": "GET", "status": None, "kind": "",
                "records": 0, "items_path": "", "fields": [],
                "tender_like": False, "proven": False, "error": ""}
        try:
            import requests
            resp = requests.get(
                endpoint,
                headers={"User-Agent": _UA, "Accept": "application/json, */*",
                         "X-Requested-With": "XMLHttpRequest"},
                timeout=min(self.timeout, 20), allow_redirects=True)
            info["status"] = resp.status_code
            text = resp.text or ""
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "json" not in ctype and text.lstrip()[:1] not in "[{":
                info["kind"] = "NOT_JSON"
                return info
            payload = self._parse(text)
            if payload is None:
                info["kind"] = "INVALID_JSON"
                return info
            shape = self._shape(payload)
            info.update(kind="JSON", records=shape["record_count"],
                        items_path=shape["items_path"], fields=shape["fields"],
                        tender_like=shape["tender_like"],
                        proven=shape["tender_like"] and shape["record_count"] >= 3)
        except Exception as exc:  # noqa: BLE001 — a failed probe is data, not an error
            info["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return info

    # ── payload shape ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse(text: str):
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _shape(payload) -> dict:
        """Locate the record array and judge whether it looks like tenders."""
        best = {"items_path": "", "record_count": 0, "fields": [],
                "tender_like": False}

        def consider(node, path: str) -> None:
            if not (isinstance(node, list) and node
                    and isinstance(node[0], dict)):
                return
            fields = sorted({k for row in node[:5] if isinstance(row, dict)
                             for k in row})
            joined = " ".join(fields).lower()
            tender_like = any(w in joined for w in _TENDER_KEYS)
            # A date-bearing array outranks a dateless one. The tenderjo API
            # serves a categories array AND a tenders array, both tender-like in
            # vocabulary; the categories won on size, the generated scraper
            # followed the evidence, and 25 category labels sailed to a false
            # PASS. Category/navigation arrays never carry dates; real listings
            # almost always do — so date-ness ranks above record count.
            dated = any(w in joined for w in ("date", "deadline", "close",
                                              "publish", "expiry", "تاريخ"))
            rank = (tender_like, dated, len(node))
            best_rank = (best["tender_like"], best.get("dated", False),
                         best["record_count"])
            if rank > best_rank:
                best.update(items_path=path, record_count=len(node),
                            fields=fields[:25], tender_like=tender_like,
                            dated=dated)

        def walk(node, path="$", depth=0) -> None:
            if depth > 5:
                return
            if isinstance(node, list):
                consider(node, f"{path}[*]")
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}", depth + 1)

        walk(payload)
        return best


class EmbeddedJsonFinder:
    """Extracts state blobs the page ships inline (Next.js, Nuxt, JSON-LD)."""

    def find(self, body: str) -> list[dict]:
        out: list[dict] = []
        for label, pattern in _EMBEDDED_JSON:
            for m in re.finditer(pattern, body, re.I | re.S):
                raw = (m.group(1) or "").strip()
                if len(raw) < 40:
                    continue
                try:
                    payload = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                shape = ApiDiscovery._shape(payload)
                out.append({
                    "source": label,
                    "bytes": len(raw),
                    "items_path": shape["items_path"],
                    "records": shape["record_count"],
                    "fields": shape["fields"],
                    "tender_like": shape["tender_like"],
                })
                if len(out) >= 6:
                    return out
        return out
