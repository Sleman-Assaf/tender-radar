"""WebsiteAnalyzer — probe a URL and produce a SiteProfile.

Deterministic detection first: content type, JSON endpoints, RSS, framework
markers, login forms, pagination shape, anti-bot signals. The model is never
asked "what kind of site is this?" — that answer is evidence-based and must be
explainable to the employee reviewing it.

The probe also captures a snapshot (F8) so generation is reproducible.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

from django.conf import settings

from ..interfaces import IWebsiteAnalyzer, SiteProfile
from .snapshot import SnapshotStore, dom_fingerprint

logger = logging.getLogger("scraper_factory")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CHALLENGE_MARKERS = (
    "cf-challenge", "cf_chl_", "just a moment", "checking your browser",
    "attention required", "ddos protection",
)

# Families, mirroring the corpus (docs §1.5).
F_HTML_CUSTOM = "HTML_CUSTOM"
F_JSON_API = "JSON_API"
F_STEALTH = "STEALTH_HTML"
F_PLAYWRIGHT = "PLAYWRIGHT"


class WebsiteAnalyzer(IWebsiteAnalyzer):
    """Probes a candidate website. Read-only; never submits forms."""

    def __init__(self, snapshot_store: Optional[SnapshotStore] = None):
        self.snapshots = snapshot_store or SnapshotStore()
        self.timeout = int(getattr(settings, "SCRAPER_REQUEST_TIMEOUT", 45))
        self.sample_chars = int(
            getattr(settings, "FACTORY_HTML_SAMPLE_CHARS", 14000))

    # ── public API ────────────────────────────────────────────────────────────

    def analyze(self, url: str, *, scraper=None,
                capture_snapshot: bool = True) -> SiteProfile:
        profile = SiteProfile(url=url)
        started = time.monotonic()

        result = self._fetch(url)
        profile.response_time_ms = int((time.monotonic() - started) * 1000)
        profile.reachable = result["ok"]
        profile.status_code = result["status"]
        profile.content_type = result["content_type"]
        profile.evidence["fetched_with"] = result["rung"]

        if not result["ok"]:
            profile.evidence["error"] = result["error"]
            # A 401/403 is itself evidence, not just a failure.
            if result["status"] in (401, 403):
                profile.login_required = result["status"] == 401
                profile.anti_bot = result["status"] == 403
                profile.family = F_STEALTH
            return profile

        body = result["body"]
        profile.anti_bot = self._is_challenged(result["status"], body)

        if "json" in profile.content_type.lower() or body.lstrip()[:1] in "[{":
            self._analyze_json(profile, body)
        elif re.search(r"<rss|<feed", body[:2000], re.I):
            profile.family = "RSS"
            profile.rss_url = url
            profile.evidence["rss"] = "feed served directly at this URL"
        else:
            self._analyze_html(profile, url, body)

        profile.site_signature = self._signature(profile, body)
        profile.listing_sample = self._trim_sample(body, profile)

        if capture_snapshot:
            try:
                kind = ("JSON_PAYLOAD" if profile.family == F_JSON_API
                        else "RSS_FEED" if profile.family == "RSS"
                        else "RAW_HTML")
                snap = self.snapshots.capture(
                    url=url, body=body, kind=kind,
                    status_code=result["status"], headers=result["headers"],
                    cookies=result["cookies"], fetched_with=result["rung"],
                    scraper=scraper)
                profile.evidence["snapshot_id"] = snap.pk
            except Exception as exc:  # noqa: BLE001 — analysis must not fail on this
                logger.warning("[factory] snapshot capture failed: %s", exc)

        return profile

    # ── fetching ──────────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> dict:
        """Plain requests, escalating to a stealth client only if blocked."""
        out = {"ok": False, "status": None, "body": "", "headers": {},
               "cookies": {}, "content_type": "", "rung": "requests",
               "error": ""}
        try:
            import requests
            resp = requests.get(
                url, headers={"User-Agent": _UA,
                              "Accept-Language": "ar,en;q=0.9"},
                timeout=self.timeout, allow_redirects=True)
            out.update(ok=resp.ok, status=resp.status_code, body=resp.text,
                       headers=dict(resp.headers), cookies=dict(resp.cookies),
                       content_type=resp.headers.get("Content-Type", ""))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]

        if out["ok"] and not self._is_challenged(out["status"], out["body"]):
            return out

        # Escalate: the corpus solves these portals with curl_cffi/cloudscraper.
        for rung, fn in (("stealth:curl_cffi", self._curl_cffi),
                         ("stealth:cloudscraper", self._cloudscraper)):
            alt = fn(url)
            if alt and alt["ok"]:
                alt["rung"] = rung
                return alt
        return out

    def _curl_cffi(self, url: str) -> dict | None:
        try:
            from curl_cffi import requests as crq
            resp = crq.get(url, timeout=self.timeout, impersonate="chrome")
            return {"ok": 200 <= resp.status_code < 400,
                    "status": resp.status_code, "body": resp.text,
                    "headers": dict(resp.headers), "cookies": {},
                    "content_type": resp.headers.get("Content-Type", ""),
                    "rung": "stealth:curl_cffi", "error": ""}
        except Exception:  # noqa: BLE001
            return None

    def _cloudscraper(self, url: str) -> dict | None:
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows",
                         "mobile": False})
            resp = s.get(url, timeout=self.timeout)
            return {"ok": resp.ok, "status": resp.status_code, "body": resp.text,
                    "headers": dict(resp.headers), "cookies": dict(resp.cookies),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "rung": "stealth:cloudscraper", "error": ""}
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _is_challenged(status: int | None, body: str) -> bool:
        if status in (403, 429, 503):
            return True
        low = (body or "")[:4000].lower()
        return any(m in low for m in _CHALLENGE_MARKERS)

    # ── analysis ──────────────────────────────────────────────────────────────

    def _analyze_json(self, profile: SiteProfile, body: str) -> None:
        import json
        profile.family = F_JSON_API
        profile.api_endpoints = [profile.url]
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            profile.evidence["json"] = "content-type says JSON but body is not"
            profile.family = F_HTML_CUSTOM
            return

        # Where is the record array? Report candidates for the prompt.
        paths: list[str] = []

        def walk(node, path="$", depth=0):
            if depth > 4 or len(paths) >= 6:
                return
            if isinstance(node, list) and node and isinstance(node[0], dict):
                paths.append(f"{path}[*]")
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}", depth + 1)

        walk(payload)
        profile.evidence["items_path_candidates"] = paths
        if isinstance(payload, dict):
            profile.evidence["top_level_keys"] = list(payload.keys())[:20]
        profile.pagination = self._json_pagination(profile.url, payload)

    @staticmethod
    def _json_pagination(url: str, payload) -> str:
        q = (urlparse(url).query or "").lower()
        if re.search(r"pagenumber|page=|pageindex|p=", q):
            return "QUERY_PARAM"
        if isinstance(payload, dict):
            keys = {k.lower() for k in payload}
            if keys & {"totalcount", "total", "totalrecords", "count"}:
                return "QUERY_PARAM"
            if keys & {"nextcursor", "next", "continuationtoken"}:
                return "CURSOR"
        return "NONE"

    def _analyze_html(self, profile: SiteProfile, url: str, body: str) -> None:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            profile.family = F_HTML_CUSTOM
            return

        soup = BeautifulSoup(body, "html.parser")
        fp = dom_fingerprint(body)
        profile.evidence["dom"] = fp

        # framework
        if soup.find("input", {"name": "__VIEWSTATE"}) or ".aspx" in url.lower():
            profile.framework = "ASPNET"
        elif re.search(r"wp-content|wp-json", body[:8000], re.I):
            profile.framework = "WORDPRESS"
        elif re.search(r'id="root"|id="app"|__NEXT_DATA__|data-reactroot',
                       body[:8000], re.I):
            profile.framework = "REACT"

        # login
        pw = soup.find("input", {"type": "password"})
        if pw is not None:
            profile.login_required = True
            profile.login_url = url
            form = pw.find_parent("form")
            if form is not None and form.get("action"):
                profile.login_url = urljoin(url, form["action"])
            profile.evidence["login"] = "password input present in raw HTML"

        # RSS discovery
        link = soup.find("link", attrs={"type": re.compile(r"rss|atom", re.I)})
        if link is not None and link.get("href"):
            profile.rss_url = urljoin(url, link["href"])
            profile.evidence["rss"] = "feed advertised via <link rel=alternate>"

        # XHR / API hints in inline scripts — the JSON_API shortcut the corpus
        # exploits (saudi_etimad, tenderjo, kenha ...).
        for m in re.finditer(r"""["'](/[^"']*?(?:api|json|ashx|handler|async)"""
                             r"""[^"']*?)["']""", body, re.I):
            candidate = urljoin(url, m.group(1))
            if candidate not in profile.api_endpoints:
                profile.api_endpoints.append(candidate)
            if len(profile.api_endpoints) >= 6:
                break

        # JS dependency: very little text but lots of script, or a known SPA root
        text_len = fp.get("text_length", 0)
        rows = fp.get("rows", 0) + fp.get("cards", 0)
        script_len = sum(len(s.get_text() or "") for s in soup.find_all("script"))
        profile.js_required = bool(
            rows == 0 and (text_len < 800 or script_len > max(text_len, 1) * 3))
        if profile.js_required:
            profile.evidence["js"] = (
                f"no listing rows found; text={text_len} chars, "
                f"script={script_len} chars")

        # pagination
        profile.pagination = self._html_pagination(soup, url)

        # language / country
        html_tag = soup.find("html")
        profile.language = (html_tag.get("lang") if html_tag else "") or ""
        if not profile.language:
            arabic = len(re.findall(r"[؀-ۿ]", body[:20000]))
            profile.language = "ar" if arabic > 200 else "en"
        profile.country_hint = self._country_from_tld(url)

        # family decision — fetch mechanism dominates, exactly as in the corpus
        if profile.anti_bot:
            profile.family = F_STEALTH
        elif profile.js_required:
            profile.family = F_PLAYWRIGHT
        elif profile.api_endpoints and rows == 0:
            profile.family = F_JSON_API
        else:
            profile.family = F_HTML_CUSTOM

    @staticmethod
    def _html_pagination(soup, url: str) -> str:
        q = (urlparse(url).query or "").lower()
        if re.search(r"\bpage\b|\bp\b=|pageindex|pagenumber", q):
            return "QUERY_PARAM"
        for a in soup.find_all("a", href=True)[:400]:
            href = a["href"].lower()
            if re.search(r"[?&](page|p|pageindex|pagenumber)=\d", href):
                return "QUERY_PARAM"
            if re.search(r"/page/\d+", href):
                return "PATH"
        if soup.find(class_=re.compile(r"pagination|pager|paging", re.I)):
            return "QUERY_PARAM"
        return "NONE"

    @staticmethod
    def _country_from_tld(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        tld_map = {
            ".jo": "JO", ".sa": "SA", ".ae": "AE", ".qa": "QA", ".om": "OM",
            ".kw": "KW", ".bh": "BH", ".iq": "IQ", ".sy": "SY", ".eg": "EG",
            ".ma": "MA", ".rw": "RW", ".et": "ET", ".ke": "KE", ".ug": "UG",
        }
        for tld, code in tld_map.items():
            if host.endswith(tld):
                return code
        return ""

    # ── signature & sampling ──────────────────────────────────────────────────

    @staticmethod
    def _signature(profile: SiteProfile, body: str) -> str:
        """Structural fingerprint used as the learning/retrieval key (F14).

        Deliberately excludes the URL so lessons generalise across portals that
        are built the same way.
        """
        dom = profile.evidence.get("dom") or {}
        parts = [
            profile.family, profile.framework, profile.pagination,
            str(profile.js_required), str(profile.login_required),
            ",".join(sorted((dom.get("repeating_classes") or {}).keys())[:12]),
            str(dom.get("tables", 0)), str(dom.get("has_viewstate", False)),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _trim_sample(self, body: str, profile: SiteProfile) -> str:
        """Return the listing region, small enough for the prompt budget.

        Sending a whole 400 KB page wastes the model's context on navigation and
        footers. We keep the densest repeating region instead.
        """
        limit = self.sample_chars
        if len(body) <= limit:
            return body
        if profile.family == F_JSON_API:
            return body[:limit]

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, "html.parser")
            for tag in soup(["script", "style", "svg", "noscript"]):
                tag.decompose()

            # Prefer the largest table, else the container holding the most
            # repeated card-like children.
            best, best_len = None, 0
            for table in soup.find_all("table"):
                html = str(table)
                if len(html) > best_len and len(table.find_all("tr")) >= 2:
                    best, best_len = html, len(html)
            if best is None:
                counts: dict[str, int] = {}
                for el in soup.find_all(class_=True):
                    for cls in el.get("class") or []:
                        counts[cls] = counts.get(cls, 0) + 1
                for cls, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
                    if n < 3:
                        continue
                    els = soup.find_all(class_=cls)
                    parent = els[0].parent if els else None
                    if parent is not None:
                        html = str(parent)
                        if len(html) > best_len:
                            best, best_len = html, len(html)
            if best:
                return best[:limit]
            return soup.decode()[:limit]
        except Exception:  # noqa: BLE001
            return body[:limit]
