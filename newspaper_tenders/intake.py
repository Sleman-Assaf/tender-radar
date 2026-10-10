# newspaper_tenders/intake.py
# PDF download + validation + hashing for Newspaper Tenders.

import hashlib
import logging
import time

import requests
from django.conf import settings

from .alrai import _headers

logger = logging.getLogger('tenders')

PDF_MAGIC = b'%PDF-'


class DownloadError(Exception):
    pass


class InvalidPdfError(Exception):
    pass


def _max_file_size() -> int:
    return int(getattr(settings, 'NEWSPAPER_MAX_FILE_SIZE', 25 * 1024 * 1024))


def download_pdf(url: str, referer: str = '') -> bytes:
    """
    Download one newspaper PDF with a realistic UA + Referer (Cloudflare /
    hotlink protection), with retries + exponential backoff on transient
    failures. `referer` should be the newspaper's own site.
    """
    retries = int(getattr(settings, 'NEWSPAPER_MAX_RETRIES', 3))
    timeout = int(getattr(settings, 'NEWSPAPER_DOWNLOAD_TIMEOUT', 60))
    delay   = 3
    headers = _headers(referer) if referer else _headers()

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            # 4xx (except 429) is permanent — retrying can't help.
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.RequestException(f'HTTP {resp.status_code}')
            if resp.status_code != 200:
                raise DownloadError(f'HTTP {resp.status_code} for {url}')
            return resp.content
        except DownloadError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(f'[newspaper.intake] attempt {attempt}/{retries} '
                           f'failed for {url}: {exc}')
            if attempt < retries:
                time.sleep(delay * (2 ** (attempt - 1)))    # 3s, 6s, 12s…
    raise DownloadError(f'Download failed after {retries} attempts: {last_exc}')


def validate_pdf(data: bytes) -> None:
    """Signature / size / emptiness checks. Raises InvalidPdfError."""
    if not data:
        raise InvalidPdfError('Empty file')
    if not data.startswith(PDF_MAGIC):
        raise InvalidPdfError('Not a PDF (bad signature)')
    if len(data) > _max_file_size():
        raise InvalidPdfError(
            f'File too large ({len(data)} bytes, limit {_max_file_size()})')


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
