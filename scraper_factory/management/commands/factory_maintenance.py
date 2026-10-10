"""Retention sweeps, resource limits and safety verification.

    python manage.py factory_maintenance --verify
    python manage.py factory_maintenance --cleanup            # dry run
    python manage.py factory_maintenance --cleanup --apply    # actually delete

Cleanup is dry-run unless --apply is given, because a retention sweep that
deletes the wrong thing is not recoverable and the counts are worth reading
first.
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Factory cleanup, resource limits and safety verification."

    def add_arguments(self, parser):
        parser.add_argument("--cleanup", action="store_true")
        parser.add_argument("--apply", action="store_true",
                            help="With --cleanup: actually delete.")
        parser.add_argument("--verify", action="store_true")
        parser.add_argument("--resources", action="store_true")

    def handle(self, *args, **options):
        from ...maintenance import ResourceLimiter, cleanup, verify_safety

        did = False
        failed = False

        if options["cleanup"]:
            did = True
            dry = not options["apply"]
            results = cleanup(dry_run=dry)
            self.stdout.write("")
            self.stdout.write("CLEANUP " + ("(dry run — nothing deleted)" if dry
                                            else "(APPLIED)"))
            for name, detail in results.items():
                if not isinstance(detail, dict):
                    continue
                extra = {k: v for k, v in detail.items()
                         if k not in ("removed", "files")}
                self.stdout.write(
                    f"  {name:<16} {detail.get('removed', 0):>6}   {extra}")
            self.stdout.write("")
            verb = "would remove" if dry else "removed"
            self.stdout.write(self.style.SUCCESS(
                f"  {verb} {results['total_removed']} item(s)"))
            if dry and results["total_removed"]:
                self.stdout.write("  re-run with --apply to delete")

        if options["resources"]:
            did = True
            self.stdout.write("")
            self.stdout.write("RESOURCE LIMITS")
            for kind in ("sandbox", "browser", "generation"):
                self.stdout.write(f"  {kind:<12} limit "
                                  f"{ResourceLimiter.limit_for(kind)}")
            live = ResourceLimiter.snapshot()
            self.stdout.write(f"  in use now : {live or '(no slots taken)'}")

        if options["verify"] or not did:
            self.stdout.write("")
            self.stdout.write("SAFETY VERIFICATION")
            for name, (ok, detail) in verify_safety().items():
                if ok:
                    self.stdout.write(f"  [ok]   {name:<22} {detail}")
                else:
                    failed = True
                    self.stdout.write(self.style.ERROR(
                        f"  [FAIL] {name:<22} {detail}"))
            self.stdout.write("")
            if failed:
                self.stdout.write(self.style.ERROR("SAFETY CHECK FAILED"))
                sys.exit(1)
            self.stdout.write(self.style.SUCCESS("all invariants hold"))
