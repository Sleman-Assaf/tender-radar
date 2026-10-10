"""The Factory's production code model, reached exactly as AJiBrain reaches it.

The provider's job is to talk to an Ollama server. Which model answers is
configuration, never code: the tag is read at runtime (database first, settings
second) and nothing here is specialised for Qwen, Gemma, Granite or anything
else. That is why the class is named for the protocol, not for a model — a
provider called after whichever model happened to be loaded on the day it was
written is a lie waiting to happen.

The HTTP transport is NOT here. It lives in `tenders.ollama_client.chat`, the one
place this project speaks Ollama, which reproduces AJiBrain's wire format
(`api/views.py::_call_ollama`, `rfp/views.py::_ollama_stream`):

    POST {base_url}/api/chat
    {model, stream: False, messages: [system, user], options: {...}}
    → resp.json()['message']['content']

So this module is a thin adapter. It resolves configuration the way AJiBrain
does, picks options suited to generating a long file, and maps transport failures
onto ModelUnavailable. Re-implementing the HTTP call here would create a second
definition of "how we talk to the model", and the two would drift.

Deviations from AJiBrain's parameters, and why each one exists:

  * `format: 'json'` is NOT sent. AJiBrain's classifier asks for a JSON object;
    a scraper is Python source.
  * `num_predict` IS sent. AJiBrain never sets it because chat replies are short;
    a generated scraper is 250-450 lines and the server default would truncate it.

Everything else — endpoint, payload keys, response extraction, error handling,
and the absence of retries — matches AJiBrain exactly.
"""

from __future__ import annotations

import logging
import re
import time

from django.conf import settings

from ..interfaces import ICodeModel

logger = logging.getLogger("scraper_factory")


class ModelUnavailable(RuntimeError):
    """Raised when the generation model cannot be reached or is unconfigured."""


def strip_code_fences(text: str) -> str:
    """Return just the Python file from a model completion.

    Local models often wrap output in markdown or add a sentence of preamble even
    when told not to. Extracting deterministically here is not "fixing the code" —
    it is reading the model's answer. The code itself is never altered.
    """
    if not text:
        return ""
    body = text.strip()

    fenced = re.findall(r"```(?:python|py)?\s*\n(.*?)```", body, re.DOTALL)
    if fenced:
        body = max(fenced, key=len).strip()
    else:
        # No fence: drop any preamble before the house path comment.
        m = re.search(r"^#\s*tenders/scrapers/\S+\.py\s*$", body, re.MULTILINE)
        if m:
            body = body[m.start():]

    # Drop trailing prose after the last plausible code line.
    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def _setting(*names, default=None):
    """First setting that is present, so old names keep working.

    The Factory's settings were originally named for Qwen. They are now named
    for the role instead, but the old names are still read: dropping them would
    silently ignore an existing FACTORY_QWEN_TIMEOUT on a deployed instance,
    which is the kind of change that looks harmless and then times out in
    production.
    """
    for name in names:
        value = getattr(settings, name, None)
        if value not in (None, ""):
            return value
    return default


def resolve_config() -> tuple[str, str, str]:
    """(base_url, model, source) resolved the way AJiBrain resolves it.

    AJiBrain prefers a database row — `LLMModelConfig.get_active_model()` — and
    falls back to settings, so the active model can be changed from the admin
    without a deploy. TenderRadar holds the same pair in
    `SiteSettings.ollama_base_url` / `.ollama_model`, edited on the AI & Site
    Settings page, so that is the equivalent row and is preferred here.

    Explicit Factory settings win over it, for the case where code generation
    should use a larger model than tender classification does. Nothing is
    hardcoded: with neither source configured this returns blanks and
    `available()` says so.
    """
    factory_url = str(_setting("FACTORY_MODEL_BASE_URL",
                               "FACTORY_QWEN_BASE_URL", default="")).strip()
    factory_model = str(_setting("FACTORY_MODEL_NAME",
                                 "FACTORY_QWEN_MODEL", default="")).strip()
    if factory_url and factory_model:
        return factory_url.rstrip("/"), factory_model, "Factory settings"

    try:
        from tenders.ollama_client import get_config
        db_url, db_model = get_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[factory] could not read SiteSettings: %s", exc)
        db_url, db_model = "", ""

    if factory_url or factory_model:
        return (factory_url.rstrip("/") or db_url, factory_model or db_model,
                "Factory settings + SiteSettings")
    return db_url, db_model, "SiteSettings (AI & Site Settings page)"


class OllamaProvider(ICodeModel):
    """Generates scraper source via the local Ollama server.

    Named for the protocol it speaks. The model is whatever the runtime
    configuration selects, so this class never needs to change when the model
    does. The Factory depends on `ICodeModel`, never on this class.
    """

    name = "ollama"

    def __init__(self):
        self.base_url, self.model, self.config_source = resolve_config()
        # Long-generation parameters, taken from AJiBrain's own long-output caller
        # (rfp/views.py::_ollama_stream). Writing an RFP is the closest workload
        # to writing a scraper, so its settings are the right precedent.
        self.num_ctx = int(_setting("FACTORY_MODEL_NUM_CTX",
                                    "FACTORY_QWEN_NUM_CTX", default=32768))
        self.timeout = int(_setting("FACTORY_MODEL_TIMEOUT",
                                    "FACTORY_QWEN_TIMEOUT", default=600))
        self.num_thread = int(_setting("FACTORY_MODEL_NUM_THREAD",
                                       "FACTORY_QWEN_NUM_THREAD", default=16))
        self.num_batch = int(_setting("FACTORY_MODEL_NUM_BATCH",
                                      "FACTORY_QWEN_NUM_BATCH", default=512))
        if self.model:
            self.name = f"ollama:{self.model}"

    # ── ICodeModel ────────────────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        """(reachable, detail). Never raises — callers show `detail` verbatim."""
        if not self.base_url or not self.model:
            return False, (
                "No model endpoint is configured. Set the Ollama base URL and "
                "model under Manage → AI & Site Settings (Ollama Direct "
                "Configuration), or set FACTORY_MODEL_BASE_URL and "
                "FACTORY_MODEL_NAME.")
        try:
            from tenders.ollama_client import list_models, server_version
            version = server_version(self.base_url)
            tags = list_models(self.base_url, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return False, f"Cannot reach Ollama at {self.base_url}: {exc}"

        # "No models" and "no server" are different problems with different
        # fixes, and an empty /api/tags looks identical either way. /api/version
        # separates them, so the message never sends someone to check a server
        # that is already running.
        if not version:
            return False, (f"Nothing is answering at {self.base_url}. Check the "
                           f"server is running and the port is reachable.")
        if not tags:
            return False, (f"Ollama {version} is running at {self.base_url} but "
                           f"has no models installed. Pull one first, e.g. "
                           f"`ollama pull {self.model}`.")
        # Ollama reports name:tag; accept a bare family name the operator typed.
        if not any(t == self.model or t.split(":")[0] == self.model.split(":")[0]
                   for t in tags):
            return False, (f"Model '{self.model}' is not installed on "
                           f"{self.base_url}. Available: {', '.join(tags[:8])}")
        return True, (f"Ollama reachable at {self.base_url} ({self.model}); "
                      f"config from {self.config_source}.")

    def generate(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        ok, detail = self.available()
        if not ok:
            raise ModelUnavailable(detail)

        from tenders.ollama_client import chat

        started = time.monotonic()
        try:
            content = chat(
                self.base_url, self.model, system, user,
                options={
                    "temperature": 0,           # deterministic, as AJiBrain's classifier
                    "num_ctx": self.num_ctx,
                    "num_thread": self.num_thread,
                    "num_batch": self.num_batch,
                    "num_predict": max_tokens,   # see module docstring
                },
                # No response_format: a scraper is Python, not JSON.
                timeout=self.timeout,
                keep_alive=-1,                  # keep the model resident, as AJiBrain does
            )
        except Exception as exc:  # noqa: BLE001
            # Matching AJiBrain, there is no retry: one attempt, reported
            # honestly, with the failures a developer acts on differently
            # distinguished from each other.
            raise ModelUnavailable(self._explain(exc)) from exc

        logger.info("[factory] %s returned %s chars in %.1fs",
                    self.name, len(content), time.monotonic() - started)
        return content

    # ── diagnostics ───────────────────────────────────────────────────────────

    def _explain(self, exc: Exception) -> str:
        """Turn a transport failure into something a developer can act on."""
        import requests

        if isinstance(exc, requests.exceptions.ConnectionError):
            return (f"Cannot connect to Ollama at {self.base_url}. The server is "
                    f"down, the port is closed, or a proxy is intercepting it.")
        if isinstance(exc, requests.exceptions.Timeout):
            return (f"Ollama did not finish within {self.timeout}s. A scraper is "
                    f"a long generation; raise FACTORY_MODEL_TIMEOUT or use a "
                    f"smaller model.")
        if isinstance(exc, requests.exceptions.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", "?")
            return (f"Ollama returned HTTP {status}. Check the model name "
                    f"'{self.model}' exists on {self.base_url}.")
        return f"{type(exc).__name__}: {exc}"

    def describe(self) -> dict:
        ok, detail = self.available()
        return {"name": self.name, "simulated": False, "reason": detail,
                "base_url": self.base_url, "model": self.model,
                "config_source": self.config_source, "reachable": ok}
