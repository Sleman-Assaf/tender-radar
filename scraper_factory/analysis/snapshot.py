"""F8 — HTML Snapshot System.

Before any generation we capture exactly what the website looked like: body,
status, response headers, cookie names, and a structural DOM fingerprint. That
gives us three things:

  * reproducibility — a generation can be replayed against the same input
  * debuggability   — "what did the page look like when this was written?"
  * regression      — F9 diffs the live site against the stored baseline

Privacy: cookie NAMES are stored, never their values. A session cookie is a
credential, and snapshots are debugging artefacts that many people can read.

Storage: small bodies live inline in the row; anything larger goes to
FACTORY_SNAPSHOT_DIR so the database stays lean.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import re
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("scraper_factory")


# ── DOM fingerprinting ────────────────────────────────────────────────────────

def dom_fingerprint(html: str) -> dict[str, Any]:
    """Summarise a page's structure so two snapshots can be compared cheaply.

    Deliberately structural, not textual: tender titles change every week, but
    the shape of the listing (table vs cards, which classes repeat, how many
    rows) is what a scraper actually depends on.
    """
    fp: dict[str, Any] = {
        "tables": 0, "rows": 0, "cards": 0, "forms": 0, "links": 0,
        "repeating_classes": {}, "table_signatures": [], "ids_sample": [],
        "has_viewstate": False, "text_length": 0,
    }
    if not html:
        return fp

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("[factory] beautifulsoup4 unavailable — coarse fingerprint")
        fp["text_length"] = len(html)
        return fp

    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")
    fp["tables"] = len(tables)
    fp["rows"] = len(soup.find_all("tr"))
    fp["forms"] = len(soup.find_all("form"))
    fp["links"] = len(soup.find_all("a"))
    fp["has_viewstate"] = bool(soup.find("input", {"name": "__VIEWSTATE"}))
    fp["text_length"] = len(soup.get_text(separator=" ", strip=True))

    # Which classes repeat many times? Those are the listing containers a
    # scraper selects on, so a change here is a strong regression signal.
    counts: dict[str, int] = {}
    for el in soup.find_all(class_=True):
        for cls in el.get("class") or []:
            counts[cls] = counts.get(cls, 0) + 1
    fp["repeating_classes"] = {
        k: v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:40]
        if v >= 2
    }
    fp["cards"] = max(
        (v for k, v in counts.items()
         if re.search(r"card|item|tender|result|listing", k, re.I)),
        default=0,
    )

    # Column headers per table — the mapping a row parser relies on.
    for table in tables[:5]:
        headers = [th.get_text(strip=True)[:40]
                   for th in table.find_all("th")[:12]]
        body_rows = len(table.find_all("tr"))
        fp["table_signatures"].append({
            "classes": table.get("class") or [],
            "headers": headers,
            "rows": body_rows,
        })

    fp["ids_sample"] = [el.get("id") for el in soup.find_all(id=True)[:25]]
    return fp


def structural_hash(fingerprint: dict[str, Any]) -> str:
    """Stable hash of the structural parts only (ignores row counts/content)."""
    import json
    stable = {
        "tables": fingerprint.get("tables"),
        "classes": sorted((fingerprint.get("repeating_classes") or {}).keys()),
        "headers": [t.get("headers") for t in
                    (fingerprint.get("table_signatures") or [])],
        "has_viewstate": fingerprint.get("has_viewstate"),
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


# ── Snapshot store ────────────────────────────────────────────────────────────

class SnapshotStore:
    """Persists and retrieves website snapshots."""

    def __init__(self, base_dir: Optional[pathlib.Path] = None):
        self.base = pathlib.Path(
            base_dir or getattr(settings, "FACTORY_SNAPSHOT_DIR",
                                pathlib.Path("scraper_factory/snapshots")))
        self.inline_max = int(
            getattr(settings, "FACTORY_SNAPSHOT_INLINE_MAX", 20000))

    # ── writing ───────────────────────────────────────────────────────────────

    def capture(self, *, url: str, body: str, kind: str = "RAW_HTML",
                status_code: int | None = None,
                headers: dict[str, str] | None = None,
                cookies: dict[str, str] | None = None,
                fetched_with: str = "", scraper=None,
                is_baseline: bool = False):
        """Store one snapshot and return the HtmlSnapshot row."""
        from ..models import HtmlSnapshot

        body = body or ""
        content_hash = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
        fingerprint = dom_fingerprint(body) if kind in ("RAW_HTML",
                                                        "RENDERED_DOM") else {}
        if fingerprint:
            fingerprint["structural_hash"] = structural_hash(fingerprint)

        inline, rel_path = "", ""
        if len(body) <= self.inline_max:
            inline = body
        else:
            rel_path = self._write_file(content_hash, body)

        return HtmlSnapshot.objects.create(
            scraper=scraper, url=url[:1000], kind=kind,
            content_hash=content_hash, content_path=rel_path,
            content_inline=inline, byte_size=len(body.encode("utf-8", "replace")),
            status_code=status_code,
            response_headers=self._safe_headers(headers),
            # Values are intentionally discarded — names only.
            cookies={k: "***" for k in (cookies or {})},
            dom_fingerprint=fingerprint, fetched_with=fetched_with,
            is_baseline=is_baseline,
        )

    def _write_file(self, content_hash: str, body: str) -> str:
        """Write the body under a date+hash path; return the relative path."""
        today = timezone.now()
        rel = pathlib.Path(f"{today:%Y}/{today:%m}") / f"{content_hash[:16]}.html"
        target = self.base / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            return str(rel).replace("\\", "/")
        except OSError as exc:
            logger.warning("[factory] could not write snapshot %s: %s", rel, exc)
            return ""

    @staticmethod
    def _safe_headers(headers: dict[str, str] | None) -> dict[str, str]:
        """Drop headers that can carry credentials."""
        drop = {"set-cookie", "authorization", "proxy-authorization",
                "www-authenticate", "cookie"}
        return {k: v[:300] for k, v in (headers or {}).items()
                if k.lower() not in drop}

    # ── reading ───────────────────────────────────────────────────────────────

    @staticmethod
    def baseline_for(scraper):
        """The snapshot an installed scraper was generated against."""
        from ..models import HtmlSnapshot
        return (HtmlSnapshot.objects
                .filter(scraper=scraper, is_baseline=True)
                .order_by("-created_at").first())

    @staticmethod
    def latest_for_url(url: str):
        from ..models import HtmlSnapshot
        return (HtmlSnapshot.objects.filter(url=url[:1000])
                .order_by("-created_at").first())

    def prune(self, days: int | None = None) -> int:
        """Delete snapshots past the retention window, keeping baselines."""
        from ..models import HtmlSnapshot
        days = days or int(
            getattr(settings, "FACTORY_SNAPSHOT_RETENTION_DAYS", 180))
        cutoff = timezone.now() - timezone.timedelta(days=days)
        qs = HtmlSnapshot.objects.filter(created_at__lt=cutoff, is_baseline=False)
        paths = [p for p in qs.values_list("content_path", flat=True) if p]
        count = qs.count()
        qs.delete()
        for rel in paths:
            try:
                (self.base / rel).unlink(missing_ok=True)
            except OSError:
                pass
        return count
