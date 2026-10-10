"""Drive the Factory's own work queue.

    python manage.py factory_scheduler --status
    python manage.py factory_scheduler --enqueue-due
    python manage.py factory_scheduler --drain 5
    python manage.py factory_scheduler --release-leases

This queue governs only the scrapers the Factory authored. The project's
APScheduler loop still runs the 77 production sources on its own schedule and is
not touched. Running `--drain` executes due jobs through the project's own runner,
so tenders are saved by the existing pipeline.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Inspect and drive the Factory's scheduled-job queue."

    def add_arguments(self, parser):
        parser.add_argument("--status", action="store_true")
        parser.add_argument("--enqueue-due", action="store_true",
                            help="Queue every installed generated scraper due now.")
        parser.add_argument("--drain", type=int, default=0, metavar="N",
                            help="Claim and run up to N due jobs.")
        parser.add_argument("--release-leases", action="store_true",
                            help="Return jobs whose worker died to the queue.")
        parser.add_argument("--list", type=int, default=0, metavar="N",
                            help="Show the next N queued jobs.")

    def handle(self, *args, **options):
        from ... import scheduling
        from ...models import ScheduledJob

        did_something = False

        if options["release_leases"]:
            n = scheduling.release_expired_leases()
            self.stdout.write(self.style.SUCCESS(
                f"released {n} expired lease(s)"))
            did_something = True

        if options["enqueue_due"]:
            n = scheduling.enqueue_due()
            self.stdout.write(self.style.SUCCESS(f"queued {n} job(s)"))
            did_something = True

        if options["drain"]:
            histories = scheduling.drain(max_jobs=options["drain"])
            if not histories:
                self.stdout.write("nothing due")
            for h in histories:
                verdict = "ok" if h.succeeded else (h.failure_category or "failed")
                self.stdout.write(
                    f"  {h.scraper.class_name:<28} attempt {h.attempt} "
                    f"{verdict:<24} {h.decision}")
            did_something = True

        if options["list"]:
            jobs = ScheduledJob.objects.select_related("scraper").order_by(
                "-priority", "next_run_at")[:options["list"]]
            self.stdout.write("")
            self.stdout.write(f"  {'scraper':<30}{'state':<10}{'pri':>4}"
                              f"{'att':>4}  due")
            for j in jobs:
                self.stdout.write(
                    f"  {j.scraper.class_name[:29]:<30}{j.state:<10}"
                    f"{j.priority:>4}{j.attempt:>4}  "
                    f"{j.next_run_at:%Y-%m-%d %H:%M}")
            did_something = True

        if options["status"] or not did_something:
            s = scheduling.queue_summary()
            self.stdout.write("")
            self.stdout.write("QUEUE")
            for key in ("pending", "leased", "due_now", "overdue_1h",
                        "retrying", "expired_leases"):
                self.stdout.write(f"  {key:<18} {s[key]}")
            self.stdout.write(f"  {'by state':<18} {s['by_state'] or '(empty)'}")

            policy = scheduling.default_policy()
            self.stdout.write("")
            self.stdout.write("DEFAULT RETRY POLICY")
            self.stdout.write(f"  {'name':<18} {policy.name}")
            self.stdout.write(f"  {'max attempts':<18} {policy.max_attempts}")
            self.stdout.write(f"  {'base delay':<18} {policy.base_delay_seconds}s")
            self.stdout.write(f"  {'multiplier':<18} {policy.backoff_multiplier}")
            self.stdout.write(f"  {'max delay':<18} {policy.max_delay_seconds}s")
            self.stdout.write(f"  {'retries':<18} "
                              f"{', '.join(policy.retry_categories) or '(transient only)'}")
            self.stdout.write("")
            self.stdout.write("  delays: " + ", ".join(
                f"attempt {a + 1} after {policy.delay_for(a)}s"
                for a in range(1, policy.max_attempts)))
