"""Retriever — F6, the retrieval side of RAG.

Assembles the ONLY corpus material that reaches the model:

  1. the 3-5 most similar scrapers (full source)
  2. the family profile they belong to
  3. reusable helper examples extracted from the corpus (date parsers, session
     priming, pagination loops) — so the model reuses house helpers instead of
     inventing new ones
  4. lessons learned from developer corrections for this kind of site (F14)

Everything is budgeted: if the references would exceed the token budget, the
count degrades (5 -> 4 -> 3) rather than silently truncating a file in half,
which would teach the model broken syntax.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings

from ..interfaces import ReferenceScraper, SiteProfile

logger = logging.getLogger("scraper_factory")

# Rough token estimate: Python averages ~3.6 chars/token for this corpus.
CHARS_PER_TOKEN = 3.6


@dataclass
class HelperExample:
    """A small, reusable snippet lifted verbatim from the corpus."""
    name: str
    purpose: str
    source_module: str
    code: str


@dataclass
class RetrievalBundle:
    """Everything retrieval contributes to a prompt."""
    profile: SiteProfile
    family: str = ""
    references: list[ReferenceScraper] = field(default_factory=list)
    helpers: list[HelperExample] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    family_notes: str = ""
    dropped_references: int = 0
    estimated_tokens: int = 0

    def reference_modules(self) -> list[str]:
        return [r.module for r in self.references]


class Retriever:
    """Builds a RetrievalBundle for one site profile."""

    def __init__(self, matcher=None, index=None):
        self._matcher = matcher
        self._index = index

    # ── public API ────────────────────────────────────────────────────────────

    def retrieve(self, profile: SiteProfile, *,
                 limit: Optional[int] = None) -> RetrievalBundle:
        matcher = self._get_matcher()
        index = self._get_index()

        max_refs = limit or int(getattr(settings, "FACTORY_REFERENCE_COUNT", 3))
        references = matcher.match(profile, limit=max_refs) if matcher else []

        bundle = RetrievalBundle(profile=profile,
                                 family=profile.family or "HTML_CUSTOM",
                                 references=references)
        bundle.family_notes = self._family_notes(bundle.family, index)
        bundle.helpers = self._helpers(profile, references, index)
        bundle.lessons = self._lessons(profile)
        self._apply_budget(bundle, profile)
        return bundle

    # ── family knowledge ──────────────────────────────────────────────────────

    @staticmethod
    def _family_notes(family: str, index) -> str:
        """A one-paragraph statement of how this family is written here."""
        count = len(index.families.get(family, [])) if index else 0
        notes = {
            "HTML_CUSTOM": (
                "This family overrides scrape() completely, fetches the listing "
                "page with self.session.get(LIST_URL, timeout=self.timeout), "
                "parses each record with a _parse_card()/_parse_row() helper "
                "returning Optional[TenderData], and returns the list."),
            "JSON_API": (
                "This family calls the portal's own JSON endpoint from scrape(), "
                "reads resp.json(), iterates the record array, and maps each "
                "record with _parse_item() returning Optional[TenderData]. "
                "Pagination uses a page parameter with PAGE_SIZE and MAX_PAGES "
                "module constants."),
            "STEALTH_HTML": (
                "This family adds a fetch ladder inside the module itself: try "
                "cloudscraper and/or curl_cffi, falling back between them, "
                "before parsing the HTML as the HTML family does."),
            "PLAYWRIGHT": (
                "This family imports playwright.sync_api lazily inside the "
                "method that needs it, renders the page headless, and falls back "
                "with a clear log message when Playwright is not installed."),
            "HTML_PARSE_PAGE": (
                "This family keeps BaseScraper's pagination loop and overrides "
                "scrape_url()/parse_page() instead of scrape()."),
            "VARIANT_SUBCLASS": (
                "This family subclasses an existing scraper and overrides only "
                "the class attributes that differ."),
        }
        text = notes.get(family, notes["HTML_CUSTOM"])
        return f"{text} There are {count} scrapers of this family in the project."

    # ── helper extraction ─────────────────────────────────────────────────────

    def _helpers(self, profile: SiteProfile, references: list[ReferenceScraper],
                 index) -> list[HelperExample]:
        """Pull house helpers the model should reuse rather than reinvent.

        Sourced from the retrieved references first (so the style matches), and
        only then from the wider corpus.
        """
        wanted: list[tuple[str, str]] = [
            (r"def _parse_\w*date\w*\(", "parse a portal date string"),
        ]
        if profile.pagination in ("QUERY_PARAM", "PATH"):
            wanted.append((r"for page in range\(", "iterate pages safely"))
        if profile.framework == "ASPNET":
            wanted.append((r"def _prime_session\(|__VIEWSTATE", "prime an ASP.NET session"))
        if profile.family == "JSON_API":
            wanted.append((r"math\.ceil\(", "compute the page count from a total"))

        helpers: list[HelperExample] = []
        pool = [(r.module, r.source_code) for r in references if r.source_code]
        if index is not None:
            import pathlib
            for module, fp in index.fingerprints.items():
                if len(pool) >= 12:
                    break
                if any(module == m for m, _ in pool) or fp.is_generated:
                    continue
                if fp.has_own_date_parser:
                    try:
                        pool.append((module, pathlib.Path(fp.path).read_text(
                            encoding="utf-8", errors="replace")))
                    except OSError:
                        pass

        for pattern, purpose in wanted:
            for module, source in pool:
                snippet = self._extract_block(source, pattern)
                if snippet:
                    helpers.append(HelperExample(
                        name=self._block_name(snippet), purpose=purpose,
                        source_module=module, code=snippet))
                    break
        return helpers

    @staticmethod
    def _extract_block(source: str, pattern: str, max_lines: int = 40) -> str:
        """Return the function/loop block whose first line matches `pattern`."""
        lines = source.splitlines()
        start = next((i for i, ln in enumerate(lines)
                      if re.search(pattern, ln)), None)
        if start is None:
            return ""
        # Include a preceding decorator so @staticmethod is not lost.
        if start > 0 and lines[start - 1].strip().startswith("@"):
            start -= 1
        indent = len(lines[start]) - len(lines[start].lstrip())
        out = [lines[start]]
        for ln in lines[start + 1:start + max_lines]:
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
                break
            out.append(ln)
        return "\n".join(out).rstrip()

    @staticmethod
    def _block_name(snippet: str) -> str:
        m = re.search(r"def (\w+)\(", snippet)
        return m.group(1) if m else "snippet"

    # ── learned lessons (F14) ─────────────────────────────────────────────────

    @staticmethod
    def _lessons(profile: SiteProfile, limit: int = 6) -> list[str]:
        """Retrieve developer corrections for similar sites, most specific first."""
        try:
            from ..models import LearningEntry
        except Exception:  # noqa: BLE001
            return []
        try:
            qs = LearningEntry.objects.filter(is_active=True).exclude(lesson="")
            exact = list(qs.filter(site_signature=profile.site_signature)
                         .order_by("-times_applied")[:limit])
            remaining = limit - len(exact)
            same_family = []
            if remaining > 0 and profile.family:
                same_family = list(
                    qs.filter(family=profile.family)
                      .exclude(pk__in=[e.pk for e in exact])
                      .order_by("-times_applied")[:remaining])
            picked = exact + same_family
            for entry in picked:
                LearningEntry.objects.filter(pk=entry.pk).update(
                    times_applied=entry.times_applied + 1)
            return [e.lesson.strip() for e in picked if e.lesson.strip()]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] lesson retrieval skipped: %s", exc)
            return []

    # ── budgeting ─────────────────────────────────────────────────────────────

    def _apply_budget(self, bundle: RetrievalBundle,
                      profile: SiteProfile) -> None:
        """Drop whole references (never partial ones) until we fit the budget."""
        budget = int(getattr(settings, "FACTORY_PROMPT_TOKEN_BUDGET", 24000))

        def estimate() -> int:
            chars = sum(len(r.source_code) for r in bundle.references)
            chars += sum(len(h.code) for h in bundle.helpers)
            chars += len(profile.listing_sample or "")
            chars += 4000  # instructions and site facts
            return int(chars / CHARS_PER_TOKEN)

        est = estimate()
        while est > budget and len(bundle.references) > 1:
            dropped = bundle.references.pop()          # lowest scoring
            bundle.dropped_references += 1
            logger.info("[factory] dropped reference %s to fit token budget "
                        "(est %s > %s)", dropped.module, est, budget)
            est = estimate()
        bundle.estimated_tokens = est

    # ── wiring ────────────────────────────────────────────────────────────────

    def _get_matcher(self):
        if self._matcher is not None:
            return self._matcher
        try:
            from .. import providers
            from ..interfaces import ISimilarityMatcher
            self._matcher = providers.get(ISimilarityMatcher)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] matcher unavailable: %s", exc)
        return self._matcher

    def _get_index(self):
        if self._index is not None:
            return self._index
        try:
            from .. import providers
            from ..interfaces import ICorpusIndexer
            self._index = providers.get(ICorpusIndexer).build()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] index unavailable: %s", exc)
        return self._index
