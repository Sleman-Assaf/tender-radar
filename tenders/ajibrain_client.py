# tenders/ajibrain_client.py
# Path: TenderRadar/tenders/ajibrain_client.py

import logging
import re
import requests
from django.conf import settings

logger = logging.getLogger('tenders')

AJIBRAIN_BASE_URL = getattr(settings, 'AJIBRAIN_BASE_URL', 'http://127.0.0.1:8001')
_TIMEOUT = 60

_ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

# The three valid tender categories returned by AJiAI (plus OTHER default)
VALID_CATEGORIES = {'BUILDING', 'INFRA', 'HEALTH', 'OTHER'}


def _is_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ''))


def classify_tender(title: str) -> tuple[bool, str]:
    """
    Combined classification via AJiAI /api/classify-tender/.

    Returns (is_aec, category) where category is one of:
        'BUILDING' | 'INFRA' | 'HEALTH' | 'OTHER'

    Falls back to the legacy /api/classify-aec/ endpoint (AEC-only,
    category='OTHER') if the new endpoint is not available on the
    AJiAI server, and to (False, 'OTHER') on any error.
    """
    if not title or not title.strip():
        return False, 'OTHER'

    # ── New combined endpoint ────────────────────────────────────────────
    try:
        resp = requests.post(
            f'{AJIBRAIN_BASE_URL}/api/classify-tender/',
            json={'tenderTitle': title},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            raise _LegacyFallback()
        resp.raise_for_status()
        data     = resp.json()
        is_aec   = bool(data.get('is_aec', False))
        category = str(data.get('category', 'OTHER')).strip().upper()
        if category not in VALID_CATEGORIES:
            category = 'OTHER'
        logger.debug(f'[ajibrain.classify_tender] {title[:60]!r} -> '
                     f'is_aec={is_aec} category={category}')
        return is_aec, category
    except _LegacyFallback:
        logger.warning('[ajibrain.classify_tender] /api/classify-tender/ not found '
                       'on AJiAI — falling back to legacy /api/classify-aec/')
    except requests.RequestException as exc:
        logger.warning(f'[ajibrain.classify_tender] API error: {exc}')
        return False, 'OTHER'
    except Exception as exc:
        logger.error(f'[ajibrain.classify_tender] Unexpected: {exc}')
        return False, 'OTHER'

    # ── Legacy fallback (AEC flag only) ──────────────────────────────────
    return classify_aec(title), 'OTHER'


class _LegacyFallback(Exception):
    pass


def classify_aec(title: str) -> bool:
    """Legacy AEC-only classification (kept for backward compatibility)."""
    if not title or not title.strip():
        return False
    try:
        resp = requests.post(
            f'{AJIBRAIN_BASE_URL}/api/classify-aec/',
            json={'tenderTitle': title},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if 'result' in data:
            result = bool(data['result'])
        else:
            answer = str(data.get('response', data.get('answer', ''))).strip().lower()
            result = answer.startswith('true')
        logger.debug(f'[ajibrain.classify_aec] {title[:60]!r} -> {result}')
        return result
    except requests.RequestException as exc:
        logger.warning(f'[ajibrain.classify_aec] API error: {exc}')
        return False
    except Exception as exc:
        logger.error(f'[ajibrain.classify_aec] Unexpected: {exc}')
        return False


def translate_title(title: str) -> str:
    """
    Translate title to Arabic via AJiBrain.
    Returns Arabic string, or original title on failure.
    """
    if not title or not title.strip():
        return title
    try:
        resp = requests.post(
            f'{AJIBRAIN_BASE_URL}/api/translate-title/',
            json={'tenderTitle': title},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        translated = resp.json().get('translatedTitle', '').strip()
        logger.debug(f'[ajibrain.translate] {title[:60]!r} -> {translated[:60]!r}')
        return translated if translated else title
    except requests.RequestException as exc:
        logger.warning(f'[ajibrain.translate] API error: {exc}')
        return title
    except Exception as exc:
        logger.error(f'[ajibrain.translate] Unexpected: {exc}')
        return title


#: The tag the news scrapers put at the front of a description to record WHY
#: an item was kept ("[TENDER] " / "[DESIGN] "). Useful on the page, noise to
#: a classifier, so it is removed before the text is sent.
_KEEP_TAG_RE = re.compile(r'^\s*\[(?:TENDER|DESIGN)\]\s*')

#: Total characters sent for a news item. Enough for a headline plus a
#: standfirst; short enough that the request stays quick and the model is not
#: handed a whole article to summarise.
_NEWS_INPUT_CAP = 600


def _news_classify_input(tender, base: str) -> str:
    """
    Build the classification text for a News item: title, entity, standfirst.

    Kept as its own function so the composition is testable without a network
    call, and so the newspaper and portal paths above stay untouched.
    """
    parts = [base]
    entity = (getattr(tender, 'entity', '') or
              getattr(tender, 'entity_ar', '') or '').strip()
    if entity and entity not in base:
        parts.append(entity)

    body = _KEEP_TAG_RE.sub('', getattr(tender, 'description', '') or '').strip()
    if body:
        # One paragraph is the standfirst; more than that is the article.
        body = body.split('\n')[0].strip()
        if body and body not in base:
            parts.append(body)

    return ' — '.join(parts)[:_NEWS_INPUT_CAP]


def classify_and_translate_tender(tender) -> dict:
    """
    Step 1 — Combined classification (always):
              is_aec + category (BUILDING / INFRA / HEALTH / OTHER).
    Step 2 — Translate title to Arabic (ONLY if is_aec is True).

    Rules:
      - title field is NEVER modified.
      - category is ALWAYS set from the AJiAI result.
      - title_ar is filled only for AEC-relevant tenders missing Arabic.
      - If title is already Arabic, title_ar = title (copy, no API call).
      - If title_ar already has Arabic, skip translation even for AEC tenders.

    Returns dict of fields to update. Does NOT save.
    """
    updates = {'is_aec_checked': True}

    raw_title    = (tender.title    or '').strip()
    raw_title_ar = (tender.title_ar or '').strip()

    # ── Step 1: Combined AEC + category classification ─────────────────────
    classify_input = raw_title or raw_title_ar
    # Newspaper tenders only: short Arabic ad titles often lack the signal
    # ("طلب استشاري..." reads AEC until you see the entity is a social
    # ministry) — append the entity so the SAME AJiAI endpoint sees the full
    # picture. Scraper tenders keep the proven title-only input untouched.
    scraper_class = (getattr(tender.source, 'scraper_class', '') or '')
    is_newspaper = scraper_class.startswith('NEWSPAPER_')
    is_news      = scraper_class.startswith('MAGAZINE_')
    if is_newspaper:
        entity = (tender.entity or tender.entity_ar or '').strip()
        if entity and entity not in classify_input:
            classify_input = f'{classify_input} — {entity}'
    elif is_news:
        # News items get the entity AND the standfirst, for the same reason
        # the newspaper branch above exists: the title alone often lacks the
        # signal. Headlines are written to intrigue rather than to describe a
        # scope — "Wedyan on Dubai Canal enters next phase as Al Ghurair
        # appoints..." reveals nothing until the line underneath it is read.
        #
        # Same endpoint, same model. Only this source type's input changes;
        # every portal scraper keeps the proven title-only input.
        classify_input = _news_classify_input(tender, classify_input)
    is_aec, category = classify_tender(classify_input)

    # Deterministic guard (newspaper only): rent / sale / auction notices are
    # never AEC procurement, but their text is full of building words
    # (مبنى، مواصفات فنية، مخططات هندسية) that keep fooling the model — e.g.
    # "طلب استئجار مبنى بديل لمركز تنمية المجتمع المحلي" (24 Aug 2026).
    # Applied AFTER the AI so category is still filled; overridable via the
    # NEWSPAPER_NON_AEC_KEYWORDS env/setting.
    if is_newspaper and is_aec:
        from django.conf import settings as _settings
        keywords = getattr(_settings, 'NEWSPAPER_NON_AEC_KEYWORDS',
                           ['استئجار', 'إستئجار', 'تأجير', 'إيجار', 'ايجار',
                            'بيع', 'مزايدة', 'مزاد'])
        haystack = f'{raw_title} {raw_title_ar}'
        hit = next((kw for kw in keywords if kw and kw in haystack), None)
        if hit:
            is_aec = False
            logger.info(f'[ajibrain] #{tender.pk} forced NON-AEC — title '
                        f'contains rent/sale/auction keyword {hit!r}')

    updates['is_aec']   = is_aec
    updates['category'] = category
    logger.info(f'[ajibrain] #{tender.pk} is_aec={is_aec} category={category} '
                f'title={classify_input[:60]!r}')

    if not is_aec:
        return updates

    # ── Step 2: Translate to Arabic (AEC tenders only) ─────────────────────
    title_ar_ok = _is_arabic(raw_title_ar)

    if title_ar_ok:
        logger.debug(f'[ajibrain] #{tender.pk} title_ar already Arabic -- skip translate')
    elif _is_arabic(raw_title):
        updates['title_ar'] = raw_title
        logger.info(f'[ajibrain] #{tender.pk} title_ar copied from Arabic title')
    elif raw_title:
        arabic = translate_title(raw_title)
        if arabic and _is_arabic(arabic):
            updates['title_ar'] = arabic
            logger.info(f'[ajibrain] #{tender.pk} title_ar translated: {arabic[:70]!r}')
        else:
            logger.warning(
                f'[ajibrain] #{tender.pk} translation did not return Arabic: {arabic[:60]!r}'
            )

    return updates
