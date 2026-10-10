"""Tender Validation — are these rows actually tenders?

A scraper that returns fourteen rows has proved nothing. Fourteen navigation
links, fourteen category chips and fourteen "most viewed" entries all look like
a successful extraction from the outside. This stage decides whether the rows
are genuine procurement records, and it runs *before* the Date Engine, AI
classification and installation, so none of those ever work on rubbish.

Every rejected row explains why. A score with no reason attached is not
reviewable, and an unreviewable rejection is indistinguishable from a bug.

The field expectations come from `corpus.fields`, measured off the 73
hand-written scrapers — not from a fixed list. Anything the corpus does not
demand is reported but never fatal, so generated scrapers are judged by the
same standard as the ones already in production.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.conf import settings

from ..corpus.fields import get_field_contract
from ..investigation.contracts import Failure

logger = logging.getLogger("scraper_factory")

#: Vocabulary that marks a row as procurement content. Contributes, never decides.
_TENDER_WORDS = (
    "tender", "bid", "notice", "procurement", "rfq", "rfp", "solicitation",
    "contract", "award", "invitation", "expression of interest", "eoi",
    "prequalification", "quotation", "auction", "framework",
    "مناقصة", "مناقصات", "عطاء", "عطاءات", "منافسة", "منافسات", "إعلان",
    "دعوة", "كراسة", "تأهيل", "عرض", "شراء",
)

#: Engineering vocabulary — AJi's domain. A weaker, additional signal.
_ENGINEERING_WORDS = (
    "construction", "engineering", "design", "supervision", "infrastructure",
    "road", "bridge", "water", "wastewater", "sewage", "building", "electrical",
    "mechanical", "civil", "consultancy", "study", "survey", "maintenance",
    "rehabilitation", "installation", "network", "plant", "station",
    "إنشاء", "تصميم", "إشراف", "بنية", "طريق", "جسر", "مياه", "صرف", "مبنى",
    "كهرب", "ميكانيك", "استشار", "دراسة", "صيانة", "تأهيل", "شبكة", "محطة",
)

#: Text that means the row is a widget, not a tender.
_WIDGET_PHRASES = (
    "popular searches", "most viewed", "most read", "trending", "featured",
    "quick links", "related links", "useful links", "follow us", "site map",
    "sitemap", "contact us", "about us", "privacy policy", "terms of use",
    "all rights reserved", "copyright", "read more", "view all", "show all",
    "load more", "next page", "previous page", "back to top", "search results",
    "no results", "no records", "filter by", "sort by", "showing results",
    "الأكثر مشاهدة", "روابط سريعة", "اتصل بنا", "من نحن", "خريطة الموقع",
    "جميع الحقوق محفوظة", "سياسة الخصوصية", "عرض الكل", "المزيد", "لا توجد",
)

#: Single words that, alone, are a menu label rather than a tender title.
_NAV_WORDS = frozenset({
    "home", "tenders", "awards", "news", "login", "register", "search",
    "contact", "about", "help", "faq", "downloads", "documents", "archive",
    "suppliers", "buyers", "categories", "all", "more", "next", "previous",
    "overview",
    "الرئيسية", "المناقصات", "الأخبار", "دخول", "تسجيل", "بحث", "الكل",
})

#: Phrases that mark an ABOUT/nav page row wherever they appear in a title.
#: Every entry was extracted from a real false-positive PASS in the corpus
#: audit: Bahrain's generated scraper returned "About Overview Vision & Mission
#: Board Members...", PDTRA's returned "نبذة عن السلطة" and "الكلمة الترحيبية",
#: ADFD's returned "Our Leadership".
_ABOUT_PHRASES = (
    "vision & mission", "vision and mission", "board members", "our leadership",
    "organisation structure", "organization structure", "who we are",
    "welcome message", "you can also", "secretary general",
    "نبذة عن", "الكلمة الترحيبية", "رؤيتنا", "رسالتنا", "مجلس الإدارة",
    "مجلس المفوضين", "الهيكل التنظيمي", "عن الهيئة", "عن السلطة",
    "كلمة الرئيس",
)

#: Whole-title lookup values — filter options and tender TYPES that portals
#: render as lists. Real procurement vocabulary, which is exactly why they
#: fooled the scorer, but as a COMPLETE title they are a dropdown, not a
#: notice: Etimad's generated scraper returned rows titled "منافسة عامة" and
#: "شراء مباشر" — the platform's tender-type lookup.
_LOOKUP_TITLES = frozenset({
    "منافسة عامة", "منافسة محدودة", "شراء مباشر", "المنافسات", "منافسات",
    "public tender", "limited tender", "direct purchase", "general competition",
    "request for proposal", "request for quotation",
})

#: Windows-1256 (Arabic) read as latin-1 produces this alphabet. One real row:
#: elan.gov.sy came back as 'ÇáÑÆíÓíÉ' — the nav item "الرئيسية" mojibake'd —
#: and scored as a plausible title. Garbled text means the CHARSET is wrong,
#: and every field from that page is damaged even when it happens to look ok.
_MOJIBAKE_RE = re.compile(r"[ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåçèéêëìíîïðñòóôõö]{3,}")

_URL_RE = re.compile(r"^https?://", re.I)
_MOSTLY_DIGITS = re.compile(r"^[\d\s\-/.:،,]+$")


@dataclass
class RowVerdict:
    """Why one row was accepted or rejected."""

    index: int
    score: int = 0
    valid: bool = False
    title: str = ""
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    fields_present: list[str] = field(default_factory=list)
    fields_missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"index": self.index, "score": self.score, "valid": self.valid,
                "title": self.title[:160], "positives": self.positives,
                "negatives": self.negatives,
                "fields_present": self.fields_present,
                "fields_missing": self.fields_missing}

    def explain(self) -> str:
        head = (f"row {self.index}: {'ACCEPTED' if self.valid else 'REJECTED'} "
                f"({self.score}%) — {self.title[:70] or '(no title)'}")
        lines = [head]
        for n in self.negatives:
            lines.append(f"      - {n}")
        if self.valid:
            for p in self.positives[:3]:
                lines.append(f"      + {p}")
        return "\n".join(lines)


@dataclass
class TenderValidationReport:
    """The verdict on a whole extraction."""

    rows_total: int = 0
    rows_valid: int = 0
    verdicts: list[RowVerdict] = field(default_factory=list)
    stop_category: str = ""
    field_coverage: dict[str, float] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)
    confidence: int = 0
    passed: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def rows_rejected(self) -> int:
        return self.rows_total - self.rows_valid

    @property
    def valid_ratio(self) -> float:
        return self.rows_valid / self.rows_total if self.rows_total else 0.0

    def to_dict(self) -> dict:
        return {
            "rows_total": self.rows_total, "rows_valid": self.rows_valid,
            "rows_rejected": self.rows_rejected,
            "valid_ratio": round(self.valid_ratio, 4),
            "stop_category": self.stop_category,
            "field_coverage": {k: round(v, 4) for k, v in self.field_coverage.items()},
            "missing_required": list(self.missing_required),
            "confidence": self.confidence, "passed": self.passed,
            "notes": list(self.notes),
            "verdicts": [v.to_dict() for v in self.verdicts[:40]],
        }

    def explain(self) -> str:
        lines = [f"TENDER VALIDATION: {self.rows_valid}/{self.rows_total} row(s) "
                 f"are genuine tenders ({self.confidence}% confidence)"]
        if self.stop_category:
            lines.append(f"  STOP: {self.stop_category}")
        if self.missing_required:
            lines.append(f"  required field(s) rarely present: "
                         f"{', '.join(self.missing_required)}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        rejected = [v for v in self.verdicts if not v.valid]
        for v in rejected[:8]:
            lines.append(v.explain())
        if len(rejected) > 8:
            lines.append(f"      … and {len(rejected) - 8} more rejected row(s)")
        return "\n".join(lines)


class TenderValidator:
    """Scores extracted rows and decides whether the extraction is usable."""

    def __init__(self, *, min_score: int | None = None,
                 min_valid_ratio: float | None = None, contract=None):
        self.min_score = int(
            min_score if min_score is not None
            else getattr(settings, "FACTORY_MIN_TENDER_SCORE", 50))
        self.min_valid_ratio = float(
            min_valid_ratio if min_valid_ratio is not None
            else getattr(settings, "FACTORY_MIN_VALID_ROW_RATIO", 0.5))
        self._contract = contract

    # ── public API ────────────────────────────────────────────────────────────

    def validate(self, rows: list[dict]) -> TenderValidationReport:
        report = TenderValidationReport(rows_total=len(rows))
        contract = self._contract or get_field_contract()

        if not rows:
            report.stop_category = Failure.EMPTY_LISTING
            report.notes.append("the scraper returned no rows at all")
            return report

        titles = [self._text(r.get("title")) for r in rows]
        duplicate_titles = self._duplicates(titles)

        for i, row in enumerate(rows):
            report.verdicts.append(
                self._score_row(i, row, contract, duplicate_titles))

        report.rows_valid = sum(1 for v in report.verdicts if v.valid)
        report.field_coverage = self._coverage(rows, contract)
        report.missing_required = [
            name for name in contract.required
            if report.field_coverage.get(name, 0.0) < 0.5]

        if report.rows_valid == 0:
            report.stop_category = self._stop_category(report, rows)
            report.confidence = 0
            return report

        # Confidence in the extraction as a whole: how many rows survived, and
        # how convincing the survivors were.
        surviving = [v.score for v in report.verdicts if v.valid]
        avg = sum(surviving) / len(surviving)
        report.confidence = int(min(99, avg * report.valid_ratio))

        # Two different questions, and only one of them may block a build.
        #
        # "Are these rows tenders?" is a gate: if they are menus, nothing
        # downstream is worth doing.
        #
        # "Did every corpus-expected field get populated?" is NOT a gate. The
        # REQUIRED set describes what the hand-written scrapers *attempt*, not
        # what every portal *publishes* — the same distinction that made the
        # date penalty wrong. The Bahrain Tender Board publishes no dates and no
        # entity across 648 live rows; a portal with no publication-date column
        # would be permanently unbuildable if absence were fatal.
        #
        # What is non-negotiable is the identity of a tender: a row with no title
        # or no link is unusable no matter what else it carries, and both are
        # present in 100% of the corpus.
        core = [f for f in ("title", "source_url")
                if f in contract.required
                and report.field_coverage.get(f, 0.0) < 0.5]
        report.passed = (report.valid_ratio >= self.min_valid_ratio
                         and not core)

        # Category lists masquerading as tenders. Found by manual testing, not
        # by any gate: a generated tenderjo scraper picked the API's categories
        # array — "computer networks and supplies", "chemical industries" — and
        # every row sailed through, because a category label has a title and a
        # URL, and absent dates are deliberately not penalised (Bahrain).
        # The signature that separates a category list from every real listing:
        # ALL titles are stubs (median ≤ 4 words) AND no row anywhere carries a
        # date AND none carries a reference. Measured against all 72 live
        # production sources before being trusted: zero would be flagged —
        # Bahrain's rows have no dates but long titles, so it stays safe.
        # Navigation/lookup saturation. Individual nav rows already score
        # negative, but a page whose "listing" IS the navigation can still
        # clear the ratio when enough rows carry URLs and plausible lengths —
        # Bahrain's about-menu and Etimad's tender-type dropdown both did. When
        # two rows in five are nav labels, about-page phrases, lookup values or
        # mojibake, the selector is pointed at the wrong element, full stop.
        if report.passed and report.rows_total >= 3:
            markers = ("navigation", "about-page", "lookup value", "mojibake")
            tainted = sum(
                1 for v in report.verdicts
                if any(any(m in n for m in markers) for n in v.negatives))
            if tainted >= max(2, int(report.rows_total * 0.4)):
                report.passed = False
                report.stop_category = "NON_TENDER_ELEMENTS_SELECTED"
                report.notes.append(
                    f"{tainted} of {report.rows_total} rows are navigation, "
                    f"about-page content, lookup values or mojibake — the "
                    f"selector is reading site chrome, not the tender listing")

        if report.passed and report.rows_total >= 5:
            import statistics as st
            titles = [str(r.get("title") or "") for r in rows]
            med_words = st.median(len(t.split()) for t in titles)
            any_date = any(r.get("publication_date") or r.get("submission_deadline")
                           for r in rows)
            any_ref = any(str(r.get("reference_no") or "").strip() for r in rows)
            if med_words <= 4 and not any_date and not any_ref:
                report.passed = False
                report.stop_category = "NON_TENDER_ELEMENTS_SELECTED"
                report.notes.append(
                    f"every row looks like a category label (median title length "
                    f"{med_words:.0f} word(s), no dates, no references anywhere) "
                    f"— the scraper has probably selected a category or "
                    f"navigation list, not the tender listing")

        if report.valid_ratio < self.min_valid_ratio:
            report.notes.append(
                f"only {report.valid_ratio:.0%} of rows are tenders (floor is "
                f"{self.min_valid_ratio:.0%}) — the row selector is probably "
                f"catching non-tender elements too")
        if core:
            report.notes.append(
                f"{', '.join(core)} missing from most rows — a tender without "
                f"these cannot be used, so this extraction is rejected")
        if report.missing_required:
            report.notes.append(
                f"reported, not fatal: {', '.join(report.missing_required)} "
                f"absent from most rows. Confirm the portal publishes them "
                f"before treating this as a defect")

        # Absence of dates does not reject a row, but a whole extraction with
        # no dates anywhere is worth a developer's attention: either the portal
        # genuinely omits them, or the date selectors are wrong.
        dated = sum(1 for r in rows
                    if self._as_date(r.get("submission_deadline"))
                    or self._as_date(r.get("publication_date")))
        if dated == 0:
            report.notes.append(
                "no row carries a deadline or publication date — confirm the "
                "portal really omits them before relying on this source")
        report.field_coverage["_any_date"] = dated / len(rows)
        return report

    # ── per-row scoring ───────────────────────────────────────────────────────

    def _score_row(self, index: int, row: dict, contract,
                   duplicate_titles: set[str]) -> RowVerdict:
        verdict = RowVerdict(index=index)
        title = self._text(row.get("title"))
        verdict.title = title
        low = title.lower()
        score = 0

        # ── title: the single most decisive field ────────────────────────────
        if not title:
            verdict.negatives.append("no title")
            score -= 40
        else:
            if len(title) >= 25:
                score += 30
                verdict.positives.append(f"descriptive title ({len(title)} chars)")
            elif len(title) >= 12:
                score += 18
                verdict.positives.append(f"short but plausible title ({len(title)} chars)")
            else:
                score -= 25
                verdict.negatives.append(
                    f"title is only {len(title)} characters — too short to be a "
                    f"procurement notice")

            if low in _NAV_WORDS:
                score -= 45
                verdict.negatives.append(
                    f"title '{title}' is a navigation label, not a tender")
            if any(p in low for p in _ABOUT_PHRASES):
                score -= 45
                verdict.negatives.append(
                    f"title '{title[:40]}' is about-page or navigation content")
            if low.strip() in _LOOKUP_TITLES:
                score -= 45
                verdict.negatives.append(
                    f"title '{title}' is a lookup value (a filter option or "
                    f"tender type), not a notice")
            if _MOJIBAKE_RE.search(title):
                score -= 45
                verdict.negatives.append(
                    "title is mojibake — the page charset was misread (Arabic "
                    "windows-1256 decoded as latin-1), so every field from "
                    "this page is damaged")
            if _MOSTLY_DIGITS.match(title):
                score -= 30
                verdict.negatives.append("title is only numbers or dates")
            widget = next((p for p in _WIDGET_PHRASES if p in low), "")
            if widget:
                score -= 40
                verdict.negatives.append(
                    f"title contains widget text '{widget}'")
            if title in duplicate_titles:
                score -= 30
                verdict.negatives.append(
                    "identical title repeated across rows — a template or "
                    "placeholder, not distinct tenders")
            ref = str(row.get("reference_no") or "").strip()
            if ref and ref == title:
                # Bahrain's generated scraper filled reference_no with the
                # title itself — "Vision & Mission" as a reference number. A
                # copied field is fabricated evidence, not a reference.
                score -= 30
                verdict.negatives.append(
                    "reference_no is a copy of the title — fabricated, not a "
                    "real reference")

        # ── vocabulary: contributes, never decides ───────────────────────────
        haystack = " ".join(str(row.get(k) or "") for k in
                            ("title", "description", "entity", "reference_no")).lower()
        if any(w in haystack for w in _TENDER_WORDS):
            score += 12
            verdict.positives.append("procurement terminology present")
        if any(w in haystack for w in _ENGINEERING_WORDS):
            score += 6
            verdict.positives.append("engineering terminology present")

        # ── identity and linkage ─────────────────────────────────────────────
        url = self._text(row.get("source_url"))
        if url and _URL_RE.match(url):
            score += 15
            verdict.positives.append("links to a detail page")
        elif url:
            score += 6
            verdict.positives.append("has a relative detail link")
        else:
            score -= 12
            verdict.negatives.append("no detail URL")

        if self._text(row.get("external_id")):
            score += 10
            verdict.positives.append("has an external id")
        if self._text(row.get("reference_no")):
            score += 10
            verdict.positives.append("has a reference number")
        if self._text(row.get("entity")):
            score += 10
            verdict.positives.append("names a contracting entity")

        # ── dates ────────────────────────────────────────────────────────────
        deadline = self._as_date(row.get("submission_deadline"))
        published = self._as_date(row.get("publication_date"))
        if deadline:
            score += 15
            verdict.positives.append("has a submission deadline")
        if published:
            score += 8
            verdict.positives.append("has a publication date")
        # Deliberately no penalty for missing dates. Measured against live data:
        # the Bahrain Tender Board scraper has produced 648 rows and not one
        # carries a date, because that listing does not publish them. Those are
        # genuine tenders, so deducting for absent dates would reject rows the
        # production system has been serving for months. Dates earn credit;
        # their absence is reported at the extraction level instead.

        if self._text(row.get("description")):
            score += 5

        # ── field bookkeeping against the measured contract ──────────────────
        for name in contract.required:
            if self._present(row.get(name)):
                verdict.fields_present.append(name)
            else:
                verdict.fields_missing.append(name)

        verdict.score = max(0, min(100, score))
        verdict.valid = verdict.score >= self.min_score
        if not verdict.valid and not verdict.negatives:
            verdict.negatives.append(
                f"score {verdict.score} is below the {self.min_score} threshold "
                f"but no single disqualifying signal was found")
        return verdict

    # ── aggregate helpers ─────────────────────────────────────────────────────

    def _coverage(self, rows: list[dict], contract) -> dict[str, float]:
        out: dict[str, float] = {}
        total = len(rows) or 1
        for name in list(contract.required) + list(contract.expected):
            out[name] = sum(1 for r in rows if self._present(r.get(name))) / total
        return out

    def _stop_category(self, report: TenderValidationReport,
                       rows: list[dict]) -> str:
        """Name the reason nothing survived, from the rejection evidence."""
        reasons = [n for v in report.verdicts for n in v.negatives]
        joined = " ".join(reasons).lower()

        if all(not self._text(r.get("title")) for r in rows):
            return Failure.EMPTY_LISTING
        if "navigation label" in joined or "widget text" in joined:
            return Failure.NON_TENDER_ELEMENTS_SELECTED
        if "too short" in joined and "no detail URL" in joined:
            return Failure.LANDING_PAGE_CONTENT
        if "identical title repeated" in joined:
            return Failure.INVALID_ROW_SELECTION
        return Failure.INVALID_ROW_SELECTION

    @staticmethod
    def _duplicates(titles: list[str]) -> set[str]:
        seen: dict[str, int] = {}
        for t in titles:
            if t:
                seen[t] = seen.get(t, 0) + 1
        # Two rows sharing a title can be coincidence; three is a template.
        return {t for t, n in seen.items() if n >= 3}

    @staticmethod
    def _text(value) -> str:
        return str(value).strip() if value not in (None, "") else ""

    @staticmethod
    def _present(value) -> bool:
        if value in (None, "", [], {}):
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    @staticmethod
    def _as_date(value):
        if isinstance(value, (date, datetime)):
            return value
        text = str(value or "").strip()
        if not text or text.lower() in ("none", "null"):
            return None
        # Sandbox rows arrive JSON-serialised, so a date is a string by then.
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text[:19], fmt).date()
            except ValueError:
                continue
        return None
