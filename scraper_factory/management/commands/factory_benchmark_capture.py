"""Propose benchmark expectations from live investigations.

Dry run by default. Nothing is written without --confirm, because a captured
expectation becomes the definition of correct for that portal, and one captured
from a bad investigation makes the suite defend the bug.

    python manage.py factory_benchmark_capture --suite "Production Portals"
    python manage.py factory_benchmark_capture --suite "..." --limit 5 --confirm
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Propose expected_data_source / strategy / technology / min_rows for "
            "benchmark cases from a live investigation. Dry run unless --confirm.")

    def add_arguments(self, parser):
        parser.add_argument("--suite", default="",
                           help="Suite name or id. Omit to use the only suite.")
        parser.add_argument("--limit", type=int, default=0,
                           help="Investigate at most this many cases.")
        parser.add_argument("--case", type=int, action="append", default=[],
                           help="Case id. Repeatable. Overrides --suite.")
        parser.add_argument("--blank-only", action="store_true",
                           help="Only cases with no expectations declared yet.")
        parser.add_argument("--confirm", action="store_true",
                           help="Actually write the proposed expectations.")
        parser.add_argument("--overwrite", action="store_true",
                           help="Also replace expectations someone already "
                                "declared. Requires --confirm.")
        parser.add_argument("--min-confidence", type=int, default=None,
                           help="Confidence floor for eligibility.")
        parser.add_argument("--no-min-rows", action="store_true",
                           help="Do not propose a min_rows floor.")
        parser.add_argument("--tier2", action="store_true",
                           help="Allow Playwright escalation while capturing.")

    def handle(self, *args, **opts):
        from ...benchmarking.capture import ExpectationCapturer, summarise
        from ...models import BenchmarkCase, BenchmarkSuite

        if opts["overwrite"] and not opts["confirm"]:
            raise CommandError("--overwrite only means something with --confirm.")

        cases = self._select(BenchmarkCase, BenchmarkSuite, opts)
        if not cases:
            self.stdout.write("No matching cases.")
            return

        mode = "WRITING" if opts["confirm"] else "DRY RUN — nothing will be written"
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"BENCHMARK EXPECTATION CAPTURE  ({mode})"))
        self.stdout.write(f"  {len(cases)} case(s); each is investigated live, so "
                          f"this takes a while.")
        self.stdout.write("")

        capturer = ExpectationCapturer(
            min_confidence=opts["min_confidence"],
            allow_tier2=True if opts["tier2"] else None)
        proposals = capturer.capture(
            cases, confirm=opts["confirm"], overwrite=opts["overwrite"],
            include_min_rows=not opts["no_min_rows"])

        for proposal in proposals:
            line = f"  {proposal.render()}"
            if proposal.written:
                self.stdout.write(self.style.SUCCESS(line))
            elif not proposal.eligible:
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)
            for name, (old, new) in proposal.already_declared.items():
                self.stdout.write(self.style.WARNING(
                    f"      declared {name}={old!r}, investigation says {new!r} "
                    f"— kept the declared value (use --overwrite to replace)"))

        stats = summarise(proposals)
        self.stdout.write("")
        self.stdout.write(f"  cases            {stats['cases']}")
        self.stdout.write(f"  eligible         {stats['eligible']}")
        self.stdout.write(f"  skipped          {stats['skipped']}")
        self.stdout.write(f"  would change     {stats['would_change']}")
        self.stdout.write(f"  disagreements    {stats['already_declared']}")
        self.stdout.write(f"  written          {stats['written']}")
        if stats["skip_reasons"]:
            self.stdout.write("")
            self.stdout.write("  why cases were skipped:")
            for reason, count in stats["skip_reasons"].items():
                self.stdout.write(f"    {count:>3}  {reason}")

        self.stdout.write("")
        if not opts["confirm"]:
            self.stdout.write(self.style.WARNING(
                "  Nothing was written. Re-run with --confirm once the proposals "
                "above look right."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  {stats['written']} case(s) updated. These are a baseline of "
                f"current behaviour, not verified ground truth — each case's "
                f"notes records that it was captured."))
        self.stdout.write("")

    @staticmethod
    def _select(BenchmarkCase, BenchmarkSuite, opts):
        if opts["case"]:
            cases = list(BenchmarkCase.objects.filter(pk__in=opts["case"]))
            missing = set(opts["case"]) - {c.pk for c in cases}
            if missing:
                raise CommandError(f"no such case id(s): {sorted(missing)}")
        else:
            suites = BenchmarkSuite.objects.all()
            name = (opts["suite"] or "").strip()
            if name:
                suite = (suites.filter(pk=name).first() if name.isdigit()
                         else suites.filter(name__icontains=name).first())
                if suite is None:
                    raise CommandError(
                        f"no suite matching {name!r}. Available: "
                        + (", ".join(s.name for s in suites) or "(none)"))
            elif suites.count() == 1:
                suite = suites.first()
            else:
                raise CommandError(
                    "--suite is required when there is more than one suite: "
                    + ", ".join(s.name for s in suites))
            cases = list(BenchmarkCase.objects
                         .filter(suite=suite, is_active=True).order_by("label"))

        if opts["blank_only"]:
            cases = [c for c in cases
                     if not (c.expected_data_source or c.expected_strategy
                             or c.expected_technology or c.min_rows)]
        if opts["limit"]:
            cases = cases[:opts["limit"]]
        return cases
