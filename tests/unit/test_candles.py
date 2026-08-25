"""IST session 4H aggregation never confirms an unfinished bucket."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from crude_research.market.candle_store import frame_to_bars, persist_bars
from crude_research.market.candles import (
    IST,
    Bar,
    aggregate_session_4h,
    completed_bars,
    mark_in_progress_bars,
    session_4h_end,
)

SESSION_CLOSE = time(23, 30)


def _h(ts: datetime, price: float, *, complete: bool = True) -> Bar:
    return Bar(
        start=ts,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10,
        oi=100,
        complete=complete,
    )


def test_4h_buckets_are_session_hours() -> None:
    day = datetime(2026, 8, 25, tzinfo=IST)
    hourly = [
        _h(day.replace(hour=h), 5000 + h)
        for h in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23)
    ]
    now = datetime(2026, 8, 26, 10, 0, tzinfo=IST)
    bars = aggregate_session_4h(hourly, now=now, session_close=SESSION_CLOSE)
    starts = [bar.start.hour for bar in bars]
    assert starts == [9, 13, 17, 21]
    first = bars[0]
    assert first.open == 5009
    assert first.close == 5012
    assert first.high == 5013
    assert first.low == 5008
    assert session_4h_end(bars[-1].start, SESSION_CLOSE) == datetime(
        2026, 8, 25, 23, 30, tzinfo=IST
    )
    assert all(bar.complete for bar in bars)


def test_unfinished_4h_bucket_is_not_complete() -> None:
    day = datetime(2026, 8, 25, tzinfo=IST)
    hourly = [_h(day.replace(hour=h), 5100.0) for h in (13, 14)]
    now = datetime(2026, 8, 25, 15, 0, tzinfo=IST)
    bars = aggregate_session_4h(hourly, now=now, session_close=SESSION_CLOSE)
    assert len(bars) == 1
    assert bars[0].complete is False
    assert completed_bars(bars) == []


def test_pre_open_hour_joins_previous_late_bucket() -> None:
    late = datetime(2026, 8, 24, 22, 0, tzinfo=IST)
    early = datetime(2026, 8, 25, 8, 0, tzinfo=IST)
    now = datetime(2026, 8, 25, 10, 0, tzinfo=IST)
    bars = aggregate_session_4h(
        [_h(late, 5000.0), _h(early, 5010.0)],
        now=now,
        session_close=SESSION_CLOSE,
    )
    assert len(bars) == 1
    assert bars[0].start == datetime(2026, 8, 24, 21, 0, tzinfo=IST)
    assert bars[0].complete is False
    assert "INCOMPLETE_SOURCE_BARS" in bars[0].notes
    # 08:00 is outside the expected 21/22/23 set and must not become the close.
    assert bars[0].close == 5000.0


def test_open_daily_and_60m_bars_marked_incomplete() -> None:
    now = datetime(2026, 8, 25, 14, 30, tzinfo=IST)
    daily = [_h(datetime(2026, 8, 25, 0, 0, tzinfo=IST), 5000.0)]
    hourly = [_h(datetime(2026, 8, 25, 14, 0, tzinfo=IST), 5010.0)]
    daily_m = mark_in_progress_bars(daily, now=now, interval="day", session_close=SESSION_CLOSE)
    hourly_m = mark_in_progress_bars(
        hourly, now=now, interval="60minute", session_close=SESSION_CLOSE
    )
    assert daily_m[0].complete is False
    assert hourly_m[0].complete is False
    later = mark_in_progress_bars(
        hourly, now=now + timedelta(hours=1), interval="60minute", session_close=SESSION_CLOSE
    )
    assert later[0].complete is True


def test_complete_4h_source_set_is_confirmed() -> None:
    day = datetime(2026, 8, 25, tzinfo=IST)
    hourly = [_h(day.replace(hour=h), 5000.0 + h) for h in (9, 10, 11, 12)]
    now = datetime(2026, 8, 25, 13, 1, tzinfo=IST)
    bars = aggregate_session_4h(hourly, now=now, session_close=SESSION_CLOSE)
    assert len(bars) == 1
    assert bars[0].complete is True
    assert bars[0].notes == ()
    assert bars[0].open == 5009
    assert bars[0].close == 5012


def test_missing_60m_bar_is_not_confirmed() -> None:
    day = datetime(2026, 8, 25, tzinfo=IST)
    hourly = [_h(day.replace(hour=h), 5000.0 + h) for h in (9, 10, 12)]
    now = datetime(2026, 8, 25, 13, 1, tzinfo=IST)
    bars = aggregate_session_4h(hourly, now=now, session_close=SESSION_CLOSE)
    assert len(bars) == 1
    assert bars[0].complete is False
    assert "INCOMPLETE_SOURCE_BARS" in bars[0].notes
    assert completed_bars(bars) == []


def test_unfinished_latest_60m_bar_is_not_confirmed() -> None:
    day = datetime(2026, 8, 25, tzinfo=IST)
    hourly = [
        _h(day.replace(hour=9), 5009.0),
        _h(day.replace(hour=10), 5010.0),
        _h(day.replace(hour=11), 5011.0),
        _h(day.replace(hour=12), 5012.0, complete=False),
    ]
    now = datetime(2026, 8, 25, 13, 1, tzinfo=IST)
    bars = aggregate_session_4h(hourly, now=now, session_close=SESSION_CLOSE)
    assert bars[0].complete is False
    assert "INCOMPLETE_SOURCE_BARS" in bars[0].notes


def test_duplicate_source_bar_does_not_double_count_volume() -> None:
    day = datetime(2026, 8, 25, tzinfo=IST)
    hourly = [_h(day.replace(hour=h), 5000.0 + h) for h in (9, 10, 11, 12)]
    dup = _h(day.replace(hour=10), 5010.0)
    conflict = Bar(
        start=day.replace(hour=10),
        open=1,
        high=99,
        low=0,
        close=50,
        volume=9999,
        complete=True,
    )
    now = datetime(2026, 8, 25, 13, 1, tzinfo=IST)
    same = aggregate_session_4h([*hourly, dup], now=now, session_close=SESSION_CLOSE)
    assert same[0].complete is True
    assert same[0].volume == 40
    bad = aggregate_session_4h([*hourly, conflict], now=now, session_close=SESSION_CLOSE)
    assert bad[0].complete is False
    assert "CONFLICTING_SOURCE_BARS" in bad[0].notes
    assert bad[0].volume == 40


def test_candle_parquet_roundtrip(tmp_path: Path) -> None:
    bar = _h(datetime(2026, 8, 25, 9, 0, tzinfo=IST), 5123.5)
    path = persist_bars(
        [bar],
        tmp_path,
        symbol="CRUDEOILM26OCTFUT",
        interval="day",
        retrieved_at=datetime(2026, 8, 25, 12, 0, tzinfo=ZoneInfo("UTC")),
    )
    loaded = frame_to_bars(pd.read_parquet(path))
    assert loaded[0].close == 5123.5
    assert loaded[0].start.tzinfo is not None
