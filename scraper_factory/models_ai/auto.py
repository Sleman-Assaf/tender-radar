"""AutoCodeModel — resolves the production generation backend.

The backend is `AJIAIProvider` — AJi AI's General Chat endpoint, which executes
the prompt with no RAG and resolves the active model from its own configuration,
so the Factory names no model anywhere. `OllamaProvider` (direct Ollama Chat API)
remains selectable with FACTORY_CODE_MODEL='ollama' for local development.

`FakeCodeModel` and its auto-fallback are gone, removed after the first real
end-to-end verification passed against production AJi AI (2026-08-03: NEPCO,
9 live tenders, install and rollback byte-identical). An unreachable model is
now a hard, honestly-reported error at generation time — never a silently
simulated file. `is_fake`/`simulated` remain in the interface for the CLI and
staff UI, permanently False, so no caller needed to change.
"""

from __future__ import annotations

import logging

from django.conf import settings

from ..interfaces import ICodeModel

logger = logging.getLogger("scraper_factory")


class AutoCodeModel(ICodeModel):
    """Delegates to the configured real provider."""

    #: Real backends, selected by FACTORY_CODE_MODEL. "ajiai" is production:
    #: AJi AI's General Chat endpoint, which resolves the active model from its
    #: own configuration so the Factory names no model at all. "ollama" speaks
    #: the Ollama Chat API directly and remains for local development.
    BACKENDS = {
        "ajiai": "scraper_factory.models_ai.ajiai_provider.AJIAIProvider",
        "ollama": "scraper_factory.models_ai.ollama_provider.OllamaProvider",
    }

    def __init__(self):
        self.backend: ICodeModel = self._real_provider()
        self.is_fake = False
        ok, detail = self.backend.available()
        self.reason = detail
        if ok:
            logger.info("[factory] using the real model: %s", detail)
        else:
            # Reported at resolution time so the CLI and dashboard say what is
            # wrong before a build is attempted; generate() will raise the same
            # reason as ModelUnavailable. Nothing is simulated.
            logger.error("[factory] the model backend is unavailable: %s", detail)

        # `name` is recorded on every run and version, so the provenance of any
        # generated file always says which backend produced it.
        self.name = getattr(self.backend, "name", "unknown")

    @classmethod
    def _real_provider(cls) -> ICodeModel:
        """The production backend, resolved from configuration.

        An unrecognised `FACTORY_CODE_MODEL` is logged and the default used,
        rather than raising during app startup where the traceback would be far
        from the setting that caused it.
        """
        from django.utils.module_loading import import_string

        choice = str(getattr(settings, "FACTORY_CODE_MODEL", "ajiai")
                     or "ajiai").strip().lower()
        dotted = cls.BACKENDS.get(choice)
        if dotted is None:
            logger.error("[factory] FACTORY_CODE_MODEL=%r is not one of %s — "
                         "using the AJi AI General Chat provider.",
                         choice, sorted(cls.BACKENDS))
            dotted = cls.BACKENDS["ajiai"]
        return import_string(dotted)()

    # ── ICodeModel ────────────────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        return self.backend.available()

    def generate(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        return self.backend.generate(system, user, max_tokens=max_tokens)

    # ── introspection for the CLI and UI ──────────────────────────────────────

    def describe(self) -> dict:
        if hasattr(self.backend, "describe"):
            out = dict(self.backend.describe())
        else:
            ok, detail = self.backend.available()
            out = {"name": self.name, "reason": detail, "reachable": ok}
        out["simulated"] = False
        return out


def active_model_summary() -> dict:
    """Describe the currently configured generation backend."""
    from .. import providers

    try:
        model = providers.get(ICodeModel)
    except Exception as exc:  # noqa: BLE001
        return {"name": "unavailable", "simulated": False, "reason": str(exc)}
    if hasattr(model, "describe"):
        return model.describe()
    ok, detail = model.available()
    return {"name": getattr(model, "name", "unknown"),
            "simulated": False, "reason": detail}
