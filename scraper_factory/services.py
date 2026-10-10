"""Shared helpers for the Scraper Factory."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse, urlunparse


def normalize_url(url: str) -> str:
    """Canonical form used for duplicate detection.

    http/https, www., and a trailing slash all describe the same portal, so they
    must not produce two scrapers for one website.
    """
    parts = urlparse((url or "").strip())
    netloc = (parts.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = (parts.path or "/").rstrip("/") or "/"
    return urlunparse(("https", netloc, path, "", parts.query, ""))


def url_fingerprint(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()
