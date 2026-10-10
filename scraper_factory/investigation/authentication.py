"""Stage 3 — authentication detection.

The question this stage answers is narrow and specific: can the tender listing
be read anonymously, or must the scraper establish something first?

A login form existing somewhere on the page is not proof that the listing is
gated — most portals show a "Supplier login" link above a fully public notice
list. The stage therefore separates "a login exists" from "the listing is
behind it", and says UNKNOWN when it cannot tell the two apart.

Per the master prompt: never assume every ASP.NET page requires POST. Measure
it. A page carrying __VIEWSTATE whose rows are already in the GET response
needs no postback at all.
"""

from __future__ import annotations

import re

from .contracts import Failure, Stage, StageResult

_GATE_PHRASES = (
    "you must log in", "please log in to", "login required",
    "sign in to continue", "access denied", "unauthorized",
    "يجب تسجيل الدخول", "الرجاء تسجيل الدخول", "غير مصرح",
)


class AuthenticationDetector:
    """Reports the auth posture of the listing, with evidence for each claim."""

    def run(self, url: str, http: StageResult, tech: StageResult) -> StageResult:
        result = StageResult(stage=Stage.AUTHENTICATION)
        body = http.data.get("body") or ""
        headers = http.data.get("headers") or {}
        status = http.data.get("status")

        if not body:
            result.fail(http.failure or Failure.EMPTY_RESPONSE,
                        "no body captured by the HTTP stage")
            return result

        signals: dict[str, list[str]] = {}

        def signal(kind: str, note: str) -> None:
            signals.setdefault(kind, []).append(note)

        # ── explicit HTTP-level auth ─────────────────────────────────────────
        if status in (401, 407):
            result.fail(Failure.LOGIN_REQUIRED, f"HTTP {status}")
            return result
        if "www-authenticate" in headers:
            scheme = (headers["www-authenticate"] or "").split()[0:1]
            result.fail(Failure.LOGIN_REQUIRED,
                        f"WWW-Authenticate: {' '.join(scheme) or 'present'}")
            return result

        # ── forms and tokens present in the markup ───────────────────────────
        soup = self._soup(body)
        password_inputs = []
        forms = []
        if soup is not None:
            password_inputs = soup.find_all("input", {"type": "password"})
            forms = soup.find_all("form")
        elif re.search(r'type="password"', body, re.I):
            password_inputs = [None]

        if password_inputs:
            signal("LOGIN_FORM", f"{len(password_inputs)} password input(s)")

        csrf = re.search(
            r'name="(csrfmiddlewaretoken|_csrf|csrf_token|authenticity_token|'
            r'__RequestVerificationToken)"', body, re.I)
        if csrf:
            signal("CSRF_TOKEN", f"hidden field {csrf.group(1)}")

        # ── ASP.NET postback machinery ───────────────────────────────────────
        viewstate = bool(re.search(r'name="__VIEWSTATE"', body, re.I))
        eventvalidation = bool(re.search(r'name="__EVENTVALIDATION"', body, re.I))
        dopostback = bool(re.search(r'__doPostBack\s*\(', body, re.I))
        if viewstate:
            signal("VIEWSTATE", "__VIEWSTATE hidden field present")
        if eventvalidation:
            signal("VIEWSTATE", "__EVENTVALIDATION hidden field present")
        if dopostback:
            signal("POSTBACK", "__doPostBack() referenced in script")

        result.data.update({
            "has_viewstate": viewstate,
            "has_eventvalidation": eventvalidation,
            "has_dopostback": dopostback,
            "form_count": len(forms),
            "signals": signals,
        })

        # ── is the listing actually gated? ───────────────────────────────────
        gate_hit = next(
            (p for p in _GATE_PHRASES if p in body[:60000].lower()), "")
        if gate_hit:
            result.conclude("LOGIN_REQUIRED", 88)
            result.add(f"gate phrase in page text: '{gate_hit}'", where="body")
            result.data["gated"] = True
            return result

        # Cookies that the server set on a plain GET are session state the
        # scraper must carry, but they are not a login.
        cookies = http.data.get("cookies") or []
        if cookies:
            signal("SESSION_COOKIE", ", ".join(cookies[:5]))

        if password_inputs and not gate_hit:
            result.conclude("PUBLIC_WITH_LOGIN_AVAILABLE", 70)
            result.add("a login form exists but the page returned content anyway",
                       where="markup",
                       detail="listing appears public; login is for suppliers")
            result.unknowns.append(
                "cannot prove from one page whether deeper detail pages are gated")
            result.data["gated"] = False
            return result

        if cookies:
            result.conclude("PUBLIC_WITH_SESSION_COOKIE", 80)
            result.add(f"server set {len(cookies)} cookie(s) on an anonymous GET",
                       where="Set-Cookie", detail=", ".join(cookies[:5]))
            result.data["gated"] = False
            return result

        result.conclude("PUBLIC", 90)
        result.add("content returned to an anonymous GET with no auth challenge",
                   where=f"HTTP {status}")
        result.data["gated"] = False
        return result

    @staticmethod
    def _soup(body: str):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return None
        try:
            return BeautifulSoup(body, "html.parser")
        except Exception:  # noqa: BLE001
            return None
