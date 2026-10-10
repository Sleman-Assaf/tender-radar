"""SimilarityMatcher — F6, step 1: find the most similar existing scrapers.

Scoring is explicit and weighted so the UI can tell the employee *why* a
reference was chosen. No embeddings, no model call: the features that matter
(fetch mechanism, framework, pagination, country, DOM shape) are all measurable,
and an explainable score is worth more here than a fuzzy one.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

from ..interfaces import ISimilarityMatcher, ReferenceScraper, SiteProfile

logger = logging.getLogger("scraper_factory")

# Weights. Family dominates because the corpus is organised that way and because
# the fetch mechanism is the part a reference actually teaches. Structural
# signals (how the data is shaped) outrank incidental ones (which country).
W_FAMILY = 40
W_FRAMEWORK = 20
W_DATA_SHAPE = 18         # JSON payload vs parsed HTML — decides the whole file
W_PAGINATION = 15
W_LAYOUT = 12             # table / cards / list — decides the parse loop
W_AUTH = 10
W_COUNTRY = 8
W_SIZE_BONUS = 5          # prefer small, readable exemplars


class SimilarityMatcher(ISimilarityMatcher):
    def __init__(self, index=None):
        self._index = index

    # ── public API ────────────────────────────────────────────────────────────

    def match(self, profile: SiteProfile, *,
              limit: Optional[int] = None) -> list[ReferenceScraper]:
        index = self._get_index()
        if index is None:
            return []

        limit = limit or int(getattr(settings, "FACTORY_REFERENCE_COUNT", 3))
        limit = min(limit, int(getattr(settings, "FACTORY_REFERENCE_MAX", 5)))

        scored: list[ReferenceScraper] = []
        for module, fp in index.fingerprints.items():
            # Generated modules are not style authorities until a human has
            # edited them, so they never become references.
            if fp.is_generated:
                continue
            score, reasons = self._score(profile, fp)
            scored.append(ReferenceScraper(
                module=module,
                class_name=(fp.classes[0] if fp.classes else ""),
                family=fp.family, score=score, reasons=reasons,
                line_count=fp.line_count))

        scored.sort(key=lambda r: (-r.score, r.line_count))
        top = scored[:limit]

        # Load source text only for the winners — this is the whole point of
        # retrieval: never send 73 files to the model.
        for ref in top:
            ref.source_code = self._read_source(index, ref.module)
        logger.info("[factory] retrieved references: %s",
                    [(r.module, r.score) for r in top])
        return top

    def rank_all(self, profile: SiteProfile) -> list[ReferenceScraper]:
        """Full ranking without source text — used by the F3 acceptance test."""
        index = self._get_index()
        if index is None:
            return []
        out = []
        for module, fp in index.fingerprints.items():
            score, reasons = self._score(profile, fp)
            out.append(ReferenceScraper(
                module=module, class_name=(fp.classes[0] if fp.classes else ""),
                family=fp.family, score=score, reasons=reasons,
                line_count=fp.line_count))
        out.sort(key=lambda r: (-r.score, r.line_count))
        return out

    # ── scoring ───────────────────────────────────────────────────────────────

    def _score(self, profile: SiteProfile, fp) -> tuple[float, list[str]]:
        """Rank by what the reference would actually teach for this site.

        Structural evidence only. URL or host similarity is never a factor: two
        portals on the same domain can be built completely differently, and two
        unrelated governments often run the identical platform.
        """
        score = 0.0
        reasons: list[str] = []
        investigation = profile.evidence.get("investigation") or {}
        stages = investigation.get("stages") or {}

        def stage(name: str) -> dict:
            return stages.get(name) or {}

        # ── family: the fetch mechanism the scraper must use ─────────────────
        if fp.family == profile.family:
            score += W_FAMILY
            reasons.append(f"same family ({fp.family})")
        elif self._family_adjacent(fp.family, profile.family):
            score += W_FAMILY * 0.4
            reasons.append(f"related family ({fp.family} vs {profile.family})")

        # ── technology ───────────────────────────────────────────────────────
        if profile.framework and fp.framework_hint == profile.framework:
            score += W_FRAMEWORK
            reasons.append(f"same technology ({profile.framework})")

        # ── data shape: JSON payload vs parsed markup ────────────────────────
        data_source = investigation.get("data_source") or (
            "API" if profile.family == "JSON_API" else "HTML")
        if data_source in ("API", "GRAPHQL", "JS_STATE"):
            if fp.uses_json:
                score += W_DATA_SHAPE
                reasons.append("parses a JSON payload, like this portal")
            if fp.uses_bs4 and not fp.uses_json:
                score -= W_DATA_SHAPE * 0.5
                reasons.append("HTML-only parser (weaker fit for a JSON source)")
            # A nested record path is a different loop from a bare array.
            api = stage("API_DISCOVERY").get("data") or {}
            proven = api.get("proven_endpoint")
            if proven:
                info = next((e for e in api.get("endpoints", [])
                             if e.get("url") == proven), {})
                if (info.get("items_path") or "").count(".") >= 1 and fp.uses_json:
                    score += 4
                    reasons.append("reads records from a nested JSON path")
        else:
            if fp.uses_bs4:
                score += W_DATA_SHAPE
                reasons.append("parses HTML with BeautifulSoup, like this portal")

        # ── pagination pattern ───────────────────────────────────────────────
        if profile.pagination and fp.pagination_hint == profile.pagination:
            score += W_PAGINATION
            reasons.append(f"same pagination ({profile.pagination})")

        # ── tender layout: table vs cards vs list ────────────────────────────
        layout = self._layout_of(stage("CANDIDATE_ROWS"), profile)
        if layout:
            fit, why = self._layout_fit(layout, fp)
            if fit:
                score += W_LAYOUT
                reasons.append(why)

        # ── authentication pattern ───────────────────────────────────────────
        auth = stage("AUTHENTICATION")
        auth_data = auth.get("data") or {}
        methods = " ".join(fp.methods).lower()
        if auth_data.get("gated") or profile.login_required:
            if "login" in methods or "authenticate" in methods:
                score += W_AUTH
                reasons.append("reference performs a login")
        elif auth_data.get("has_viewstate"):
            if "viewstate" in methods or "postback" in methods:
                score += W_AUTH
                reasons.append("reference handles ASP.NET postbacks")
        elif auth.get("conclusion") == "PUBLIC_WITH_SESSION_COOKIE":
            if fp.uses_session_get:
                score += W_AUTH * 0.6
                reasons.append("reference keeps a session across requests")

        # ── anti-bot: must match how the bytes are actually obtained ─────────
        if profile.anti_bot and fp.uses_stealth:
            score += W_AUTH
            reasons.append("reference uses a stealth client")

        # ── country, as a tiebreaker only ────────────────────────────────────
        if profile.country_hint and fp.country_code == profile.country_hint:
            score += W_COUNTRY
            reasons.append(f"same country ({fp.country_code})")

        # Prefer compact exemplars: they teach the style with fewer tokens.
        if fp.line_count and fp.line_count <= 300:
            score += W_SIZE_BONUS
            reasons.append(f"compact exemplar ({fp.line_count} lines)")

        return score, reasons

    # ── layout helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _layout_of(rows_stage: dict, profile: SiteProfile) -> str:
        """TABLE | CARDS | LIST | RECORDS, from the investigation's own finding."""
        if rows_stage.get("conclusion") == "API_RECORDS":
            return "RECORDS"
        best = (rows_stage.get("data") or {}).get("best") or {}
        where = (best.get("where") or "").lower()
        if "table" in where:
            return "TABLE"
        if "ul >" in where or "li" in where:
            return "LIST"
        if where:
            return "CARDS"
        # Fall back to the DOM fingerprint when no investigation is attached.
        dom = profile.evidence.get("dom") or {}
        if dom.get("tables", 0) and dom.get("rows", 0) >= 2:
            return "TABLE"
        if dom.get("cards", 0) >= 3:
            return "CARDS"
        return ""

    @staticmethod
    def _layout_fit(layout: str, fp) -> tuple[bool, str]:
        methods = set(fp.methods)
        table_like = methods & {"_parse_table", "_parse_row", "_parse_rows",
                                "_rows", "_extract_rows"}
        card_like = methods & {"_parse_card", "_parse_cards", "_parse_item",
                               "_parse_items", "_extract_card"}
        if layout == "TABLE" and table_like:
            return True, f"table-row parser ({sorted(table_like)[0]})"
        if layout in ("CARDS", "LIST") and card_like:
            return True, f"card/item parser ({sorted(card_like)[0]})"
        if layout == "RECORDS" and fp.uses_json and card_like:
            return True, f"iterates JSON records ({sorted(card_like)[0]})"
        return False, ""

    @staticmethod
    def _family_adjacent(a: str, b: str) -> bool:
        """Families that share most of their code shape."""
        groups = [
            {"HTML_CUSTOM", "HTML_PARSE_PAGE", "STEALTH_HTML"},
            {"JSON_API"},
            {"PLAYWRIGHT", "STEALTH_HTML"},
        ]
        return any(a in g and b in g for g in groups)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _read_source(index, module: str) -> str:
        fp = index.fingerprints.get(module)
        if fp is None:
            return ""
        import pathlib
        try:
            return pathlib.Path(fp.path).read_text(encoding="utf-8",
                                                   errors="replace")
        except OSError as exc:
            logger.warning("[factory] cannot read reference %s: %s", module, exc)
            return ""

    def _get_index(self):
        if self._index is not None:
            return self._index
        try:
            from .. import providers
            from ..interfaces import ICorpusIndexer
            self._index = providers.get(ICorpusIndexer).build()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] style index unavailable: %s", exc)
            self._index = None
        return self._index
