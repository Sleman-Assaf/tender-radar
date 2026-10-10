"""The Factory's production code model: AJi AI's General Chat API.

    Factory -> POST http://172.30.0.4:8000/api/general-chat/ -> Ollama -> model

The endpoint is `api/general_chat.py::general_chat` in the AJi AI project, whose
own header says what makes it the right one: "NO RAG, NO knowledge base, NO
grounding. The answer comes straight from the pretrained knowledge of the active
model." The Factory's system prompt is the only system prompt the model sees —
the property every earlier AJi Brain route lacked.

The API is used exactly as implemented; AJi AI is not modified. Request fields,
their server-side clamps, and the response shape all come from reading that file,
not from assumption:

    POST /api/general-chat/
      system       str            (also accepted as system_prompt)
      prompt       str            (also message / question / input)
      temperature  0.0..2.0
      seed         int            (reproducibility)
      num_ctx      512..131072    (server default 4096 — too small; always sent)
      max_tokens   -1..32768      (maps to num_predict)
      timeout      5..600         (server clamps at MAX_TIMEOUT = 600)
      stream       false
    -> { answer, model, done_reason, usage:{prompt_tokens, completion_tokens,
         total_tokens}, elapsed_ms }
    errors: 400 bad input / 401 bad key / 404 model missing (+available_models)
            502 LLM rejected / 503 unreachable / 504 timed out — all JSON

    GET /api/general-chat/info/
    -> { llm_online, active_model, models, auth_required, defaults, ... }

Model-agnosticism is the server's job here, and that is the point: no `model` key
is sent, so AJi AI resolves the active model from its own configuration
(`LLMModelConfig.get_active_model()` -> settings). A model upgrade on the server
changes nothing in the Factory — not even a setting. `AJIAI_MODEL` exists as the
explicit escape hatch and is empty by default.

Auth is the endpoint's optional shared key: if AJi AI sets GENERAL_CHAT_API_KEY,
the Factory sends it as X-API-Key; if not, nothing is sent. The server also
accepts `Authorization: Bearer`, but one header is enough and X-API-Key is the
one its error message names.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings

from ..interfaces import ICodeModel
# Imported, never redefined: `generation/orchestrator.py` catches exactly this
# class, so a second definition would look identical and never be caught — a
# clean "the model is unavailable" would escape as an unhandled exception and be
# misdiagnosed as an engine fault. `strip_code_fences` is generic text handling.
from .ollama_provider import ModelUnavailable, strip_code_fences

logger = logging.getLogger("scraper_factory")

__all__ = ["AJIAIProvider", "ModelUnavailable", "strip_code_fences",
           "ajiai_config"]

#: The server refuses to wait longer than this for Ollama (MAX_TIMEOUT in
#: general_chat.py). Sending more would be silently clamped, so it is the ceiling
#: here too, and the HTTP timeout rides slightly above it so the server's own
#: 504 — which names the model and the timeout — arrives instead of a client-side
#: abort that says nothing.
SERVER_MAX_TIMEOUT = 600
HTTP_MARGIN = 30


def ajiai_config() -> dict:
    """Connection settings for the AJi AI General Chat API."""
    def setting(name, default):
        value = getattr(settings, name, None)
        return default if value in (None, "") else value

    return {
        "base_url": str(setting("AJIAI_API_BASE",
                                "http://172.30.0.4:8000")).strip().rstrip("/"),
        "chat_path": str(setting("AJIAI_CHAT_PATH", "/api/general-chat/")),
        "info_path": str(setting("AJIAI_INFO_PATH", "/api/general-chat/info/")),
        "api_key": str(setting("AJIAI_API_KEY", "")).strip(),
        # Body-level timeout, clamped by the server at 600.
        "timeout": min(int(setting("AJIAI_TIMEOUT", SERVER_MAX_TIMEOUT)),
                       SERVER_MAX_TIMEOUT),
        # Fixed seed + temperature 0: two runs of one prompt must produce the
        # same file, or a benchmark regression cannot be attributed to a change.
        "seed": int(setting("AJIAI_SEED", 42)),
        "temperature": float(setting("AJIAI_TEMPERATURE", 0)),
        # Reuses the Factory's existing context setting rather than adding a
        # parallel one. The server's default is 4096, so this is always sent.
        "num_ctx": int(setting("FACTORY_MODEL_NUM_CTX", 32768)),
        # Floor for the completion budget. Callers ask for 8192, which fits the
        # file — but reasoning models (the active gpt-oss:20b among them) spend
        # part of the budget thinking before writing, and a real NEPCO run hit
        # done_reason=length at exactly 8192. The server accepts up to 32768.
        "max_tokens_floor": int(setting("AJIAI_MAX_TOKENS", 16384)),
        # Empty by default: AJi AI resolves the active model itself.
        "model": str(setting("AJIAI_MODEL", "")).strip(),
        "trust_proxy": bool(setting("AJIAI_TRUST_PROXY", False)),
    }


class AJIAIProvider(ICodeModel):
    """Generates scraper source through AJi AI's General Chat endpoint.

    Named for the service. Which model answers is AJi AI's decision — resolved
    from its own database-first configuration — and may change to any family or
    version without this class, any Factory call site, or even a Factory setting
    changing.
    """

    name = "ajiai"

    def __init__(self):
        config = ajiai_config()
        self.base_url = config["base_url"]
        self.chat_url = self.base_url + config["chat_path"]
        self.info_url = self.base_url + config["info_path"]
        self.api_key = config["api_key"]
        self.timeout = config["timeout"]
        self.seed = config["seed"]
        self.temperature = config["temperature"]
        self.num_ctx = config["num_ctx"]
        self.max_tokens_floor = config["max_tokens_floor"]
        self.model_override = config["model"]
        self.trust_proxy = config["trust_proxy"]
        #: Whatever model AJi AI reports. Provenance only — recorded on every
        #: GenerationRun so a file's origin is traceable, never used to decide.
        self.reported_model = ""
        self.last_usage: dict = {}

    # ── transport ─────────────────────────────────────────────────────────────

    def _session(self):
        """A session that keeps internal traffic off the corporate proxy.

        Same idiom as the rest of the project: honouring HTTP_PROXY for a server
        on our own network hands the request to the proxy, which answers it with
        an error page of its own. AJIAI_TRUST_PROXY restores normal behaviour for
        a deployment that genuinely routes through one.
        """
        import requests

        session = requests.Session()
        session.trust_env = self.trust_proxy
        return session

    def _headers(self) -> dict:
        # The endpoint is open when AJi AI has no GENERAL_CHAT_API_KEY set;
        # sending nothing is then correct rather than lazy.
        return {"X-API-Key": self.api_key} if self.api_key else {}

    # ── ICodeModel ────────────────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        """(reachable, detail) from /api/general-chat/info/. Never raises.

        One call answers everything /api/tags and a health route used to:
        reachability, whether the LLM behind AJi AI is up, which model is
        active, and whether a key is required.
        """
        if not self.base_url:
            return False, ("AJi AI is not configured. Set AJIAI_API_BASE to the "
                           "production server, e.g. http://172.30.0.4:8000")
        try:
            resp = self._session().get(self.info_url, headers=self._headers(),
                                       timeout=10)
            resp.raise_for_status()
            info = resp.json()
        except Exception as exc:  # noqa: BLE001
            return False, self._explain(exc, doing="reach")

        self.reported_model = str(info.get("active_model") or "")
        if info.get("auth_required") and not self.api_key:
            return False, ("AJi AI requires an API key "
                           "(GENERAL_CHAT_API_KEY is set on the server). Set "
                           "AJIAI_API_KEY to the shared key.")
        if not info.get("llm_online", True):
            return False, (f"AJi AI is running at {self.base_url} but reports "
                           f"its LLM offline — no models are installed or "
                           f"Ollama is down behind it.")

        model_note = (f"active model {self.reported_model}"
                      if self.reported_model else "no active model named")
        return True, (f"AJi AI General Chat reachable at {self.base_url} "
                      f"({model_note}); generating via {self.chat_url}.")

    def generate(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        ok, detail = self.available()
        if not ok:
            raise ModelUnavailable(detail)

        # The documented contract, field for field. No `model` key: AJi AI
        # resolves the active model from its own configuration, which is what
        # keeps the Factory model-agnostic without even a setting to maintain.
        payload = {
            "system": system,
            "prompt": user,
            "temperature": self.temperature,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
            # The caller's ask is a floor, not the ceiling: reasoning models
            # spend completion tokens thinking before the file starts, so the
            # budget rides above the file size. Clamped at the server's 32768.
            "max_tokens": min(max(int(max_tokens), self.max_tokens_floor), 32768),
            "timeout": self.timeout,
            "stream": False,
        }
        if self.model_override:
            payload["model"] = self.model_override

        started = time.monotonic()
        try:
            resp = self._session().post(
                self.chat_url, json=payload, headers=self._headers(),
                # Above the body timeout so the server's 504 — which names the
                # model and the timeout — is the error that arrives.
                timeout=self.timeout + HTTP_MARGIN)
            data = self._body(resp)
            if resp.status_code >= 400:
                raise ModelUnavailable(self._http_error(resp.status_code, data))
        except ModelUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelUnavailable(self._explain(exc, doing="generate with")) from exc

        if data is None:
            raise ModelUnavailable(
                f"The reply from {self.chat_url} was not JSON, so it did not "
                f"come from AJi AI's API. Check the base URL, and whether a "
                f"proxy or web server is answering instead.")

        answer = str(data.get("answer") or "")
        self.reported_model = str(data.get("model") or "") or self.reported_model
        self.last_usage = dict(data.get("usage") or {})
        done_reason = str(data.get("done_reason") or "")
        elapsed_ms = int(data.get("elapsed_ms")
                         or (time.monotonic() - started) * 1000)

        logger.info("[factory] AJi AI returned %s chars in %sms "
                    "(model %s, done_reason %s, %s completion tokens)",
                    len(answer), elapsed_ms,
                    self.reported_model or "unreported", done_reason or "?",
                    self.last_usage.get("completion_tokens", "?"))

        if not answer.strip():
            raise ModelUnavailable(
                f"AJi AI answered in {elapsed_ms}ms but the completion was "
                f"empty (model {self.reported_model or '?'}, done_reason "
                f"{done_reason or '?'}).")

        # done_reason 'length' means the file was cut off mid-function. Caught
        # here by name, because passed on it fails validation as a syntax error
        # and the repair loop chases a prompt that was never the problem.
        if done_reason == "length":
            raise ModelUnavailable(
                f"AJi AI's answer was truncated at "
                f"{self.last_usage.get('completion_tokens', '?')} tokens "
                f"(done_reason=length). Raise max_tokens — the request sent "
                f"{payload['max_tokens']} — or use a smaller prompt.")
        return answer

    # ── response and error handling ───────────────────────────────────────────

    @staticmethod
    def _body(resp):
        """Parsed JSON, or None when the reply was not JSON.

        None rather than {}: an empty object is AJi AI answering, a non-JSON
        body is usually a proxy answering instead of it, and the two point at
        different machines.
        """
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            return None

    def _http_error(self, status: int, data) -> str:
        """The endpoint's own JSON error, turned into an actionable sentence."""
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("error") or data.get("detail") or "")

        if status == 401:
            return ("AJi AI rejected the API key. Check AJIAI_API_KEY against "
                    "the server's GENERAL_CHAT_API_KEY.")
        if status == 404:
            available = (data or {}).get("available_models") if isinstance(data, dict) else None
            if available is not None:
                # The endpoint's model-not-installed answer, which helpfully
                # lists what IS there.
                return (f"{detail or 'The requested model is not installed.'} "
                        f"Available: {', '.join(available) or '(none)'}")
            return (f"AJi AI has no endpoint at {self.chat_url}. Check "
                    f"AJIAI_CHAT_PATH against the routes in AJi AI's "
                    f"api/urls.py.")
        if status == 502:
            return (f"AJi AI reached its LLM but the request was rejected"
                    f"{': ' + detail if detail else '.'}")
        if status == 503:
            return (f"AJi AI cannot reach its LLM — Ollama is down behind it"
                    f"{': ' + detail if detail else '.'}")
        if status == 504:
            return (f"AJi AI timed out waiting for the model after "
                    f"{self.timeout}s (the server caps at "
                    f"{SERVER_MAX_TIMEOUT}s). A smaller prompt or a faster "
                    f"model is the fix; the cap itself is AJi AI's.")
        return (f"AJi AI returned HTTP {status}"
                + (f": {detail}" if detail else "."))

    def _explain(self, exc: Exception, *, doing: str) -> str:
        import requests

        if isinstance(exc, requests.exceptions.ConnectionError):
            return (f"AJi AI is not reachable at {self.base_url}. Please ensure "
                    f"the AJi AI server is running and reachable from this "
                    f"host.")
        if isinstance(exc, requests.exceptions.Timeout):
            return (f"AJi AI did not answer within {self.timeout + HTTP_MARGIN}s "
                    f"when trying to {doing} it.")
        if isinstance(exc, ValueError):
            return (f"AJi AI's reply was not JSON. Check that {self.base_url} "
                    f"is the API and that no proxy is intercepting it.")
        return f"{type(exc).__name__}: {exc}"

    def describe(self) -> dict:
        ok, detail = self.available()
        return {"name": self.name, "simulated": False, "reason": detail,
                "base_url": self.base_url, "endpoint": self.chat_url,
                "model": self.reported_model or "(resolved by AJi AI)",
                "config_source": "AJIAI_* settings", "reachable": ok}
