"""CorpusIndexer — reads `tenders/scrapers/` and derives the coding standard.

The repository is the specification. Nothing in this module encodes a personal
preference: every rule is a measurement of the existing corpus, and if the
corpus changes, the derived standard changes with it.

The index has three parts:

  invariants   Properties that hold for 100% of the corpus. These become HARD
               validation gates — a generated file that breaks one is rejected.
  conventions  Properties that dominate but are not universal (e.g. 84% single
               quotes, 94% of lines <= 79 chars). These are SCORED, never
               enforced absolutely, because ten hand-written scrapers would
               fail an absolute rule and the repository always wins.
  families     Which structural family each module belongs to, so the matcher
               can pick like-for-like references.

Nothing here modifies any file. The indexer is read-only over the corpus.
"""

from __future__ import annotations

import ast
import collections
import hashlib
import logging
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from django.conf import settings

from ..interfaces import ICorpusIndexer

logger = logging.getLogger("scraper_factory")

# Modules that are infrastructure, not scrapers — excluded from the standard.
EXCLUDED = {"__init__.py", "base.py"}

#: Prefix used by the sandbox for temporary candidate modules. Anything matching
#: it is never part of the coding standard: a crashed test could otherwise leave
#: a file behind and silently corrupt the specification the factory learns from.
TEMP_PREFIX = "_factory_tmp_"


def is_corpus_module(path) -> bool:
    """True when a file in the scrapers directory counts as a corpus scraper."""
    name = getattr(path, "name", str(path))
    if name in EXCLUDED:
        return False
    return not name.startswith("_")

# Families, in detection priority order (a Playwright scraper that also parses
# JSON is a PLAYWRIGHT scraper — the fetch mechanism dominates).
FAMILY_PLAYWRIGHT = "PLAYWRIGHT"
FAMILY_STEALTH = "STEALTH_HTML"
FAMILY_JSON_API = "JSON_API"
FAMILY_HTML_PARSE_PAGE = "HTML_PARSE_PAGE"
FAMILY_VARIANT = "VARIANT_SUBCLASS"
FAMILY_HTML_CUSTOM = "HTML_CUSTOM"


# ── Per-module fingerprint ────────────────────────────────────────────────────

@dataclass
class ModuleFingerprint:
    """Everything the matcher and prompt-builder need about one corpus module."""
    module: str
    path: str
    line_count: int
    family: str = ""
    classes: list[str] = field(default_factory=list)
    base_classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    class_attrs: list[str] = field(default_factory=list)
    module_consts: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    country_code: str = ""
    source_name: str = ""
    uses_bs4: bool = False
    uses_json: bool = False
    uses_playwright: bool = False
    uses_stealth: bool = False
    uses_session_get: bool = False
    uses_base_get: bool = False
    has_own_date_parser: bool = False
    pagination_hint: str = ""
    framework_hint: str = ""
    docstring_sections: list[str] = field(default_factory=list)
    # Provenance: generated modules are excluded from the style-authority set
    # until a human has edited them, so the standard cannot drift onto itself.
    is_generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


# ── The index ─────────────────────────────────────────────────────────────────

@dataclass
class StyleIndex:
    """The derived coding standard plus the module fingerprints."""
    corpus_hash: str = ""
    module_count: int = 0
    total_lines: int = 0
    invariants: dict[str, dict] = field(default_factory=dict)
    conventions: dict[str, dict] = field(default_factory=dict)
    families: dict[str, list[str]] = field(default_factory=dict)
    fingerprints: dict[str, ModuleFingerprint] = field(default_factory=dict)
    console_vocabulary: list[str] = field(default_factory=list)
    separator_widths: dict[str, int] = field(default_factory=dict)

    # ── helpers used by the matcher and the prompt builder ────────────────────

    def family_of(self, module: str) -> str:
        fp = self.fingerprints.get(module)
        return fp.family if fp else ""

    def modules_in_family(self, family: str) -> list[str]:
        return list(self.families.get(family, []))

    def smallest_in_family(self, family: str, limit: int = 4) -> list[str]:
        """Smallest exemplars carry the full style signal at the least token cost."""
        mods = [(self.fingerprints[m].line_count, m)
                for m in self.families.get(family, [])
                if m in self.fingerprints and not self.fingerprints[m].is_generated]
        return [m for _, m in sorted(mods)[:limit]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_hash": self.corpus_hash,
            "module_count": self.module_count,
            "total_lines": self.total_lines,
            "invariants": self.invariants,
            "conventions": self.conventions,
            "families": self.families,
            "console_vocabulary": self.console_vocabulary,
            "separator_widths": self.separator_widths,
            "fingerprints": {k: v.to_dict() for k, v in self.fingerprints.items()},
        }


class CorpusIndexer(ICorpusIndexer):
    """Builds a StyleIndex from the scrapers directory."""

    def __init__(self, scrapers_dir: Optional[pathlib.Path] = None):
        self.dir = pathlib.Path(
            scrapers_dir or getattr(settings, "FACTORY_SCRAPERS_DIR",
                                    pathlib.Path("tenders/scrapers")))

    # ── public API ────────────────────────────────────────────────────────────

    def build(self, *, refresh: bool = False) -> StyleIndex:
        """Return the StyleIndex, using the DB cache unless it is stale."""
        corpus_hash = self.corpus_hash()

        if not refresh:
            cached = self._load_cached(corpus_hash)
            if cached is not None:
                return cached

        index = self._build_fresh(corpus_hash)
        self._save_cache(index)
        return index

    def corpus_hash(self) -> str:
        """Hash of module names + sizes + mtimes; changes when the corpus changes."""
        h = hashlib.sha256()
        for path in self._module_paths():
            stat = path.stat()
            h.update(path.name.encode())
            h.update(str(stat.st_size).encode())
            h.update(str(int(stat.st_mtime)).encode())
        return h.hexdigest()

    # ── building ──────────────────────────────────────────────────────────────

    def _module_paths(self) -> list[pathlib.Path]:
        if not self.dir.exists():
            return []
        return sorted(p for p in self.dir.glob("*.py") if is_corpus_module(p))

    def _build_fresh(self, corpus_hash: str) -> StyleIndex:
        paths = self._module_paths()
        index = StyleIndex(corpus_hash=corpus_hash, module_count=len(paths))

        # Raw tallies used to derive invariants vs conventions.
        t = {
            "path_comment": 0, "no_tabs": 0, "lf_only": 0, "logger_line": 0,
            "module_docstring": 0, "td_blocks": 0, "td_aligned": 0,
            "classes": 0, "classes_scraper_suffix": 0,
            "logger_literal_calls": 0, "logger_tagged": 0,
            "bare_except": 0, "banner_unicode": 0, "banner_ascii": 0,
            "single_quotes": 0, "double_quotes": 0,
            "lines_total": 0, "lines_le_79": 0,
            "blank1_before_def": 0, "blank0_before_def": 0,
            "except_exc": 0, "base_scraper": 0,
        }
        console = collections.Counter()
        separators = collections.Counter()
        families: dict[str, list[str]] = collections.defaultdict(list)

        for path in paths:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
            fp = self._fingerprint(path, text)
            index.fingerprints[fp.module] = fp
            families[fp.family].append(fp.module)
            index.total_lines += fp.line_count

            self._tally(text, raw, t, console, separators)

        index.families = {k: sorted(v) for k, v in families.items()}
        index.console_vocabulary = [p for p, _ in console.most_common(40)]
        index.separator_widths = dict(separators.most_common(10))

        n = max(len(paths), 1)
        index.invariants = self._derive_invariants(t, n)
        index.conventions = self._derive_conventions(t, n)

        logger.info("[factory] indexed %s modules, %s lines, families=%s",
                    index.module_count, index.total_lines,
                    {k: len(v) for k, v in index.families.items()})
        return index

    # ── fingerprinting one module ─────────────────────────────────────────────

    def _fingerprint(self, path: pathlib.Path, text: str) -> ModuleFingerprint:
        fp = ModuleFingerprint(module=path.stem, path=str(path),
                               line_count=len(text.splitlines()))
        low = text.lower()

        fp.uses_bs4 = "beautifulsoup" in low
        fp.uses_json = ".json()" in text
        fp.uses_playwright = "playwright" in low
        fp.uses_stealth = "cloudscraper" in text or "curl_cffi" in text
        fp.uses_session_get = "self.session.get(" in text
        fp.uses_base_get = bool(re.search(r"self\.get\(", text))
        fp.has_own_date_parser = bool(re.search(r"def _parse_\w*date", text))

        try:
            tree = ast.parse(text)
        except SyntaxError:
            fp.family = FAMILY_HTML_CUSTOM
            return fp

        doc = ast.get_docstring(tree) or ""
        for marker in ("URL", "Pagination", "API", "Page", "HTML structure",
                       "Strategy", "Portal", "NOTES", "Relevance",
                       "How it works", "Technology", "Date format"):
            if re.search(rf"^\s*{re.escape(marker)}", doc, re.MULTILINE):
                fp.docstring_sections.append(marker)
        if re.search(r"generated by (the )?scraper factory", doc, re.IGNORECASE):
            fp.is_generated = True

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                fp.imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                fp.imports.append(node.module or "")
            elif isinstance(node, ast.ClassDef):
                fp.classes.append(node.name)
                fp.base_classes.extend(ast.unparse(b) for b in node.bases)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fp.methods.append(item.name)
                    elif isinstance(item, ast.Assign):
                        for tgt in item.targets:
                            if isinstance(tgt, ast.Name):
                                fp.class_attrs.append(tgt.id)
                                if tgt.id in ("country_code", "source_name"):
                                    try:
                                        val = ast.literal_eval(item.value)
                                        setattr(fp, tgt.id, str(val))
                                    except (ValueError, TypeError):
                                        pass
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id.isupper():
                        fp.module_consts.append(tgt.id)

        # Pagination and framework hints (used by the matcher).
        if re.search(r"PageNumber|\?page=|'page'|\"page\"|Page=", text):
            fp.pagination_hint = "QUERY_PARAM"
        elif re.search(r"/page/\d|/\{page\}", text):
            fp.pagination_hint = "PATH"
        elif "MAX_PAGES" not in fp.module_consts and "max_pages" not in text:
            fp.pagination_hint = "NONE"
        if "__VIEWSTATE" in text or ".aspx" in low:
            fp.framework_hint = "ASPNET"
        elif "wp-json" in low or "wp-content" in low:
            fp.framework_hint = "WORDPRESS"

        fp.family = self._classify_family(fp)
        return fp

    @staticmethod
    def _classify_family(fp: ModuleFingerprint) -> str:
        """Fetch mechanism dominates; then structure."""
        if fp.uses_playwright:
            return FAMILY_PLAYWRIGHT
        if fp.uses_stealth:
            return FAMILY_STEALTH
        if fp.uses_json:
            return FAMILY_JSON_API
        if "parse_page" in fp.methods and "scrape" not in fp.methods:
            return FAMILY_HTML_PARSE_PAGE
        if fp.base_classes and not any(
                b == "BaseScraper" for b in fp.base_classes):
            return FAMILY_VARIANT
        return FAMILY_HTML_CUSTOM

    # ── tallying style facts ──────────────────────────────────────────────────

    @staticmethod
    def _tally(text: str, raw: bytes, t: dict,
               console: collections.Counter, separators: collections.Counter):
        lines = text.splitlines()

        if lines and re.match(r"^#\s*tenders/scrapers/\S+\.py\s*$", lines[0]):
            t["path_comment"] += 1
        if "\t" not in text:
            t["no_tabs"] += 1
        if b"\r\n" not in raw:
            t["lf_only"] += 1
        if "logger = logging.getLogger('tenders')" in text:
            t["logger_line"] += 1
        try:
            if ast.get_docstring(ast.parse(text)):
                t["module_docstring"] += 1
        except SyntaxError:
            pass

        # TenderData(...) kwarg alignment
        for m in re.finditer(r"TenderData\(\s*\n((?:\s*\w+\s*=.*\n){2,})", text):
            t["td_blocks"] += 1
            cols = [ln.index("=") for ln in m.group(1).splitlines() if "=" in ln]
            if cols and len(set(cols)) == 1:
                t["td_aligned"] += 1

        # class naming
        for m in re.finditer(r"^class (\w+)\(", text, re.MULTILINE):
            t["classes"] += 1
            if m.group(1).endswith("Scraper"):
                t["classes_scraper_suffix"] += 1

        # logger calls with a LITERAL message must carry a [TAG] prefix.
        # Calls passing a variable are not judged (the corpus has both).
        for m in re.finditer(r"logger\.\w+\(\s*f?['\"]", text):
            t["logger_literal_calls"] += 1
            tail = text[m.end() - 1:m.end() + 40]
            if re.match(r"['\"]\s*\[", tail):
                t["logger_tagged"] += 1

        t["bare_except"] += len(re.findall(r"except\s*:", text))
        t["except_exc"] += len(re.findall(r"except Exception as exc", text))
        t["base_scraper"] += len(re.findall(r"\(BaseScraper\)", text))

        for ln in lines:
            b = re.match(r"^\s*#\s*([─\-])\1+\s*.+?\s*\1+\s*$", ln)
            if b:
                if b.group(1) == "─":
                    t["banner_unicode"] += 1
                else:
                    t["banner_ascii"] += 1

        t["single_quotes"] += len(re.findall(r"(?<![\"'\w])'[^'\n]{1,60}'", text))
        t["double_quotes"] += len(re.findall(r'(?<![\'"\w])"[^"\n]{1,60}"', text))

        t["lines_total"] += len(lines)
        t["lines_le_79"] += sum(1 for ln in lines if len(ln) <= 79)

        for i, ln in enumerate(lines):
            if re.match(r"^    (def |@staticmethod)", ln):
                j, blanks = i - 1, 0
                while j >= 0 and lines[j].strip() == "":
                    blanks += 1
                    j -= 1
                if j >= 0 and lines[j].lstrip().startswith("#"):
                    continue
                if blanks == 1:
                    t["blank1_before_def"] += 1
                elif blanks == 0:
                    t["blank0_before_def"] += 1

        for m in re.finditer(r"print\(f?['\"]([^'\"]{2,40})", text):
            s = m.group(1).strip()
            if s:
                console[s[:34]] += 1
        for m in re.finditer(r'\{["\']([─_=~-])["\']\s*\*\s*(\d+)\}', text):
            separators[f'{m.group(1)}*{m.group(2)}'] += 1

    # ── deriving the standard ─────────────────────────────────────────────────

    @staticmethod
    def _derive_invariants(t: dict, n: int) -> dict[str, dict]:
        """Only properties that hold for 100% of the corpus become hard gates.

        Each entry records the measurement, so a reviewer can see WHY it is a
        gate. If a future corpus change breaks universality, `holds` flips to
        False and the validator automatically demotes the rule to scored —
        the repository stays the source of truth without a code change.
        """
        def inv(code: str, desc: str, hits: int, total: int) -> dict:
            return {"code": code, "description": desc, "hits": hits,
                    "total": total, "holds": total > 0 and hits == total}

        return {
            "I1": inv("I1", "Line 1 is '# tenders/scrapers/<module>.py'",
                      t["path_comment"], n),
            "I2": inv("I2", "No tab characters (indent is 4 spaces)",
                      t["no_tabs"], n),
            "I3": inv("I3", "LF line endings (no CRLF)", t["lf_only"], n),
            "I4": inv("I4", "TenderData(...) kwargs vertically aligned on '='",
                      t["td_aligned"], t["td_blocks"]),
            "I5": inv("I5", "Scraper class names end with 'Scraper'",
                      t["classes_scraper_suffix"], t["classes"]),
            "I6": inv("I6", "logger calls with a literal message start with [TAG]",
                      t["logger_tagged"], t["logger_literal_calls"]),
            "I7": inv("I7", "No bare 'except:'",
                      n - min(t["bare_except"], n), n),
        }

    @staticmethod
    def _derive_conventions(t: dict, n: int) -> dict[str, dict]:
        """Dominant-but-not-universal properties. Scored, never absolute.

        `n` is the module count, used as the denominator for per-module facts
        (docstring present, logger line present) — as opposed to per-occurrence
        facts (quotes, line lengths) which have their own totals.
        """
        def conv(code: str, desc: str, hits: int, total: int,
                 weight: int) -> dict:
            ratio = (hits / total) if total else 0.0
            return {"code": code, "description": desc, "hits": hits,
                    "total": total, "ratio": round(min(ratio, 1.0), 4),
                    "weight": weight}

        quotes = t["single_quotes"] + t["double_quotes"]
        blanks = t["blank1_before_def"] + t["blank0_before_def"]
        banners = t["banner_unicode"] + t["banner_ascii"]

        return {
            "D-quotes": conv("D-quotes", "Single-quoted strings dominate",
                             t["single_quotes"], quotes, 15),
            "D-linelen": conv("D-linelen", "Lines <= 79 characters",
                              t["lines_le_79"], t["lines_total"], 20),
            "D-logger": conv("D-logger", "logger = logging.getLogger('tenders')",
                             t["logger_line"], n, 15),
            "D-docstring": conv("D-docstring", "Module docstring present",
                                t["module_docstring"], n, 15),
            "D-banner": conv("D-banner", "Section banners use U+2500 '─'",
                             t["banner_unicode"], banners, 10),
            "D-blank": conv("D-blank", "One blank line before a method def",
                            t["blank1_before_def"], blanks, 10),
            "D-except": conv("D-except", "'except Exception as exc' house form",
                             t["except_exc"], max(t["except_exc"], 1), 5),
            "D-base": conv("D-base", "Subclasses BaseScraper",
                           t["base_scraper"], max(t["base_scraper"], 1), 10),
        }

    # ── cache ─────────────────────────────────────────────────────────────────

    def _load_cached(self, corpus_hash: str) -> Optional[StyleIndex]:
        try:
            from ..models import StyleIndexSnapshot
            snap = StyleIndexSnapshot.objects.filter(corpus_hash=corpus_hash).first()
            if snap is None:
                return None
            data = snap.index_data or {}
            index = StyleIndex(
                corpus_hash=snap.corpus_hash, module_count=snap.module_count,
                total_lines=snap.total_lines, invariants=snap.invariants or {},
                conventions=snap.conventions or {}, families=snap.families or {},
                console_vocabulary=data.get("console_vocabulary", []),
                separator_widths=data.get("separator_widths", {}),
            )
            for mod, d in (data.get("fingerprints") or {}).items():
                index.fingerprints[mod] = ModuleFingerprint(**d)
            return index
        except Exception as exc:  # noqa: BLE001 — a cache miss is never fatal
            logger.debug("[factory] index cache unavailable: %s", exc)
            return None

    def _save_cache(self, index: StyleIndex) -> None:
        try:
            from ..models import StyleIndexSnapshot
            StyleIndexSnapshot.objects.update_or_create(
                corpus_hash=index.corpus_hash,
                defaults={
                    "module_count": index.module_count,
                    "total_lines": index.total_lines,
                    "index_data": index.to_dict(),
                    "invariants": index.invariants,
                    "conventions": index.conventions,
                    "families": index.families,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] could not cache index: %s", exc)
