"""F5 + F12 — dynamic prompt builder with versioned recipes.

The prompt is assembled per website from:
  * the style contract, generated from the LIVE corpus index (so it always
    states what the repository currently does, never a hard-coded opinion)
  * the fragments that apply to this site's traits (F5)
  * the retrieved references, helpers and lessons (F6)
  * the site's own listing sample

The recipe (template + fragment set + retrieval policy) is versioned as a
PromptVersion row (F12) so generations remain comparable and reproducible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings

from ..corpus.retriever import RetrievalBundle
from . import fragments as frag
from . import strategies as frag_strategies

logger = logging.getLogger("scraper_factory")

CURRENT_PROMPT_VERSION = "1.0.0"

SYSTEM_TEMPLATE = """\
You are a senior Python developer on the AJi TenderRadar team. You have written \
every scraper in this project. You are now writing ONE more scraper file for a \
new tender portal.

The existing repository is the specification. Reproduce its style exactly.

ABSOLUTE RULES — a file that breaks any of these is rejected automatically:
{invariants}

HOUSE CONVENTIONS — match these; they are measurements of the existing code:
{conventions}

CONSOLE OUTPUT — the project prints progress as well as logging. Reproduce this
dialect verbatim (note the exact spacing before each colon):
{console}

FORBIDDEN — these would make the file obviously machine-written or unsafe:
  - Do NOT call save_tender_data(). scrape() returns list[TenderData]; the
    caller persists. No scraper in this project persists anything.
  - Do NOT write to the database (no .save()/.create()/.delete() on Tender).
  - Do NOT import anything outside: logging, re, time, math, json, hashlib,
    random, datetime, typing, urllib.parse, requests, bs4, cloudscraper,
    curl_cffi, playwright, and `from .base import BaseScraper, TenderData`.
  - Do NOT use dataclasses, type aliases, f-string logging via %, pathlib for
    scraping, async, or any abstraction the existing scrapers do not use.
  - Do NOT refactor, modernise, tidy or improve the house patterns. If the
    existing scrapers repeat themselves, repeat too.
  - Do NOT add explanatory comments about being AI-generated.

OUTPUT FORMAT — output ONLY the complete Python file. No markdown fences, no
commentary before or after. The first line must be the path comment.
"""

USER_TEMPLATE = """\
TARGET PORTAL
  URL          : {url}
  Country      : {country}
  Language     : {language}
  Family       : {family}
  Framework    : {framework}
  Pagination   : {pagination}
  JS required  : {js_required}
  Login needed : {login_required}
  Anti-bot     : {anti_bot}
  HTTP status  : {status} ({fetched_with})

FILE TO WRITE
  path         : tenders/scrapers/{module_name}.py
  class        : {class_name}(BaseScraper)
  country_code : '{country_code}'
  source_name  : '{source_name}'

{strategy_task}

GENERATION STRATEGY — selected from measured evidence, not preference
{strategy_block}

INVESTIGATION EVIDENCE — what was actually measured on this portal
{evidence_block}

FIELD CONTRACT — measured from the production scrapers, not invented
{field_block}

WHAT VALIDATION WILL CHECK — the rows you extract are scored before install
{tender_rules}

DATES
{date_rules}
{lessons_block}

FAMILY GUIDANCE
{family_notes}

{fragments}

REFERENCE SCRAPERS FROM THIS PROJECT
These are real files from tenders/scrapers/. Copy their structure, spacing,
banner comments, print statements, logging and docstring shape. Do not copy
their selectors — those belong to their own portals.

{references}

LISTING MARKUP FROM THE TARGET PORTAL
Derive the selectors from this sample. It is the densest repeating region of the
live page, captured at {captured_at}.

```
{listing_sample}
```

Write the complete file now, starting with the line:
# tenders/scrapers/{module_name}.py
"""

REPAIR_TEMPLATE = """\
The file you produced was rejected by the project's validator. Fix ONLY the
problems listed below and output the COMPLETE corrected file again.

Do not change anything else. Do not reformat untouched code. Do not add
comments about the fixes.

THE FILE MUST STILL BE
  path         : tenders/scrapers/{module_name}.py
  first line   : # tenders/scrapers/{module_name}.py
  class        : {class_name}(BaseScraper)
  and the ScraperSource lookup inside scrape() must use
  scraper_class='{class_name}'

VALIDATION FAILURES
{failures}

{extra_guidance}

Here is the file you produced:

```
{previous_code}
```

Output only the corrected complete Python file, starting with the path comment.
"""


@dataclass
class BuiltPrompt:
    """The prompt actually sent to the model, plus its provenance."""
    system: str
    user: str
    version: str = CURRENT_PROMPT_VERSION
    fragment_ids: list[str] = field(default_factory=list)
    reference_modules: list[str] = field(default_factory=list)
    estimated_tokens: int = 0

    def char_size(self) -> int:
        return len(self.system) + len(self.user)


class PromptBuilder:
    """Builds generation and repair prompts. Modular and extendable."""

    def __init__(self, index=None, version: str = CURRENT_PROMPT_VERSION):
        self._index = index
        self.version = version

    # ── generation prompt ─────────────────────────────────────────────────────

    def build(self, bundle: RetrievalBundle, *, module_name: str,
              class_name: str, country_code: str, source_name: str,
              allowed_fragment_ids: Optional[list[str]] = None,
              plan=None, investigation=None) -> BuiltPrompt:
        profile = bundle.profile
        chosen = frag.fragments_for(bundle, allowed_fragment_ids)

        rendered = []
        for f in chosen:
            text = f.render(bundle)
            if text:
                rendered.append(text)

        system = SYSTEM_TEMPLATE.format(
            invariants=self._invariants_text(),
            conventions=self._conventions_text(),
            console=self._console_text(),
        )

        user = USER_TEMPLATE.format(
            url=profile.url,
            country=profile.country_hint or country_code or "unknown",
            language=profile.language or "unknown",
            family=bundle.family,
            framework=profile.framework or "none detected",
            pagination=profile.pagination or "NONE",
            js_required="yes" if profile.js_required else "no",
            login_required="yes" if profile.login_required else "no",
            anti_bot="yes" if profile.anti_bot else "no",
            status=profile.status_code, fetched_with=profile.evidence.get(
                "fetched_with", "requests"),
            module_name=module_name, class_name=class_name,
            country_code=country_code, source_name=source_name,
            strategy_task=frag_strategies.brief_for(
                getattr(plan, "strategy", ""), class_name=class_name),
            strategy_block=self._strategy_text(plan),
            evidence_block=self._evidence_text(investigation),
            field_block=self._field_text(),
            tender_rules=self._tender_rules_text(),
            date_rules=self._date_rules_text(),
            lessons_block=self._lessons_text(investigation, plan),
            family_notes=bundle.family_notes,
            fragments="\n\n".join(rendered),
            references=self._references_text(bundle),
            listing_sample=profile.listing_sample or "(no sample captured)",
            captured_at="generation time",
        )

        prompt = BuiltPrompt(
            system=system, user=user, version=self.version,
            fragment_ids=[f.id for f in chosen],
            reference_modules=bundle.reference_modules(),
            estimated_tokens=int((len(system) + len(user)) / 3.6),
        )
        self._ensure_version_row(prompt)
        return prompt

    # ── repair prompt (F7) ────────────────────────────────────────────────────

    def build_repair(self, previous_code: str, report, *,
                     module_name: str = "", class_name: str = "",
                     extra_guidance: str = "") -> BuiltPrompt:
        """A repair prompt contains ONLY the validation failures.

        Never a blind regeneration: the model sees its own file plus a numbered
        list of what is wrong, which is the smallest, most reliable ask.

        The target identity (module and class name) is restated because a model
        that has lost track of it would otherwise "fix" the failures while
        drifting the file name or class name, which fails validation again.
        """
        failures = []
        for i, v in enumerate(getattr(report, "violations", []), 1):
            where = f" (line {v.line})" if v.line else ""
            severity = "MUST FIX" if v.hard else "style"
            failures.append(f"{i}. [{severity}] {v.code}{where}: {v.message}")

        user = REPAIR_TEMPLATE.format(
            module_name=module_name or "<module>",
            class_name=class_name or "<Class>",
            failures="\n".join(failures) or "(none reported)",
            extra_guidance=extra_guidance,
            previous_code=previous_code,
        )
        system = SYSTEM_TEMPLATE.format(
            invariants=self._invariants_text(),
            conventions=self._conventions_text(),
            console=self._console_text(),
        )
        return BuiltPrompt(system=system, user=user, version=self.version,
                           fragment_ids=["repair"],
                           estimated_tokens=int((len(system) + len(user)) / 3.6))

    # ── strategy directives (Phase 1) ─────────────────────────────────────────

    _STRATEGY_NOTES = {
        "API": "Call the endpoint below directly with requests and parse JSON. "
               "Do NOT parse HTML and do NOT use a browser.",
        "GRAPHQL": "POST the GraphQL query to the endpoint below and read the "
                   "JSON response. Do NOT parse HTML.",
        "JAVASCRIPT": "The data is already embedded in the page as JSON. Fetch "
                      "the page, extract that blob with a regex, json.loads it, "
                      "and iterate the records. Do NOT use a browser.",
        "STATIC_HTML": "Parse the served HTML with BeautifulSoup. The rows are "
                       "already present in the response; no browser is needed.",
        "ASPNET_POSTBACK": "Read __VIEWSTATE, __EVENTVALIDATION and "
                           "__VIEWSTATEGENERATOR from the first page, then POST "
                           "them back with __EVENTTARGET/__EVENTARGUMENT to reach "
                           "later pages. Keep the session cookies.",
        "BROWSER_ASSISTED": "The listing only exists after JavaScript runs and no "
                            "reproducible request was found, so drive Playwright.",
        "LOGIN_SESSION": "Authenticate first, then reuse the session for the "
                         "listing requests.",
        "HYBRID": "The listing and the detail pages come from different places; "
                  "follow the directives for each.",
    }

    def _strategy_text(self, plan) -> str:
        """State the decided strategy as instructions, with its measured facts.

        The model is told what was *observed*, never asked to choose an approach:
        letting it decide would reintroduce exactly the guessing the
        investigation exists to remove.
        """
        if plan is None:
            return "  (no plan supplied — follow the family guidance below)"

        d = plan.directives or {}
        lines = [f"  Strategy   : {plan.strategy} (confidence {plan.confidence}%)"]
        note = self._STRATEGY_NOTES.get(plan.strategy)
        if note:
            lines.append(f"  Directive  : {note}")

        if d.get("endpoint"):
            # A GraphQL plan carries an endpoint and a query document but no
            # explicit method; default rather than KeyError on prompt assembly.
            lines.append(f"  Endpoint   : {d.get('method') or 'GET'} "
                         f"{d['endpoint']}")
        if d.get("items_path"):
            lines.append(f"  Records at : {d['items_path']}")
        if d.get("fields"):
            lines.append(f"  Fields     : {', '.join(d['fields'][:18])}")
        if d.get("embedded_source"):
            lines.append(f"  Blob       : {d['embedded_source']}")
        if d.get("row_selector"):
            lines.append(f"  Row select : {d['row_selector']} "
                         f"({d.get('row_count', 0)} matched, "
                         f"{d.get('row_container', '')})")
        if d.get("sample_text"):
            lines.append(f"  Row sample : {d['sample_text'][:120]}")
        if d.get("request_headers"):
            lines.append(f"  Headers    : {d['request_headers']}")
        if d.get("post_data"):
            lines.append(f"  POST body  : {str(d['post_data'])[:200]}")
        if d.get("event_targets"):
            lines.append(f"  Postback   : {d['event_targets'][:3]}")

        client = d.get("http_client", "requests")
        if client != "requests":
            lines.append(f"  HTTP client: {client} — plain requests was blocked, "
                         f"so use this client exactly")

        pagination = d.get("pagination") or {}
        if pagination:
            extra = {k: v for k, v in pagination.items()
                     if k in ("param", "size_param", "cursor_key", "style")}
            lines.append(f"  Pagination : {pagination.get('strategy', 'UNKNOWN')} "
                         f"({pagination.get('confidence', 0)}%) {extra or ''}")

        if d.get("dates_missing"):
            lines.append("  Note       : no dates in the listing rows — the "
                         "deadline may only exist on the detail page")
        for risk in (plan.risks or [])[:3]:
            lines.append(f"  Risk       : {risk}")
        return "\n".join(lines)

    @staticmethod
    def _evidence_text(investigation) -> str:
        """The per-stage findings, so every instruction above is traceable.

        Conclusions only, with their confidence — the full evidence list would
        double the prompt without changing a single decision the model makes.
        """
        if investigation is None:
            return "  (no investigation attached)"
        from ..investigation.contracts import Stage

        lines = []
        for name in Stage.ORDER:
            result = investigation.stages.get(name)
            if result is None:
                continue
            head = f"{result.conclusion} ({result.confidence}%)"
            lines.append(f"  {name:<15}: {head}")
            for e in result.evidence[:2]:
                lines.append(f"      · {e.render()[:140]}")
        unknowns = investigation.all_unknowns()
        if unknowns:
            lines.append(f"  UNKNOWNS ({len(unknowns)}) — do not invent answers "
                         f"for these:")
            for u in unknowns[:5]:
                lines.append(f"      ? {u[:130]}")
        return "\n".join(lines)

    @staticmethod
    def _field_text() -> str:
        """What to populate, measured off the corpus rather than declared."""
        try:
            from ..corpus.fields import get_field_contract
            contract = get_field_contract()
        except Exception:  # noqa: BLE001
            return "  (field contract unavailable — populate every field you can)"
        lines = [
            f"  Populate these whenever the portal exposes them "
            f"({contract.corpus_size} production scrapers measured):",
            f"    REQUIRED : {', '.join(contract.required)}",
            f"    EXPECTED : {', '.join(contract.expected)}",
            f"  A corpus scraper populates {contract.min_field_count}–"
            f"{contract.median_field_count} data fields. Extract every field the "
            f"page actually shows; leave a field at its default rather than "
            f"guessing a value.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _tender_rules_text() -> str:
        """State the acceptance test the output will face.

        The generator should be aiming at the same target the validator checks,
        otherwise the two disagree and the repair loop arbitrates by trial.
        """
        from django.conf import settings as s
        score = int(getattr(s, "FACTORY_MIN_TENDER_SCORE", 50))
        ratio = float(getattr(s, "FACTORY_MIN_VALID_ROW_RATIO", 0.5))
        return (
            f"  Each extracted row is scored 0-100 and must reach {score}; at "
            f"least {ratio:.0%} of rows must pass or the scraper is rejected.\n"
            f"  Scores rise with: a descriptive title, a detail URL, an external "
            f"id, a reference number, a contracting entity, dates.\n"
            f"  Scores fall for: an empty or very short title, a title that is "
            f"only digits, navigation labels ('Home', 'Contact'), widget text "
            f"('Most viewed', 'Quick links'), and the same title repeated on "
            f"three or more rows.\n"
            f"  So: select the tender rows only. A selector that also catches "
            f"menu items or promo cards will fail validation even though it "
            f"'returns rows'.")

    @staticmethod
    def _date_rules_text() -> str:
        return (
            "  Parse dates into datetime.date objects, never strings.\n"
            "  Prefer a machine-readable source when one exists (a datetime "
            "attribute, an ISO field in JSON) over the displayed text.\n"
            "  Return None for a date you cannot parse — never today's date, "
            "never a guess. A wrong deadline is worse than a missing one.\n"
            "  If the listing shows no dates at all, that is acceptable: several "
            "production portals publish none.")

    @staticmethod
    def _lessons_text(investigation, plan) -> str:
        """Prior knowledge from portals of the same structural shape.

        Presented as precedent, not instruction. The measured evidence above
        always wins where the two disagree, so a stale lesson cannot override
        what was just observed on this site.
        """
        if investigation is None:
            return ""
        from ..learning import LearningStore
        try:
            store = LearningStore()
            lessons = store.lessons_for(investigation, plan)
            if not lessons:
                return ""
            store.mark_applied(lessons)
            lines = ["", "WHAT THE FACTORY LEARNED FROM SIMILAR PORTALS",
                     "  (precedent only — where this conflicts with the measured "
                     "evidence above, follow the evidence)"]
            for lesson in lessons:
                lines.append(f"  · {lesson.render()}")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 — memory must never block generation
            return ""

    # ── style contract, generated from the live corpus ────────────────────────

    def _invariants_text(self) -> str:
        index = self._get_index()
        if index is None:
            return "  (corpus index unavailable)"
        lines = []
        for code, meta in sorted((index.invariants or {}).items()):
            if not meta.get("holds"):
                continue
            lines.append(f"  {code}. {meta['description']} "
                         f"(true in {meta['hits']}/{meta['total']} cases)")
        return "\n".join(lines) or "  (none measured)"

    def _conventions_text(self) -> str:
        index = self._get_index()
        if index is None:
            return "  (corpus index unavailable)"
        lines = []
        for code, meta in sorted((index.conventions or {}).items()):
            if code.startswith("_"):
                continue
            lines.append(f"  - {meta['description']} "
                         f"({meta['ratio']:.0%} of the corpus)")
        return "\n".join(lines) or "  (none measured)"

    def _console_text(self) -> str:
        index = self._get_index()
        samples = [
            "print(f'\\n{\"_\" * 62}')",
            "print(f'  SOURCE  : {source.name}')",
            "print(f'  SCRAPER : <ClassName>')",
            "print(f'  MODE    : <short description>')",
            "print(f'  URL     : {LIST_URL}')",
            "print(f'  FILTER  : <policy>')",
            "print(f'  Keywords: {rel_count} relevance [{rel_src}]'",
            "      f'  |  {cat_count} category [{cat_src}]')",
            "print(f'{\"_\" * 62}')",
            "print(f'       > Found {len(cards)} tender card(s)')",
            "print(f'  X  [TAG] Error: {exc}')",
            "print(f'\\n  {\"=\" * 58}')",
            "print(f'  TOTAL : {len(results)} <thing>(s)')",
            "print(f'  {\"=\" * 58}\\n')",
        ]
        text = "\n".join(f"  {s}" for s in samples)
        if index is not None and index.separator_widths:
            widths = ", ".join(f'{k}' for k in list(index.separator_widths)[:4])
            text += f"\n  (separator widths used in the corpus: {widths})"
        return text

    def _references_text(self, bundle: RetrievalBundle) -> str:
        blocks = []
        for ref in bundle.references:
            why = "; ".join(ref.reasons[:4]) or "closest match"
            blocks.append(
                f"----- tenders/scrapers/{ref.module}.py  "
                f"({ref.line_count} lines · {ref.family} · chosen because: {why})\n"
                f"{ref.source_code}\n----- end of {ref.module}.py")
        if bundle.dropped_references:
            blocks.append(
                f"(note: {bundle.dropped_references} further reference(s) were "
                "omitted to stay inside the context budget)")
        return "\n\n".join(blocks) or "(no references retrieved)"

    # ── F12 versioning ────────────────────────────────────────────────────────

    def _ensure_version_row(self, prompt: BuiltPrompt) -> None:
        """Record the recipe so generations can be compared across versions."""
        try:
            from ..models import PromptVersion
            PromptVersion.objects.get_or_create(
                version=prompt.version,
                defaults={
                    "label": "dynamic fragment composition",
                    "system_template": SYSTEM_TEMPLATE,
                    "fragment_ids": [f.id for f in frag.ALL_FRAGMENTS],
                    "retrieval_policy": {
                        "reference_count": int(getattr(
                            settings, "FACTORY_REFERENCE_COUNT", 3)),
                        "reference_max": int(getattr(
                            settings, "FACTORY_REFERENCE_MAX", 5)),
                        "token_budget": int(getattr(
                            settings, "FACTORY_PROMPT_TOKEN_BUDGET", 24000)),
                        "html_sample_chars": int(getattr(
                            settings, "FACTORY_HTML_SAMPLE_CHARS", 14000)),
                    },
                    "notes": "Auto-registered on first use.",
                },
            )
        except Exception as exc:  # noqa: BLE001 — never block generation
            logger.debug("[factory] prompt version row skipped: %s", exc)

    def _get_index(self):
        if self._index is not None:
            return self._index
        try:
            from .. import providers
            from ..interfaces import ICorpusIndexer
            self._index = providers.get(ICorpusIndexer).build()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] index unavailable for prompt: %s", exc)
        return self._index
