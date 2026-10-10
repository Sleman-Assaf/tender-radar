# tenders/openai_client.py
# Path: TenderRadar/tenders/openai_client.py
"""
OpenAI batch classification and translation for TenderRadar.

Two operations:
  1. classify_batch(titles)  → {title: True/False}
     Sends up to `batch_size` titles in one prompt.
     Returns dict mapping each title to its AEC classification.

  2. translate_batch(titles) → {title: arabic_string}
     Sends up to `batch_size` titles in one prompt.
     Returns dict mapping each title to its Arabic translation.

Both use JSON responses for reliable parsing.
"""

import json
import logging
import re
import time
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


def _get_client():
    """Get OpenAI API key and model from SiteSettings."""
    from tenders.models import SiteSettings
    cfg = SiteSettings.get()
    api_key = cfg.openai_api_key.strip()
    model   = cfg.openai_model.strip() or 'gpt-4o-mini'
    if not api_key:
        raise ValueError('OpenAI API key not set in Site Settings → AI Service')
    return api_key, model


def _call_openai(api_key: str, model: str, system: str, user: str,
                 max_tokens: int = 2000) -> str:
    """Call OpenAI chat completions and return content string."""
    url = 'https://api.openai.com/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type':  'application/json',
    }
    payload = {
        'model':       model,
        'temperature': 0,
        'max_tokens':  max_tokens,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user',   'content': user},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def classify_batch(titles: list[str]) -> dict[str, bool]:
    """
    Classify a batch of titles for AEC relevance.
    Returns {title: bool} for all input titles.
    Falls back to False on any error.
    """
    if not titles:
        return {}

    api_key, model = _get_client()

    # Build numbered list for the prompt
    indexed = {str(i): t for i, t in enumerate(titles)}
    user_msg = 'Classify these tender titles:\n' + json.dumps(indexed, ensure_ascii=False)

    try:
        raw = _call_openai(api_key, model, AEC_SYSTEM_PROMPT, user_msg,
                           max_tokens=len(titles) * 10 + 100)
        result_map = json.loads(raw)
        # Map back index → title → bool
        out = {}
        for i, title in enumerate(titles):
            val = result_map.get(str(i), False)
            if isinstance(val, bool):
                out[title] = val
            elif isinstance(val, str):
                out[title] = val.strip().lower() == 'true'
            else:
                out[title] = bool(val)
        logger.info(f'[openai] classify_batch: {len(titles)} titles → '
                    f'{sum(out.values())} AEC')
        return out
    except Exception as exc:
        logger.error(f'[openai] classify_batch failed: {exc}')
        return {t: False for t in titles}


def translate_batch(titles: list[str]) -> dict[str, str]:
    """
    Translate a batch of titles to Arabic.
    Returns {title: arabic_title} for all input titles.
    Falls back to original title on any error.
    """
    if not titles:
        return {}

    api_key, model = _get_client()

    indexed = {str(i): t for i, t in enumerate(titles)}
    user_msg = 'Translate these tender titles to Arabic:\n' + json.dumps(indexed, ensure_ascii=False)

    try:
        raw = _call_openai(api_key, model, TRANSLATE_SYSTEM_PROMPT, user_msg,
                           max_tokens=len(titles) * 30 + 200)
        result_map = json.loads(raw)
        out = {}
        for i, title in enumerate(titles):
            arabic = result_map.get(str(i), '').strip()
            out[title] = arabic if arabic else title
        logger.info(f'[openai] translate_batch: {len(titles)} titles translated')
        return out
    except Exception as exc:
        logger.error(f'[openai] translate_batch failed: {exc}')
        return {t: t for t in titles}


def process_scraper_results(tenders_qs, batch_classify: int = 50,
                             batch_translate: int = 30,
                             ai_service: str = 'OPENAI') -> dict:
    """Route to correct AI client based on ai_service."""
    if ai_service == 'OLLAMA':
        from tenders.ollama_client import (
            classify_batch as _classify,
            translate_batch as _translate,
        )
    else:
        _classify  = classify_batch
        _translate = translate_batch
    return _process_scraper_results(
        tenders_qs, batch_classify, batch_translate, _classify, _translate
    )


def _process_scraper_results(tenders_qs, batch_classify, batch_translate,
                              _classify, _translate) -> dict:
    """
    Full OpenAI processing pipeline for a queryset of Tender objects:
      1. Classify all unchecked tenders in batches → set is_aec, is_aec_checked
      2. Translate titles of AEC tenders that lack Arabic → set title_ar

    Returns summary dict {classified, aec_found, translated}.
    """
    import re as _re
    _arabic_re = _re.compile(r'[\u0600-\u06FF]')

    # ── Step 1: Classify unchecked tenders ───────────────────────────────────
    unchecked = list(tenders_qs.filter(is_aec_checked=False))
    classified = aec_found = 0

    if unchecked:
        print(f'  [OpenAI] Classifying {len(unchecked)} tenders in batches of {batch_classify}...')
        logger.info(f'[openai] Classifying {len(unchecked)} tenders')

        for i in range(0, len(unchecked), batch_classify):
            batch   = unchecked[i:i + batch_classify]
            titles  = [t.title or t.title_ar or '' for t in batch]
            results = _classify(titles)

            to_update = []
            for tender, title in zip(batch, titles):
                is_aec = results.get(title, False)
                tender.is_aec         = is_aec
                tender.is_aec_checked = True
                to_update.append(tender)
                classified += 1
                if is_aec:
                    aec_found += 1

            # Bulk update
            from tenders.models import Tender as TenderModel
            TenderModel.objects.bulk_update(to_update, ['is_aec', 'is_aec_checked'])
            print(f'    batch {i//batch_classify + 1}: {len(batch)} classified, '
                  f'{sum(results.values())} AEC')
            time.sleep(0.5)  # rate limit courtesy

    # ── Step 2: Translate AEC tenders missing Arabic ──────────────────────────
    # Re-query from DB to get fresh is_aec values after bulk_update
    from django.db.models import Q
    from tenders.models import Tender as TenderModel
    # Get all AEC tenders for this source that need translation
    all_aec = list(
        TenderModel.objects.filter(
            pk__in=[t.pk for t in tenders_qs]
        ).filter(
            is_aec=True,
        ).filter(
            Q(title_ar='') | Q(title_ar__isnull=True)
        ).exclude(title='')
    )
    needs_translation = [
        t for t in all_aec
        if not _arabic_re.search(t.title or '')
    ]

    translated = 0
    if needs_translation:
        print(f'  [OpenAI] Translating {len(needs_translation)} AEC titles '
              f'in batches of {batch_translate}...')
        logger.info(f'[openai] Translating {len(needs_translation)} titles')

        for i in range(0, len(needs_translation), batch_translate):
            batch   = needs_translation[i:i + batch_translate]
            titles  = [t.title for t in batch]
            results = _translate(titles)

            to_update = []
            for tender in batch:
                arabic = results.get(tender.title, '')
                if arabic and _arabic_re.search(arabic):
                    tender.title_ar = arabic
                    to_update.append(tender)
                    translated += 1

            from tenders.models import Tender as TenderModel
            if to_update:
                updated = TenderModel.objects.bulk_update(to_update, ['title_ar'])
                logger.info(f'[openai] bulk_update title_ar: {updated} rows updated')
            print(f'    batch {i//batch_translate + 1}: '
                  f'{len(to_update)}/{len(batch)} translated')
            for t in to_update:
                en = t.title[:70] + ('…' if len(t.title) > 70 else '')
                ar = t.title_ar[:70] + ('…' if len(t.title_ar) > 70 else '')
                print(f'      EN: {en}')
                print(f'      AR: {ar}')
            time.sleep(0.5)

    return {
        'classified': classified,
        'aec_found':  aec_found,
        'translated': translated,
    }
