"""Derives the acceptable TenderData field set from the production corpus.

The v2.0 prompt lists nine required fields. The repository disagrees: real
portals do not all publish a reference number, a buyer and a publication date,
and the hand-written scrapers reflect that. Enforcing the doc's list literally
would reject generated scrapers that are indistinguishable from ones running in
production today.

So the corpus is the specification here, exactly as it already is for style.
This module measures which fields the 73 hand-written scrapers actually set, and
turns that into two things:

  * REQUIRED  — fields set by (almost) every scraper. A generated scraper that
                omits one of these is doing something the corpus never does.
  * EXPECTED  — fields common enough that their absence is worth reporting, but
                not a rejection.

Everything else stays opportunistic: extract it whenever it can be found, which
is the "maximise extraction quality" half of the instruction.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("scraper_factory")

#: Fields that carry no portal data — never part of a quality judgement.
_NON_DATA_FIELDS = frozenset({
    "country_code",       # supplied by the pipeline, not scraped
    "sort_order",         # presentation hint
    "force_relevant",     # a filter override
    "category",           # defaulted to OTHER, usually set by AI later
    "status",             # defaulted to UNKNOWN
})

#: A field is REQUIRED when at least this share of the corpus sets it.
REQUIRED_RATIO = 0.90
#: EXPECTED when at least this share sets it.
EXPECTED_RATIO = 0.40


@dataclass
class FieldProfile:
    """What the corpus does with one TenderData field."""

    name: str
    scrapers_setting: int = 0
    total_scrapers: int = 0
    modules: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.scrapers_setting / self.total_scrapers
                if self.total_scrapers else 0.0)

    def to_dict(self) -> dict:
        return {"name": self.name, "count": self.scrapers_setting,
                "total": self.total_scrapers, "ratio": round(self.ratio, 4)}


@dataclass
class FieldContract:
    """The measured field expectations a generated scraper is judged against."""

    required: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    profiles: dict[str, FieldProfile] = field(default_factory=dict)
    corpus_size: int = 0
    #: Modules that inherit their extraction from another scraper and never
    #: build a TenderData themselves. Excluded from the ratios: they express no
    #: field choice of their own, and counting them would understate every field.
    inheriting_modules: list[str] = field(default_factory=list)
    #: Median count of data fields a corpus scraper populates.
    median_field_count: int = 0
    #: Lowest count any single corpus scraper populates — the honest floor.
    min_field_count: int = 0

    def to_dict(self) -> dict:
        return {
            "required": list(self.required),
            "expected": list(self.expected),
            "optional": list(self.optional),
            "corpus_size": self.corpus_size,
            "median_field_count": self.median_field_count,
            "min_field_count": self.min_field_count,
            "profiles": {k: v.to_dict() for k, v in self.profiles.items()},
        }

    def describe(self) -> str:
        lines = [f"Field contract derived from {self.corpus_size} production "
                 f"scraper(s):"]
        lines.append(f"  REQUIRED ({len(self.required)}): "
                     f"{', '.join(self.required) or 'none'}")
        for name in self.required:
            p = self.profiles[name]
            lines.append(f"      {name:<20} {p.ratio:.0%} ({p.scrapers_setting}/{p.total_scrapers})")
        lines.append(f"  EXPECTED ({len(self.expected)}): "
                     f"{', '.join(self.expected) or 'none'}")
        for name in self.expected:
            p = self.profiles[name]
            lines.append(f"      {name:<20} {p.ratio:.0%} ({p.scrapers_setting}/{p.total_scrapers})")
        lines.append(f"  OPTIONAL ({len(self.optional)}): "
                     f"{', '.join(self.optional) or 'none'}")
        lines.append(f"  a corpus scraper populates {self.min_field_count}–"
                     f"{self.median_field_count}+ data fields "
                     f"(min–median)")
        if self.inheriting_modules:
            lines.append(f"  excluded, extraction inherited from a parent "
                         f"scraper: {', '.join(self.inheriting_modules)}")
        return "\n".join(lines)


class FieldAnalyzer:
    """Reads the corpus and reports which TenderData fields it populates."""

    def __init__(self, scrapers_dir: Path | None = None):
        self.dir = Path(scrapers_dir or getattr(
            settings, "FACTORY_SCRAPERS_DIR",
            Path(settings.BASE_DIR) / "tenders" / "scrapers"))

    # ── public API ────────────────────────────────────────────────────────────

    def build(self) -> FieldContract:
        modules = [p for p in sorted(self.dir.glob("*.py"))
                   if not p.name.startswith("_") and p.name != "base.py"]
        all_fields = self._tenderdata_fields()
        counts: dict[str, FieldProfile] = {
            name: FieldProfile(name=name, total_scrapers=0) for name in all_fields}
        per_module_counts: list[int] = []
        analysed = 0

        inheriting: list[str] = []
        for path in modules:
            used = self._fields_used(path)
            if used is None:
                continue                       # unparseable; excluded entirely
            data_fields = used & set(all_fields) - _NON_DATA_FIELDS
            if not data_fields:
                # A subclass that reuses its parent's parser (iqtenders,
                # sytenders, tendersa). It sets these fields through
                # inheritance, so scoring it as "sets nothing" would drag every
                # ratio down and invent a zero-field floor that no scraper has.
                inheriting.append(path.stem)
                continue
            analysed += 1
            per_module_counts.append(len(data_fields))
            for name in all_fields:
                counts[name].total_scrapers += 1
                if name in used:
                    counts[name].scrapers_setting += 1
                    counts[name].modules.append(path.stem)

        contract = FieldContract(corpus_size=analysed, profiles=counts,
                                 inheriting_modules=sorted(inheriting))
        for name, profile in counts.items():
            if name in _NON_DATA_FIELDS:
                contract.optional.append(name)
            elif profile.ratio >= REQUIRED_RATIO:
                contract.required.append(name)
            elif profile.ratio >= EXPECTED_RATIO:
                contract.expected.append(name)
            else:
                contract.optional.append(name)

        contract.required.sort()
        contract.expected.sort()
        contract.optional.sort()

        if per_module_counts:
            ordered = sorted(per_module_counts)
            contract.median_field_count = ordered[len(ordered) // 2]
            contract.min_field_count = ordered[0]

        logger.info("[factory] field contract: required=%s expected=%s "
                    "(from %s scrapers)", contract.required, contract.expected,
                    analysed)
        return contract

    # ── analysis ──────────────────────────────────────────────────────────────

    def _tenderdata_fields(self) -> list[str]:
        """Read the field names straight off the TenderData dataclass."""
        base = self.dir / "base.py"
        try:
            tree = ast.parse(base.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            logger.warning("[factory] cannot read TenderData: %s", exc)
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "TenderData":
                return [item.target.id for item in node.body
                        if isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)]
        return []

    @staticmethod
    def _fields_used(path: Path) -> set[str] | None:
        """Which TenderData fields does this module pass as keyword arguments?

        Scrapers construct TenderData with keywords, so the keyword names are
        the honest signal. Attribute assignment after construction is counted
        too, because a few scrapers patch a field once the detail page is read.
        """
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            logger.warning("[factory] skipping %s: %s", path.name, exc)
            return None

        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (func.id if isinstance(func, ast.Name)
                        else func.attr if isinstance(func, ast.Attribute) else "")
                if name == "TenderData":
                    used.update(kw.arg for kw in node.keywords if kw.arg)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        used.add(target.attr)
        return used


_cached: FieldContract | None = None


def get_field_contract(*, refresh: bool = False) -> FieldContract:
    """Process-cached contract; the corpus does not change at runtime."""
    global _cached
    if _cached is None or refresh:
        _cached = FieldAnalyzer().build()
    return _cached
