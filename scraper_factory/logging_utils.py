"""Structured logging — machine-readable events with a correlation id.

The Factory's prose logging is good for reading one incident. It is useless for
answering "how long does investigation take on ASP.NET portals?" or "which stage
did last night's twelve failures die in?", because the facts are welded into
English sentences.

So consequential moments also emit one line of JSON on the `scraper_factory.events`
logger, each carrying a correlation id that ties every stage of one build or run
together. Prose logging is unchanged and still goes to the same place — this is
additional, not a replacement, because a human reading a traceback should not have
to parse JSON to follow it.

    event("job.start", correlation_id=cid, scraper="FooScraper", attempt=2)
    → {"event": "job.start", "cid": "a3f9…", "scraper": "FooScraper", "attempt": 2,
       "ts": "2026-08-02T…"}

`timed()` measures a block and emits its duration, so performance metrics come
from the same mechanism rather than a parallel one that can disagree.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from typing import Any

#: Deliberately a separate logger so events can be routed to their own file or
#: shipper without doubling the prose log.
events_logger = logging.getLogger("scraper_factory.events")
logger = logging.getLogger("scraper_factory")

#: The current correlation id, so nested calls need not thread it through every
#: signature. Set by `correlation()`.
_current: dict[str, str] = {"cid": ""}


def new_correlation_id() -> str:
    """A short id — long enough to be unique in a day's logs, short enough to grep."""
    return uuid.uuid4().hex[:12]


def current_correlation_id() -> str:
    return _current.get("cid", "")


@contextlib.contextmanager
def correlation(cid: str = ""):
    """Bind a correlation id for the duration of a block."""
    previous = _current.get("cid", "")
    _current["cid"] = cid or new_correlation_id()
    try:
        yield _current["cid"]
    finally:
        _current["cid"] = previous


def event(name: str, *, correlation_id: str = "", level: int = logging.INFO,
          **fields: Any) -> None:
    """Emit one structured event. Never raises."""
    try:
        payload: dict[str, Any] = {
            "event": name,
            "cid": correlation_id or current_correlation_id(),
            "ts": _now(),
        }
        for key, value in fields.items():
            payload[key] = _safe(value)
        events_logger.log(level, json.dumps(payload, ensure_ascii=False,
                                            default=str))
    except Exception:  # noqa: BLE001 — logging must never break the caller
        pass


@contextlib.contextmanager
def timed(name: str, *, correlation_id: str = "", **fields: Any):
    """Measure a block and emit `<name>` with duration_ms, success and any error.

    The event is emitted on the way out whether or not the block raised, so a
    stage that crashes still contributes a timing rather than vanishing from the
    metrics.
    """
    started = time.monotonic()
    failed = ""
    try:
        yield
    except Exception as exc:  # noqa: BLE001
        failed = f"{type(exc).__name__}: {exc}"[:200]
        raise
    finally:
        event(name, correlation_id=correlation_id,
              duration_ms=int((time.monotonic() - started) * 1000),
              success=not failed, error=failed, **fields)


def _now() -> str:
    from django.utils import timezone
    return timezone.now().isoformat(timespec="seconds")


def _safe(value: Any) -> Any:
    """Keep events small and JSON-serialisable.

    A truncated field is more useful than a log line so large nobody ships it, and
    an un-serialisable object must not silently drop the whole event.
    """
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return value if len(value) <= 300 else value[:297] + "..."
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in list(value)[:12]]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in list(value.items())[:16]}
    return str(value)[:300]
