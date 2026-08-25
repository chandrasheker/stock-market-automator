"""Futures bars and MCX-session 4H aggregation. Unfinished buckets are never confirmed."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from crude_research.quant.time import require_aware

IST = ZoneInfo("Asia/Kolkata")


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

    def __post_init__(self) -> None:
        require_aware(self.start, label="bar.start")


def completed_bars(bars: Sequence[Bar]) -> list[Bar]:
    return [bar for bar in bars if bar.complete]


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


def session_4h_end(start: datetime, session_close: time = time(23, 30)) -> datetime:
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


def aggregate_session_4h(
    bars_60m: Sequence[Bar],
    *,
    now: datetime,
    session_close: time = time(23, 30),
) -> list[Bar]:
    """Build IST session 4H candles from 60-minute bars.

    Buckets: 09:00–13:00, 13:00–17:00, 17:00–21:00, 21:00–session close.
    A bucket is `complete` only when `now` is at or after its end.
    """
    require_aware(now, label="now")
    groups: dict[datetime, list[Bar]] = {}
    for bar in bars_60m:
        key = _bucket_start(bar.start)
        groups.setdefault(key, []).append(bar)
    out: list[Bar] = []
    for start in sorted(groups):
        members = sorted(groups[start], key=lambda item: item.start)
        end = _bucket_end(start, session_close)
        complete = now.astimezone(IST) >= end
        out.append(
            Bar(
                start=start,
                open=members[0].open,
                high=max(item.high for item in members),
                low=min(item.low for item in members),
                close=members[-1].close,
                volume=sum(item.volume for item in members),
                oi=members[-1].oi,
                complete=complete,
            )
        )
    return out


def bar_is_complete(
    start: datetime,
    *,
    now: datetime,
    interval: Literal["day", "60minute"],
    session_close: time = time(23, 30),
) -> bool:
    local_now = now.astimezone(IST)
    local_start = start.astimezone(IST)
    if interval == "day":
        close_at = datetime.combine(local_start.date(), session_close, tzinfo=IST)
        return local_now >= close_at
    return local_now >= local_start + timedelta(hours=1)


def mark_in_progress_bars(
    bars: Sequence[Bar],
    *,
    now: datetime,
    interval: Literal["day", "60minute"],
    session_close: time = time(23, 30),
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
