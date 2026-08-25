"""Transparent EMA, ATR, SuperTrend, and slope. No look-ahead."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from crude_research.market.candles import Bar


def closes(bars: Sequence[Bar]) -> np.ndarray:
    return np.array([bar.close for bar in bars], dtype=float)


def highs(bars: Sequence[Bar]) -> np.ndarray:
    return np.array([bar.high for bar in bars], dtype=float)


def lows(bars: Sequence[Bar]) -> np.ndarray:
    return np.array([bar.low for bar in bars], dtype=float)


def ema(values: np.ndarray, span: int) -> np.ndarray:
    out = np.full(values.shape, np.nan, dtype=float)
    if values.size == 0 or span < 1:
        return out
    alpha = 2.0 / (span + 1.0)
    out[0] = float(values[0])
    for i in range(1, values.size):
        out[i] = alpha * float(values[i]) + (1.0 - alpha) * out[i - 1]
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.empty_like(close)
    prev[0] = close[0]
    prev[1:] = close[:-1]
    return np.array(
        np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev))),
        dtype=float,
    )


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = true_range(high, low, close)
    atr = np.full(tr.shape, np.nan, dtype=float)
    if tr.size < period or period < 1:
        return atr
    atr[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, tr.size):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return SuperTrend line and direction (+1 bullish, -1 bearish)."""
    atr = wilder_atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    final_upper = np.copy(basic_upper)
    final_lower = np.copy(basic_lower)
    direction = np.zeros(close.shape, dtype=int)
    line = np.full(close.shape, np.nan, dtype=float)
    for i in range(close.size):
        if np.isnan(atr[i]):
            continue
        if i == 0 or np.isnan(atr[i - 1]):
            direction[i] = 1 if close[i] >= hl2[i] else -1
            line[i] = final_lower[i] if direction[i] > 0 else final_upper[i]
            continue
        if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]
        if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]
        if direction[i - 1] <= 0:
            direction[i] = 1 if close[i] > final_upper[i] else -1
        else:
            direction[i] = -1 if close[i] < final_lower[i] else 1
        line[i] = final_lower[i] if direction[i] > 0 else final_upper[i]
    return line, direction


def last_slope(values: np.ndarray, lookback: int = 5) -> float | None:
    if values.size < lookback + 1 or np.isnan(values[-1]) or np.isnan(values[-1 - lookback]):
        return None
    return float(values[-1] - values[-1 - lookback])


def linear_regression_slope(values: np.ndarray, lookback: int = 20) -> float | None:
    if values.size < lookback:
        return None
    window = values[-lookback:]
    if np.any(np.isnan(window)):
        return None
    x = np.arange(lookback, dtype=float)
    slope, _intercept = np.polyfit(x, window, 1)
    return float(slope)


def percentile_rank(value: float, history: np.ndarray) -> float | None:
    clean = history[np.isfinite(history)]
    if clean.size == 0 or not np.isfinite(value):
        return None
    return float(np.sum(clean <= value) / clean.size * 100.0)


def zscore(value: float, history: np.ndarray) -> float | None:
    clean = history[np.isfinite(history)]
    if clean.size < 2 or not np.isfinite(value):
        return None
    std = float(np.std(clean, ddof=1))
    if std == 0:
        return 0.0
    return float((value - float(np.mean(clean))) / std)
