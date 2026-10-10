"""Repair strategy mapping and scope isolation.

One diagnosis maps to exactly one repair, and that repair is allowed to touch
exactly one part of the file. This is the discipline that stops the loop
"fixing" a working selector because the real problem was pagination.

Scope is expressed as a list of concerns the repair prompt may mention. The
prompt builder turns it into an explicit instruction, and anything outside the
scope is stated as off-limits — a model told only what to fix will still
happily rewrite the rest unless told not to.

Some diagnoses are deliberately not repairable by regenerating code:
credentials, an unreachable network, a browser that is not installed. Burning
three repair attempts on those produces three identical failures and hides the
real ask from the developer.
"""

from __future__ import annotations

from ..investigation.contracts import Failure
from .engine import DiagnosticReport, Subsystem


class Repair:
    """The repair strategies the Factory can actually carry out."""

    SELECTORS = "REPAIR_SELECTORS"
    API_CALL = "REPAIR_API_CALL"
    RENDERING = "REPAIR_RENDERING"
    PAGINATION = "REPAIR_PAGINATION"
    DATE_PARSING = "REPAIR_DATE_PARSING"
    FIELD_MAPPING = "REPAIR_FIELD_MAPPING"
    HTTP_CLIENT = "REPAIR_HTTP_CLIENT"
    CODE_STYLE = "REPAIR_CODE_STYLE"
    NONE = "NONE"


#: diagnosis → (strategy, scope, repairable, suggestion)
#: `scope` names the only concerns the repair may alter.
_MAP: dict[tuple[str, str], tuple[str, tuple[str, ...], bool, str]] = {
    # ── network and access: not fixable by rewriting the scraper ─────────────
    (Subsystem.HTTP, Failure.NETWORK_ERROR): (
        Repair.NONE, (), False,
        "the site was unreachable — check connectivity and the proxy before "
        "regenerating anything"),
    (Subsystem.HTTP, Failure.DNS_ERROR): (
        Repair.NONE, (), False, "the hostname does not resolve; confirm the URL"),
    (Subsystem.HTTP, Failure.SSL_ERROR): (
        Repair.NONE, (), False, "the TLS handshake failed; this is an "
        "infrastructure issue, not a code issue"),
    (Subsystem.HTTP, Failure.TIMEOUT): (
        Repair.NONE, (), False, "the site timed out; retry before regenerating"),
    (Subsystem.HTTP, Failure.REQUEST_CONFIGURATION): (
        Repair.NONE, (), False,
        "the URL returned 4xx — the address or its parameters are wrong, so a "
        "developer should confirm the correct listing URL"),
    (Subsystem.NETWORK, Failure.NETWORK_ERROR): (
        Repair.NONE, (), False,
        "the scraper could not reach the site during the run; the selectors "
        "were never exercised and must not be blamed"),
    (Subsystem.NETWORK, Failure.TIMEOUT): (
        Repair.NONE, (), False, "the run timed out; retry before regenerating"),

    # ── anti-bot: fixable, by changing the client ────────────────────────────
    (Subsystem.HTTP, Failure.BOT_PROTECTION): (
        Repair.HTTP_CLIENT, ("fetch client", "request headers"), True,
        "the portal blocks plain requests; the scraper must use curl_cffi or "
        "cloudscraper as several production scrapers already do"),
    (Subsystem.HTTP, Failure.RATE_LIMIT): (
        Repair.HTTP_CLIENT, ("request pacing", "retry handling"), True,
        "the portal rate-limited us; add pacing between requests"),
    (Subsystem.HTTP, Failure.BLOCKED): (
        Repair.HTTP_CLIENT, ("fetch client", "request headers"), True,
        "the portal refused the request; a browser-like client may be required"),

    # ── auth: needs a human, not a regeneration ──────────────────────────────
    (Subsystem.AUTHENTICATION, Failure.LOGIN_REQUIRED): (
        Repair.NONE, (), False,
        "the listing is behind a login; a developer must supply credentials "
        "before any scraper can be generated"),
    (Subsystem.AUTHENTICATION, Failure.SESSION_REQUIRED): (
        Repair.NONE, (), False, "a session must be established first"),
    (Subsystem.AUTHENTICATION, Failure.CSRF_REQUIRED): (
        Repair.API_CALL, ("token harvesting", "request headers"), True,
        "harvest the CSRF token from the page before posting"),

    # ── rendering ────────────────────────────────────────────────────────────
    (Subsystem.RENDERING, Failure.JAVASCRIPT_RENDERING): (
        Repair.RENDERING, ("fetch strategy", "rendering"), True,
        "the rows are produced by JavaScript; either call the underlying "
        "request directly or drive a browser — do not touch the selectors"),

    # ── API ──────────────────────────────────────────────────────────────────
    (Subsystem.API_DISCOVERY, Failure.API_DISCOVERY_REQUIRED): (
        Repair.API_CALL, ("endpoint", "query parameters", "request headers"), True,
        "the data comes from an API; correct the endpoint call only"),
    (Subsystem.API_DISCOVERY, Failure.GRAPHQL_REQUIRED): (
        Repair.API_CALL, ("graphql query", "endpoint", "request headers"), True,
        "the portal serves GraphQL; correct the query document"),
    (Subsystem.API_DISCOVERY, Failure.POST_REQUEST_REQUIRED): (
        Repair.API_CALL, ("http method", "post body"), True,
        "the endpoint requires POST; correct the method and body only"),

    # ── pagination ───────────────────────────────────────────────────────────
    (Subsystem.PAGINATION, Failure.PAGINATION_REQUIRED): (
        Repair.PAGINATION, ("pagination",), True,
        "only page one was collected; repair the paging loop and nothing else"),
    (Subsystem.PAGINATION, Failure.INFINITE_SCROLL_REQUIRED): (
        Repair.PAGINATION, ("pagination",), True,
        "the listing pages by scroll; reproduce the follow-up request"),

    # ── rows and selectors ───────────────────────────────────────────────────
    (Subsystem.CANDIDATE_ROWS, "SELECTOR_MISMATCH"): (
        Repair.SELECTORS, ("row selector", "field selectors"), True,
        "the row selector matched nothing although the rows are present; "
        "correct the selectors only"),
    (Subsystem.CANDIDATE_ROWS, Failure.LANDING_PAGE_CONTENT): (
        Repair.NONE, (), False,
        "this URL looks like a landing or category page; a developer should "
        "supply the actual listing URL"),
    (Subsystem.CANDIDATE_ROWS, Failure.CATEGORY_PAGE): (
        Repair.NONE, (), False, "this URL is a category index, not a listing"),
    (Subsystem.CANDIDATE_ROWS, Failure.EMPTY_LISTING): (
        Repair.NONE, (), False,
        "the listing is genuinely empty right now; retry later rather than "
        "regenerating the scraper"),

    # ── tender validation ────────────────────────────────────────────────────
    (Subsystem.TENDER_VALIDATION, Failure.NON_TENDER_ELEMENTS_SELECTED): (
        Repair.SELECTORS, ("row selector",), True,
        "the selector is catching menus or widgets; narrow it to the tender "
        "rows only"),
    (Subsystem.TENDER_VALIDATION, Failure.INVALID_ROW_SELECTION): (
        Repair.SELECTORS, ("row selector", "field selectors"), True,
        "the rows extracted are not tenders; correct the row selector"),
    (Subsystem.TENDER_VALIDATION, Failure.EMPTY_LISTING): (
        Repair.NONE, (), False, "nothing was extracted and the page has no rows"),
    (Subsystem.TENDER_VALIDATION, Failure.LANDING_PAGE_CONTENT): (
        Repair.NONE, (), False,
        "the rows are page furniture; the URL is probably not the listing"),

    # ── fields and dates ─────────────────────────────────────────────────────
    (Subsystem.FIELD_EXTRACTION, Failure.FIELD_EXTRACTION_FAILED): (
        Repair.FIELD_MAPPING, ("field selectors",), True,
        "rows are correct but fields are empty; repair the field mapping only"),
    (Subsystem.DATE_EXTRACTION, Failure.DATE_EXTRACTION_FAILED): (
        Repair.DATE_PARSING, ("date parsing",), True,
        "dates were found but not parsed; repair the date handling only"),

    # ── code ─────────────────────────────────────────────────────────────────
    (Subsystem.CODE_VALIDATION, "CODE_REJECTED"): (
        Repair.CODE_STYLE, ("the reported violations",), True,
        "fix exactly the reported violations and change nothing else"),
    (Subsystem.INSTALLATION, Failure.INSTALLATION_FAILED): (
        Repair.NONE, (), False,
        "installation failed; this is an environment problem, not a code one"),
}

#: Fallback per subsystem when the exact category is unmapped, so a new failure
#: code degrades to a sensible scope instead of no diagnosis at all.
_BY_SUBSYSTEM: dict[str, tuple[str, tuple[str, ...], bool, str]] = {
    Subsystem.HTTP: (Repair.NONE, (), False, "reachability problem"),
    Subsystem.NETWORK: (Repair.NONE, (), False, "network problem"),
    Subsystem.AUTHENTICATION: (Repair.NONE, (), False, "access problem"),
    Subsystem.RENDERING: (Repair.RENDERING, ("fetch strategy",), True,
                          "the fetch strategy is wrong for this portal"),
    Subsystem.API_DISCOVERY: (Repair.API_CALL, ("endpoint",), True,
                              "the API call is wrong"),
    Subsystem.PAGINATION: (Repair.PAGINATION, ("pagination",), True,
                           "paging is wrong"),
    Subsystem.CANDIDATE_ROWS: (Repair.SELECTORS, ("row selector",), True,
                               "the row selector is wrong"),
    Subsystem.TENDER_VALIDATION: (Repair.SELECTORS, ("row selector",), True,
                                  "the extracted rows are not tenders"),
    Subsystem.FIELD_EXTRACTION: (Repair.FIELD_MAPPING, ("field selectors",), True,
                                 "field mapping is wrong"),
    Subsystem.DATE_EXTRACTION: (Repair.DATE_PARSING, ("date parsing",), True,
                                "date handling is wrong"),
    Subsystem.CODE_VALIDATION: (Repair.CODE_STYLE, ("the reported violations",),
                                True, "the file breaks house rules"),
    Subsystem.CODE_GENERATION: (Repair.CODE_STYLE, ("the whole file",), True,
                                "the model produced unusable output"),
    Subsystem.SANDBOX_EXECUTION: (Repair.NONE, (), False,
                                  "the scraper crashed; see the traceback"),
    Subsystem.INSTALLATION: (Repair.NONE, (), False, "installation problem"),
    Subsystem.CLASSIFICATION: (Repair.NONE, (), False, "classification problem"),
}


def map_repair(report: DiagnosticReport) -> DiagnosticReport:
    """Attach the single permitted repair for this diagnosis."""
    if report.root_cause is None:
        report.repair_strategy = Repair.NONE
        return report

    key = (report.root_cause.subsystem, report.root_cause.category)
    entry = _MAP.get(key)
    if entry is None:
        entry = _BY_SUBSYSTEM.get(report.root_cause.subsystem,
                                  (Repair.NONE, (), False, ""))
    strategy, scope, repairable, suggestion = entry

    report.repair_strategy = strategy
    report.repair_scope = list(scope)
    report.repairable = repairable
    if suggestion:
        report.suggestions.append(suggestion)

    # A confident diagnosis is worth remembering; a guess is not.
    if report.root_cause.confidence >= 70:
        report.learning = (
            f"{report.root_cause.subsystem}/{report.root_cause.category} → "
            f"{strategy}: {suggestion}")
    return report


#: What each strategy is expected to achieve, so the repair has a target rather
#: than only a prohibition. A model told what "fixed" looks like stops when it
#: gets there; one told only what not to touch keeps going.
_EXPECTED_OUTCOME = {
    Repair.SELECTORS: "the scraper returns the tender rows that the "
                      "investigation proved are on the page, and nothing else",
    Repair.API_CALL: "the endpoint returns the record array and the scraper "
                     "iterates it",
    Repair.RENDERING: "the rows are obtained by the method that actually "
                      "produces them on this portal",
    Repair.PAGINATION: "the scraper reaches page two and continues until the "
                       "pages run out",
    Repair.DATE_PARSING: "dates become datetime.date objects, or None when "
                         "genuinely unparseable",
    Repair.FIELD_MAPPING: "each row's fields are populated from the correct "
                          "part of the record",
    Repair.HTTP_CLIENT: "the request succeeds instead of being blocked",
    Repair.CODE_STYLE: "every reported violation is gone and nothing else "
                       "changed",
}

#: Named explicitly per strategy. "Do not change anything else" is too abstract
#: to act on; naming the specific temptations is what makes it stick.
_FORBIDDEN = {
    Repair.SELECTORS: ("the request URL, the HTTP client, the pagination loop, "
                       "the date parsing, the banner or the class structure"),
    Repair.API_CALL: ("the row-parsing loop, the field mapping, the date "
                      "parsing or the class structure"),
    Repair.RENDERING: ("the field selectors, the date parsing or the class "
                       "structure"),
    Repair.PAGINATION: ("the row selector, the field selectors, the request "
                        "headers or the date parsing"),
    Repair.DATE_PARSING: ("the row selector, the field selectors, the request "
                          "or the pagination loop"),
    Repair.FIELD_MAPPING: ("the row selector, the request, the pagination loop "
                           "or the date parsing"),
    Repair.HTTP_CLIENT: ("the selectors, the parsing loop or the pagination"),
    Repair.CODE_STYLE: ("any behaviour — this is a compliance fix, not a "
                        "logic change"),
}


def repair_instruction(report: DiagnosticReport, *,
                       known_repair: str = "") -> str:
    """The structured constraint block handed to the repair prompt.

    Five sections, always in the same order: root cause, allowed, forbidden,
    scope, expected outcome. Naming only the target invites the model to tidy
    everything around it, which is how a one-line fix becomes a rewrite that
    fails validation for new reasons.
    """
    if not report.repairable or not report.repair_scope:
        return ""

    allowed = ", ".join(report.repair_scope)
    forbidden = _FORBIDDEN.get(report.repair_strategy,
                               "anything not named under ALLOWED CHANGES")
    outcome = _EXPECTED_OUTCOME.get(report.repair_strategy,
                                    "the reported failure no longer occurs")

    lines = [
        "REPAIR CONSTRAINTS",
        "",
        f"ROOT CAUSE       : {report.subsystem} → {report.category}",
    ]
    if report.root_cause is not None:
        lines.append(f"                   {report.root_cause.summary}")
        for e in report.root_cause.evidence[:3]:
            lines.append(f"                   · {e.render()[:120]}")

    lines += [
        "",
        f"ALLOWED CHANGES  : {allowed}",
        f"FORBIDDEN        : {forbidden}",
        "                   Do not reformat, rename, reorder or 'improve' any "
        "untouched code.",
        f"SCOPE            : this is a {report.repair_strategy} repair. Only the "
        f"failing subsystem is being fixed; the rest of the file is known to "
        f"work.",
        f"EXPECTED OUTCOME : {outcome}",
    ]

    if known_repair:
        lines += [
            "",
            f"PRIOR ART        : a previous scraper with this exact diagnosis "
            f"was fixed like this — {known_repair}",
        ]
    for s in report.suggestions[:2]:
        lines.append(f"GUIDANCE         : {s}")
    return "\n".join(lines)
