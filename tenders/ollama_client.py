# tenders/ollama_client.py
# Path: TenderRadar/tenders/ollama_client.py
"""
Direct Ollama API client for batch AEC classification and translation.
Uses the same prompt design as openai_client but calls local Ollama.
Supports any model available in Ollama: qwen2.5, llama3, mistral, etc.
"""

import json
import logging
import time
import re
from typing import Optional

import requests

logger = logging.getLogger('tenders')

AEC_SYSTEM_PROMPT = """\
You are an AEC tender classifier for an engineering consultancy firm.

Classify as true ONLY if the tender is seeking PROFESSIONAL CONSULTANCY
SERVICES in one of these five families:

1. CONSULTATION & SUPERVISION:
- Architectural, structural, civil, or MEP engineering design consultancy
- Construction supervision, site supervision, resident engineer services
- Contract administration, quantity surveying, cost consultancy
- Feasibility, technical, or geotechnical studies
- Urban planning and master planning consultancy

2. ENVIRONMENTAL STUDIES:
- Environmental impact assessment (EIA / ESIA)
- Environmental and social studies for construction or infrastructure

3. AEC PMO / PROJECT MANAGEMENT:
- Project management consultancy (PMC / PMO) for construction or infrastructure
- Programme management for capital projects

4. AEC INTERIOR DESIGN:
- Interior design and fit-out DESIGN consultancy for buildings

5. FACILITY MANAGEMENT:
- Facility management consulting or advisory services

Classify as false — even if the project is a building or infrastructure
project — when the tender involves:
- Implementation / execution / actual construction or civil WORKS
- Contractor services, general contracting, EPC, design-build, turnkey
- Supply or procurement of goods, materials, or equipment
  (including "supply and install")
- Installation, fit-out execution, or any physical delivery work
- Maintenance or operation contracts
- IT systems, software, or telecommunications
- Medical services, education, agriculture
- Security, cleaning, or catering services

The test: consultant (design, supervise, study, manage, advise) = true;
contractor/supplier (build, execute, supply, install, maintain) = false.
Mixed scopes ("design and build", "supply and installation") = false.

Respond ONLY with a JSON object mapping each index to true or false.
Example: {"0": true, "1": false, "2": true}"""

TRANSLATE_SYSTEM_PROMPT = """\
You are a professional Arabic translator specializing in procurement and engineering.
Translate each tender title to Arabic accurately and concisely.
Keep technical terms precise. Do not add explanations.
Respond ONLY with a JSON object mapping each index to the Arabic translation.
Example: {"0": "عنوان العطاء بالعربية", "1": "عطاء آخر"}"""


def get_config():
    """(base_url, model) from SiteSettings — the DB is the source of truth.

    Mirrors how AJiBrain resolves its model: a database row first
    (LLMModelConfig.get_active_model() there, SiteSettings here), so the model
    can be changed from the admin without a deploy.
    """
    from tenders.models import SiteSettings
    cfg = SiteSettings.get()
    return cfg.ollama_base_url.rstrip('/'), cfg.ollama_model.strip() or 'qwen2.5:7b'


#: Kept as an alias: other modules imported the private name.
_get_config = get_config

#: AJiBrain's default for short calls. Longer workloads pass their own.
DEFAULT_TIMEOUT = 120


def _direct(base_url: str) -> Optional[dict]:
    """Bypass the HTTP proxy when talking to our own Ollama server.

    This machine exports HTTP_PROXY=http://172.10.0.90:3128 with a NO_PROXY that
    does not list the Ollama host, so every model request is sent to the
    corporate proxy and dies on the read timeout. The symptom is a "cannot reach
    Ollama" message that names the proxy rather than the model server, which
    sends whoever reads it to debug the wrong machine.

    An RFC1918 check is not enough here: this network uses 172.10.0.0/16, which is
    public address space, so `ip.is_private` is False for the real Ollama host.

    The rule applied instead is simply that a corporate web proxy has no business
    carrying traffic to a model server the operator configured themselves —
    AJiBrain reaches Ollama on 127.0.0.1 and never meets a proxy at all. Set
    OLLAMA_USE_ENV_PROXY=True if the endpoint genuinely sits behind one.
    """
    from django.conf import settings as dj

    if getattr(dj, 'OLLAMA_USE_ENV_PROXY', False):
        return None
    return {'http': None, 'https': None}


def chat(base_url: str, model: str, system: str, user: str, *,
         options: Optional[dict] = None, response_format: str = '',
         timeout: int = DEFAULT_TIMEOUT, keep_alive=None) -> str:
    """The single place this project speaks Ollama's /api/chat.

    Extracted so classification, translation and the Scraper Factory all use one
    transport rather than three near-copies. The wire format is exactly the one
    AJiBrain uses (api/views.py::_call_ollama and rfp/views.py::_ollama_stream):

        POST {base_url}/api/chat
        {model, stream: False, messages: [system, user], options: {...}}
        → resp.json()['message']['content']

    Callers differ only in `options`, `response_format` and `timeout`:
      * classification/translation want a JSON object back  → format='json'
      * the Factory wants raw Python source                 → no format
    """
    payload = {
        'model':  model,
        'stream': False,
        'options': options or {'temperature': 0},
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user',   'content': user},
        ],
    }
    if response_format:
        payload['format'] = response_format
    if keep_alive is not None:
        payload['keep_alive'] = keep_alive

    resp = requests.post(f'{base_url}/api/chat', json=payload, timeout=timeout,
                         proxies=_direct(base_url))
    resp.raise_for_status()
    return (resp.json().get('message') or {}).get('content', '')


def list_models(base_url: str, timeout: int = 10) -> list[str]:
    """Model tags installed on the server, via /api/tags. [] when unreachable.

    Note the ambiguity: [] means BOTH "unreachable" and "reachable with nothing
    installed". Callers that need to tell those apart must use server_version(),
    because telling an operator to check a server that is already running sends
    them to the wrong problem.
    """
    try:
        resp = requests.get(f'{base_url}/api/tags', timeout=timeout,
                            proxies=_direct(base_url))
        resp.raise_for_status()
        return [m.get('name', '') for m in resp.json().get('models', [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f'[ollama] cannot list models at {base_url}: {exc}')
        return []


def server_version(base_url: str, timeout: int = 8) -> str:
    """The server's version string, or '' when it cannot be reached.

    /api/version is the cheapest proof that something is actually listening and
    speaking Ollama, independent of whether any model has been pulled.
    """
    try:
        resp = requests.get(f'{base_url}/api/version', timeout=timeout,
                            proxies=_direct(base_url))
        resp.raise_for_status()
        return str(resp.json().get('version') or '').strip()
    except Exception:  # noqa: BLE001
        return ''


def _call_ollama(base_url: str, model: str, system: str, user: str) -> str:
    """Classification/translation call. Wire format unchanged: format=json,
    temperature 0, 120 s — it just delegates to the shared transport now."""
    return chat(base_url, model, system, user,
                options={'temperature': 0}, response_format='json',
                timeout=DEFAULT_TIMEOUT)


def classify_batch(titles: list[str]) -> dict[str, bool]:
    """Classify a batch of titles. Returns {title: bool}."""
    if not titles:
        return {}
    base_url, model = _get_config()
    indexed  = {str(i): t for i, t in enumerate(titles)}
    user_msg = 'Classify these tender titles:\n' + json.dumps(indexed, ensure_ascii=False)
    try:
        raw = _call_ollama(base_url, model, AEC_SYSTEM_PROMPT, user_msg)
        # Strip markdown code blocks if model adds them
        raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
        result_map = json.loads(raw)
        out = {}
        for i, title in enumerate(titles):
            val = result_map.get(str(i), False)
            out[title] = val if isinstance(val, bool) else str(val).lower() == 'true'
        logger.info(f'[ollama] classify_batch: {len(titles)} → {sum(out.values())} AEC')
        return out
    except Exception as exc:
        logger.error(f'[ollama] classify_batch failed: {exc}')
        return {t: False for t in titles}


def translate_batch(titles: list[str]) -> dict[str, str]:
    """Translate a batch of titles to Arabic. Returns {title: arabic}."""
    if not titles:
        return {}
    base_url, model = _get_config()
    indexed  = {str(i): t for i, t in enumerate(titles)}
    user_msg = 'Translate these tender titles to Arabic:\n' + json.dumps(indexed, ensure_ascii=False)
    try:
        raw = _call_ollama(base_url, model, TRANSLATE_SYSTEM_PROMPT, user_msg)
        raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
        result_map = json.loads(raw)
        out = {}
        for i, title in enumerate(titles):
            out[title] = result_map.get(str(i), '').strip() or title
        logger.info(f'[ollama] translate_batch: {len(titles)} titles translated')
        return out
    except Exception as exc:
        logger.error(f'[ollama] translate_batch failed: {exc}')
        return {t: t for t in titles}


def test_connection(base_url: str, model: str) -> tuple[bool, str]:
    """Test Ollama connection. Returns (success, message)."""
    try:
        r = requests.get(f'{base_url}/api/tags', timeout=10)
        r.raise_for_status()
        models = [m['name'] for m in r.json().get('models', [])]
        if model in models or any(m.startswith(model.split(':')[0]) for m in models):
            return True, f'Connected. Model "{model}" available. Models: {", ".join(models[:5])}'
        else:
            return False, f'Connected but model "{model}" not found. Available: {", ".join(models[:8])}'
    except Exception as exc:
        return False, f'Connection failed: {exc}'
