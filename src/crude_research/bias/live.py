"""Fetch mapped-futures history and evaluate M4 bias. Read-only market data."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from crude_research.bias.engine import (
    BiasSnapshot,
    collect_predictions,
    evaluate_bias,
    merge_predictions,
)
from crude_research.bias.store import load_predictions, persist_predictions, predictions_path
from crude_research.config import Settings
from crude_research.exceptions import QuoteRequestError
from crude_research.market.candle_store import persist_bars
from crude_research.market.candles import (
    Bar,
    aggregate_session_4h,
    bars_from_kite_candles,
    mark_in_progress_bars,
)
from crude_research.market.models import Instrument
from crude_research.quant.time import parse_timezone
from crude_research.zerodha.client import MarketDataBroker

DAILY_LOOKBACK_DAYS = 400
MINUTE_LOOKBACK_DAYS = 90
DAY_CHUNK = timedelta(days=400)
MINUTE_CHUNK = timedelta(days=30)


def _windows(start: datetime, end: datetime, step: timedelta) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        nxt = min(cursor + step, end)
        yield cursor, nxt
        cursor = nxt


def fetch_interval_bars(
    client: MarketDataBroker,
    instrument_token: int,
    *,
    interval: str,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    now: datetime,
    session_close: time,
) -> list[Bar]:
    chunk = DAY_CHUNK if interval == "day" else MINUTE_CHUNK
    rows: list[dict[str, object]] = []
    seen: set[datetime] = set()
    for from_dt, to_dt in _windows(start, end, chunk):
        try:
            payload = client.historical_data(instrument_token, from_dt, to_dt, interval, oi=True)
        except Exception as exc:
            from crude_research.diagnostics.kite_auth import format_kite_exception
            from crude_research.exceptions import CrudeResearchError

            if isinstance(exc, CrudeResearchError):
                raise
            raise QuoteRequestError(
                f"Kite historical_data({interval}) failed: {format_kite_exception(exc)}"
            ) from exc
        for row in payload:
            rows.append(row)
    bars = bars_from_kite_candles(rows, tz=tz, complete=True)
    unique: list[Bar] = []
    for bar in bars:
        if bar.start in seen:
            continue
        seen.add(bar.start)
        unique.append(bar)
    unique.sort(key=lambda item: item.start)
    if interval == "day":
        return mark_in_progress_bars(
            unique, now=now, interval="day", session_close=session_close
        )
    if interval == "60minute":
        return mark_in_progress_bars(
            unique, now=now, interval="60minute", session_close=session_close
        )
    return unique


def build_futures_bias(
    client: MarketDataBroker,
    future: Instrument,
    settings: Settings,
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> tuple[BiasSnapshot, dict[str, Path]]:
    """Score direction from the mapped MCX future. Option premiums are not used."""
    tz = parse_timezone(settings.timezone)
    as_of = now or datetime.now(tz=tz)
    session_close = settings.parsed_expiry_time()
    token = future.instrument_token
    daily = fetch_interval_bars(
        client,
        token,
        interval="day",
        start=as_of - timedelta(days=DAILY_LOOKBACK_DAYS),
        end=as_of,
        tz=tz,
        now=as_of,
        session_close=session_close,
    )
    hourly = fetch_interval_bars(
        client,
        token,
        interval="60minute",
        start=as_of - timedelta(days=MINUTE_LOOKBACK_DAYS),
        end=as_of,
        tz=tz,
        now=as_of,
        session_close=session_close,
    )
    if not daily or not hourly:
        raise QuoteRequestError(
            f"Kite historical_data returned no futures candles for {future.tradingsymbol}"
        )
    h4 = aggregate_session_4h(hourly, now=as_of, session_close=session_close)
    written: dict[str, Path] = {}
    if persist:
        written["day"] = persist_bars(
            daily, settings.data_dir, symbol=future.tradingsymbol, interval="day", retrieved_at=as_of
        )
        written["60minute"] = persist_bars(
            hourly,
            settings.data_dir,
            symbol=future.tradingsymbol,
            interval="60minute",
            retrieved_at=as_of,
        )
        written["4h"] = persist_bars(
            h4, settings.data_dir, symbol=future.tradingsymbol, interval="4h", retrieved_at=as_of
        )
    pred_path = predictions_path(settings.data_dir, symbol=future.tradingsymbol)
    stored = load_predictions(pred_path)
    merged = collect_predictions(
        daily=daily,
        h4=h4,
        h1=hourly,
        settings=settings,
        existing=stored,
        session_close=session_close,
    )
    _, added = merge_predictions(stored, merged)
    if persist and (added or not pred_path.exists()):
        persist_predictions(merged, pred_path)
        written["predictions"] = pred_path
    snapshot = evaluate_bias(
        daily=daily,
        h4=h4,
        h1=hourly,
        settings=settings,
        predictions=merged,
    )
    return snapshot, written
