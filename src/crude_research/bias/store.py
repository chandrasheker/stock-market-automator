"""Append-only directional predictions. Past 4H rows are never rewritten."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from crude_research.bias.health import DirectionPrediction
from crude_research.market.candles import IST


def _as_float(value: object) -> float:
    return float(str(value))


def _as_int(value: object) -> int:
    return int(str(value))


def predictions_path(data_dir: Path, *, symbol: str) -> Path:
    return Path(data_dir) / "bias" / f"symbol={symbol}" / "predictions.parquet"


def load_predictions(path: Path) -> list[DirectionPrediction]:
    if not path.exists():
        return []
    frame = pd.read_parquet(str(path))
    out: list[DirectionPrediction] = []
    for row in frame.itertuples(index=False):
        start = row.bar_start
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=IST)
        close = _as_float(getattr(row, "price_at_prediction", None) or row.close)
        atr = _as_float(getattr(row, "atr_at_prediction", None) or row.atr)
        horizon = _as_int(getattr(row, "prediction_horizon", 5) or 5)
        stamp = getattr(row, "prediction_timestamp", None)
        if not isinstance(stamp, datetime):
            stamp = start
        elif stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        out.append(
            DirectionPrediction(
                bar_start=start,
                direction=_as_int(row.direction),
                close=close,
                atr=atr,
                prediction_horizon=horizon,
                prediction_timestamp=stamp,
            )
        )
    return out


def persist_predictions(
    predictions: Sequence[DirectionPrediction],
    path: Path,
) -> Path:
    """Replace the file with the merged prediction set (keys only grow)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "bar_start": item.bar_start,
            "direction": item.direction,
            "close": item.close,
            "atr": item.atr,
            "price_at_prediction": item.price_at_prediction,
            "atr_at_prediction": item.atr_at_prediction,
            "prediction_timestamp": item.prediction_timestamp or item.bar_start,
            "prediction_horizon": item.prediction_horizon,
            "stored_at_utc": datetime.now(tz=UTC),
        }
        for item in predictions
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "bar_start",
            "direction",
            "close",
            "atr",
            "price_at_prediction",
            "atr_at_prediction",
            "prediction_timestamp",
            "prediction_horizon",
            "stored_at_utc",
        ],
    )
    frame.to_parquet(path, engine="pyarrow", index=False)
    return path
