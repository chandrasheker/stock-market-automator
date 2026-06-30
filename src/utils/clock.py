"""IST-aware clock helpers.

The app's trading logic is all in India Standard Time, but cloud VMs
(e.g. Oracle Cloud) usually run in UTC. Always use these helpers for
market-hours, entry-window, and expiry checks so behaviour is correct
regardless of the server timezone.
"""

from __future__ import annotations

from datetime import datetime, time as dt_time

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - fallback if tzdata missing
    from datetime import timedelta, timezone

    IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    """Current time in IST (naive — tz stripped for easy comparisons)."""
    return datetime.now(IST).replace(tzinfo=None)


def ist_time() -> dt_time:
    return ist_now().time()


def ist_today():
    return ist_now().date()
