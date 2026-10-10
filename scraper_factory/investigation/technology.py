"""Stage 2 — technology detection.

Each technology is claimed only when a marker is actually present, and the
marker is recorded as the evidence. Several markers may match (a Drupal site
rendering a React widget is real), so the stage reports the strongest match as
the conclusion and keeps the rest in `data['all_matches']`.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .contracts import Stage, StageResult, UNKNOWN

#: (technology, weight, [(marker label, compiled pattern)])
#: Weight breaks ties — a __VIEWSTATE field is far more decisive than a
#: generic "wp-content" string that could appear in a copied asset URL.
_SIGNATURES: tuple[tuple[str, int, tuple[tuple[str, str], ...]], ...] = (
    ("ASPNET", 100, (
        ("__VIEWSTATE hidden input", r'name="__VIEWSTATE"'),
        ("__EVENTVALIDATION hidden input", r'name="__EVENTVALIDATION"'),
        ("__VIEWSTATEGENERATOR hidden input", r'name="__VIEWSTATEGENERATOR"'),
        ("__doPostBack script", r'__doPostBack\s*\('),
        (".aspx URL", r'\.aspx'),
        ("ASP.NET session cookie", r'ASP\.NET_SessionId'),
    )),
    ("NEXTJS", 95, (
        ("__NEXT_DATA__ payload", r'id="__NEXT_DATA__"'),
        ("_next static bundle", r'/_next/static/'),
    )),
    ("NUXT", 95, (
        ("__NUXT__ payload", r'window\.__NUXT__'),
        ("_nuxt bundle", r'/_nuxt/'),
    )),
    ("ANGULAR", 90, (
        ("ng-version attribute", r'ng-version="'),
        ("app-root element", r'<app-root'),
        ("Angular runtime bundle", r'runtime\.[0-9a-f]+\.js'),
    )),
    ("REACT", 85, (
        ("data-reactroot attribute", r'data-reactroot'),
        ("ReactDOM render call", r'ReactDOM\.(?:createRoot|render)\s*\('),
        ("React root container", r'<div[^>]+id="root"'),
    )),
    ("VUE", 85, (
        ("Vue app mount", r'new Vue\s*\(|createApp\s*\('),
        ("data-v scoped attribute", r'data-v-[0-9a-f]{8}'),
        ("Vue app container", r'<div[^>]+id="app"'),
    )),
    ("BLAZOR", 90, (
        ("Blazor runtime", r'_framework/blazor\.'),
    )),
    ("DRUPAL", 80, (
        ("drupalSettings object", r'drupalSettings'),
        ("Drupal core assets", r'/sites/(?:default|all)/(?:files|modules)/'),
        ("Drupal generator meta", r'content="Drupal'),
    )),
    ("WORDPRESS", 80, (
        ("wp-content assets", r'/wp-content/'),
        ("wp-json REST root", r'/wp-json/'),
        ("WordPress generator meta", r'content="WordPress'),
    )),
    ("LARAVEL", 70, (
        ("Laravel session cookie", r'laravel_session'),
        ("CSRF token meta", r'name="csrf-token"'),
    )),
    ("DJANGO", 70, (
        ("Django CSRF field", r'name="csrfmiddlewaretoken"'),
        ("Django admin media", r'/static/admin/'),
    )),
    ("RAILS", 70, (
        ("Rails CSRF param", r'name="authenticity_token"'),
    )),
    ("SPRING", 60, (
        ("JSESSIONID cookie", r'JSESSIONID'),
        ("Spring form binding", r'th:|/webjars/'),
    )),
    ("PHP", 50, (
        ("PHP session cookie", r'PHPSESSID'),
        (".php URL", r'\.php(?:\?|"|\')'),
    )),
)

#: Server headers that identify the stack independently of the markup.
_SERVER_HINTS = (
    ("ASPNET", ("asp.net", "microsoft-iis")),
    ("PHP", ("php/",)),
    ("SPRING", ("tomcat", "jetty")),
)


class TechnologyDetector:
    """Identifies the stack from markup, headers and cookies — never by guess."""

    def run(self, url: str, http: StageResult) -> StageResult:
        result = StageResult(stage=Stage.TECHNOLOGY)
        body = http.data.get("body") or ""
        headers = http.data.get("headers") or {}
        cookies = http.data.get("cookies") or []

        if not body:
            result.fail(http.failure or "HTML_EMPTY",
                        "no body captured by the HTTP stage")
            return result

        haystack = body[:200000]
        matches: dict[str, dict] = {}

        for tech, weight, markers in _SIGNATURES:
            hits = []
            for label, pattern in markers:
                if re.search(pattern, haystack, re.I):
                    hits.append(label)
            if hits:
                matches[tech] = {"weight": weight, "markers": hits,
                                 "score": weight + 5 * (len(hits) - 1)}

        # Headers and cookies corroborate independently of the HTML.
        server = (headers.get("server") or "").lower()
        powered = (headers.get("x-powered-by") or "").lower()
        for tech, needles in _SERVER_HINTS:
            if any(n in server or n in powered for n in needles):
                entry = matches.setdefault(
                    tech, {"weight": 60, "markers": [], "score": 60})
                entry["markers"].append(f"Server/X-Powered-By header: {server or powered}")
                entry["score"] += 15
        for cookie in cookies:
            low = cookie.lower()
            if low.startswith("asp.net") or low == "asp.net_sessionid":
                entry = matches.setdefault(
                    "ASPNET", {"weight": 100, "markers": [], "score": 100})
                entry["markers"].append(f"cookie {cookie}")
                entry["score"] += 10

        if not matches:
            result.conclude("STATIC_HTML", 55)
            result.add("no framework markers found in markup, headers or cookies",
                       where="full response")
            result.unknowns.append(
                "server-rendered but framework unidentified — treated as static HTML")
            result.data["all_matches"] = {}
            return result

        best = max(matches.items(), key=lambda kv: kv[1]["score"])
        tech, meta = best
        # Confidence tracks how decisive the markers are, capped so a single
        # weak marker never reads as certainty.
        confidence = min(98, meta["score"])
        if len(meta["markers"]) == 1 and meta["weight"] < 80:
            confidence = min(confidence, 65)

        result.conclude(tech, confidence)
        for marker in meta["markers"][:5]:
            result.add(marker, where="response")

        others = {k: v["markers"] for k, v in matches.items() if k != tech}
        result.data["all_matches"] = {k: v["markers"] for k, v in matches.items()}
        if others:
            result.add(f"also matched: {', '.join(others)}",
                       where="secondary markers",
                       detail="a host stack rendering an embedded SPA is common")

        result.data["is_spa"] = tech in ("REACT", "VUE", "ANGULAR", "NEXTJS",
                                         "NUXT", "BLAZOR")
        result.data["is_aspnet"] = tech == "ASPNET"
        result.data["server"] = server
        result.data["url_extension"] = (urlparse(url).path or "").rsplit(".", 1)[-1][:8]
        return result


class LocaleDetector:
    """Language and country. Cheap, but the prompt and matcher both need it."""

    _TLD_MAP = {
        ".jo": "JO", ".sa": "SA", ".ae": "AE", ".qa": "QA", ".om": "OM",
        ".kw": "KW", ".bh": "BH", ".iq": "IQ", ".sy": "SY", ".eg": "EG",
        ".ma": "MA", ".rw": "RW", ".et": "ET", ".ke": "KE", ".ug": "UG",
        ".tn": "TN", ".dz": "DZ", ".ly": "LY", ".ps": "PS", ".lb": "LB",
    }

    def detect(self, url: str, body: str) -> tuple[str, str]:
        language = ""
        m = re.search(r'<html[^>]+lang="([a-zA-Z-]{2,8})"', body[:4000], re.I)
        if m:
            language = m.group(1).lower().split("-")[0]
        if not language:
            arabic = len(re.findall(r"[؀-ۿ]", body[:20000]))
            language = "ar" if arabic > 200 else "en"

        host = (urlparse(url).hostname or "").lower()
        country = ""
        for tld, code in self._TLD_MAP.items():
            if host.endswith(tld):
                country = code
                break
        return language, country
