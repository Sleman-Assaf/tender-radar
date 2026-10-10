# newspaper_tenders/ai_client.py
# HTTP client for the AJiAI Newspaper Vision API (/api/newspaper/extract/).
#
# Follows the same conventions as tenders/ajibrain_client.py: settings-driven
# base URL, defensive parsing, clear log tags. AI retries live server-side
# (newspaper/vision.py); this client only distinguishes retryable transport
# failures from permanent ones.

import logging

import requests
from django.conf import settings

logger = logging.getLogger('tenders')


class AIServiceError(Exception):
    """AI extraction failed. `retryable` tells the pipeline whether the file
    should stay in a retryable state."""

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def _base_url() -> str:
    # Dedicated override first; falls back to the same AJiAI server the rest
    # of TenderRadar already talks to.
    url = getattr(settings, 'NEWSPAPER_AI_API_URL', '') or getattr(
        settings, 'AJIBRAIN_BASE_URL', 'http://127.0.0.1:8001')
    return url.rstrip('/')


def _timeout() -> int:
    return int(getattr(settings, 'NEWSPAPER_AI_TIMEOUT', 900))


def extract_tenders(pdf_bytes: bytes, *, edition_date: str, source_url: str,
                    pdf_url: str, page_number: int = 1) -> list[dict]:
    """
    Send one PDF to the AI service, return the list of extracted tender dicts
    (may be empty — zero tenders is a valid result). `page_number` is the
    base page label — used when the payload is one page cut out of a larger
    newspaper PDF (Al-Ghad).

    Raises AIServiceError on failure.
    """
    endpoint = f'{_base_url()}/api/newspaper/extract/'
    filename = pdf_url.rsplit('/', 1)[-1] or 'page.pdf'

    try:
        resp = requests.post(
            endpoint,
            files={'file': (filename, pdf_bytes, 'application/pdf')},
            data={
                'edition_date': edition_date,
                'source_url':   source_url,
                'pdf_url':      pdf_url,
                'page_number':  str(page_number),
            },
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        raise AIServiceError(f'AI service unreachable: {exc}', retryable=True)

    if resp.status_code in (502, 503, 504, 429):
        raise AIServiceError(f'AI service temporary failure: HTTP {resp.status_code}',
                             retryable=True)
    if resp.status_code != 200:
        detail = ''
        try:
            detail = resp.json().get('error', {}).get('message', '')
        except Exception:
            pass
        raise AIServiceError(
            f'AI service rejected the request: HTTP {resp.status_code} {detail}',
            retryable=False)

    try:
        body = resp.json()
    except ValueError:
        raise AIServiceError('AI service returned non-JSON body', retryable=True)

    if not body.get('success') or not isinstance(body.get('tenders'), list):
        raise AIServiceError(f'AI service returned malformed payload: {str(body)[:200]}',
                             retryable=False)

    logger.info(f'[newspaper.ai] {filename}: {len(body["tenders"])} tender(s) '
                f'from model {body.get("model", "?")}')
    return body['tenders']


def extract_from_image(image_b64: str, *, edition_date: str,
                       page_number: int = 1) -> list[dict]:
    """
    Zoom re-read: send ONE ad's cropped image back to the SAME AI endpoint
    (which already accepts 'image' uploads — no AJiAI change involved) so the
    model reads it at full resolution. This mirrors what a human gets by
    uploading the single ad to the AJi Brain chat: the whole-page pass can
    misread decorative headlines (استئجار مبنى بديل → استشاري مبدئي لبدائل,
    24 Aug 2026); the single-ad pass reads them correctly.

    Returns the tender list (may be empty). Raises AIServiceError on failure.
    """
    import base64
    endpoint = f'{_base_url()}/api/newspaper/extract/'
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        raise AIServiceError('Invalid ad image payload', retryable=False)

    try:
        resp = requests.post(
            endpoint,
            files={'image': ('ad.jpg', image_bytes, 'image/jpeg')},
            data={'edition_date': edition_date,
                  'page_number': str(page_number)},
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        raise AIServiceError(f'AI service unreachable: {exc}', retryable=True)

    if resp.status_code in (502, 503, 504, 429):
        raise AIServiceError(f'AI service temporary failure: HTTP {resp.status_code}',
                             retryable=True)
    if resp.status_code != 200:
        detail = ''
        try:
            detail = resp.json().get('error', {}).get('message', '')
        except Exception:
            pass
        raise AIServiceError(
            f'AI service rejected the re-read: HTTP {resp.status_code} {detail}',
            retryable=False)

    try:
        body = resp.json()
    except ValueError:
        raise AIServiceError('AI service returned non-JSON body', retryable=True)
    if not body.get('success') or not isinstance(body.get('tenders'), list):
        raise AIServiceError(f'AI service returned malformed payload: {str(body)[:200]}',
                             retryable=False)
    return body['tenders']


def health() -> dict:
    """GET the AI service health endpoint. Never raises."""
    try:
        resp = requests.get(f'{_base_url()}/api/newspaper/health/', timeout=15)
        return resp.json()
    except Exception as exc:
        return {'status': 'unreachable', 'error': str(exc)}
