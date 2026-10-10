# tenders/ajibrain_tests.py
# Path: TenderRadar/tenders/ajibrain_tests.py
#
# "Test API" suite for the AJiAI (AJi Brain) integration — used by the
# Integrations settings page. Predefined tender titles + bundled sample
# newspaper files exercise every AJiAI endpoint TenderRadar depends on:
#
#   GET  /api/newspaper/health/     — service + vision-model readiness
#   POST /api/classify-tender/      — AEC flag + category
#   POST /api/translate-title/      — English → Arabic title
#   POST /api/newspaper/extract/    — vision extraction (PDF / image)

import os
import time

import requests
from django.conf import settings

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), 'ajibrain_test_assets')


def _base_url() -> str:
    return getattr(settings, 'AJIBRAIN_BASE_URL', 'http://127.0.0.1:8001').rstrip('/')


# ── Predefined tender titles (id, title, what a correct answer looks like) ───
SAMPLE_TITLES = [
    {
        'id': 'aec_en_building',
        'title': 'Consultancy Services for the Design and Supervision of a New '
                 'Government Office Complex in Amman',
        'expect': 'AEC — BUILDING',
    },
    {
        'id': 'aec_en_infra',
        'title': 'Engineering Consultancy for the Rehabilitation of the '
                 'Zarqa–Mafraq Highway and Stormwater Drainage Networks',
        'expect': 'AEC — INFRA',
    },
    {
        'id': 'aec_ar_health',
        'title': 'تقديم الخدمات الاستشارية الهندسية للإشراف على تنفيذ مشروع '
                 'إنشاء مبنى مركز صحي شامل في محافظة الزرقاء',
        'expect': 'AEC — HEALTH',
    },
    {
        'id': 'non_aec_supply',
        'title': 'Supply and Delivery of Office Furniture and Stationery '
                 'for Ministry Headquarters',
        'expect': 'NOT AEC',
    },
    {
        'id': 'non_aec_ar_rent',
        'title': 'طلب استئجار مبنى بديل لمركز تنمية المجتمع المحلي',
        'expect': 'NOT AEC (rent notice)',
    },
]

# ── Bundled sample newspaper files ────────────────────────────────────────────
SAMPLE_FILES = [
    {
        'id': 'ad_jpg',
        'label': 'Newspaper ad — image (JPEG, Arabic, 1 tender)',
        'filename': 'sample_ad_health_center.jpg',
        'field': 'image',
        'mime': 'image/jpeg',
    },
    {
        'id': 'ad_pdf',
        'label': 'Newspaper page — PDF (scanned-style, Arabic, 1 tender)',
        'filename': 'sample_ad_health_center.pdf',
        'field': 'file',
        'mime': 'application/pdf',
    },
]


def _title_by_id(title_id):
    return next((t for t in SAMPLE_TITLES if t['id'] == title_id), None)


def _file_by_id(file_id):
    return next((f for f in SAMPLE_FILES if f['id'] == file_id), None)


# ── Individual tests ──────────────────────────────────────────────────────────

def test_health() -> dict:
    """Connectivity + vision-model readiness on the AJiAI server."""
    base = _base_url()
    t0 = time.perf_counter()
    try:
        resp = requests.get(f'{base}/api/newspaper/health/', timeout=15)
        ms = int((time.perf_counter() - t0) * 1000)
        data = resp.json() if 'json' in resp.headers.get('Content-Type', '') else {}
        if resp.status_code == 200:
            model = data.get('model') or data.get('vision_model') or '?'
            return {'ok': True,
                    'detail': f'AJiAI reachable at {base} ({ms} ms). '
                              f'Vision model: {model}.'}
        return {'ok': False,
                'detail': f'AJiAI answered HTTP {resp.status_code} at {base} ({ms} ms).'}
    except requests.RequestException as exc:
        return {'ok': False, 'detail': f'Cannot reach AJiAI at {base}: {exc}'}


def test_title(title_id: str = '', custom_title: str = '') -> dict:
    """Classify (and translate if AEC) one predefined or custom title."""
    from . import ajibrain_client

    if custom_title.strip():
        title, expect = custom_title.strip(), '(custom title)'
    else:
        row = _title_by_id(title_id)
        if not row:
            return {'ok': False, 'detail': f'Unknown sample title id {title_id!r}.'}
        title, expect = row['title'], row['expect']

    t0 = time.perf_counter()
    is_aec, category = ajibrain_client.classify_tender(title)
    classify_ms = int((time.perf_counter() - t0) * 1000)

    lines = [f'Title: {title[:100]}',
             f'Expected: {expect}',
             f'Result: is_aec={is_aec}, category={category}  ({classify_ms} ms)']

    if is_aec and not ajibrain_client._is_arabic(title):
        t1 = time.perf_counter()
        arabic = ajibrain_client.translate_title(title)
        translate_ms = int((time.perf_counter() - t1) * 1000)
        ok_ar = ajibrain_client._is_arabic(arabic)
        lines.append(f'Translation ({translate_ms} ms): '
                     f'{arabic[:100] if ok_ar else "FAILED — " + arabic[:80]}')

    return {'ok': True, 'detail': '\n'.join(lines)}


def test_newspaper_file(file_id: str) -> dict:
    """POST one bundled sample file to /api/newspaper/extract/."""
    row = _file_by_id(file_id)
    if not row:
        return {'ok': False, 'detail': f'Unknown sample file id {file_id!r}.'}

    path = os.path.join(_ASSETS_DIR, row['filename'])
    if not os.path.exists(path):
        return {'ok': False,
                'detail': f'Sample file missing on server: {row["filename"]} '
                          f'(expected in tenders/ajibrain_test_assets/).'}

    base = _base_url()
    t0 = time.perf_counter()
    try:
        with open(path, 'rb') as fh:
            resp = requests.post(
                f'{base}/api/newspaper/extract/',
                files={row['field']: (row['filename'], fh, row['mime'])},
                data={'edition_date': '2026-08-24',
                      'source_url': 'tenderradar://api-test',
                      'page_number': '1'},
                timeout=int(getattr(settings, 'NEWSPAPER_AI_TIMEOUT', 600)),
            )
    except requests.RequestException as exc:
        return {'ok': False, 'detail': f'Cannot reach AJiAI extract endpoint: {exc}'}

    ms = int((time.perf_counter() - t0) * 1000)
    try:
        body = resp.json()
    except ValueError:
        return {'ok': False,
                'detail': f'HTTP {resp.status_code} — non-JSON reply ({ms} ms).'}

    if resp.status_code != 200 or not body.get('success'):
        err = (body.get('error') or {})
        return {'ok': False,
                'detail': f'HTTP {resp.status_code} {err.get("code", "")} — '
                          f'{err.get("message", "extraction failed")} ({ms} ms)'}

    tenders = body.get('tenders') or []
    lines = [f'{row["label"]}',
             f'Model: {body.get("model", "?")} — pages: '
             f'{body.get("pages_processed", "?")} — {ms} ms',
             f'Extracted {len(tenders)} tender(s):']
    for i, t in enumerate(tenders[:5], 1):
        title = (t.get('title') or t.get('title_ar') or '?')[:90]
        entity = (t.get('entity') or t.get('entity_ar') or '')[:50]
        lines.append(f'  {i}. {title}' + (f'  — {entity}' if entity else ''))
    if not tenders:
        lines.append('  (zero tenders is a valid reply, but this sample '
                     'contains one — check the vision model.)')
    return {'ok': bool(tenders), 'detail': '\n'.join(lines)}
