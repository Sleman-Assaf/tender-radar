"""F5 — modular prompt fragments.

A prompt is never static. It is composed from fragments, each of which knows a
single website trait (family, framework, pagination, JS, auth, anti-bot, HTML
complexity) and contributes only the instructions that trait requires.

Adding support for a new trait means adding one Fragment subclass and listing
its id in the active PromptVersion — no changes to the builder.

Every instruction here describes what the EXISTING corpus does. Fragments never
introduce a style rule that the repository does not already follow.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..corpus.retriever import RetrievalBundle


class Fragment(abc.ABC):
    """One composable piece of prompt guidance."""

    id: str = ""
    #: Lower numbers are emitted first.
    order: int = 100

    @abc.abstractmethod
    def applies(self, bundle: "RetrievalBundle") -> bool:
        """Whether this fragment is relevant to the analysed site."""

    @abc.abstractmethod
    def render(self, bundle: "RetrievalBundle") -> str:
        """The text to inject. Return '' to contribute nothing."""


# ── Family fragments ──────────────────────────────────────────────────────────

class HtmlCustomFamilyFragment(Fragment):
    id = "family.html_custom"
    order = 10

    def applies(self, bundle):
        return bundle.family in ("HTML_CUSTOM", "STEALTH_HTML")

    def render(self, bundle):
        return (
            "STRUCTURE (this is how all 37 HTML scrapers in the project are written):\n"
            "  1. def scrape(self) -> list[TenderData]:\n"
            "  2. import BeautifulSoup inside scrape() in a try/except ImportError\n"
            "     that prints '  X  [TAG] beautifulsoup4 not installed' and returns []\n"
            "  3. look up the source:\n"
            "       from tenders.models import ScraperSource\n"
            "       try:  source = ScraperSource.objects.get(\n"
            "                 scraper_class='<ClassName>', is_active=True)\n"
            "       except ScraperSource.DoesNotExist: print + return []\n"
            "       except ScraperSource.MultipleObjectsReturned: take .first()\n"
            "  4. self.session.headers.update(HEADERS)\n"
            "  5. rel_src, cat_src = self._reload_keywords_from_db()\n"
            "  6. print the banner block (SOURCE / SCRAPER / MODE / URL / FILTER /\n"
            "     Keywords) exactly in the house format\n"
            "  7. fetch with self.session.get(LIST_URL, timeout=self.timeout)\n"
            "     inside try/except; on failure print '  X  [TAG] Error: {exc}',\n"
            "     logger.error(...), and return []\n"
            "  8. soup = BeautifulSoup(resp.text, 'html.parser'); select the rows\n"
            "  9. loop rows -> self._parse_card(...) / self._parse_row(...)\n"
            " 10. print the TOTAL banner, logger.info(f'[TAG] complete — total={n}')\n"
            " 11. return results"
        )


class JsonApiFamilyFragment(Fragment):
    id = "family.json_api"
    order = 10

    def applies(self, bundle):
        return bundle.family == "JSON_API"

    def render(self, bundle):
        endpoints = bundle.profile.api_endpoints[:4]
        candidates = (bundle.profile.evidence.get("items_path_candidates") or [])[:4]
        text = (
            "STRUCTURE (this is how all 22 JSON API scrapers in the project are written):\n"
            "  1. module constants: BASE_URL, API_URL, PAGE_SIZE, MAX_PAGES, HEADERS\n"
            "  2. def scrape(self) -> list[TenderData]: resolve the ScraperSource,\n"
            "     update session headers, reload keywords, print the banner block\n"
            "  3. loop pages up to MAX_PAGES, calling the endpoint with the page\n"
            "     parameter; read resp.json()\n"
            "  4. iterate the record array and map each record with\n"
            "     self._parse_item(record) -> Optional[TenderData]\n"
            "  5. stop when a page returns no records\n"
            "  6. print the TOTAL banner and return the list"
        )
        if endpoints:
            text += "\n\nEndpoints observed on the page:\n" + "\n".join(
                f"  - {e}" for e in endpoints)
        if candidates:
            text += ("\n\nCandidate record-array paths found in the JSON:\n"
                     + "\n".join(f"  - {c}" for c in candidates))
        return text


class PlaywrightFamilyFragment(Fragment):
    id = "family.playwright"
    order = 10

    def applies(self, bundle):
        return bundle.family == "PLAYWRIGHT"

    def render(self, bundle):
        return (
            "STRUCTURE (this is how the 5 Playwright scrapers in the project are written):\n"
            "  1. Do NOT import playwright at module level. Import\n"
            "     'from playwright.sync_api import sync_playwright' INSIDE the\n"
            "     method that needs it, wrapped in try/except ImportError that\n"
            "     logs a clear install hint and falls back or returns [].\n"
            "  2. Keep a requests-based fallback path when possible, as dewa.py does.\n"
            "  3. Launch headless, goto(url), wait for the listing selector, take\n"
            "     page.content(), then parse that HTML with BeautifulSoup exactly\n"
            "     like the HTML family does.\n"
            "  4. Always close the browser in a finally block."
        )


class VariantFamilyFragment(Fragment):
    id = "family.variant"
    order = 10

    def applies(self, bundle):
        return bundle.family == "VARIANT_SUBCLASS"

    def render(self, bundle):
        return (
            "STRUCTURE: this site is a variant of an existing portal. Subclass the "
            "existing scraper class and override ONLY the class attributes and the "
            "few methods that genuinely differ, as sytenders.py and tendersa.py do. "
            "Do not copy the parent's logic."
        )


# ── Technology fragments ──────────────────────────────────────────────────────

class AspNetFragment(Fragment):
    id = "tech.aspnet"
    order = 20

    def applies(self, bundle):
        return bundle.profile.framework == "ASPNET"

    def render(self, bundle):
        return (
            "ASP.NET WebForms portal. The corpus handles these as follows:\n"
            "  - Element ids are auto-generated, so never match a full id. Use\n"
            "    suffix/regex matching like jordan_gtd.py and nepco_tenders.py do:\n"
            "      soup.find('span', id=lambda x: x and x.endswith('tenderNo'))\n"
            "    or a select with a partial attribute match: span[id*=\"lblTitle\"]\n"
            "  - Carry every hidden input (__VIEWSTATE, __EVENTVALIDATION) if a\n"
            "    POST is required.\n"
            "  - These servers are slow and bot-sensitive: keep\n"
            "    timeout=self.timeout and be polite between pages."
        )


class WordPressFragment(Fragment):
    id = "tech.wordpress"
    order = 20

    def applies(self, bundle):
        return bundle.profile.framework == "WORDPRESS"

    def render(self, bundle):
        return (
            "WordPress site. Prefer the wp-json REST API if one is exposed (it is "
            "far more stable than the theme's HTML). Otherwise parse the post list "
            "with the theme's repeating article/post classes."
        )


class JsRequiredFragment(Fragment):
    id = "tech.js_required"
    order = 25

    def applies(self, bundle):
        return bundle.profile.js_required

    def render(self, bundle):
        why = bundle.profile.evidence.get("js", "")
        text = ("The listing is rendered by JavaScript, so a plain requests fetch "
                "returns no rows.")
        if why:
            text += f" Evidence: {why}."
        text += ("\nPrefer intercepting the site's own JSON endpoint if one was "
                 "found — that is what adb.py does and it is much faster and more "
                 "stable than driving a browser. Only fall back to Playwright if "
                 "no endpoint is usable.")
        return text


class AntiBotFragment(Fragment):
    id = "tech.anti_bot"
    order = 25

    def applies(self, bundle):
        return bundle.profile.anti_bot

    def render(self, bundle):
        return (
            "This host returned an anti-bot challenge to a plain request. Follow "
            "kuwait_capt.py / era_ethiopia.py: build the fetch ladder INSIDE this "
            "module — try curl_cffi with impersonate='chrome' first, then "
            "cloudscraper.create_scraper(...), logging which rung succeeded. Import "
            "both lazily inside the fetch helper and degrade gracefully when a "
            "library is missing."
        )


# ── Pagination / auth / complexity ────────────────────────────────────────────

class PaginationFragment(Fragment):
    id = "pagination"
    order = 30

    def applies(self, bundle):
        return True

    def render(self, bundle):
        style = bundle.profile.pagination or "NONE"
        if style == "NONE":
            return ("PAGINATION: none detected — all records appear on one page. "
                    "Fetch the single listing URL and do not invent a paging loop "
                    "(nepco_tenders.py is the reference for a single-page source).")
        if style == "PATH":
            return ("PAGINATION: path segments (/page/N/). Define MAX_PAGES as a "
                    "module constant and stop as soon as a page yields no rows.")
        if style == "CURSOR":
            return ("PAGINATION: cursor/token based. Read the next-cursor value "
                    "from each response and stop when it is absent.")
        return ("PAGINATION: query parameter. Define MAX_PAGES (and PAGE_SIZE for "
                "an API) as module constants, loop with range(1, MAX_PAGES + 1), "
                "and break as soon as a page returns no records.")


class AuthFragment(Fragment):
    id = "auth"
    order = 35

    def applies(self, bundle):
        return bundle.profile.login_required

    def render(self, bundle):
        return (
            "AUTHENTICATION: this portal requires a login "
            f"(form detected at {bundle.profile.login_url or bundle.profile.url}).\n"
            "  - Never hard-code credentials. Read them from the ScraperSource "
            "record or settings, exactly as the project does elsewhere.\n"
            "  - Prime the session with a GET before posting, carry hidden form "
            "fields, and verify success by checking that the password field is gone.\n"
            "  - If login fails, print and log the reason and return [] — never "
            "raise out of scrape()."
        )


class HtmlComplexityFragment(Fragment):
    id = "complexity.html"
    order = 40

    def applies(self, bundle):
        return bool(bundle.profile.evidence.get("dom"))

    def render(self, bundle):
        dom = bundle.profile.evidence.get("dom") or {}
        tables, rows = dom.get("tables", 0), dom.get("rows", 0)
        cards = dom.get("cards", 0)
        classes = list((dom.get("repeating_classes") or {}).items())[:10]
        sigs = dom.get("table_signatures") or []

        lines = ["OBSERVED PAGE STRUCTURE (measured, use it to choose selectors):",
                 f"  tables={tables}  table rows={rows}  card-like elements={cards}"]
        if sigs and sigs[0].get("headers"):
            lines.append("  first table column headers: "
                         + " | ".join(h for h in sigs[0]["headers"] if h))
        if classes:
            lines.append("  most repeated classes: "
                         + ", ".join(f"{c} x{n}" for c, n in classes))
        if tables and rows >= 2:
            lines.append("  -> a table-based row parser (_parse_row) fits this page")
        elif cards >= 3:
            lines.append("  -> a card-based parser (_parse_card) fits this page")
        return "\n".join(lines)


class LessonsFragment(Fragment):
    """Injects developer corrections learned from earlier generations (F14)."""

    id = "learning.lessons"
    order = 50

    def applies(self, bundle):
        return bool(bundle.lessons)

    def render(self, bundle):
        items = "\n".join(f"  - {lesson}" for lesson in bundle.lessons)
        return ("LESSONS FROM OUR DEVELOPERS (corrections they made to earlier "
                f"generated scrapers for sites like this — follow them):\n{items}")


class HelperReuseFragment(Fragment):
    id = "helpers"
    order = 55

    def applies(self, bundle):
        return bool(bundle.helpers)

    def render(self, bundle):
        blocks = []
        for h in bundle.helpers:
            blocks.append(
                f"# from tenders/scrapers/{h.source_module}.py — {h.purpose}\n"
                f"{h.code}")
        return ("HOUSE HELPERS — reuse these shapes instead of inventing your own:\n\n"
                + "\n\n".join(blocks))


# ── Registry ──────────────────────────────────────────────────────────────────

ALL_FRAGMENTS: list[Fragment] = [
    HtmlCustomFamilyFragment(), JsonApiFamilyFragment(),
    PlaywrightFamilyFragment(), VariantFamilyFragment(),
    AspNetFragment(), WordPressFragment(), JsRequiredFragment(),
    AntiBotFragment(), PaginationFragment(), AuthFragment(),
    HtmlComplexityFragment(), LessonsFragment(), HelperReuseFragment(),
]


def fragments_for(bundle: "RetrievalBundle",
                  allowed_ids: list[str] | None = None) -> list[Fragment]:
    """Select and order the fragments that apply to this site."""
    chosen = [f for f in ALL_FRAGMENTS
              if (not allowed_ids or f.id in allowed_ids) and f.applies(bundle)]
    return sorted(chosen, key=lambda f: f.order)
