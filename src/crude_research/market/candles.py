"""Futures bars and MCX-session 4H aggregation. Unfinished buckets are never confirmed."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from crude_research.quant.time import require_aware

IST = ZoneInfo("Asia/Kolkata")
log = logging.getLogger(__name__)

INCOMPLETE_SOURCE_BARS = "INCOMPLETE_SOURCE_BARS"
CONFLICTING_SOURCE_BARS = "CONFLICTING_SOURCE_BARS"


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    oi: float | None = None
    complete: bool = True
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_aware(self.start, label="bar.start")


def completed_bars(bars: Sequence[Bar]) -> list[Bar]:
    return [bar for bar in bars if bar.complete]


def _hour_start(ts: datetime) -> datetime:
    local = ts.astimezone(IST)
    return datetime(local.year, local.month, local.day, local.hour, 0, tzinfo=IST)


def _bucket_start(ts: datetime) -> datetime:
    local = ts.astimezone(IST)
    hour = local.hour
    if 9 <= hour < 13:
        start_h = 9
        day = local.date()
    elif 13 <= hour < 17:
        start_h = 13
        day = local.date()
    elif 17 <= hour < 21:
        start_h = 17
        day = local.date()
    elif hour >= 21:
        start_h = 21
        day = local.date()
    else:
        prev = local.date() - timedelta(days=1)
        return datetime(prev.year, prev.month, prev.day, 21, 0, tzinfo=IST)
    return datetime(day.year, day.month, day.day, start_h, 0, tzinfo=IST)


def session_4h_end(start: datetime, session_close: time) -> datetime:
    """Inclusive end of the IST 4H session bucket that begins at `start`."""
    return _bucket_end(start, session_close)


def _bucket_end(start: datetime, session_close: time) -> datetime:
    local = start.astimezone(IST)
    if local.hour == 21:
        return datetime(
            local.year,
            local.month,
            local.day,
            session_close.hour,
            session_close.minute,
            session_close.second,
            tzinfo=IST,
        )
    return local + timedelta(hours=4)


def expected_60m_starts(bucket_start: datetime, session_close: time) -> list[datetime]:
    """Hour-aligned 60m starts that must exist before a 4H bucket can be confirmed."""
    local = bucket_start.astimezone(IST)
    if local.hour == 21:
        close_at = _bucket_end(local, session_close)
        starts: list[datetime] = []
        cursor = local
        while cursor < close_at:
            starts.append(cursor)
            cursor += timedelta(hours=1)
        return starts
    return [local + timedelta(hours=offset) for offset in range(4)]


def _same_ohlcv(left: Bar, right: Bar) -> bool:
    return (
        left.open == right.open
        and left.high == right.high
        and left.low == right.low
        and left.close == right.close
        and left.volume == right.volume
        and left.oi == right.oi
    )


def _dedupe_hour_bars(members: Sequence[Bar]) -> tuple[dict[datetime, Bar], bool]:
    by_hour: dict[datetime, Bar] = {}
    conflict = False
    for bar in members:
        key = _hour_start(bar.start)
        existing = by_hour.get(key)
        if existing is None:
            by_hour[key] = replace(bar, start=key)
            continue
        if not _same_ohlcv(existing, bar):
            conflict = True
        # Exact duplicates are ignored. Conflicts keep the first row and never sum volume.
    return by_hour, conflict


def aggregate_session_4h(
    bars_60m: Sequence[Bar],
    *,
    now: datetime,
    session_close: time,
) -> list[Bar]:
    """Build IST session 4H candles from 60-minute bars.

    Buckets: 09:00–13:00, 13:00–17:00, 17:00–21:00, 21:00–session close.
    A bucket is `complete` only when wall-clock has passed its end AND every
    expected completed 60-minute source bar is present with no OHLC conflicts.
    Missing hours are never interpolated.
    """
    require_aware(now, label="now")
    groups: dict[datetime, list[Bar]] = {}
    for bar in bars_60m:
        key = _bucket_start(bar.start)
        groups.setdefault(key, []).append(bar)
    out: list[Bar] = []
    now_local = now.astimezone(IST)
    for start in sorted(groups):
        by_hour, conflict = _dedupe_hour_bars(groups[start])
        expected = expected_60m_starts(start, session_close)
        if not expected:
            log.info("4H bucket %s skipped: empty expected 60m set", start.isoformat())
            continue
        usable = [by_hour[ts] for ts in expected if ts in by_hour]
        if not usable:
            log.info(
                "4H bucket %s skipped: no expected 60m bars (%s)",
                start.isoformat(),
                INCOMPLETE_SOURCE_BARS,
            )
            continue
        end = _bucket_end(start, session_close)
        have_complete = {ts for ts in expected if ts in by_hour and by_hour[ts].complete}
        notes: list[str] = []
        complete = now_local >= end and have_complete == set(expected) and not conflict
        if conflict:
            notes.append(CONFLICTING_SOURCE_BARS)
            complete = False
        if have_complete != set(expected):
            notes.append(INCOMPLETE_SOURCE_BARS)
            complete = False
        if not complete:
            log.info(
                "4H bucket %s complete=false reasons=%s expected=%s have=%s",
                start.isoformat(),
                ",".join(notes) or "WINDOW_OPEN",
                [ts.strftime("%H:%M") for ts in expected],
                [ts.strftime("%H:%M") for ts in sorted(have_complete)],
            )
        out.append(
            Bar(
                start=start,
                open=usable[0].open,
                high=max(item.high for item in usable),
                low=min(item.low for item in usable),
                close=usable[-1].close,
                volume=sum(item.volume for item in usable),
                oi=usable[-1].oi,
                complete=complete,
                notes=tuple(notes),
            )
        )
    return out


def bar_is_complete(
    start: datetime,
    *,
    now: datetime,
    interval: Literal["day", "60minute"],
    session_close: time,
) -> bool:
    local_now = now.astimezone(IST)
    local_start = start.astimezone(IST)
    if interval == "day":
        close_at = datetime.combine(local_start.date(), session_close, tzinfo=IST)
        return local_now >= close_at
    hour_end = local_start + timedelta(hours=1)
    if local_start.hour >= 21:
        close_at = datetime.combine(local_start.date(), session_close, tzinfo=IST)
        if hour_end > close_at:
            hour_end = close_at
    return local_now >= hour_end


def mark_in_progress_bars(
    bars: Sequence[Bar],
    *,
    now: datetime,
    interval: Literal["day", "60minute"],
    session_close: time,
) -> list[Bar]:
    """Mark Kite candles that have not finished as `complete=False`."""
    require_aware(now, label="now")
    out: list[Bar] = []
    for bar in bars:
        done = bar_is_complete(
            bar.start, now=now, interval=interval, session_close=session_close
        )
        out.append(bar if bar.complete == done else replace(bar, complete=done))
    return out


def bars_from_kite_candles(rows: Sequence[dict[str, object]], *, tz: ZoneInfo, complete: bool = True) -> list[Bar]:
    bars: list[Bar] = []
    for row in rows:
        raw_dt = row.get("date")
        start: datetime | None = None
        if isinstance(raw_dt, datetime):
            start = raw_dt if raw_dt.tzinfo else raw_dt.replace(tzinfo=tz)
            start = start.astimezone(tz)
        elif hasattr(raw_dt, "to_pydatetime"):
            converted = raw_dt.to_pydatetime()  # pandas.Timestamp
            if isinstance(converted, datetime):
                start = converted if converted.tzinfo else converted.replace(tzinfo=tz)
                start = start.astimezone(tz)
        if start is None:
            continue
        oi_raw = row.get("oi")
        oi = float(oi_raw) if isinstance(oi_raw, (int, float)) else None
        bars.append(
            Bar(
                start=start,
                open=float(row["open"]),  # type: ignore[arg-type]
                high=float(row["high"]),  # type: ignore[arg-type]
                low=float(row["low"]),  # type: ignore[arg-type]
                close=float(row["close"]),  # type: ignore[arg-type]
                volume=float(row.get("volume") or 0),  # type: ignore[arg-type]
                oi=oi,
                complete=complete,
            )
        )
    return bars
