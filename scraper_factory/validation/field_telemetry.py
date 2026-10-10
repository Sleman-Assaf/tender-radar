"""Per-field and per-date extraction telemetry.

The diagnostic engine has always had FIELD_EXTRACTION and DATE_EXTRACTION in its
subsystem order, with repair strategies mapped to both — but nothing ever
produced a finding for either, because "did the fields come out?" was never
measured. Any build whose rows were valid but whose fields were empty had to be
attributed to something else, and the closest available answer was the row
selector, which is usually innocent.

This module measures it. Two questions, answered separately because they have
different repairs:

    fields  did each field get a value, and does that value look like the thing
            it claims to be?
    dates   were date strings parsed into dates, or silently dropped?

The distinction matters. A deadline column that was found but not parsed is a
date-parsing bug (`REPAIR_DATE_PARSING`); a deadline column that was never read
is a field-mapping bug (`REPAIR_FIELD_MAPPING`). Conflating them sends a
developer to the wrong half of the file.

Absence is never assumed to be a defect. Which fields may legitimately be absent
was measured, not guessed — 72 live sources, 40 most recent rows each:

    field                  min   median   mean   sources <50%
    title                  85%     100%   100%              0
    source_url            100%     100%   100%              0
    external_id           100%     100%   100%              0
    reference_no            0%     100%    87%              9
    entity                  0%     100%    90%              7
    description             0%     100%    82%             13
    publication_date        0%     100%    74%             19
    submission_deadline     0%     100%    70%             21

The first three never fail anywhere, so their absence is a defect. The other five
are absent on up to 21 healthy production sources, so their absence is a fact
about the portal. This distinction is the whole design: an earlier version of
this module treated the corpus's eight "required" fields as all mandatory and
flagged 45 of 72 live sources — a 62% false-positive rate, which would have
trained everyone to ignore the alert. The corpus's required set means "the
scraper assigns this field", not "every portal publishes it".

The Bahrain Tender Board publishes no dates at all across 648 live rows, so "no
dates" is reported as a fact about the portal and only becomes a suspicion when
the listing markup shows dates the rows do not carry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime

logger = logging.getLogger("scraper_factory")

#: A date-looking substring, in the formats the corpus actually meets.
#:
#: The components are range-checked rather than matched as bare digit runs. A
#: loose \d{1,4}[/.-]\d{1,2}[/.-]\d{1,4} matched "11/0.433" — the voltage rating
#: in a live Kuwaiti PAHW tender title (11/0.433 kV) — and would have reported a
#: date-handling defect against a healthy source.
_DATE_TEXT = re.compile(
    r"\b\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b"          # ISO
    r"|\b(?:0?[1-9]|[12]\d|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.]\d{2,4}\b"  # d/m/y
    r"|\b(?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])[/\-.]\d{2,4}\b"  # m/d/y
    r"|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"[a-z]*\.?\s+\d{2,4}\b", re.I)

#: Fields whose value should look like a URL, an id, or free text. Used to catch
#: a mapping that populated the right key from the wrong column.
_SHAPES = {
    "source_url": re.compile(r"^(https?://|/)", re.I),
    "external_id": re.compile(r"\S"),
    "reference_no": re.compile(r"\S"),
}


@dataclass
class FieldStat:
    name: str
    present: int = 0
    total: int = 0
    malformed: int = 0
    examples: list[str] = dc_field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.present / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {"name": self.name, "present": self.present, "total": self.total,
                "coverage": round(self.coverage, 4),
                "malformed": self.malformed, "examples": self.examples[:3]}


@dataclass
class FieldReport:
    """What came out of the rows, field by field."""

    rows: int = 0
    fields: dict[str, FieldStat] = dc_field(default_factory=dict)

    #: Core fields absent from most rows. A real defect — see module docstring.
    missing_core: list[str] = dc_field(default_factory=list)
    #: Portal-dependent fields with low coverage. Observed, never a failure.
    sparse_fields: list[str] = dc_field(default_factory=list)
    malformed_fields: list[str] = dc_field(default_factory=list)
    #: Rows where every field except the core three is empty.
    bare_rows: int = 0

    dates_expected: bool = False
    dates_parsed: int = 0
    dates_unparsed: int = 0
    date_strings_seen: list[str] = dc_field(default_factory=list)
    #: Rows whose free text contains a date while the date fields are empty.
    dates_only_in_text: int = 0

    notes: list[str] = dc_field(default_factory=list)

    @property
    def date_parse_rate(self) -> float:
        attempted = self.dates_parsed + self.dates_unparsed
        return self.dates_parsed / attempted if attempted else 0.0

    @property
    def field_extraction_failed(self) -> bool:
        """Rows exist but the extraction demonstrably did not work.

        Two ways to be sure, both measured against production rather than
        assumed. A core field missing (never happens on any healthy source), or
        rows carrying nothing but the core three — a bare link list, which means
        the per-field mapping never ran. Neither occurs anywhere in the live
        corpus, so neither produces a false positive.
        """
        if not self.rows:
            return False
        return bool(self.missing_core) or self.bare_rows >= self.rows * 0.9

    @property
    def date_extraction_failed(self) -> bool:
        """Date text was visible in the rows and did not become dates.

        Requires evidence that dates were there to be had. A portal that
        publishes none is not failing.
        """
        return bool(self.dates_expected and self.dates_unparsed
                    and self.date_parse_rate < 0.5)

    @property
    def dates_never_read(self) -> bool:
        """The portal publishes dates and no row carries one.

        Distinct from `date_extraction_failed`: nothing reached the date fields at
        all, so the parser is not the culprit — the mapping is. Requires the date
        text in most rows, because one row in forty is noise: a live Kenyan KeNHA
        title contains "15.09.25" inside a procurement reference, and a single
        such row must not indict a healthy source.
        """
        return bool(self.rows
                    and self.dates_parsed == 0
                    and self.dates_unparsed == 0
                    and self.dates_only_in_text >= self.rows * 0.5)

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "missing_core": list(self.missing_core),
            "sparse_fields": list(self.sparse_fields),
            "malformed_fields": list(self.malformed_fields),
            "bare_rows": self.bare_rows,
            "dates_expected": self.dates_expected,
            "dates_parsed": self.dates_parsed,
            "dates_unparsed": self.dates_unparsed,
            "date_parse_rate": round(self.date_parse_rate, 4),
            "date_strings_seen": self.date_strings_seen[:5],
            "field_extraction_failed": self.field_extraction_failed,
            "date_extraction_failed": self.date_extraction_failed,
            "notes": list(self.notes),
        }

    def explain(self) -> str:
        lines = [f"FIELD TELEMETRY over {self.rows} row(s):"]
        for stat in sorted(self.fields.values(), key=lambda s: s.coverage):
            flag = ""
            if stat.name in self.missing_core:
                flag = "  <- CORE, never empty on a healthy source"
            elif stat.malformed:
                flag = f"  <- {stat.malformed} malformed"
            elif stat.name in self.sparse_fields:
                flag = "  <- sparse (normal for many portals)"
            lines.append(f"  {stat.name:<22} {stat.coverage:>6.0%} "
                         f"({stat.present}/{stat.total}){flag}")
        if self.dates_expected or self.dates_parsed or self.dates_unparsed:
            lines.append(f"  dates: {self.dates_parsed} parsed, "
                         f"{self.dates_unparsed} unparsed "
                         f"({self.date_parse_rate:.0%})")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


class FieldTelemetry:
    """Measures field and date extraction from the rows a scraper produced."""

    #: Below this share of rows, a field counts as absent rather than patchy.
    MIN_COVERAGE = 0.5

    #: The fields that hold at 100% across all 72 live sources. Their absence is
    #: a defect; every other field's absence is a property of the portal.
    CORE = ("title", "source_url", "external_id")

    def measure(self, rows: list[dict], *, listing_sample: str = "",
                contract=None) -> FieldReport:
        report = FieldReport(rows=len(rows))
        if not rows:
            report.notes.append("no rows to measure")
            return report

        if contract is None:
            from ..corpus.fields import get_field_contract
            contract = get_field_contract()

        interesting = (list(contract.required) + list(contract.expected))
        for name in interesting:
            report.fields[name] = FieldStat(name=name, total=len(rows))

        for row in rows:
            for name in interesting:
                stat = report.fields[name]
                value = row.get(name)
                if not self._present(value):
                    continue
                stat.present += 1
                if len(stat.examples) < 3:
                    stat.examples.append(str(value)[:80])
                shape = _SHAPES.get(name)
                if shape is not None and not shape.search(str(value)):
                    stat.malformed += 1

        report.missing_core = [
            name for name in self.CORE
            if name in report.fields
            and report.fields[name].coverage < self.MIN_COVERAGE]
        report.sparse_fields = [
            s.name for s in report.fields.values()
            if s.name not in self.CORE and s.coverage < self.MIN_COVERAGE]
        report.malformed_fields = [
            s.name for s in report.fields.values()
            if s.present and s.malformed / s.present > 0.5]

        # A row carrying only the core three is a bare link: the row selector
        # worked and the per-field mapping did not. No live source produces one,
        # so this is a clean signal.
        non_core = [n for n in interesting if n not in self.CORE]
        if non_core:
            report.bare_rows = sum(
                1 for row in rows
                if not any(self._present(row.get(n)) for n in non_core))

        self._measure_dates(rows, report, listing_sample)

        if report.malformed_fields:
            report.notes.append(
                f"{', '.join(report.malformed_fields)} carry values of the wrong "
                f"shape — the field mapping may be reading the wrong column")
        if report.sparse_fields and not report.missing_core:
            report.notes.append(
                f"{', '.join(report.sparse_fields)} sparse or absent — normal for "
                f"many portals, not treated as a failure")
        return report

    # ── dates ─────────────────────────────────────────────────────────────────

    def _measure_dates(self, rows: list[dict], report: FieldReport,
                       listing_sample: str) -> None:
        """Separate "not published" from "published but not parsed"."""
        date_fields = ("submission_deadline", "publication_date", "opening_date")

        for row in rows:
            for name in date_fields:
                value = row.get(name)
                if isinstance(value, (date, datetime)):
                    report.dates_parsed += 1
                elif isinstance(value, str) and value.strip():
                    # A string in a date field means the scraper captured text
                    # and never converted it. The corpus stores date objects.
                    if self._parses(value):
                        report.dates_parsed += 1
                    else:
                        report.dates_unparsed += 1
                        if len(report.date_strings_seen) < 5:
                            report.date_strings_seen.append(value[:40])

        # Was there date text in the rows at all? If a row's free text contains
        # a date but the date fields are empty, the portal does publish dates and
        # the scraper is not reading them.
        text_dates = 0
        for row in rows:
            blob = " ".join(str(row.get(k) or "") for k in
                            ("title", "description", "entity", "reference_no"))
            if _DATE_TEXT.search(blob):
                text_dates += 1

        report.dates_only_in_text = text_dates
        sample_dates = bool(listing_sample and _DATE_TEXT.search(listing_sample))
        report.dates_expected = bool(report.dates_parsed or report.dates_unparsed
                                     or text_dates or sample_dates)

        if not report.dates_expected:
            report.notes.append(
                "no dates anywhere in the rows or the listing markup — this "
                "portal appears not to publish them, which several production "
                "sources also do")
            return

        if report.dates_parsed == 0 and (text_dates or sample_dates):
            report.notes.append(
                "the listing shows dates but no row carries one — the date "
                "fields are probably not being read at all")
        elif report.dates_unparsed:
            report.notes.append(
                f"{report.dates_unparsed} date value(s) stayed as text, e.g. "
                f"{report.date_strings_seen[:2]} — the parser does not handle "
                f"this format")

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _present(value) -> bool:
        if value in (None, "", [], {}):
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    @staticmethod
    def _parses(text: str) -> bool:
        raw = str(text).strip()[:19]
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y",
                    "%m/%d/%Y", "%d.%m.%Y"):
            try:
                datetime.strptime(raw, fmt)
                return True
            except ValueError:
                continue
        return False
