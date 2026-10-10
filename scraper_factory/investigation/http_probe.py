"""Stage 1 — HTTP investigation.

Establishes whether the site can be reached at all and how. Every later stage
reads the body this stage captured, so a failure here is always the root cause
and the pipeline must not continue past it.

Fetch escalates through the same rungs the corpus already uses in production
(requests → curl_cffi → cloudscraper). That is deliberate: if a hand-written
scraper needs curl_cffi for a portal, the generated one will too, and the
Planner needs to know which rung actually worked.
"""

from __future__ import annotations

import logging
import re
import socket
import time
from urllib.parse import urlparse

from django.conf import settings

from .contracts import Failure, Stage, StageResult

logger = logging.getLogger("scraper_factory")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CHALLENGE_MARKERS = (
    "cf-challenge", "cf_chl_", "just a moment", "checking your browser",
    "attention required", "ddos protection", "captcha",
)

_SECURITY_HEADERS = (
    "strict-transport-security", "content-security-policy",
    "x-frame-options", "x-content-type-options",
)


class HttpProbe:
    """Fetches the URL and reports everything observable about the exchange."""

    def __init__(self, timeout: int | None = None):
        self.timeout = int(
            timeout or getattr(settings, "SCRAPER_REQUEST_TIMEOUT", 45))

    def run(self, url: str) -> StageResult:
        result = StageResult(stage=Stage.HTTP)
        started = time.monotonic()

        attempts: list[dict] = []
        best = None
        self._last_error = ""
        for rung, fetch in (("requests", self._requests),
                            ("requests_noverify", self._requests_noverify),
                            ("curl_cffi", self._curl_cffi),
                            ("cloudscraper", self._cloudscraper),
                            ("requests_direct", self._requests_direct)):
            attempt = fetch(url)
            if attempt is None:
                attempts.append({"rung": rung, "skipped": "client not installed"})
                continue
            attempts.append({
                "rung": rung, "status": attempt.get("status"),
                "error": attempt.get("error", ""),
                "bytes": len(attempt.get("body") or ""),
            })
            self._last_error = attempt.get("error", "")
            if attempt["ok"] and not self._challenged(attempt["status"],
                                                      attempt["body"]):
                best = attempt
                best["rung"] = rung
                break
            # Keep the most informative failure to report if every rung fails.
            if best is None or (attempt.get("status") and not best.get("status")):
                best = attempt
                best["rung"] = rung

        elapsed = int((time.monotonic() - started) * 1000)
        result.data["attempts"] = attempts
        result.data["response_time_ms"] = elapsed

        if best is None:
            result.fail(Failure.NETWORK_ERROR, "no HTTP client available")
            return result

        status = best.get("status")
        body = best.get("body") or ""
        headers = {k.lower(): v for k, v in (best.get("headers") or {}).items()}

        result.data.update({
            "status": status,
            "rung": best.get("rung", "requests"),
            "content_type": headers.get("content-type", ""),
            "charset": self._charset(headers.get("content-type", "")),
            "server": headers.get("server", ""),
            "content_encoding": headers.get("content-encoding", ""),
            "cache_control": headers.get("cache-control", ""),
            "location": headers.get("location", ""),
            "response_bytes": len(body),
            "cookies": list((best.get("cookies") or {}).keys()),
            "redirects": best.get("redirects", []),
            "final_url": best.get("final_url", url),
            "security_headers": [h for h in _SECURITY_HEADERS if h in headers],
            "body": body,
            "headers": headers,
        })

        if not best["ok"]:
            self._classify_error(result, best, status, body)
            return result

        if self._challenged(status, body):
            result.fail(Failure.BOT_PROTECTION,
                        "challenge page returned by every fetch rung")
            result.add("anti-bot challenge markers in body", where="response body")
            return result

        if not body.strip():
            result.fail(Failure.EMPTY_RESPONSE, f"HTTP {status} with a zero-length body")
            return result

        result.conclude("REACHABLE", 95 if best["rung"] == "requests" else 85)
        result.add(f"HTTP {status} via {best['rung']}",
                   where="response", detail=f"{len(body)} bytes in {elapsed} ms")
        if best.get("redirects"):
            result.add(f"{len(best['redirects'])} redirect(s) followed",
                       where="Location", detail=" → ".join(best["redirects"][:3]))
        if best["rung"] != "requests":
            result.add("plain requests was blocked; a stealth client was needed",
                       where="fetch escalation",
                       detail="the generated scraper must use the same client")
        if result.data["cookies"]:
            result.add(f"{len(result.data['cookies'])} cookie(s) set",
                       where="Set-Cookie",
                       detail=", ".join(result.data["cookies"][:5]))
        if not result.data["security_headers"]:
            result.unknowns.append("no standard security headers — server is permissive "
                                   "or behind a proxy that strips them")
        return result

    # ── fetch rungs ───────────────────────────────────────────────────────────

    def _requests(self, url: str) -> dict | None:
        try:
            import requests
        except ImportError:
            return None
        out = {"ok": False, "status": None, "body": "", "headers": {},
               "cookies": {}, "error": "", "redirects": [], "final_url": url}
        try:
            resp = requests.get(
                url, headers={"User-Agent": _UA, "Accept-Language": "ar,en;q=0.9"},
                timeout=self.timeout, allow_redirects=True)
            out.update(ok=resp.ok, status=resp.status_code, body=resp.text,
                       headers=dict(resp.headers), cookies=dict(resp.cookies),
                       redirects=[r.headers.get("Location", "") for r in resp.history],
                       final_url=resp.url)
        except Exception as exc:  # noqa: BLE001 — the exception type is the evidence
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out

    def _requests_noverify(self, url: str) -> dict | None:
        """The verify=False rung, tried only after a certificate failure.

        Several production portals run broken TLS chains, and the corpus
        already answers that with verify=False (era_ethiopia among others), so
        an SSL error must not end an investigation the corpus itself would
        survive. Gated on the previous rung failing with a certificate error,
        so a healthy site is never fetched insecurely; the rung name lands in
        the evidence, which tells the prompt builder the generated code needs
        verify=False too.
        """
        if "certificate" not in (self._last_error or "").lower() \
                and "ssl" not in (self._last_error or "").lower():
            return None
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except ImportError:
            return None
        out = {"ok": False, "status": None, "body": "", "headers": {},
               "cookies": {}, "error": "", "redirects": [], "final_url": url}
        try:
            resp = requests.get(
                url, headers={"User-Agent": _UA, "Accept-Language": "ar,en;q=0.9"},
                timeout=self.timeout, allow_redirects=True, verify=False)
            out.update(ok=resp.ok, status=resp.status_code, body=resp.text,
                       headers=dict(resp.headers), cookies=dict(resp.cookies),
                       redirects=[r.headers.get("Location", "") for r in resp.history],
                       final_url=resp.url)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out

    def _requests_direct(self, url: str) -> dict | None:
        """The no-proxy rung, last in the ladder.

        Every earlier rung honours HTTP_PROXY, so when the corporate proxy is
        the thing that is failing — DNS errors for .ae/.qa portals, HTTP 500
        pages of its own — all of them fail identically and the site takes the
        blame. One direct attempt separates "the portal is down" from "the
        proxy cannot carry this host". Where the network forbids direct egress
        this fails in milliseconds and changes nothing.
        """
        try:
            import requests
        except ImportError:
            return None
        out = {"ok": False, "status": None, "body": "", "headers": {},
               "cookies": {}, "error": "", "redirects": [], "final_url": url}
        try:
            session = requests.Session()
            session.trust_env = False
            resp = session.get(
                url, headers={"User-Agent": _UA, "Accept-Language": "ar,en;q=0.9"},
                timeout=self.timeout, allow_redirects=True)
            out.update(ok=resp.ok, status=resp.status_code, body=resp.text,
                       headers=dict(resp.headers), cookies=dict(resp.cookies),
                       redirects=[r.headers.get("Location", "") for r in resp.history],
                       final_url=resp.url)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out

    def _curl_cffi(self, url: str) -> dict | None:
        try:
            from curl_cffi import requests as crq
        except ImportError:
            return None
        out = {"ok": False, "status": None, "body": "", "headers": {},
               "cookies": {}, "error": "", "redirects": [], "final_url": url}
        try:
            resp = crq.get(url, timeout=self.timeout, impersonate="chrome")
            out.update(ok=200 <= resp.status_code < 400, status=resp.status_code,
                       body=resp.text, headers=dict(resp.headers))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out

    def _cloudscraper(self, url: str) -> dict | None:
        try:
            import cloudscraper
        except ImportError:
            return None
        out = {"ok": False, "status": None, "body": "", "headers": {},
               "cookies": {}, "error": "", "redirects": [], "final_url": url}
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False})
            resp = scraper.get(url, timeout=self.timeout)
            out.update(ok=resp.ok, status=resp.status_code, body=resp.text,
                       headers=dict(resp.headers), cookies=dict(resp.cookies))
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out

    # ── classification ────────────────────────────────────────────────────────

    def _classify_error(self, result: StageResult, attempt: dict,
                        status: int | None, body: str) -> None:
        """Map the observed failure onto exactly one taxonomy category."""
        error = (attempt.get("error") or "").lower()

        if status == 429:
            result.fail(Failure.RATE_LIMIT, f"HTTP {status}")
            return
        if status in (401, 407):
            result.fail(Failure.LOGIN_REQUIRED, f"HTTP {status}")
            return
        if status == 403:
            category = (Failure.BOT_PROTECTION if self._challenged(status, body)
                        else Failure.BLOCKED)
            result.fail(category, f"HTTP {status}")
            return
        if status and status >= 500:
            # A 5xx may come from the origin or from a proxy standing in front of
            # it. Saying "the origin" when a proxy answered sends whoever reads
            # this to debug the wrong machine.
            via = self._proxy_evidence(result, attempt)
            result.fail(Failure.NETWORK_ERROR,
                        f"HTTP {status} from {via}" if via
                        else f"HTTP {status} from the origin")
            if via:
                result.unknowns.append(
                    "the target site was never actually reached, so nothing "
                    "about it can be concluded from this run")
            return
        if status and 400 <= status < 500:
            result.fail(Failure.REQUEST_CONFIGURATION,
                        f"HTTP {status} — the URL or its parameters are wrong")
            return

        if "timeout" in error or "timed out" in error:
            result.fail(Failure.TIMEOUT, error[:200])
            return
        if "ssl" in error or "certificate" in error:
            result.fail(Failure.SSL_ERROR, error[:200])
            return
        if self._is_dns_failure(error, attempt):
            result.fail(Failure.DNS_ERROR, error[:200])
            return
        if "proxy" in error or "operation not permitted" in error:
            result.fail(Failure.NETWORK_ERROR,
                        f"{error[:180]} — this looks like the local proxy, "
                        f"not the target site")
            return
        result.fail(Failure.NETWORK_ERROR, error[:200] or "unreachable")

    @staticmethod
    def _proxy_evidence(result: StageResult, attempt: dict) -> str:
        """Name the responder when a proxy, not the origin, produced the reply.

        Returns a description if a proxy is implicated, else an empty string.
        """
        import os

        headers = {k.lower(): v for k, v in (attempt.get("headers") or {}).items()}
        if "via" in headers:
            return f"a proxy ({headers['via'][:60]}), not the target site"
        if "x-squid-error" in headers or "proxy-connection" in headers:
            return "a proxy, not the target site"

        host = (urlparse(attempt.get("final_url") or "").hostname or "").lower()
        proxy_set = any(os.environ.get(v) for v in
                        ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"))
        if not proxy_set or not host:
            return ""

        no_proxy = (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "")
        bypassed = any(
            host == part.strip().lstrip(".").lower()
            or host.endswith("." + part.strip().lstrip(".").lower())
            for part in no_proxy.split(",") if part.strip())
        if bypassed:
            return ""

        # The host does not resolve locally either: the proxy answered for it.
        try:
            socket.getaddrinfo(host, None)
            return ("the proxy or the origin — a proxy is configured for this "
                    "host, so the responder is ambiguous")
        except OSError:
            result.data["proxy_intercepted"] = True
            return (f"the configured proxy — '{host}' does not resolve, so the "
                    f"target was never contacted")

    @staticmethod
    def _is_dns_failure(error: str, attempt: dict) -> bool:
        if "name or service not known" in error or "nodename nor servname" in error:
            return True
        if "getaddrinfo" in error or "name resolution" in error:
            return True
        # A connection error may be DNS or routing; resolving settles it.
        if "connection" in error:
            host = urlparse(attempt.get("final_url") or "").hostname
            if host:
                try:
                    socket.getaddrinfo(host, None)
                except OSError:
                    return True
        return False

    @staticmethod
    def _challenged(status: int | None, body: str) -> bool:
        if status in (403, 429, 503):
            return True
        low = (body or "")[:4000].lower()
        return any(m in low for m in _CHALLENGE_MARKERS)

    @staticmethod
    def _charset(content_type: str) -> str:
        m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
        return m.group(1).lower() if m else ""
