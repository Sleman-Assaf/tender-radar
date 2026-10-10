# newspaper_tenders/runlock.py
"""
One newspaper run at a time, whoever started it.

This flag used to live in views.py, which was fine while the button was the
only way to start a run. The 08:00 scheduler changes that: a scheduled run has
to claim the SAME slot the button claims, for two reasons.

The obvious one is that two runs must not read one edition at once. The one
that actually matters is Reset: it asks "is a run in progress?" before deleting
an edition's tenders and photos, and a scheduled run invisible to that guard
could be writing rows while Reset wiped them.

Per-process is the right scope — the run is a thread in this process. A second
web worker against the same database would need a database lock instead, and
would also need the scheduler switched off (NEWSPAPER_INPROCESS_SCHEDULER=0) so
only one process schedules.
"""
import threading

_lock = threading.Lock()
_running = False


def claim() -> bool:
    """
    Take the run slot.

    True when it was free, and the caller then owns it and must release() in a
    finally. False when a run is already in progress.
    """
    global _running
    with _lock:
        if _running:
            return False
        _running = True
        return True


def release() -> None:
    """Give the slot back. Safe to call when not held."""
    global _running
    with _lock:
        _running = False


def is_running() -> bool:
    """True while a run holds the slot."""
    with _lock:
        return _running
