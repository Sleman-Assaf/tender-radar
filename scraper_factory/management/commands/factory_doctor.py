"""Diagnose and repair the factory's installation state.

A crashed or stale process can leave a scraper file and a registry entry behind
without the database records that describe them. Such an orphan silently blocks
every later build for that name (validation rejects the duplicate), so it needs
to be findable and fixable in one command rather than by hand.

    python manage.py factory_doctor           # report only
    python manage.py factory_doctor --fix     # remove orphans

Checks performed:
  * orphan files      — a module in tenders/scrapers/ that the factory created
                        but which has no GeneratedScraper record
  * orphan registry   — an import/registry entry pointing at a missing module
  * stale records     — GeneratedScraper marked INSTALLED whose file is gone
  * dangling sources  — ScraperSource rows for a scraper_class that nothing
                        provides any more
  * leftover temp     — sandbox modules (_factory_tmp_*) left in the corpus

Hand-written scrapers are never touched: only modules the factory itself owns
(recorded in GeneratedScraper, or matching the sandbox temp prefix) are eligible.
"""

from __future__ import annotations

import pathlib
import re

from django.conf import settings
from django.core.management.base import BaseCommand

from scraper_factory.corpus.indexer import TEMP_PREFIX
from scraper_factory.models import FactoryStatus, GeneratedScraper


class Command(BaseCommand):
    help = "Report (and optionally fix) orphaned scraper files and registry entries."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true",
                            help="Actually remove what is found.")

    def handle(self, *args, **opts):
        fix = opts["fix"]
        self.scrapers_dir = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR"))
        self.registry_path = self.scrapers_dir / "__init__.py"
        registry_text = self.registry_path.read_text(encoding="utf-8")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  FACTORY DOCTOR{'  (fixing)' if fix else '  (report only)'}\n"))

        problems = 0
        problems += self._temp_files(fix)
        problems += self._orphan_files(registry_text, fix)
        problems += self._unreferenced_files(registry_text)
        problems += self._orphan_registry(registry_text, fix)
        problems += self._stale_records(fix)
        problems += self._dangling_sources(fix)

        if problems == 0:
            self.stdout.write(self.style.SUCCESS(
                "  No problems found. Installation state is consistent.\n"))
        elif fix:
            self.stdout.write(self.style.SUCCESS(
                f"\n  Fixed {problems} problem(s). Re-run to confirm.\n"))
        else:
            self.stdout.write(self.style.WARNING(
                f"\n  {problems} problem(s) found. Re-run with --fix to repair.\n"))

    # ── checks ────────────────────────────────────────────────────────────────

    def _temp_files(self, fix: bool) -> int:
        found = sorted(self.scrapers_dir.glob(f"{TEMP_PREFIX}*.py"))
        for path in found:
            self.stdout.write(self.style.WARNING(
                f"    leftover sandbox module: {path.name}"))
            if fix:
                path.unlink(missing_ok=True)
                self.stdout.write("        removed")
        # Runner scripts are written next to manage.py.
        root = self.scrapers_dir.parent.parent
        for path in sorted(root.glob("_factory_runner_*.py")):
            found.append(path)
            self.stdout.write(self.style.WARNING(
                f"    leftover sandbox runner: {path.name}"))
            if fix:
                path.unlink(missing_ok=True)
                self.stdout.write("        removed")
        return len(found)

    def _orphan_files(self, registry_text: str, fix: bool) -> int:
        """Factory-OWNED files with no GeneratedScraper record backing them.

        Ownership comes from the installer's manifest, never from inspecting the
        file. Generated scrapers are deliberately indistinguishable from
        hand-written ones, so any content-based heuristic would eventually
        misidentify a developer's scraper — and this command deletes files.
        Only modules the factory explicitly claimed are ever eligible.
        """
        from scraper_factory.installation.installer import ScraperInstaller

        installer = ScraperInstaller()
        owned = installer.owned_modules()
        if not owned:
            return 0

        known = set(GeneratedScraper.objects.values_list("module_name", flat=True))
        count = 0
        for module, meta in sorted(owned.items()):
            path = self.scrapers_dir / f"{module}.py"
            if not path.exists():
                # Claimed but gone: just drop the stale claim.
                self.stdout.write(self.style.WARNING(
                    f"    stale ownership claim (file gone): {module}.py"))
                count += 1
                if fix:
                    installer.release_module(module)
                    self.stdout.write("        claim released")
                continue
            if module in known:
                continue
            count += 1
            self.stdout.write(self.style.WARNING(
                f"    orphan file (factory-owned, no DB record): {module}.py"))
            if fix:
                self._remove(module, meta.get("class_name", ""))
        return count

    def _unreferenced_files(self, registry_text: str) -> int:
        """Report-only: files in the corpus that the registry does not import.

        Never auto-removed. A file here may be a factory leftover or a scraper a
        developer is still working on, and the difference is not ours to guess.
        """
        count = 0
        for path in sorted(self.scrapers_dir.glob("*.py")):
            if path.name in ("__init__.py", "base.py") or \
                    path.name.startswith("_"):
                continue
            if re.search(rf"^from \.{re.escape(path.stem)}\s+import",
                         registry_text, re.MULTILINE):
                continue
            count += 1
            self.stdout.write(self.style.WARNING(
                f"    not imported by __init__.py: {path.name}  "
                "(review by hand; never auto-removed)"))
        return count

    def _orphan_registry(self, registry_text: str, fix: bool) -> int:
        """Registry imports whose module file no longer exists."""
        count = 0
        for m in re.finditer(r"^from \.(\w+)\s+import\s+(\w+)\s*$",
                             registry_text, re.MULTILINE):
            module, cls = m.group(1), m.group(2)
            if (self.scrapers_dir / f"{module}.py").exists():
                continue
            count += 1
            self.stdout.write(self.style.ERROR(
                f"    registry points at a missing module: "
                f"from .{module} import {cls}"))
            if fix:
                self._remove(module, cls)
        return count

    def _stale_records(self, fix: bool) -> int:
        count = 0
        for rec in GeneratedScraper.objects.filter(
                status=FactoryStatus.INSTALLED):
            if (self.scrapers_dir / f"{rec.module_name}.py").exists():
                continue
            count += 1
            self.stdout.write(self.style.WARNING(
                f"    record #{rec.pk} says INSTALLED but "
                f"{rec.module_name}.py is missing"))
            if fix:
                rec.status = FactoryStatus.RETIRED
                rec.save(update_fields=["status", "updated_at"])
                self.stdout.write("        marked RETIRED")
        return count

    def _dangling_sources(self, fix: bool) -> int:
        """Active ScraperSource rows whose scraper_class nothing provides."""
        from tenders.models import ScraperSource
        from tenders.scrapers import SCRAPER_REGISTRY

        count = 0
        factory_classes = set(
            GeneratedScraper.objects.values_list("class_name", flat=True))
        for src in ScraperSource.objects.filter(is_active=True):
            cls = src.scraper_class
            if cls in SCRAPER_REGISTRY or cls == "__DYNAMIC__":
                continue
            # Only report sources the factory could have created; a legacy row
            # pointing at a class a developer removed is not ours to judge.
            if cls not in factory_classes and "sandbox" not in src.name.lower() \
                    and "dry-run" not in src.name.lower():
                continue
            count += 1
            self.stdout.write(self.style.WARNING(
                f"    active source #{src.pk} '{src.name}' has no scraper "
                f"class '{cls}'"))
            if fix:
                src.is_active = False
                src.save(update_fields=["is_active"])
                self.stdout.write("        deactivated (tenders preserved)")
        return count

    # ── helpers ───────────────────────────────────────────────────────────────

    def _remove(self, module: str, class_name: str) -> None:
        from scraper_factory.installation.installer import (InstallError,
                                                            ScraperInstaller)
        try:
            result = ScraperInstaller().uninstall(
                module_name=module, class_name=class_name or f"{module}Scraper")
            self.stdout.write(f"        removed ({', '.join(result.steps)})")
        except InstallError as exc:
            self.stdout.write(self.style.ERROR(f"        could not remove: {exc}"))
