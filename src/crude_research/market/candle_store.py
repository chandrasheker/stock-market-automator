"""Append-only Parquet persistence for futures candles."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from crude_research.market.candles import Bar


def _as_float(value: object) -> float:
    return float(str(value))


def bars_to_frame(bars: Sequence[Bar], *, symbol: str, interval: str) -> pd.DataFrame:
    rows = [
        {
            "symbol": symbol,
            "interval": interval,
            "start": bar.start,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "oi": bar.oi,
            "complete": bar.complete,
            "notes": "|".join(bar.notes),
        }
        for bar in bars
    ]
    return pd.DataFrame(rows)


def frame_to_bars(frame: pd.DataFrame) -> list[Bar]:
    bars: list[Bar] = []
    for row in frame.itertuples(index=False):
        start = row.start
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        oi = row.oi
        oi_value = None if oi is None or pd.isna(oi) else _as_float(oi)
        raw_notes = getattr(row, "notes", "") or ""
        notes = tuple(part for part in str(raw_notes).split("|") if part)
        bars.append(
            Bar(
                start=start,
                open=_as_float(row.open),
                high=_as_float(row.high),
                low=_as_float(row.low),
                close=_as_float(row.close),
                volume=_as_float(row.volume),
                oi=oi_value,
                complete=bool(row.complete),
                notes=notes,
            )
        )
    return bars


def persist_bars(
    bars: Sequence[Bar],
    data_dir: Path,
    *,
    symbol: str,
    interval: str,
    retrieved_at: datetime,
) -> Path:
    """Write a new Parquet file. Existing candle files are never overwritten."""
    folder = Path(data_dir) / "candles" / f"symbol={symbol}" / f"interval={interval}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"bars_{stamp}.parquet"
    suffix = 1
    while path.exists():
        suffix += 1
        path = folder / f"bars_{stamp}_{suffix}.parquet"
    bars_to_frame(bars, symbol=symbol, interval=interval).to_parquet(path, engine="pyarrow", index=False)
    return path
