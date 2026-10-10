"""Adapts an InvestigationReport + GenerationPlan onto the existing SiteProfile.

The retriever, similarity matcher and prompt builder were all written against
SiteProfile and work well. Rather than rewrite three working modules — and risk
changing which reference scrapers get retrieved — the investigation output is
projected onto that same shape. Downstream code needs no changes at all, and the
full report travels along inside `evidence` for anything that wants the detail.
"""

from __future__ import annotations

from ..interfaces import SiteProfile
from .contracts import InvestigationReport, Stage

#: Corpus families, mirroring analysis.analyzer.
F_HTML_CUSTOM = "HTML_CUSTOM"
F_JSON_API = "JSON_API"
F_STEALTH = "STEALTH_HTML"
F_PLAYWRIGHT = "PLAYWRIGHT"


def to_site_profile(report: InvestigationReport, plan=None) -> SiteProfile:
    """Project the investigation onto the DTO the rest of the Factory expects."""
    http = report.stage(Stage.HTTP)
    tech = report.stage(Stage.TECHNOLOGY)
    auth = report.stage(Stage.AUTHENTICATION)
    rendering = report.stage(Stage.RENDERING)
    api = report.stage(Stage.API_DISCOVERY)
    pagination = report.stage(Stage.PAGINATION)

    rung = http.data.get("rung", "requests")
    anti_bot = rung != "requests" or http.failure == "BOT_PROTECTION"

    profile = SiteProfile(url=report.url)
    profile.reachable = http.conclusion == "REACHABLE"
    profile.status_code = http.data.get("status")
    profile.content_type = http.data.get("content_type", "")
    profile.response_time_ms = http.data.get("response_time_ms", 0)

    profile.family = _family(report, plan, anti_bot)
    profile.framework = "" if tech.conclusion in ("STATIC_HTML", "UNKNOWN") else tech.conclusion
    profile.js_required = rendering.conclusion == "JAVASCRIPT_RENDERED"
    profile.anti_bot = anti_bot
    profile.login_required = bool(auth.data.get("gated"))
    profile.login_url = report.url if profile.login_required else ""

    endpoints = []
    proven = api.data.get("proven_endpoint")
    if proven:
        endpoints.append(proven)
    for entry in api.data.get("endpoints", []):
        url = entry.get("url")
        if url and url not in endpoints:
            endpoints.append(url)
    profile.api_endpoints = endpoints[:6]

    profile.pagination = _pagination(pagination.conclusion)
    profile.language = report.language
    profile.country_hint = report.country_hint
    profile.site_signature = report.site_signature
    profile.listing_sample = report.listing_sample

    # Everything the prompt and the reviewer might want, without new plumbing.
    profile.evidence = {
        "fetched_with": rung,
        "investigation": report.to_dict(),
        "dom": (rendering.data.get("structure") or {}),
        "snapshot_id": report.snapshot_id,
        "data_source": report.data_source,
    }
    if plan is not None:
        profile.evidence["plan"] = plan.to_dict()
    return profile


def _family(report: InvestigationReport, plan, anti_bot: bool) -> str:
    """Pick the corpus family whose reference scrapers actually fit.

    Fetch mechanism wins over data shape: a portal that only answers curl_cffi
    needs the stealth references regardless of whether its rows are HTML or
    JSON, because getting the bytes at all is the hard part.
    """
    if anti_bot:
        return F_STEALTH
    if plan is not None and getattr(plan, "family", ""):
        return plan.family
    if report.data_source in ("API", "GRAPHQL", "JS_STATE"):
        return F_JSON_API
    if report.stage(Stage.RENDERING).conclusion == "JAVASCRIPT_RENDERED":
        return F_PLAYWRIGHT
    return F_HTML_CUSTOM


def _pagination(conclusion: str) -> str:
    """Map investigation vocabulary onto the strings the corpus already uses."""
    return {
        "QUERY_PARAM": "QUERY_PARAM",
        "PATH": "PATH",
        "CURSOR": "CURSOR",
        "POSTBACK": "POSTBACK",
        "LOAD_MORE": "INFINITE_SCROLL",
        "INFINITE_SCROLL": "INFINITE_SCROLL",
        "NONE": "NONE",
    }.get(conclusion, "NONE")
