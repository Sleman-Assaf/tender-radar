"""Provider registry — resolves factory interfaces to concrete classes.

Overridable via `settings.FACTORY_PROVIDERS` (dotted paths) so the code model
can be swapped (Ollama, a vLLM endpoint, a fake for tests) without
touching any call site.
"""

from __future__ import annotations

import threading
from typing import Any, Type

from django.conf import settings
from django.utils.module_loading import import_string

from . import interfaces

_DEFAULTS: dict[type, str] = {
    # AutoCodeModel resolves the real provider named by FACTORY_CODE_MODEL
    # (AJi AI's General Chat endpoint in production). Override this mapping
    # entirely with settings.FACTORY_PROVIDERS['code_model'].
    interfaces.ICodeModel:       "scraper_factory.models_ai.auto.AutoCodeModel",
    interfaces.ICorpusIndexer:   "scraper_factory.corpus.indexer.CorpusIndexer",
    interfaces.ICodeValidator:   "scraper_factory.validation.validator.CodeValidator",
    interfaces.IWebsiteAnalyzer: "scraper_factory.analysis.analyzer.WebsiteAnalyzer",
    interfaces.ISimilarityMatcher: "scraper_factory.corpus.matcher.SimilarityMatcher",
    interfaces.ISandbox:         "scraper_factory.validation.sandbox.SubprocessSandbox",
    interfaces.IInvestigator:    "scraper_factory.investigation.engine.InvestigationEngine",
    interfaces.IPlanner:         "scraper_factory.planning.planner.Planner",
}

_SETTINGS_KEYS: dict[type, str] = {
    interfaces.ICodeModel: "code_model",
    interfaces.ICorpusIndexer: "corpus_indexer",
    interfaces.ICodeValidator: "validator",
    interfaces.IWebsiteAnalyzer: "analyzer",
    interfaces.ISimilarityMatcher: "matcher",
    interfaces.ISandbox: "sandbox",
    interfaces.IInvestigator: "investigator",
    interfaces.IPlanner: "planner",
}

_cache: dict[type, Any] = {}
_lock = threading.Lock()


def _dotted_path_for(interface: Type) -> str:
    overrides = getattr(settings, "FACTORY_PROVIDERS", {}) or {}
    key = _SETTINGS_KEYS.get(interface)
    if key and key in overrides:
        return overrides[key]
    if interface in _DEFAULTS:
        return _DEFAULTS[interface]
    raise KeyError(f"No provider registered for {interface.__name__}")


def get(interface: Type):
    """Resolve and cache the implementation registered for an interface."""
    if interface in _cache:
        return _cache[interface]
    with _lock:
        if interface in _cache:
            return _cache[interface]
        path = _dotted_path_for(interface)
        try:
            cls = import_string(path)
        except ImportError as exc:
            raise ImportError(
                f"Provider '{path}' for {interface.__name__} is not available "
                f"yet (later increment) or is misconfigured: {exc}"
            ) from exc
        instance = cls()
        _cache[interface] = instance
        return instance


def reset_cache():
    """Testing helper — clear resolved providers after overriding settings."""
    with _lock:
        _cache.clear()
