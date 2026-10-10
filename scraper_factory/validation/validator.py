"""CodeValidator — VERIFY ONLY (gates A, B, C, D).

This module inspects generated source text and reports findings. It must NEVER
rewrite, reformat, normalise or otherwise modify the code: the model produces
the final file directly, and any deviation is reported back for the model (or a
human) to fix. There is deliberately no formatter, no autofix, and no
`transform()` anywhere in this package.

Gate E (sandbox execution) lives in `sandbox.py`.

Hard vs scored
--------------
Hard gates come from `StyleIndex.invariants` — properties measured to hold for
100% of the corpus. If a corpus change makes an invariant non-universal, the
indexer marks it `holds=False` and this validator automatically treats it as a
scored convention instead. The repository stays the source of truth without
anyone editing this file.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Optional

from django.conf import settings

from ..interfaces import ICodeValidator, StyleViolation, ValidationReport

logger = logging.getLogger("scraper_factory")


# ── Gate B: safety policy ─────────────────────────────────────────────────────
# Allowlist derived from what the corpus actually imports.
ALLOWED_IMPORTS = {
    "logging", "re", "time", "math", "json", "hashlib", "random", "os",
    "datetime", "typing", "urllib", "urllib.parse", "urllib3", "html",
    "base64", "collections", "dataclasses", "email", "email.utils",
    "pathlib", "decimal", "itertools", "functools", "string", "unicodedata",
    "requests", "bs4", "cloudscraper", "curl_cffi", "playwright",
    "playwright.sync_api", "lxml",
    # project-internal
    "tenders", "tenders.models", "tenders.services", "tenders.scrapers",
    "django", "django.conf", "django.utils",
    ".base", "base",
}

# Models a scraper must never write to: creating tenders or scrape logs would
# bypass the save pipeline. Writes to CountryConfig ARE allowed because the
# corpus does exactly that (e.g. ebrd.py calls
# CountryConfig.objects.get_or_create to ensure a country row exists before a
# tender references it). The repository is the specification.
PROTECTED_MODELS = ("Tender", "ScrapeLog")

FORBIDDEN_CALLS = {
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "setattr", "delattr", "breakpoint", "input",
}
FORBIDDEN_MODULES = {
    "subprocess", "shutil", "socket", "pickle", "marshal", "ctypes",
    "multiprocessing", "importlib", "pty", "tempfile", "webbrowser",
}
# ORM mutation methods — a scraper returns data; it never persists.
FORBIDDEN_ORM_METHODS = {
    "save", "delete", "update", "create", "get_or_create",
    "update_or_create", "bulk_create", "bulk_update", "raw",
}


class CodeValidator(ICodeValidator):
    """Runs gates A–D over candidate source text. Read-only."""

    def __init__(self, index=None):
        self._index = index

    # ── public API ────────────────────────────────────────────────────────────

    def validate(self, source_code: str, *, module_name: str = "",
                 class_name: str = "",
                 check_collisions: bool = True) -> ValidationReport:
        """Verify candidate source. Never modifies it.

        `check_collisions=False` is used when validating a file that is ALREADY
        installed (e.g. the corpus self-check), where a name clash with itself
        is expected and meaningless.
        """
        report = ValidationReport()
        violations: list[StyleViolation] = []
        self._check_collisions_enabled = check_collisions

        # Gate A — syntax
        tree = self._gate_a(source_code, violations)
        report.gates["A_syntax"] = tree is not None
        if tree is None:
            report.violations = violations
            report.passed = False
            return report

        index = self._get_index()

        # Gate B — safety
        report.gates["B_safety"] = self._gate_b(tree, violations)

        # Gate C — project contract
        report.gates["C_contract"] = self._gate_c(
            tree, source_code, violations, module_name, class_name)

        # Gate D — style (hard invariants + scored conventions)
        score = self._gate_d(source_code, tree, violations, index, module_name)
        report.style_score = score
        report.gates["D_style"] = (
            score >= self.style_floor(index) and not any(
                v.hard for v in violations if v.code.startswith("I")))

        report.violations = violations
        report.passed = (not any(v.hard for v in violations)
                         and report.gates["D_style"])
        return report

    # ── Gate A ────────────────────────────────────────────────────────────────

    @staticmethod
    def _gate_a(source: str, out: list[StyleViolation]):
        try:
            return ast.parse(source)
        except SyntaxError as exc:
            out.append(StyleViolation(
                code="A1", hard=True, line=exc.lineno,
                message=f"Python syntax error: {exc.msg}"))
            return None

    # ── Gate B ────────────────────────────────────────────────────────────────

    def _gate_b(self, tree: ast.AST, out: list[StyleViolation]) -> bool:
        before = len(out)

        for node in ast.walk(tree):
            # imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, node.lineno, out)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level and not mod:
                    mod = ".base" if any(a.name in ("BaseScraper", "TenderData")
                                         for a in node.names) else "."
                elif node.level:
                    mod = f".{mod}"
                self._check_import(mod, node.lineno, out)

            # dangerous calls
            elif isinstance(node, ast.Call):
                fname = self._call_name(node.func)
                # Only BARE builtin calls are dangerous. `re.compile(...)` is an
                # attribute call used by 56 corpus modules and must not be
                # confused with the builtin `compile(...)`.
                if isinstance(node.func, ast.Name) and fname in FORBIDDEN_CALLS:
                    out.append(StyleViolation(
                        code="B2", hard=True, line=node.lineno,
                        message=f"Forbidden call '{fname}()' — generated scrapers "
                                "must not execute dynamic code."))
                if isinstance(node.func, ast.Name) and fname == "open":
                    mode = ""
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    if any(ch in mode for ch in ("w", "a", "x", "+")):
                        out.append(StyleViolation(
                            code="B3", hard=True, line=node.lineno,
                            message=f"Forbidden file write: open(..., '{mode}')."))
                # ORM mutations against protected models only
                attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if attr in FORBIDDEN_ORM_METHODS and \
                        self._writes_protected_model(node.func):
                    out.append(StyleViolation(
                        code="B4", hard=True, line=node.lineno,
                        message=f"Forbidden database write '.{attr}()' on a "
                                "protected model — a scraper returns "
                                "list[TenderData]; persisting tenders is the "
                                "caller's job."))
        return len(out) == before

    def _check_import(self, module: str, lineno: int, out: list[StyleViolation]):
        if not module:
            return
        root = module.split(".")[0]
        if module in FORBIDDEN_MODULES or root in FORBIDDEN_MODULES:
            out.append(StyleViolation(
                code="B1", hard=True, line=lineno,
                message=f"Forbidden import '{module}'."))
            return
        if module in ALLOWED_IMPORTS or root in ALLOWED_IMPORTS:
            return
        if module.startswith("."):
            return  # relative imports inside tenders.scrapers are fine
        out.append(StyleViolation(
            code="B1", hard=True, line=lineno,
            message=f"Import '{module}' is not on the allowlist. The corpus only "
                    "uses stdlib, requests, bs4, cloudscraper/curl_cffi, "
                    "playwright and project modules."))

    @staticmethod
    def _call_name(func: ast.AST) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ""

    @staticmethod
    def _writes_protected_model(func: ast.AST) -> bool:
        """True only for ORM writes against Tender / ScrapeLog.

        `CountryConfig.objects.get_or_create(...)` is deliberately permitted —
        ebrd.py in the corpus does it to ensure a country row exists.
        """
        chain = []
        node = func
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            chain.append(node.id)
        text = ".".join(reversed(chain))
        if "CountryConfig" in text:
            return False
        return any(re.search(rf"\b{m}\b", text) for m in PROTECTED_MODELS)

    # ── Gate C ────────────────────────────────────────────────────────────────

    def _gate_c(self, tree: ast.AST, source: str, out: list[StyleViolation],
                module_name: str, class_name: str) -> bool:
        before = len(out)

        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        scraper_classes = [
            c for c in classes
            if any("Scraper" in ast.unparse(b) for b in c.bases)
        ] or classes

        if not scraper_classes:
            out.append(StyleViolation(
                code="C1", hard=True,
                message="No scraper class found. The module must define a class "
                        "that subclasses BaseScraper."))
            return False

        cls = scraper_classes[0]
        bases = [ast.unparse(b) for b in cls.bases]

        # C1 subclasses BaseScraper (directly, or a sibling scraper for variants)
        if not any(b == "BaseScraper" or b.endswith("Scraper") for b in bases):
            out.append(StyleViolation(
                code="C1", hard=True, line=cls.lineno,
                message=f"class {cls.name} must subclass BaseScraper "
                        f"(got: {', '.join(bases) or 'nothing'})."))

        # C2 name ends with Scraper
        if not cls.name.endswith("Scraper"):
            out.append(StyleViolation(
                code="C2", hard=True, line=cls.lineno,
                message=f"Class name '{cls.name}' must end with 'Scraper'."))
        if class_name and cls.name != class_name:
            out.append(StyleViolation(
                code="C2", hard=True, line=cls.lineno,
                message=f"Class is named '{cls.name}' but '{class_name}' was "
                        "requested."))

        # C3 required class attributes.
        #
        # Hard only for a CONCRETE scraper. The corpus contains abstract
        # intermediates — e.g. BidDetailScraper and TenderJOScraper — that
        # deliberately omit these attrs and let their regional subclasses set
        # them. A class subclassed by another class in the same module is
        # treated as abstract, so the rule matches the repository.
        subclassed = {
            base for other in classes if other is not cls
            for base in (ast.unparse(b) for b in other.bases)
        }
        # Subclassing also happens ACROSS modules: sytenders.py and tendersa.py
        # subclass TenderJOScraper from tenderjo.py. Consult the corpus index so
        # cross-module abstract bases are recognised too.
        index = self._get_index()
        if index is not None:
            for fp in index.fingerprints.values():
                if cls.name in (fp.base_classes or []):
                    subclassed.add(cls.name)
                    break
        is_abstract_intermediate = cls.name in subclassed
        attrs = {t.id for item in cls.body if isinstance(item, ast.Assign)
                 for t in item.targets if isinstance(t, ast.Name)}
        # A variant subclass inherits the attrs it does not restate.
        inherits_from_sibling = any(
            b != "BaseScraper" and b.endswith("Scraper") for b in bases)

        for required in ("country_code", "source_name", "base_url"):
            if required in attrs:
                continue
            if is_abstract_intermediate or inherits_from_sibling:
                continue
            out.append(StyleViolation(
                code="C3", hard=True, line=cls.lineno,
                message=f"Missing class attribute '{required}'. Concrete corpus "
                        "scrapers define country_code, source_name and base_url."))

        # C6 every scraper_class lookup string matches the class name.
        #
        # The house pattern finds its ScraperSource row with
        # `ScraperSource.objects.get(scraper_class='<ClassName>')`, so the string
        # and the class must agree exactly. The first real AJi AI generation
        # failed here: the class was NepcoAjiaiProbeScraper and the model wrote
        # the lookup as 'NePcoAjiaiProbeScraper' — one wrong capital, invisible
        # in review, and the scraper finds no source row at runtime. A string
        # comparison catches it deterministically at validation, where the
        # repair loop fixes it, instead of in the sandbox where it costs a full
        # live run and surfaces as a confusing CONTRACT failure.
        # A lookup may also name a class from ANOTHER corpus module: the three
        # Saudi Etimad variants share their parent's source row by looking up
        # 'SaudiEtimadScraper' from their own files, and the corpus is the
        # standard. So the string must name a class in this file or one the
        # corpus knows — a typo like 'NePco...' exists in neither and is caught.
        allowed = {c.name for c in classes}
        if index is not None:
            for fp in index.fingerprints.values():
                allowed.update(fp.classes or [])
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            for kw in call.keywords:
                if kw.arg != "scraper_class":
                    continue
                if not isinstance(kw.value, ast.Constant) \
                        or not isinstance(kw.value.value, str):
                    continue
                looked_up = kw.value.value
                if looked_up not in allowed:
                    out.append(StyleViolation(
                        code="C6", hard=True, line=call.lineno,
                        message=f"scraper_class='{looked_up}' matches no class in "
                                f"this file ({', '.join(sorted(c.name for c in classes))}) "
                                f"and no existing corpus scraper. The "
                                f"ScraperSource lookup string must equal the "
                                f"class name exactly, or the scraper finds no "
                                f"source row at runtime."))

        # C4 entry point
        methods = {m.name: m for m in cls.body
                   if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "scrape" not in methods and not ("parse_page" in methods
                                            or "scrape_url" in methods):
            out.append(StyleViolation(
                code="C4", hard=True, line=cls.lineno,
                message="Must define scrape() (the pattern in 73 of 74 corpus "
                        "modules) or the parse_page()/scrape_url() hooks."))

        # C5 scrape() returns a list
        scrape = methods.get("scrape")
        if scrape is not None:
            ann = ast.unparse(scrape.returns) if scrape.returns else ""
            if "list" not in ann.lower():
                out.append(StyleViolation(
                    code="C5", line=scrape.lineno,
                    message=f"scrape() should be annotated '-> list[TenderData]' "
                            f"(got '{ann or 'no annotation'}')."))
            returns = [n for n in ast.walk(scrape) if isinstance(n, ast.Return)]
            if not returns:
                out.append(StyleViolation(
                    code="C5", hard=True, line=scrape.lineno,
                    message="scrape() never returns. It must return "
                            "list[TenderData] on every path."))

        # C6 must NOT persist. Detected via AST, not text: mca_jordan.py
        # mentions save_tender_data in a COMMENT, which is perfectly fine.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and \
                    self._call_name(node.func) == "save_tender_data":
                out.append(StyleViolation(
                    code="C6", hard=True, line=node.lineno,
                    message="Must not call save_tender_data(). A scraper returns "
                            "list[TenderData]; persistence belongs to the caller "
                            "(tasks.py / apps.py / run_scrapers.py)."))
                break

        # C7 per-record parsers return Optional[TenderData]
        for name, fn in methods.items():
            if re.match(r"_parse_(row|card|item|tender)", name) and fn.returns:
                ann = ast.unparse(fn.returns)
                if "Optional" not in ann and "None" not in ann:
                    out.append(StyleViolation(
                        code="C7", line=fn.lineno,
                        message=f"{name}() should return Optional[TenderData] "
                                f"(got '{ann}')."))

        # C8 registry / filename collisions (skipped for already-installed files)
        if getattr(self, "_check_collisions_enabled", True):
            self._check_collisions(cls.name, module_name, out)

        # C9 logger present
        if "logging.getLogger('tenders')" not in source:
            out.append(StyleViolation(
                code="C9",
                message="Missing \"logger = logging.getLogger('tenders')\" — "
                        "present in 72 of 73 corpus modules."))

        return len(out) == before

    @staticmethod
    def _check_collisions(cls_name: str, module_name: str,
                          out: list[StyleViolation]):
        try:
            from tenders.scrapers import SCRAPER_REGISTRY
            if cls_name in SCRAPER_REGISTRY:
                out.append(StyleViolation(
                    code="C8", hard=True,
                    message=f"'{cls_name}' is already registered in "
                            "SCRAPER_REGISTRY. Choose another class name."))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[factory] registry check skipped: %s", exc)

        if module_name:
            try:
                scrapers_dir = getattr(settings, "FACTORY_SCRAPERS_DIR", None)
                if scrapers_dir and (scrapers_dir / f"{module_name}.py").exists():
                    out.append(StyleViolation(
                        code="C8", hard=True,
                        message=f"tenders/scrapers/{module_name}.py already "
                                "exists. Choose another module name."))
            except Exception as exc:  # noqa: BLE001
                logger.debug("[factory] filename check skipped: %s", exc)

    # ── Gate D ────────────────────────────────────────────────────────────────

    def _gate_d(self, source: str, tree: ast.AST, out: list[StyleViolation],
                index, module_name: str) -> int:
        """Hard invariants (from the index) + scored conventions. Verify only."""
        lines = source.splitlines()
        invariants = (index.invariants if index else {}) or {}

        def enforce(code: str, ok: bool, message: str, line: int | None = None):
            """Hard if the corpus says the property is universal, else scored."""
            meta = invariants.get(code, {})
            holds = bool(meta.get("holds"))
            if not ok:
                out.append(StyleViolation(code=code, hard=holds,
                                          message=message, line=line))
            return ok

        # I1 — path comment
        want = f"# tenders/scrapers/{module_name}.py" if module_name else None
        first_ok = bool(lines) and bool(
            re.match(r"^#\s*tenders/scrapers/\S+\.py\s*$", lines[0]))
        enforce("I1", first_ok,
                f"Line 1 must be the path comment"
                + (f" '{want}'" if want else " '# tenders/scrapers/<module>.py'")
                + ". All 73 corpus modules start with it.", 1)
        if first_ok and want and lines[0].strip() != want:
            out.append(StyleViolation(
                code="I1", hard=bool(invariants.get("I1", {}).get("holds")),
                line=1,
                message=f"Path comment is '{lines[0].strip()}' but the file will "
                        f"be installed as '{want}'."))

        # I2 — no tabs
        tab_line = next((i + 1 for i, ln in enumerate(lines) if "\t" in ln), None)
        enforce("I2", tab_line is None,
                "Tab character found; the corpus uses 4 spaces exclusively.",
                tab_line)

        # I3 — LF endings
        enforce("I3", "\r" not in source,
                "CRLF line endings found; the corpus is LF-only.")

        # I4 — TenderData kwargs aligned
        for m in re.finditer(r"TenderData\(\s*\n((?:[ \t]*\w+\s*=.*\n){2,})", source):
            block = m.group(1).splitlines()
            cols = [ln.index("=") for ln in block if "=" in ln]
            if cols and len(set(cols)) != 1:
                line_no = source[:m.start()].count("\n") + 1
                enforce("I4", False,
                        "TenderData(...) keyword arguments must be vertically "
                        "aligned on '='; all 77 corpus blocks are aligned.",
                        line_no)
                break

        # I6 — logger [TAG] prefix on literal messages
        for m in re.finditer(r"logger\.\w+\(\s*f?['\"]", source):
            tail = source[m.end() - 1:m.end() + 40]
            if not re.match(r"['\"]\s*\[", tail):
                enforce("I6", False,
                        "logger messages must start with a '[TAG]' prefix, as all "
                        "411 corpus logger calls do.",
                        source[:m.start()].count("\n") + 1)
                break

        # I7 — no bare except
        bare = next((i + 1 for i, ln in enumerate(lines)
                     if re.match(r"^\s*except\s*:\s*$", ln)), None)
        enforce("I7", bare is None,
                "Bare 'except:' is not used anywhere in the corpus; use "
                "'except Exception as exc:'.", bare)

        # ── scored conventions ────────────────────────────────────────────────
        return self._score_conventions(source, lines, tree, out, index)

    def _score_conventions(self, source: str, lines: list[str], tree: ast.AST,
                           out: list[StyleViolation], index) -> int:
        conv = (index.conventions if index else {}) or {}

        def weight(code: str, default: int) -> int:
            return int(conv.get(code, {}).get("weight", default))

        checks: list[tuple[str, bool, int, str]] = []

        # quotes
        single = len(re.findall(r"(?<![\"'\w])'[^'\n]{1,60}'", source))
        double = len(re.findall(r'(?<![\'"\w])"[^"\n]{1,60}"', source))
        total_q = single + double
        checks.append((
            "D-quotes", (single / total_q if total_q else 1.0) >= 0.70,
            weight("D-quotes", 15),
            f"Single quotes should dominate (corpus 84%); this file is "
            f"{(single*100//total_q) if total_q else 100}%."))

        # line length
        long_lines = [i + 1 for i, ln in enumerate(lines) if len(ln) > 99]
        ratio_79 = (sum(1 for ln in lines if len(ln) <= 79) / len(lines)) if lines else 1
        checks.append((
            "D-linelen", ratio_79 >= 0.85, weight("D-linelen", 20),
            f"94% of corpus lines are <= 79 chars; this file is "
            f"{int(ratio_79*100)}%"
            + (f" (over 99 chars at line {long_lines[0]})" if long_lines else "")))

        # logger line
        checks.append((
            "D-logger", "logging.getLogger('tenders')" in source,
            weight("D-logger", 15),
            "Expected \"logger = logging.getLogger('tenders')\" at module level."))

        # module docstring
        has_doc = bool(ast.get_docstring(tree))
        checks.append((
            "D-docstring", has_doc, weight("D-docstring", 15),
            "Module docstring missing; 95% of corpus modules have one "
            "documenting URL, HTML structure and date format."))

        # banner style
        uni = len(re.findall(r"^\s*#\s*──+\s*.+?\s*─+\s*$", source, re.MULTILINE))
        asc = len(re.findall(r"^\s*#\s*--+\s*.+?\s*-+\s*$", source, re.MULTILINE))
        checks.append((
            "D-banner", uni >= asc, weight("D-banner", 10),
            "Section banners should use '─' (U+2500) as 308 of 318 corpus "
            "banners do."))

        # module constants
        consts = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and t.id.isupper()}
        checks.append((
            "D-consts", "BASE_URL" in consts, weight("D-consts", 10),
            "Expected a module-level BASE_URL constant (67 of 73 corpus modules)."))

        # console output vocabulary
        checks.append((
            "D-console", "print(" in source, weight("D-console", 5),
            "The corpus prints progress to the console as well as logging; "
            "this file has no print() calls."))

        earned = possible = 0
        for code, ok, w, msg in checks:
            possible += w
            if ok:
                earned += w
            else:
                out.append(StyleViolation(code=code, hard=False, message=msg))

        return int(round(earned * 100 / possible)) if possible else 100

    # ── score floor, derived from the corpus ─────────────────────────────────

    @staticmethod
    def style_floor(index=None) -> int:
        """The minimum acceptable style score.

        Derived from the corpus, not chosen: generated code must be at least as
        conformant as the LEAST conformant hand-written scraper. The floor is
        measured by `manage.py factory_selfcheck`, which stores the corpus score
        distribution on the index snapshot.

        `settings.FACTORY_MIN_STYLE_SCORE` acts only as a ceiling on how strict
        we may be: if it is higher than what the repository itself achieves, the
        corpus wins, because the repository is the specification.
        """
        configured = int(getattr(settings, "FACTORY_MIN_STYLE_SCORE", 90))
        measured = None
        if index is not None:
            stats = (index.conventions or {}).get("_corpus_scores") or {}
            measured = stats.get("min")
        if measured is None:
            return configured
        return min(configured, int(measured))

    # ── index access ──────────────────────────────────────────────────────────

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
