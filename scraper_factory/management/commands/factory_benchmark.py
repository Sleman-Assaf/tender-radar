"""End-to-end benchmark of the Scraper Factory across many websites.

Runs the COMPLETE pipeline for each URL — analyse, match, prompt, generate,
validate, sandbox, install, register, execute, save — in dry-run mode by default
so dozens of real portals can be evaluated without touching the repository.

    # from a file, one URL per line (optionally "url | Name | CC")
    python manage.py factory_benchmark --file urls.txt

    # inline
    python manage.py factory_benchmark --url https://a.test/t --url https://b.test/t

    # real installations (careful: this modifies the project)
    python manage.py factory_benchmark --file urls.txt --mode install

Per website it records: family, references used, generation time, validation
result, sandbox result, installation result, scheduler execution result, tender
count, duplicate count, runtime errors and overall pass/fail — then prints a
comparison table and a summary. Results persist in GenerationRun and
BenchmarkReport, so runs can be compared over time and across prompt versions.
"""

from __future__ import annotations

import csv
import pathlib
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from scraper_factory.generation.pipeline import ScraperFactoryPipeline
from scraper_factory.models import BenchmarkReport, GenerationRun


class Command(BaseCommand):
    help = ("Benchmark the whole factory pipeline against multiple real "
            "websites and report per-site and aggregate results.")

    def add_arguments(self, parser):
        parser.add_argument("--url", action="append", default=[],
                            help="A URL to benchmark; repeatable.")
        parser.add_argument("--file", default=None,
                            help="File with one URL per line. Optional "
                                 "'url | Display Name | CC' format.")
        parser.add_argument("--mode", choices=["dry_run", "install"],
                            default="dry_run",
                            help="dry_run (default, no changes) or install.")
        parser.add_argument("--csv", default=None,
                            help="Also write the results table to this CSV path.")
        parser.add_argument("--stop-on-failure", action="store_true",
                            help="Abort the run at the first failure.")

    # ── main ──────────────────────────────────────────────────────────────────

    def handle(self, *args, **opts):
        targets = self._targets(opts)
        if not targets:
            raise CommandError("No URLs supplied. Use --url or --file.")

        model = None

        mode = opts["mode"]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  SCRAPER FACTORY BENCHMARK\n"
            f"    websites : {len(targets)}\n"
            f"    mode     : {mode}"
            f"{'  (nothing will be installed)' if mode == 'dry_run' else ''}\n"
            f"    model    : {getattr(model, 'name', None) or 'configured (AJi AI)'}\n"
            f"    started  : {timezone.now():%Y-%m-%d %H:%M}\n"))

        rows: list[dict] = []
        started_all = time.monotonic()

        for index, (url, name, country) in enumerate(targets, 1):
            self.stdout.write(f"  [{index}/{len(targets)}] {url}")
            row = self._benchmark_one(url, name, country, mode, model)
            rows.append(row)
            self._print_one(row)
            if opts["stop_on_failure"] and not row["overall"]:
                self.stdout.write(self.style.ERROR(
                    "  Stopping: --stop-on-failure was set."))
                break

        self._print_table(rows)
        self._print_summary(rows, time.monotonic() - started_all)

        if opts["csv"]:
            self._write_csv(rows, opts["csv"])

    # ── one website ───────────────────────────────────────────────────────────

    def _benchmark_one(self, url: str, name: str, country: str, mode: str,
                       model) -> dict:
        row = {
            "url": url, "name": name, "family": "", "references": "",
            "generation_ms": 0, "validation": "", "style_score": 0,
            "sandbox": "", "sandbox_tenders": 0, "installation": "",
            "execution": "", "tenders": 0, "duplicates": 0,
            "runtime_errors": "", "attempts": 0, "repairs": 0,
            "overall": False, "duration_ms": 0, "stage": "",
        }
        pipeline = ScraperFactoryPipeline(model=model)
        try:
            outcome = pipeline.build(url, country_code=country,
                                     source_name=name, mode=mode)
        except Exception as exc:  # noqa: BLE001 — one bad site must not stop the run
            row["runtime_errors"] = f"{type(exc).__name__}: {exc}"[:200]
            row["stage"] = "CRASH"
            return row

        internal = outcome.internal or {}
        row["stage"] = outcome.stage
        row["duration_ms"] = outcome.duration_ms
        row["tenders"] = outcome.tenders_saved
        row["attempts"] = internal.get("attempts", 0)
        row["repairs"] = internal.get("repairs", 0)
        row["style_score"] = internal.get("style_score", 0)
        row["references"] = ", ".join(internal.get("references", [])[:3])
        row["overall"] = bool(outcome.ok)

        errors = []
        if internal.get("first_run_error"):
            errors.append(str(internal["first_run_error"])[:120])
        if internal.get("install_error"):
            errors.append(str(internal["install_error"])[:120])
        if internal.get("crash"):
            errors.append(str(internal["crash"])[:120])
        row["runtime_errors"] = " | ".join(errors)

        # Pull the authoritative detail from the stored run + benchmark.
        run = None
        run_id = internal.get("generation_run_id")
        if run_id:
            run = GenerationRun.objects.filter(pk=run_id).first()
        if run is not None:
            row["family"] = run.family or ""
            row["generation_ms"] = run.generation_ms
            row["validation"] = self._validation_label(run)
            bench = BenchmarkReport.objects.filter(generation_run=run).first()
            if bench is not None:
                row["sandbox"] = "PASS" if bench.execution_success else "FAIL"
                row["sandbox_tenders"] = bench.tenders_extracted
                row["duplicates"] = round(
                    bench.duplicate_rate * max(bench.tenders_extracted, 0))
                row["style_score"] = int(round(bench.style_similarity * 100))

        if outcome.needs_developer:
            row["installation"] = "SKIPPED"
            row["execution"] = "SKIPPED"
        elif mode == "dry_run":
            row["installation"] = "DRY-RUN OK"
            row["execution"] = ("PASS" if not internal.get("first_run_error")
                                else "FAIL")
        else:
            row["installation"] = "INSTALLED"
            row["execution"] = ("PASS" if not internal.get("first_run_error")
                                else "FAIL")
        return row

    @staticmethod
    def _validation_label(run) -> str:
        for entry in reversed(run.attempt_log or []):
            if "passed" in entry:
                return "PASS" if entry["passed"] else "FAIL"
        return "FAIL" if run.outcome != GenerationRun.Outcome.PASSED else "PASS"

    # ── output ────────────────────────────────────────────────────────────────

    def _print_one(self, row: dict):
        mark = self.style.SUCCESS("PASS") if row["overall"] else \
            self.style.ERROR("FAIL")
        self.stdout.write(
            f"        {mark}  family={row['family'] or '-':14} "
            f"validation={row['validation'] or '-':5} "
            f"sandbox={row['sandbox'] or '-':5} "
            f"tenders={row['tenders']:<4} "
            f"style={row['style_score']:<3} "
            f"repairs={row['repairs']} ({row['duration_ms']} ms)")
        if row["runtime_errors"]:
            self.stdout.write(f"              {row['runtime_errors'][:110]}")

    def _print_table(self, rows: list[dict]):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n  RESULTS\n"))
        header = (f"    {'#':<3} {'website':38} {'family':14} {'val':4} "
                  f"{'sbx':4} {'inst':11} {'exec':5} {'tndr':5} {'dup':4} "
                  f"{'sty':4} {'rpr':4} {'ms':>7}")
        self.stdout.write(header)
        self.stdout.write("    " + "-" * (len(header) - 4))
        for i, r in enumerate(rows, 1):
            host = r["url"].replace("https://", "").replace("http://", "")[:38]
            self.stdout.write(
                f"    {i:<3} {host:38} {r['family'][:14]:14} "
                f"{(r['validation'] or '-'):4} {(r['sandbox'] or '-'):4} "
                f"{(r['installation'] or '-'):11} {(r['execution'] or '-'):5} "
                f"{r['tenders']:<5} {r['duplicates']:<4} "
                f"{r['style_score']:<4} {r['repairs']:<4} {r['duration_ms']:>7}")

    def _print_summary(self, rows: list[dict], elapsed_s: float):
        total = len(rows) or 1
        passed = sum(1 for r in rows if r["overall"])
        gen_times = [r["generation_ms"] for r in rows if r["generation_ms"]]
        styles = [r["style_score"] for r in rows if r["style_score"]]
        repairs = [r["repairs"] for r in rows]
        tenders = sum(r["tenders"] for r in rows)

        def pct(n):
            return f"{n * 100 // total}%"

        self.stdout.write(self.style.MIGRATE_HEADING("\n  SUMMARY\n"))
        self.stdout.write(f"    websites tested        : {len(rows)}")
        self.stdout.write(f"    overall success        : {passed}/{len(rows)} "
                          f"({pct(passed)})")
        self.stdout.write(f"    validation passed      : "
                          f"{sum(1 for r in rows if r['validation'] == 'PASS')}")
        self.stdout.write(f"    sandbox passed         : "
                          f"{sum(1 for r in rows if r['sandbox'] == 'PASS')}")
        self.stdout.write(f"    execution passed       : "
                          f"{sum(1 for r in rows if r['execution'] == 'PASS')}")
        self.stdout.write(f"    queued for developers  : "
                          f"{sum(1 for r in rows if r['installation'] == 'SKIPPED')}")
        self.stdout.write(f"    total tenders extracted: {tenders}")
        if gen_times:
            self.stdout.write(
                f"    avg generation time    : {sum(gen_times)//len(gen_times)} ms")
        if styles:
            self.stdout.write(
                f"    avg style score        : {sum(styles)//len(styles)}/100")
        self.stdout.write(
            f"    avg repair iterations  : {sum(repairs)/max(len(repairs),1):.2f}")

        families: dict[str, int] = {}
        for r in rows:
            if r["family"]:
                families[r["family"]] = families.get(r["family"], 0) + 1
        if families:
            self.stdout.write("    families selected      : " + ", ".join(
                f"{k}={v}" for k, v in sorted(families.items(),
                                              key=lambda kv: -kv[1])))
        self.stdout.write(f"    wall clock             : {elapsed_s:.1f}s\n")

        failures = [r for r in rows if not r["overall"]]
        if failures:
            self.stdout.write(self.style.WARNING("  FAILURES"))
            for r in failures:
                self.stdout.write(
                    f"    {r['url'][:60]:62} stage={r['stage']:10} "
                    f"{r['runtime_errors'][:70]}")
            self.stdout.write(
                "\n  Inspect any of them with: python manage.py factory_queue\n")

    def _write_csv(self, rows: list[dict], path: str):
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = ["url", "name", "family", "references", "generation_ms",
                  "validation", "style_score", "sandbox", "sandbox_tenders",
                  "installation", "execution", "tenders", "duplicates",
                  "attempts", "repairs", "runtime_errors", "overall",
                  "duration_ms", "stage"]
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        self.stdout.write(f"  results written to {target}\n")

    # ── input ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _targets(opts) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for url in opts["url"]:
            out.append((url.strip(), "", ""))
        if opts["file"]:
            path = pathlib.Path(opts["file"])
            if not path.exists():
                raise CommandError(f"File not found: {path}")
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                url = parts[0]
                name = parts[1] if len(parts) > 1 else ""
                country = parts[2] if len(parts) > 2 else ""
                out.append((url, name, country))
        return out
