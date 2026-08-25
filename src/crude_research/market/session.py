"""MCX energy session close. Distinct from OPTION_EXPIRY_TIME / Black-76 T."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from crude_research.exceptions import ConfigurationError

NY = ZoneInfo("America/New_York")

# MCX crude evening close tracks CME/NYMEX: 23:55 IST while the US is on DST, else 23:30 IST.
_DST_CLOSE = time(23, 55)
_STANDARD_CLOSE = time(23, 30)


@dataclass(frozen=True)
class SessionClose:
    clock: time
    rule: str
    trading_date: date


def parse_clock(value: str, *, label: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ConfigurationError(f"{label} must be HH:MM:SS, got {value!r}")
    try:
        hour, minute, second = (int(p) for p in parts)
        return time(hour=hour, minute=minute, second=second)
    except ValueError as exc:
        raise ConfigurationError(f"{label} is not a valid clock time: {value!r}") from exc


def resolve_mcx_session_close(
    trading_date: date,
    *,
    override: time | None = None,
) -> SessionClose:
    """Return the MCX energy session close for `trading_date`.

    Never guesses. An explicit config override wins. Otherwise US DST in
    America/New_York selects 23:55 vs 23:30. If tzdata cannot answer, raise.
    """
    if override is not None:
        return SessionClose(clock=override, rule="CONFIGURED_OVERRIDE", trading_date=trading_date)
    try:
        probe = datetime(trading_date.year, trading_date.month, trading_date.day, 12, 0, tzinfo=NY)
        dst = probe.dst()
    except Exception as exc:
        raise ConfigurationError(
            "SESSION_CLOSE_UNRESOLVED: America/New_York DST is unavailable"
        ) from exc
    if dst is None:
        raise ConfigurationError("SESSION_CLOSE_UNRESOLVED: DST offset is unknown")
    if dst != timedelta(0):
        return SessionClose(clock=_DST_CLOSE, rule="NY_DST_2355", trading_date=trading_date)
    return SessionClose(clock=_STANDARD_CLOSE, rule="NY_STANDARD_2330", trading_date=trading_date)
