"""F2 acceptance test — validate the validator against the corpus itself.

The repository is the specification, so the only honest way to test the style
checker is to point it at the 73 hand-written scrapers. Every one of them MUST
pass: any hard violation, or a style score below the configured minimum, means
the CHECKER is wrong, not the scraper.

    python manage.py factory_selfcheck
    python manage.py factory_selfcheck --verbose
    python manage.py factory_selfcheck --refresh      # rebuild the index

Exit code is non-zero when a corpus module fails, so this can gate CI.
"""

from __future__ import annotations

import pathlib

from django.conf import settings
from django.core.management.base import BaseCommand

from scraper_factory.corpus.indexer import EXCLUDED, CorpusIndexer
from scraper_factory.validation.validator import CodeValidator


class Command(BaseCommand):
    help = ("Verify that every existing scraper passes the factory's own style "
            "and contract gates (the F2 acceptance test).")

    def add_arguments(self, parser):
        parser.add_argument("--verbose", action="store_true",
                            help="List every violation, not just failures.")
        parser.add_argument("--refresh", action="store_true",
                            help="Rebuild the corpus index from disk.")
        parser.add_argument("--module", type=str, default=None,
                            help="Check a single module (without .py).")

    def handle(self, *args, **options):
        verbose = options["verbose"]
        scrapers_dir = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR"))

        indexer = CorpusIndexer(scrapers_dir)
        index = indexer.build(refresh=options["refresh"])

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  Scraper Factory — corpus self-check\n"
            f"  corpus : {scrapers_dir}\n"
            f"  modules: {index.module_count}   lines: {index.total_lines:,}\n"
            f"  hash   : {index.corpus_hash[:16]}\n"))

        # ── what the corpus taught us ────────────────────────────────────────
        self.stdout.write("  INVARIANTS (hard gates only where universal):")
        for code, meta in sorted(index.invariants.items()):
            mark = "HARD" if meta["holds"] else "soft"
            self.stdout.write(
                f"    [{mark}] {code}  {meta['hits']}/{meta['total']}  "
                f"{meta['description']}")

        self.stdout.write("\n  CONVENTIONS (scored):")
        for code, meta in sorted(index.conventions.items()):
            if code.startswith("_"):
                continue  # bookkeeping entries such as _corpus_scores
            self.stdout.write(
                f"    {code:12} {meta['ratio']:>7.1%}  w={meta['weight']:<3} "
                f"{meta['description']}")

        stats = index.conventions.get("_corpus_scores")
        if stats:
            self.stdout.write(
                f"\n  CORPUS SCORE DISTRIBUTION (from a previous run):"
                f"  min={stats['min']}  median={stats['median']}  "
                f"avg={stats['avg']}  max={stats['max']}")

        self.stdout.write("\n  FAMILIES:")
        for fam, mods in sorted(index.families.items(),
                                key=lambda kv: -len(kv[1])):
            smallest = index.smallest_in_family(fam, 3)
            self.stdout.write(
                f"    {fam:18} {len(mods):>3}   references: "
                f"{', '.join(smallest) or '—'}")

        # ── validate every module ────────────────────────────────────────────
        validator = CodeValidator(index=index)
        paths = sorted(p for p in scrapers_dir.glob("*.py")
                       if p.name not in EXCLUDED)
        if options["module"]:
            paths = [p for p in paths if p.stem == options["module"]]

        failures: list[tuple[str, int, list]] = []
        soft_only: list[tuple[str, int, int]] = []
        scores: list[int] = []
        # Pass 1 measures the corpus honestly, with no floor applied, so the
        # floor can be DERIVED from the repository rather than chosen by us.
        min_score = validator.style_floor(index)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  Validating {len(paths)} corpus module(s) "
            f"(style floor {min_score}, derived from the corpus)\n"))

        for path in paths:
            source = path.read_text(encoding="utf-8", errors="replace")
            # module_name is passed so the I1 path-comment check is exact.
            # class_name is left blank and collision checks are disabled: these
            # files are ALREADY installed, so they necessarily "collide" with
            # themselves. C8 is a pre-flight check for NEW scrapers only.
            report = validator.validate(source, module_name=path.stem,
                                        check_collisions=False)
            scores.append(report.style_score)

            hard = report.hard_violations
            if hard or report.style_score < min_score:
                failures.append((path.stem, report.style_score, report.violations))
                self.stdout.write(self.style.ERROR(
                    f"    FAIL  {path.stem:34} score={report.style_score:>3}  "
                    f"hard={len(hard)}"))
                for v in report.violations:
                    if v.hard or verbose:
                        where = f":{v.line}" if v.line else ""
                        self.stdout.write(
                            f"            {v.code}{where} {v.message[:96]}")
            else:
                soft = len(report.violations)
                soft_only.append((path.stem, report.style_score, soft))
                if verbose:
                    self.stdout.write(
                        f"    ok    {path.stem:34} score={report.style_score:>3}"
                        f"  notes={soft}")
                    for v in report.violations:
                        self.stdout.write(f"            {v.code} {v.message[:90]}")

        # ── record the corpus score distribution on the index ────────────────
        # This is what makes the style floor data-driven: the worst hand-written
        # scraper defines the minimum any generated scraper must reach.
        avg = sum(scores) // len(scores) if scores else 0
        worst = min(scores) if scores else 0
        ordered = sorted(scores)
        median = ordered[len(ordered) // 2] if ordered else 0
        if not options["module"] and scores:
            index.conventions["_corpus_scores"] = {
                "min": worst, "avg": avg, "median": median,
                "max": max(scores), "count": len(scores),
            }
            indexer._save_cache(index)  # noqa: SLF001 — same package, intentional
            self.stdout.write(
                f"\n  recorded corpus score floor on the index: min={worst} "
                f"median={median} avg={avg}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  {'=' * 66}\n"
            f"  checked={len(paths)}  passed={len(soft_only)}  "
            f"failed={len(failures)}\n"
            f"  style score: avg={avg}  median={median}  min={worst}\n"
            f"  {'=' * 66}\n"))

        if failures:
            self.stdout.write(self.style.ERROR(
                "  VERDICT: the checker rejects hand-written scrapers, so the\n"
                "  CHECKER IS WRONG. The repository is the specification —\n"
                "  relax the offending rule or demote it to a scored convention.\n"))
            for name, score, _ in failures[:20]:
                self.stdout.write(f"    - {name} (score {score})")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            "  VERDICT: every existing scraper passes. The checker agrees with\n"
            "  the repository, so it is safe to judge generated code with it.\n"))
