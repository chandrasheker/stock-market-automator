"""Timezone-aware year-fraction for Black-76. Integer DTE/365 is not used."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from crude_research.exceptions import ConfigurationError
from crude_research.market.models import ExpiryTimeSource

SECONDS_PER_YEAR_365_25: float = 365.25 * 24.0 * 3600.0
"""Gregorian mean year in seconds. Used as the default year-fraction denominator."""

THETA_DAYS_PER_YEAR: float = 365.0
"""Display divisor for theta_per_day = theta / 365. Not used for pricing T."""


def require_aware(ts: datetime, *, label: str) -> datetime:
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ConfigurationError(f"{label} must be timezone-aware; got naive {ts!r}")
    return ts


def parse_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ConfigurationError(f"Unknown TIMEZONE {name!r}") from exc


def attach_timezone(ts: datetime, tz: ZoneInfo, *, assume_already_local: bool = True) -> datetime:
    """Make a datetime timezone-aware.

    Naive REST quote stamps from Kite are exchange-local (Asia/Kolkata) strings.
    Naive websocket stamps from kiteconnect are `datetime.fromtimestamp` in OS local time.
    Callers must pass the correct `tz` for the source. This function never guesses IST
    when the value is already aware.
    """
    if ts.tzinfo is not None and ts.tzinfo.utcoffset(ts) is not None:
        return ts.astimezone(tz)
    if assume_already_local:
        return ts.replace(tzinfo=tz)
    return ts.replace(tzinfo=tz)


def assume_expiry_timestamp(
    expiry_date: date,
    *,
    tz: ZoneInfo,
    expiry_time: time,
) -> tuple[datetime, ExpiryTimeSource]:
    """Build an expiry timestamp from a date plus an explicit configured clock time.

    MCX publishes last-trading-day dates. The exact last-tick clock time on that date
    (23:30 vs 23:55 IST depending on US DST) is NOT taken from the instrument master.
    This helper records `CONFIGURED_ASSUMPTION` so research files never look like we
    knew the official exercise timestamp.
    """
    ts = datetime(
        expiry_date.year,
        expiry_date.month,
        expiry_date.day,
        expiry_time.hour,
        expiry_time.minute,
        expiry_time.second,
        expiry_time.microsecond,
        tzinfo=tz,
    )
    return ts, ExpiryTimeSource.CONFIGURED_ASSUMPTION


def time_to_expiry(
    now: datetime,
    expiry_timestamp: datetime,
    *,
    seconds_per_year: float = SECONDS_PER_YEAR_365_25,
) -> float:
    """Exact year fraction (expiry - now) / seconds_per_year.

    Both arguments must be timezone-aware. A negative result means the contract has
    already expired under the supplied timestamps; callers must treat T <= 0 as invalid
    for IV, not as a silent zero-vol option.
    """
    if seconds_per_year <= 0:
        raise ConfigurationError("seconds_per_year must be positive")
    now_aware = require_aware(now, label="now")
    expiry_aware = require_aware(expiry_timestamp, label="expiry_timestamp")
    delta = expiry_aware - now_aware.astimezone(expiry_aware.tzinfo)
    return delta.total_seconds() / seconds_per_year
