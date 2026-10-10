"""Remove an installed generated scraper — the fast rollback path.

Because the MVP installs automatically with no human review, a one-command
removal matters: if a generated scraper misbehaves in production this pulls it
out of the registry, deletes its file and deactivates its source in one step.

Already-scraped tenders are ALWAYS preserved — removing a scraper must never
destroy collected data.

    python manage.py factory_uninstall AqabaUtilitiesScraper
    python manage.py factory_uninstall --id 7
    python manage.py factory_uninstall AqabaUtilitiesScraper --keep-file
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from scraper_factory.installation.installer import InstallError, ScraperInstaller
from scraper_factory.models import (AuditAction, FactoryAuditLog, FactoryStatus,
                                     GeneratedScraper)


class Command(BaseCommand):
    help = ("Uninstall a generated scraper: unregister it, delete its file and "
            "deactivate its source. Collected tenders are preserved.")

    def add_arguments(self, parser):
        parser.add_argument("class_name", nargs="?", default=None,
                            help="Scraper class name, e.g. AqabaUtilitiesScraper")
        parser.add_argument("--id", type=int, default=None,
                            help="GeneratedScraper id instead of a class name.")
        parser.add_argument("--keep-file", action="store_true",
                            help="Leave the .py file in place (unregister only).")
        parser.add_argument("--reason", default="Manual uninstall",
                            help="Recorded in the audit log.")

    def handle(self, *args, **opts):
        record = None
        if opts["id"]:
            record = GeneratedScraper.objects.filter(pk=opts["id"]).first()
            if record is None:
                raise CommandError(f"No generated scraper with id {opts['id']}.")
        elif opts["class_name"]:
            record = GeneratedScraper.objects.filter(
                class_name=opts["class_name"]).first()
        else:
            raise CommandError("Provide a class name or --id.")

        class_name = record.class_name if record else opts["class_name"]
        module_name = record.module_name if record else None
        if module_name is None:
            raise CommandError(
                f"'{class_name}' is not a factory-generated scraper. Hand-written "
                "scrapers must be removed by a developer, not by this command.")

        self.stdout.write(self.style.WARNING(
            f"\n  Uninstalling {class_name} "
            f"(tenders/scrapers/{module_name}.py)"))

        try:
            result = ScraperInstaller().uninstall(
                module_name=module_name, class_name=class_name,
                delete_file=not opts["keep_file"])
        except InstallError as exc:
            raise CommandError(str(exc)) from exc

        if record is not None:
            record.status = FactoryStatus.RETIRED
            record.save(update_fields=["status", "updated_at"])
            try:
                FactoryAuditLog.objects.create(
                    action=AuditAction.UNINSTALL, scraper=record,
                    new_value={"steps": result.steps},
                    reason=opts["reason"][:300])
            except Exception:  # noqa: BLE001
                pass

        self.stdout.write(self.style.SUCCESS(
            f"  {result.message}\n  steps: {', '.join(result.steps)}\n"))
