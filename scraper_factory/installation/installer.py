"""ScraperInstaller — transactional install of a generated scraper (R1).

Registration strategy R1, as approved: the scraper file goes into
`tenders/scrapers/` and `tenders/scrapers/__init__.py` gains an import line and a
`SCRAPER_REGISTRY` entry — byte-identical to what a developer writes by hand, so
the repository stays indistinguishable from a manually maintained one.

Because there is no human review gate in the MVP, installation is the last line
of defence and is therefore strictly transactional. Every step is undoable and
any failure restores the repository to its exact previous state:

    1. pre-flight  : names free, code non-empty
    2. backup      : copy __init__.py to the backup directory
    3. write file  : temp file + os.replace (atomic)
    4. verify      : import the module in a SUBPROCESS; a syntax error or a bad
                     import can therefore never poison the running server
    5. registry    : AST-guided insertion of the import + dict entry
    6. verify again: import the whole package in a subprocess
    7. db rows     : ScraperSource(is_active=True) + TenderingURL
    8. runtime reg : SCRAPER_REGISTRY[class] = cls  (usable with no restart)

Any exception after step 3 triggers a full rollback in reverse order.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .lock import install_lock

logger = logging.getLogger("scraper_factory")


class InstallError(RuntimeError):
    """Raised when installation fails; the repository is left untouched."""


#: Installation modes.
MODE_INSTALL = "install"
MODE_DRY_RUN = "dry_run"


@dataclass
class InstallResult:
    ok: bool = False
    mode: str = MODE_INSTALL
    module_path: str = ""
    file_hash: str = ""
    scraper_source_id: Optional[int] = None
    registered: bool = False
    registry_patched: bool = False
    backup_path: str = ""
    replaced_backup_id: Optional[int] = None
    message: str = ""
    steps: list[str] = field(default_factory=list)


class ScraperInstaller:
    """Installs a validated, sandbox-tested scraper into the live project."""

    def __init__(self):
        self.scrapers_dir = pathlib.Path(getattr(settings, "FACTORY_SCRAPERS_DIR"))
        self.backup_dir = pathlib.Path(getattr(settings, "FACTORY_BACKUP_DIR"))
        self.project_root = self.scrapers_dir.parent.parent
        self.registry_path = self.scrapers_dir / "__init__.py"

    # ── public API ────────────────────────────────────────────────────────────

    def install(self, *, source_code: str, module_name: str, class_name: str,
                country_code: str, source_name: str, website_url: str,
                generated_scraper=None, user=None,
                allow_replace: bool = False) -> InstallResult:
        """Permanently install a scraper.

        With ``allow_replace`` an existing module of the same name is backed up
        first (content + metadata) so it can be restored with one operation.
        """
        # Serialised: concurrent builds would corrupt each other's registry
        # rollback (see installation/lock.py).
        with install_lock(purpose=f"install:{module_name}"):
            return self._install_locked(
                source_code=source_code, module_name=module_name,
                class_name=class_name, country_code=country_code,
                source_name=source_name, website_url=website_url,
                generated_scraper=generated_scraper, user=user,
                allow_replace=allow_replace)

    def _install_locked(self, *, source_code: str, module_name: str,
                        class_name: str, country_code: str, source_name: str,
                        website_url: str, generated_scraper=None, user=None,
                        allow_replace: bool = False) -> InstallResult:
        result = InstallResult(mode=MODE_INSTALL)
        module_file = self.scrapers_dir / f"{module_name}.py"

        replaced_backup = None
        if allow_replace and module_file.exists():
            replaced_backup = self.backup_existing(
                module_name=module_name, class_name=class_name,
                generated_scraper=generated_scraper, user=user)
            result.replaced_backup_id = getattr(replaced_backup, "pk", None)
            result.steps.append("backup-replaced-version")
            # Take the old registration out of the way before rewriting.
            self._unpatch_registry(module_name, class_name)

        self._preflight(source_code, module_name, class_name, module_file,
                        allow_existing=bool(replaced_backup))
        result.steps.append("preflight")

        backup = self._backup_registry()
        result.backup_path = str(backup) if backup else ""
        result.steps.append("backup")

        wrote_file = False
        patched = False
        source_row = None

        try:
            self._atomic_write(module_file, source_code)
            wrote_file = True
            self.claim_module(module_name, class_name)
            result.module_path = str(module_file)
            result.file_hash = hashlib.sha256(
                source_code.encode("utf-8")).hexdigest()
            result.steps.append("write")

            self._verify_module_imports(module_name, class_name)
            result.steps.append("verify-module")

            self._patch_registry(module_name, class_name)
            patched = True
            result.registry_patched = True
            result.steps.append("patch-registry")

            self._verify_package_imports(class_name)
            result.steps.append("verify-package")

            source_row = self._create_db_rows(
                class_name=class_name, country_code=country_code,
                source_name=source_name, website_url=website_url)
            result.scraper_source_id = source_row.pk
            result.steps.append("db-rows")

            result.registered = self._register_runtime(module_name, class_name)
            result.steps.append("runtime-register")

            result.ok = True
            result.message = (
                f"Installed tenders/scrapers/{module_name}.py, registered "
                f"{class_name}, and created an active ScraperSource.")
            self._record(generated_scraper, result, user)
            logger.info("[factory] installed %s", class_name)
            return result

        except Exception as exc:  # noqa: BLE001 — always roll back
            logger.error("[factory] install failed, rolling back: %s", exc,
                         exc_info=True)
            self._rollback(module_file=module_file if wrote_file else None,
                           backup=backup if patched else None,
                           source_row=source_row, class_name=class_name)
            # A replacement that fails must leave the PREVIOUS working scraper
            # in place — a failed upgrade may never cost us a live scraper.
            if replaced_backup is not None:
                try:
                    self.restore(replaced_backup)
                    logger.info("[factory] restored previous version of %s",
                                module_name)
                except Exception as restore_exc:  # noqa: BLE001
                    logger.error("[factory] CRITICAL: could not restore previous "
                                 "version of %s: %s", module_name, restore_exc)
            raise InstallError(
                f"Installation failed and was rolled back: {exc}") from exc

    @contextlib.contextmanager
    def dry_run(self, *, source_code: str, module_name: str, class_name: str,
                country_code: str, source_name: str, website_url: str):
        """Install temporarily, yield, then undo EVERYTHING.

        Used by "Generate & Test": the scraper is written, registered, given a
        real ScraperSource and executed for real — then the filesystem and the
        database are both returned to their exact previous state.

        Filesystem changes are undone in a finally block; database changes are
        undone by rolling back the surrounding transaction. The caller runs the
        scraper inside the `with` body, so tenders it saves are written and then
        discarded, which exercises the true persistence path without keeping any
        rows.
        """
        module_file = self.scrapers_dir / f"{module_name}.py"
        result = InstallResult(mode=MODE_DRY_RUN)

        # Held for the whole dry run, including the caller's `with` body, so no
        # other build can patch the registry while this one is rolled back.
        lock = install_lock(purpose=f"dry_run:{module_name}")
        lock.__enter__()
        self._preflight(source_code, module_name, class_name, module_file)
        registry_backup = self._backup_registry()
        result.backup_path = str(registry_backup) if registry_backup else ""

        wrote_file = False
        patched = False

        try:
            self._atomic_write(module_file, source_code)
            wrote_file = True
            self.claim_module(module_name, class_name)
            result.module_path = str(module_file)
            result.file_hash = hashlib.sha256(
                source_code.encode("utf-8")).hexdigest()
            result.steps.append("write(temp)")

            self._verify_module_imports(module_name, class_name)
            result.steps.append("verify-module")

            self._patch_registry(module_name, class_name)
            patched = True
            result.registry_patched = True
            result.steps.append("patch-registry(temp)")

            self._verify_package_imports(class_name)
            result.steps.append("verify-package")

            result.registered = self._register_runtime(module_name, class_name)
            result.steps.append("runtime-register(temp)")

            # Everything inside this transaction is discarded on exit.
            with transaction.atomic():
                source_row = self._create_db_rows(
                    class_name=class_name, country_code=country_code,
                    source_name=f"[dry-run] {source_name}",
                    website_url=website_url)
                result.scraper_source_id = source_row.pk
                result.steps.append("db-rows(temp)")
                result.ok = True
                try:
                    yield result
                finally:
                    transaction.set_rollback(True)
                    result.steps.append("db-rollback")

        finally:
            # Undo the filesystem side no matter what happened.
            #
            # Remove exactly the two lines we added rather than restoring the
            # whole file: a whole-file restore would clobber any entry another
            # build added in the meantime. The captured backup stays as a
            # last-resort safety net if the surgical removal fails.
            if patched:
                try:
                    self._unpatch_registry(module_name, class_name)
                    result.steps.append("registry-unpatched")
                except Exception as exc:  # noqa: BLE001
                    logger.error("[factory] surgical unpatch failed (%s); "
                                 "falling back to the file backup", exc)
                    if registry_backup is not None:
                        self._restore_registry(registry_backup)
                        result.steps.append("registry-restored(fallback)")
            if wrote_file:
                try:
                    module_file.unlink(missing_ok=True)
                    self._purge_pycache(module_name)
                    self.release_module(module_name)
                    result.steps.append("file-removed")
                except OSError as exc:
                    logger.error("[factory] dry run could not remove %s: %s",
                                 module_file, exc)
            try:
                from tenders.scrapers import SCRAPER_REGISTRY
                SCRAPER_REGISTRY.pop(class_name, None)
                result.steps.append("runtime-unregistered")
            except Exception:  # noqa: BLE001
                pass
            lock.__exit__(None, None, None)

    # ── versioning: backup and restore ───────────────────────────────────────

    def backup_existing(self, *, module_name: str, class_name: str,
                        reason: str = "REPLACED", generated_scraper=None,
                        user=None):
        """Snapshot an installed scraper file before it is replaced."""
        from ..models import ScraperCodeVersion, ScraperFileBackup

        module_file = self.scrapers_dir / f"{module_name}.py"
        if not module_file.exists():
            return None

        content = module_file.read_text(encoding="utf-8", errors="replace")
        registry_backup = self._backup_registry()

        code_version = None
        model_name = prompt_version = ""
        if generated_scraper is not None:
            code_version = (ScraperCodeVersion.objects
                            .filter(scraper=generated_scraper)
                            .order_by("-version_number").first())
            if code_version is not None:
                model_name = code_version.model_name or ""
                prompt_version = str(
                    (code_version.model_params or {}).get("prompt_version", ""))

        return ScraperFileBackup.objects.create(
            module_name=module_name, class_name=class_name,
            source_code=content,
            file_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            reason=reason,
            was_generated=generated_scraper is not None,
            scraper=generated_scraper, code_version=code_version,
            model_name=model_name, prompt_version=prompt_version,
            registry_backup_path=str(registry_backup) if registry_backup else "",
            created_by=user,
        )

    def restore(self, backup) -> InstallResult:
        """Restore a previous scraper version from a backup, transactionally."""
        result = InstallResult(mode=MODE_INSTALL)
        module_file = self.scrapers_dir / f"{backup.module_name}.py"
        safety = self._backup_registry()

        try:
            self._atomic_write(module_file, backup.source_code)
            result.module_path = str(module_file)
            result.file_hash = backup.file_hash
            result.steps.append("restore-file")

            self._verify_module_imports(backup.module_name, backup.class_name)
            result.steps.append("verify-module")

            # Re-register only if the registry no longer mentions it.
            text = self.registry_path.read_text(encoding="utf-8")
            if f"import {backup.class_name}" not in text:
                self._patch_registry(backup.module_name, backup.class_name)
                result.registry_patched = True
                result.steps.append("patch-registry")

            self._verify_package_imports(backup.class_name)
            result.steps.append("verify-package")

            result.registered = self._register_runtime(
                backup.module_name, backup.class_name)

            from tenders.models import ScraperSource
            ScraperSource.objects.filter(
                scraper_class=backup.class_name).update(is_active=True)
            result.steps.append("reactivate-source")

            backup.restored_at = timezone.now()
            backup.save(update_fields=["restored_at"])

            result.ok = True
            result.message = (
                f"Restored {backup.class_name} from the backup taken "
                f"{backup.created_at:%Y-%m-%d %H:%M}.")
            return result
        except Exception as exc:  # noqa: BLE001
            if safety is not None:
                self._restore_registry(safety)
            raise InstallError(f"Restore failed: {exc}") from exc

    def uninstall(self, *, module_name: str, class_name: str,
                  delete_file: bool = True, deactivate_source: bool = True
                  ) -> InstallResult:
        """Reverse an installation. Scraped tenders are always preserved."""
        result = InstallResult()
        with install_lock(purpose=f"uninstall:{module_name}"):
            backup = self._backup_registry()
            result.backup_path = str(backup) if backup else ""
            return self._uninstall_locked(module_name, class_name, delete_file,
                                          deactivate_source, backup, result)

    def _uninstall_locked(self, module_name, class_name, delete_file,
                          deactivate_source, backup, result):
        try:
            if self._unpatch_registry(module_name, class_name):
                result.registry_patched = True
                result.steps.append("unpatch-registry")

            if delete_file:
                path = self.scrapers_dir / f"{module_name}.py"
                path.unlink(missing_ok=True)
                self._purge_pycache(module_name)
                self.release_module(module_name)
                result.steps.append("delete-file")

            if deactivate_source:
                from tenders.models import ScraperSource
                updated = ScraperSource.objects.filter(
                    scraper_class=class_name).update(is_active=False)
                if updated:
                    result.steps.append("deactivate-source")

            try:
                from tenders.scrapers import SCRAPER_REGISTRY
                SCRAPER_REGISTRY.pop(class_name, None)
                result.steps.append("runtime-unregister")
            except Exception:  # noqa: BLE001
                pass

            self._verify_package_imports(None)
            result.ok = True
            result.message = (f"Uninstalled {class_name}. Existing tenders were "
                              "kept; the source is deactivated.")
            return result
        except Exception as exc:  # noqa: BLE001
            if backup is not None:
                self._restore_registry(backup)
            raise InstallError(f"Uninstall failed: {exc}") from exc

    # ── steps ─────────────────────────────────────────────────────────────────

    def _preflight(self, source_code: str, module_name: str, class_name: str,
                   module_file: pathlib.Path,
                   allow_existing: bool = False) -> None:
        if not source_code.strip():
            raise InstallError("Refusing to install empty source code.")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", module_name):
            raise InstallError(
                f"Module name '{module_name}' is not lower_snake_case.")
        if not class_name.endswith("Scraper"):
            raise InstallError(
                f"Class name '{class_name}' must end with 'Scraper'.")
        if module_file.exists() and not allow_existing:
            raise InstallError(
                f"tenders/scrapers/{module_name}.py already exists.")
        if not allow_existing:
            try:
                from tenders.scrapers import SCRAPER_REGISTRY
                if class_name in SCRAPER_REGISTRY:
                    raise InstallError(
                        f"'{class_name}' is already in SCRAPER_REGISTRY.")
            except ImportError:
                pass
        if not self.registry_path.exists():
            raise InstallError(f"Registry not found at {self.registry_path}.")

    def _backup_registry(self) -> Optional[pathlib.Path]:
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = timezone.now().strftime("%Y%m%d_%H%M%S_%f")
            target = self.backup_dir / f"scrapers_init_{stamp}.py.bak"
            shutil.copy2(self.registry_path, target)
            return target
        except OSError as exc:
            raise InstallError(f"Could not back up the registry: {exc}") from exc

    @staticmethod
    def _atomic_write(path: pathlib.Path, content: str) -> None:
        """Write via a temp file + os.replace so the file is never half-written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            os.replace(tmp, path)
        except Exception:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise

    # ── verification in a child process ───────────────────────────────────────

    def _verify_module_imports(self, module_name: str, class_name: str) -> None:
        code = (
            "import django, os;"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE','tender_radar.settings');"
            "django.setup();"
            f"from tenders.scrapers.{module_name} import {class_name} as C;"
            "from tenders.scrapers.base import BaseScraper;"
            "assert issubclass(C, BaseScraper), 'not a BaseScraper subclass';"
            "assert C.country_code and C.source_name, 'missing class attributes';"
            "print('OK')"
        )
        self._run_child(code, "the generated module could not be imported")

    def _verify_package_imports(self, class_name: Optional[str]) -> None:
        """Import the whole scrapers package — catches a broken registry edit."""
        extra = ""
        if class_name:
            extra = (f"assert '{class_name}' in R, "
                     f"'{class_name} missing from SCRAPER_REGISTRY';")
        code = (
            "import django, os;"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE','tender_radar.settings');"
            "django.setup();"
            "from tenders.scrapers import SCRAPER_REGISTRY as R;"
            f"{extra}"
            "print('OK', len(R))"
        )
        self._run_child(code, "tenders/scrapers/__init__.py is broken")

    def _run_child(self, code: str, what: str) -> None:
        env = dict(os.environ)
        env["DJANGO_SETTINGS_MODULE"] = "tender_radar.settings"
        env["PYTHONPATH"] = str(self.project_root)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code], cwd=str(self.project_root),
                env=env, capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise InstallError(f"{what}: verification timed out") from exc
        if proc.returncode != 0 or "OK" not in (proc.stdout or ""):
            detail = ((proc.stderr or "").strip().splitlines() or ["(no output)"])[-1]
            raise InstallError(f"{what}: {detail}")

    # ── registry editing (AST-guided, reversible) ─────────────────────────────

    def _patch_registry(self, module_name: str, class_name: str) -> None:
        """Insert the import line and the dict entry, exactly as a human would."""
        text = self.registry_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        import_line = f"from .{module_name} import {class_name}"
        entry_line = f"    '{class_name}': {class_name},"

        # Locate the last `from .x import Y` line via AST, so we insert into the
        # real import block rather than guessing with a regex.
        tree = ast.parse(text)
        last_import_lineno = 0
        registry_node = None
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                last_import_lineno = max(last_import_lineno,
                                         getattr(node, "end_lineno", node.lineno))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and \
                            target.id == "SCRAPER_REGISTRY":
                        registry_node = node
            elif isinstance(node, ast.AnnAssign):
                # The project declares it as an ANNOTATED assignment:
                #   SCRAPER_REGISTRY: dict[str, type] = { ... }
                # which is an AnnAssign, not an Assign.
                if isinstance(node.target, ast.Name) and \
                        node.target.id == "SCRAPER_REGISTRY":
                    registry_node = node

        if last_import_lineno == 0 or registry_node is None:
            raise InstallError(
                "Could not locate the import block and SCRAPER_REGISTRY in "
                "tenders/scrapers/__init__.py.")

        # The dict's closing brace is the last line of the assignment.
        registry_end = getattr(registry_node, "end_lineno", None)
        if registry_end is None:
            raise InstallError("Could not determine the end of SCRAPER_REGISTRY.")

        closing_idx = registry_end - 1
        if "}" not in lines[closing_idx]:
            closing_idx = next(
                (i for i in range(registry_end - 1,
                                  max(registry_end - 6, 0), -1)
                 if "}" in lines[i]), None)
            if closing_idx is None:
                raise InstallError(
                    "Could not find the closing brace of SCRAPER_REGISTRY.")

        # Insert the dict entry first (higher line number), then the import, so
        # the earlier insertion does not shift the later index.
        new_lines = list(lines)
        new_lines.insert(closing_idx, entry_line)
        new_lines.insert(last_import_lineno, import_line)

        patched = "\n".join(new_lines)
        if not patched.endswith("\n"):
            patched += "\n"

        # Never write a registry that does not parse.
        try:
            ast.parse(patched)
        except SyntaxError as exc:
            raise InstallError(
                f"Refusing to write a registry that does not parse: {exc}") from exc

        self._atomic_write(self.registry_path, patched)
        self._purge_pycache("__init__")

    def _unpatch_registry(self, module_name: str, class_name: str) -> bool:
        text = self.registry_path.read_text(encoding="utf-8")
        import_pat = re.compile(
            rf"^from \.{re.escape(module_name)} import {re.escape(class_name)}\s*$")
        entry_pat = re.compile(
            rf"^\s*'{re.escape(class_name)}'\s*:\s*{re.escape(class_name)},\s*$")

        kept = [ln for ln in text.splitlines()
                if not import_pat.match(ln) and not entry_pat.match(ln)]
        patched = "\n".join(kept)
        if not patched.endswith("\n"):
            patched += "\n"
        if patched == text:
            return False
        ast.parse(patched)
        self._atomic_write(self.registry_path, patched)
        self._purge_pycache("__init__")
        return True

    def _restore_registry(self, backup: pathlib.Path) -> None:
        try:
            shutil.copy2(backup, self.registry_path)
            self._purge_pycache("__init__")
            logger.info("[factory] restored registry from %s", backup.name)
        except OSError as exc:
            logger.error("[factory] CRITICAL: could not restore registry from "
                         "%s: %s", backup, exc)

    # ── database rows ─────────────────────────────────────────────────────────

    @transaction.atomic
    def _create_db_rows(self, *, class_name: str, country_code: str,
                        source_name: str, website_url: str):
        """Create the REAL, active source so the existing scheduler runs it."""
        from tenders.models import CountryConfig, ScraperSource, TenderingURL

        country = CountryConfig.objects.filter(code=country_code.upper()).first()
        if country is None:
            raise InstallError(
                f"Country '{country_code}' is not in CountryConfig. Add it first.")

        source = ScraperSource.objects.create(
            name=source_name[:200], country=country,
            base_url=website_url[:500], scraper_class=class_name,
            is_active=True,
            description=("Created by the Scraper Factory. Runs on the existing "
                         "schedule like any other source."),
        )
        TenderingURL.objects.create(
            source=source, label="Main listing", url=website_url[:1000],
            max_pages=5, is_active=True)
        return source

    # ── runtime registration ──────────────────────────────────────────────────

    @staticmethod
    def _register_runtime(module_name: str, class_name: str) -> bool:
        from ..registration import register_runtime
        return register_runtime(module_name, class_name)

    # ── rollback ──────────────────────────────────────────────────────────────

    def _rollback(self, *, module_file: Optional[pathlib.Path],
                  backup: Optional[pathlib.Path], source_row,
                  class_name: str) -> None:
        if source_row is not None:
            try:
                from tenders.models import TenderingURL
                TenderingURL.objects.filter(source=source_row).delete()
                source_row.delete()
            except Exception as exc:  # noqa: BLE001
                logger.error("[factory] rollback: could not delete source: %s", exc)

        if backup is not None:
            self._restore_registry(backup)

        if module_file is not None:
            try:
                module_file.unlink(missing_ok=True)
                self._purge_pycache(module_file.stem)
                self.release_module(module_file.stem)
            except OSError as exc:
                logger.error("[factory] rollback: could not delete %s: %s",
                             module_file, exc)

        try:
            from tenders.scrapers import SCRAPER_REGISTRY
            SCRAPER_REGISTRY.pop(class_name, None)
        except Exception:  # noqa: BLE001
            pass

    # ── ownership manifest ───────────────────────────────────────────────────
    # The generated file must be indistinguishable from a hand-written one, so
    # it cannot carry an "AI generated" marker. Ownership is therefore recorded
    # OUTSIDE the corpus, in a manifest the factory maintains. Without this,
    # any cleanup tool would have to guess which files it owns — and guessing
    # wrong means deleting a developer's scraper.

    def _manifest_path(self) -> pathlib.Path:
        return pathlib.Path(
            getattr(settings, "FACTORY_BACKUP_DIR")).parent / "owned_modules.json"

    def _read_manifest(self) -> dict:
        import json
        path = self._manifest_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}

    def _write_manifest(self, data: dict) -> None:
        import json
        path = self._manifest_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(
                path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        except Exception as exc:  # noqa: BLE001 — never fail an install over this
            logger.warning("[factory] could not update ownership manifest: %s",
                           exc)

    def claim_module(self, module_name: str, class_name: str) -> None:
        data = self._read_manifest()
        data[module_name] = {
            "class_name": class_name,
            "claimed_at": timezone.now().isoformat(),
        }
        self._write_manifest(data)

    def release_module(self, module_name: str) -> None:
        data = self._read_manifest()
        if data.pop(module_name, None) is not None:
            self._write_manifest(data)

    def owned_modules(self) -> dict:
        """Modules the factory created — the only ones cleanup may touch."""
        return self._read_manifest()

    def _purge_pycache(self, stem: str) -> None:
        cache = self.scrapers_dir / "__pycache__"
        if not cache.exists():
            return
        for pyc in cache.glob(f"{stem}.*.pyc"):
            try:
                pyc.unlink(missing_ok=True)
            except OSError:
                pass

    # ── bookkeeping ───────────────────────────────────────────────────────────

    @staticmethod
    def _record(generated_scraper, result: InstallResult, user) -> None:
        if generated_scraper is None:
            return
        try:
            from ..models import AuditAction, FactoryAuditLog, FactoryStatus
            generated_scraper.status = FactoryStatus.INSTALLED
            generated_scraper.installed_file_hash = result.file_hash
            generated_scraper.installed_at = timezone.now()
            if result.scraper_source_id:
                from tenders.models import ScraperSource
                generated_scraper.scraper_source = ScraperSource.objects.filter(
                    pk=result.scraper_source_id).first()
            generated_scraper.save(update_fields=[
                "status", "installed_file_hash", "installed_at",
                "scraper_source", "updated_at"])
            FactoryAuditLog.objects.create(
                actor=user, action=AuditAction.INSTALL,
                scraper=generated_scraper,
                new_value={"module_path": result.module_path,
                           "file_hash": result.file_hash,
                           "steps": result.steps},
                reason="Automatic installation (MVP one-click pipeline)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[factory] could not record installation: %s", exc)
