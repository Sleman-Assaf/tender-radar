"""Investigate a website and print the report and plan — no code is generated.

    python manage.py factory_investigate https://portal.example.jo/tenders
    python manage.py factory_investigate <url> --no-tier2 --json
    python manage.py factory_investigate <url> --save

This is the tool a developer reaches for when a build was rejected: it shows the
evidence the Factory acted on, the root cause if a stage failed, and why the
Planner chose (or refused) a strategy.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from ...investigation.engine import InvestigationEngine
from ...planning.planner import Planner


class Command(BaseCommand):
    help = "Investigate a tender website and report the evidence and plan."

    def add_arguments(self, parser):
        parser.add_argument("url")
        parser.add_argument("--no-tier2", action="store_true",
                            help="Never launch a browser, even if Tier 1 is inconclusive.")
        parser.add_argument("--no-snapshot", action="store_true",
                            help="Do not store an HTML snapshot.")
        parser.add_argument("--json", action="store_true",
                            help="Emit the machine-readable report.")
        parser.add_argument("--save", action="store_true",
                            help="Persist an InvestigationRecord row.")

    def handle(self, *args, **options):
        url = options["url"]
        if not url.lower().startswith(("http://", "https://")):
            raise CommandError("URL must start with http:// or https://")

        # None, not True, when the flag is absent — otherwise the command would
        # always override FACTORY_TIER2_ENABLED and the setting would do nothing.
        report = InvestigationEngine().investigate(
            url, allow_tier2=(False if options["no_tier2"] else None),
            capture_snapshot=not options["no_snapshot"])
        plan = Planner().plan(report)

        if options["json"]:
            self.stdout.write(json.dumps(
                {"investigation": report.to_dict(), "plan": plan.to_dict()},
                indent=2, ensure_ascii=False))
        else:
            self.stdout.write(report.render())
            self.stdout.write("")
            self.stdout.write(plan.render())
            self.stdout.write("")
            self.stdout.write(f"  corpus family : {plan.family}")
            self.stdout.write(f"  duration      : {report.duration_ms} ms")
            unknowns = report.all_unknowns()
            if unknowns:
                self.stdout.write(f"\n  {len(unknowns)} unknown(s):")
                for u in unknowns:
                    self.stdout.write(f"    ? {u}")

        if options["save"]:
            record = self._save(report, plan)
            self.stdout.write(self.style.SUCCESS(
                f"\nSaved InvestigationRecord #{record.pk}"))

        if plan.viable:
            self.stdout.write(self.style.SUCCESS(
                f"\nReady to generate: {plan.strategy}"))
        else:
            self.stdout.write(self.style.WARNING(
                "\nGeneration would be refused — see the blockers above."))

    @staticmethod
    def _save(report, plan):
        from ...models import InvestigationRecord

        root = report.root_cause()
        return InvestigationRecord.objects.create(
            url=report.url[:500], tier=report.tier, escalated=report.escalated,
            escalation_reason=report.escalation_reason[:300],
            data_source=report.data_source,
            data_source_confidence=report.data_source_confidence,
            overall_confidence=report.overall_confidence(),
            complete=report.complete,
            root_cause_stage=(root.stage if root else ""),
            failure_category=(root.failure if root else ""),
            stages={k: v.to_dict() for k, v in report.stages.items()},
            unknowns=report.all_unknowns(),
            strategy=plan.strategy, strategy_confidence=plan.confidence,
            plan_viable=plan.viable, plan=plan.to_dict(),
            site_signature=report.site_signature,
            language=report.language[:8], country_hint=report.country_hint[:4],
            duration_ms=report.duration_ms)
