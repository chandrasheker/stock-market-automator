"""Causal model-health: score a past directional prediction only after N completed 4H bars."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from crude_research.market.candles import Bar, completed_bars

HealthStatus = Literal["HEALTHY", "DEGRADED", "WARMING_UP"]


@dataclass(frozen=True)
class DirectionPrediction:
    bar_start: datetime
    direction: int
    close: float
    atr: float


@dataclass(frozen=True)
class HealthReport:
    status: HealthStatus
    sample_count: int
    accuracy: float | None
    recent_accuracy: float | None


def _outcome(move: float, atr: float, band: float) -> int:
    if atr <= 0:
        return 0
    if move > band * atr:
        return 1
    if move < -band * atr:
        return -1
    return 0


def evaluate_health(
    predictions: Sequence[DirectionPrediction],
    h4: Sequence[Bar],
    *,
    horizon: int,
    min_samples: int,
    deterioration: float,
    recent_window: int = 20,
    atr_band: float = 0.25,
) -> HealthReport:
    completed = completed_bars(h4)
    by_start = {bar.start: i for i, bar in enumerate(completed)}
    hits: list[bool] = []
    for pred in predictions:
        idx = by_start.get(pred.bar_start)
        if idx is None:
            continue
        later = idx + horizon
        if later >= len(completed):
            continue
        move = completed[later].close - pred.close
        outcome = _outcome(move, pred.atr, atr_band)
        if outcome == 0:
            continue
        hits.append(outcome == pred.direction)
    n = len(hits)
    if n == 0:
        return HealthReport("WARMING_UP", 0, None, None)
    overall = sum(hits) / n
    recent_slice = hits[-recent_window:]
    recent = sum(recent_slice) / len(recent_slice)
    status: HealthStatus
    if n < min_samples:
        status = "WARMING_UP"
    elif recent < overall - deterioration:
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    return HealthReport(status, n, overall, recent)
