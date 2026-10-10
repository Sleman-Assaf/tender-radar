"""Factory Memory — what worked, what failed, and what to do next time.

The unit of memory is the *structural signature*, never the URL. A lesson keyed
to `portal.gov.jo` helps exactly one portal; the same lesson keyed to
"ASP.NET + server-rendered + postback paging + no API" helps every portal built
that way, which is the difference between a memory and a hack.

Two things are stored:

  * PATTERNS — "sites shaped like this are best scraped like that", written on
    success. Retrieved before generation and injected into the prompt.
  * REPAIRS  — "when this subsystem failed this way, this repair fixed it",
    written when a repair succeeds. Retrieved during diagnosis so a known fix is
    suggested before the loop rediscovers it.

Nothing here is website-specific, and nothing overrides measured evidence: a
lesson is a prior, and the investigation always wins where they disagree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.conf import settings

logger = logging.getLogger("scraper_factory")


@dataclass
class Lesson:
    """One retrieved memory, ready to be rendered into a prompt."""

    kind: str
    signature: str
    family: str
    text: str
    times_applied: int = 0
    confidence: int = 0

    def render(self) -> str:
        suffix = (f" (applied {self.times_applied}x)"
                  if self.times_applied else "")
        return f"{self.text}{suffix}"


class LearningStore:
    """Reads and writes architectural lessons keyed by structural signature."""

    def __init__(self, *, enabled: bool | None = None, limit: int | None = None):
        self.enabled = bool(
            enabled if enabled is not None
            else getattr(settings, "FACTORY_LEARNING_ENABLED", True))
        self.limit = int(limit or getattr(settings, "FACTORY_LEARNING_LIMIT", 5))

    # ── writing ───────────────────────────────────────────────────────────────

    def record_success(self, *, investigation, plan, scraper=None,
                       tender_report=None, rows_extracted: int = 0) -> None:
        """Remember a shape that generated cleanly, so the next one is easier."""
        if not self.enabled or investigation is None or plan is None:
            return
        text = self._success_text(investigation, plan, rows_extracted)
        self._write(kind="PATTERN", signature=investigation.site_signature,
                    family=plan.family, framework=self._framework(investigation),
                    text=text, scraper=scraper)

    def record_failure(self, *, investigation, diagnosis, scraper=None) -> None:
        """Remember a failure only when the diagnosis is confident.

        A guess written to memory is worse than no memory: it will be retrieved
        with the same authority as a measured fact and quietly steer future
        generations wrong.
        """
        if not self.enabled or diagnosis is None or diagnosis.root_cause is None:
            return
        if diagnosis.root_cause.confidence < 70:
            logger.debug("[factory] not learning from a %s%% diagnosis",
                         diagnosis.root_cause.confidence)
            return
        signature = getattr(investigation, "site_signature", "") if investigation else ""
        text = (f"{diagnosis.subsystem} failed as {diagnosis.category}. "
                f"{diagnosis.suggestions[0] if diagnosis.suggestions else ''}").strip()
        self._write(kind="FAILURE", signature=signature,
                    family=self._family(investigation),
                    framework=self._framework(investigation),
                    text=text, scraper=scraper)

    def record_repair(self, *, investigation, diagnosis, worked: bool,
                      scraper=None) -> None:
        """Remember which repair actually resolved which diagnosis."""
        if not self.enabled or diagnosis is None or diagnosis.root_cause is None:
            return
        verdict = "fixed" if worked else "did not fix"
        text = (f"{diagnosis.repair_strategy} {verdict} "
                f"{diagnosis.subsystem}/{diagnosis.category}")
        self._write(kind="REPAIR", signature=getattr(
                        investigation, "site_signature", "") if investigation else "",
                    family=self._family(investigation),
                    framework=self._framework(investigation),
                    text=text, scraper=scraper)

    # ── reading ───────────────────────────────────────────────────────────────

    def lessons_for(self, investigation, plan=None) -> list[Lesson]:
        """Prior knowledge for a site of this shape, most-proven first.

        Exact signature matches come first because they describe the same
        structure; family matches follow as weaker, more general advice.
        """
        if not self.enabled or investigation is None:
            return []
        from ..models import LearningEntry

        signature = investigation.site_signature
        family = plan.family if plan is not None else self._family(investigation)
        out: list[Lesson] = []
        seen: set[str] = set()

        try:
            exact = (LearningEntry.objects
                     .filter(is_active=True, site_signature=signature)
                     .exclude(lesson="")
                     .order_by("-times_applied", "-created_at")[:self.limit])
            for row in exact:
                if row.lesson in seen:
                    continue
                seen.add(row.lesson)
                out.append(Lesson(kind=row.kind, signature=row.site_signature,
                                  family=row.family, text=row.lesson,
                                  times_applied=row.times_applied, confidence=90))

            if len(out) < self.limit and family:
                general = (LearningEntry.objects
                           .filter(is_active=True, family=family)
                           .exclude(site_signature=signature)
                           .exclude(lesson="")
                           .order_by("-times_applied", "-created_at")
                           [:self.limit - len(out)])
                for row in general:
                    if row.lesson in seen:
                        continue
                    seen.add(row.lesson)
                    out.append(Lesson(kind=row.kind, signature=row.site_signature,
                                      family=row.family, text=row.lesson,
                                      times_applied=row.times_applied,
                                      confidence=60))
        except Exception as exc:  # noqa: BLE001 — memory must never break a build
            logger.warning("[factory] could not read lessons: %s", exc)
        return out

    def known_repair_for(self, diagnosis, investigation=None) -> str:
        """A repair that previously fixed this exact diagnosis, if any."""
        if not self.enabled or diagnosis is None or diagnosis.root_cause is None:
            return ""
        from ..models import LearningEntry

        needle = f"{diagnosis.subsystem}/{diagnosis.category}"
        try:
            # Must match what _kind_choice() actually writes, not the internal
            # label passed to record_repair().
            row = (LearningEntry.objects
                   .filter(is_active=True, kind=LearningEntry.Kind.REPAIR_FIX,
                           lesson__contains=needle)
                   .exclude(lesson__contains="did not fix")
                   .order_by("-times_applied")
                   .first())
            return row.lesson if row else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] could not read repair memory: %s", exc)
            return ""

    def mark_applied(self, lessons: list[Lesson]) -> None:
        """Count a lesson as used, so proven advice outranks untested advice."""
        if not self.enabled or not lessons:
            return
        from django.db.models import F

        from ..models import LearningEntry
        try:
            LearningEntry.objects.filter(
                lesson__in=[l.text for l in lessons]
            ).update(times_applied=F("times_applied") + 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] could not count lesson use: %s", exc)

    # ── internals ─────────────────────────────────────────────────────────────

    def _write(self, *, kind: str, signature: str, family: str, framework: str,
               text: str, scraper=None) -> None:
        if not text or not signature:
            return
        from ..models import LearningEntry
        try:
            # get_or_create keyed on the lesson text so repetition strengthens an
            # existing memory instead of filling the table with duplicates.
            row, created = LearningEntry.objects.get_or_create(
                site_signature=signature, lesson=text[:2000],
                defaults={"kind": self._kind_choice(kind), "family": family[:20],
                          "framework": framework[:20], "scraper": scraper,
                          "is_active": True})
            if not created:
                from django.db.models import F
                LearningEntry.objects.filter(pk=row.pk).update(
                    times_applied=F("times_applied") + 1)
            logger.info("[factory] learned (%s): %s", kind, text[:120])
        except Exception as exc:  # noqa: BLE001 — never fail a build over memory
            logger.warning("[factory] could not store lesson: %s", exc)

    @staticmethod
    def _kind_choice(kind: str) -> str:
        """Map onto the existing LearningEntry.Kind choices (additive-only rule).

        The model predates this module and its choices cannot be extended
        without a migration that alters an existing table, which is forbidden.
        REVIEW_NOTE is the honest fit for machine-derived observations.
        """
        from ..models import LearningEntry
        return {"REPAIR": LearningEntry.Kind.REPAIR_FIX}.get(
            kind, LearningEntry.Kind.REVIEW_NOTE)

    @staticmethod
    def _success_text(investigation, plan, rows: int) -> str:
        from ..investigation.contracts import Stage

        tech = investigation.stage(Stage.TECHNOLOGY).conclusion
        pagination = investigation.stage(Stage.PAGINATION).conclusion
        bits = [f"{tech} portals serving {investigation.data_source}"]
        if investigation.tier == 2:
            bits.append("(request captured with a browser, then called directly)")
        bits.append(f"are best scraped with the {plan.strategy} strategy")
        if pagination and pagination != "NONE":
            bits.append(f"paging via {pagination}")
        client = (plan.directives or {}).get("http_client", "requests")
        if client != "requests":
            bits.append(f"fetching with {client}")
        if rows:
            bits.append(f"— produced {rows} tender(s) on the first run")
        return " ".join(bits)

    @staticmethod
    def _family(investigation) -> str:
        if investigation is None:
            return ""
        return {"API": "JSON_API", "GRAPHQL": "JSON_API",
                "JS_STATE": "JSON_API"}.get(investigation.data_source,
                                            "HTML_CUSTOM")

    @staticmethod
    def _framework(investigation) -> str:
        if investigation is None:
            return ""
        from ..investigation.contracts import Stage
        return investigation.stage(Stage.TECHNOLOGY).conclusion or ""
