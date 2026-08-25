"""M4 directional bias, volatility regime, and model-health veto. Futures only."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from crude_research.bias.health import DirectionPrediction, evaluate_health
from crude_research.bias.indicators import (
    closes,
    ema,
    highs,
    last_slope,
    linear_regression_slope,
    lows,
    percentile_rank,
    supertrend,
    wilder_atr,
    zscore,
)
from crude_research.bias.structure import classify_structure
from crude_research.config import Settings
from crude_research.market.candles import IST, Bar, completed_bars, session_4h_end

BiasLabel = Literal["BULLISH", "BEARISH", "NEUTRAL"]
VolRegime = Literal["LOW", "NORMAL", "HIGH", "EXTREME"]


class BiasSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float
    bias: BiasLabel
    reasons: tuple[str, ...]
    volatility: VolRegime
    atr: float | None
    atr_percentile: float | None
    atr_zscore: float | None
    atr_roc: float | None
    range_atr: float | None
    model_health: str
    model_sample_count: int
    model_accuracy: float | None
    model_recent_accuracy: float | None
    allow_entry: bool
    no_trade_reasons: tuple[str, ...] = ()
    daily_st: int = 0
    h4_st: int = 0
    h1_st: int = 0


def oi_confirmation(daily: Sequence[Bar]) -> str | None:
    if len(daily) < 2:
        return None
    prev, last = daily[-2], daily[-1]
    if prev.oi is None or last.oi is None:
        return None
    dp = last.close - prev.close
    doi = last.oi - prev.oi
    if dp > 0 and doi > 0:
        return "LONG_BUILDUP"
    if dp < 0 and doi > 0:
        return "SHORT_BUILDUP"
    if dp > 0 and doi < 0:
        return "SHORT_COVERING"
    if dp < 0 and doi < 0:
        return "LONG_UNWINDING"
    return None


def _sign(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _classify_vol(
    *,
    percentile: float | None,
    z: float | None,
    range_atr: float | None,
    settings: Settings,
) -> VolRegime:
    extreme = False
    if percentile is not None and percentile >= settings.atr_extreme_percentile:
        extreme = True
    if z is not None and z >= settings.atr_extreme_zscore:
        extreme = True
    if range_atr is not None and range_atr >= settings.atr_extreme_range_mult:
        extreme = True
    if extreme:
        return "EXTREME"
    if percentile is not None and percentile >= 80:
        return "HIGH"
    if percentile is not None and percentile <= 20:
        return "LOW"
    return "NORMAL"


def evaluate_bias(
    *,
    daily: Sequence[Bar],
    h4: Sequence[Bar],
    h1: Sequence[Bar],
    settings: Settings,
    predictions: Sequence[DirectionPrediction] | None = None,
) -> BiasSnapshot:
    """Score direction from completed futures bars only. Incomplete 4H bars are ignored."""
    daily_c = completed_bars(daily)
    h4_c = completed_bars(h4)
    h1_c = completed_bars(h1)
    reasons: list[str] = []
    score = 0.0

    d_close = closes(daily_c)
    d_high = highs(daily_c)
    d_low = lows(daily_c)
    daily_st = 0
    if d_close.size >= settings.atr_period + 2:
        _line, d_dir = supertrend(d_high, d_low, d_close)
        daily_st = int(d_dir[-1])
        ema20 = ema(d_close, 20)
        ema50 = ema(d_close, 50) if d_close.size >= 50 else ema(d_close, min(20, d_close.size))
        slope = last_slope(ema20, 5)
        if daily_st > 0:
            score += 15
            reasons.append("DAILY_SUPERTREND_BULLISH")
        elif daily_st < 0:
            score -= 15
            reasons.append("DAILY_SUPERTREND_BEARISH")
        if not (len(ema20) == 0 or len(ema50) == 0) and ema20[-1] > ema50[-1]:
            score += 10
            reasons.append("DAILY_EMA_ALIGNMENT_BULLISH")
        elif ema20[-1] < ema50[-1]:
            score -= 10
            reasons.append("DAILY_EMA_ALIGNMENT_BEARISH")
        slope_sign = _sign(slope)
        if slope_sign > 0:
            score += 5
            reasons.append("DAILY_EMA_SLOPE_BULLISH")
        elif slope_sign < 0:
            score -= 5
            reasons.append("DAILY_EMA_SLOPE_BEARISH")

    h4_st = 0
    atr_last: float | None = None
    atr_pct: float | None = None
    atr_z: float | None = None
    atr_roc: float | None = None
    range_atr: float | None = None
    if len(h4_c) >= settings.atr_period + 2:
        hi = highs(h4_c)
        lo = lows(h4_c)
        c = closes(h4_c)
        _line, dir4 = supertrend(hi, lo, c)
        h4_st = int(dir4[-1])
        e20 = ema(c, 20)
        e50 = ema(c, 50) if c.size >= 50 else ema(c, min(20, c.size))
        lr = linear_regression_slope(c, min(20, c.size))
        atr = wilder_atr(hi, lo, c, settings.atr_period)
        atr_last = float(atr[-1]) if atr.size and not math.isnan(float(atr[-1])) else None
        look = min(settings.atr_percentile_lookback, int(sum(1 for x in atr if x == x)))
        hist = atr[-look:] if look else atr
        if atr_last is not None:
            atr_pct = percentile_rank(atr_last, hist)
            atr_z = zscore(atr_last, hist)
            if atr.size > 20 and not math.isnan(float(atr[-21])):
                prev = float(atr[-21])
                if prev:
                    atr_roc = (atr_last - prev) / prev
            last_range = h4_c[-1].high - h4_c[-1].low
            range_atr = last_range / atr_last if atr_last else None
        if h4_st > 0:
            score += 15
            reasons.append("4H_SUPERTREND_BULLISH")
        elif h4_st < 0:
            score -= 15
            reasons.append("4H_SUPERTREND_BEARISH")
        if e20[-1] > e50[-1]:
            score += 10
            reasons.append("4H_EMA_ALIGNMENT_BULLISH")
        elif e20[-1] < e50[-1]:
            score -= 10
            reasons.append("4H_EMA_ALIGNMENT_BEARISH")
        lr_sign = _sign(lr)
        if lr_sign > 0:
            score += 10
            reasons.append("4H_LR_SLOPE_BULLISH")
        elif lr_sign < 0:
            score -= 10
            reasons.append("4H_LR_SLOPE_BEARISH")

    h1_st = 0
    if len(h1_c) >= settings.atr_period + 2:
        hi = highs(h1_c)
        lo = lows(h1_c)
        c = closes(h1_c)
        _line, dir1 = supertrend(hi, lo, c)
        h1_st = int(dir1[-1])
        e20 = ema(c, 20)
        e50 = ema(c, 50) if c.size >= 50 else ema(c, min(20, c.size))
        ema_dir = 1 if e20[-1] > e50[-1] else -1 if e20[-1] < e50[-1] else 0
        if h1_st > 0 and ema_dir >= 0:
            score += 10
            reasons.append("1H_CONFIRMATION_BULLISH")
        elif h1_st < 0 and ema_dir <= 0:
            score -= 10
            reasons.append("1H_CONFIRMATION_BEARISH")

    structure = classify_structure(h4_c)
    if structure == "HH_HL":
        score += 15
        reasons.append("4H_HIGHER_HIGH_HIGHER_LOW")
    elif structure == "LH_LL":
        score -= 15
        reasons.append("4H_LOWER_HIGH_LOWER_LOW")

    oi = oi_confirmation(daily_c)
    if oi == "LONG_BUILDUP":
        score += 10
        reasons.append("FUT_OI_LONG_BUILDUP")
    elif oi == "SHORT_BUILDUP":
        score -= 10
        reasons.append("FUT_OI_SHORT_BUILDUP")
    elif oi is not None:
        reasons.append(f"FUT_OI_{oi}")

    score = max(-100.0, min(100.0, score))
    if score >= settings.bias_bullish_threshold:
        bias: BiasLabel = "BULLISH"
    elif score <= settings.bias_bearish_threshold:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    vol = _classify_vol(
        percentile=atr_pct, z=atr_z, range_atr=range_atr, settings=settings
    )
    health = evaluate_health(
        predictions or (),
        h4_c,
        horizon=settings.prediction_horizon_4h,
        min_samples=settings.model_health_min_samples,
        deterioration=settings.model_health_deterioration,
    )
    no_trade: list[str] = []
    if vol == "EXTREME":
        no_trade.append("EXTREME_VOLATILITY")
    if bias == "NEUTRAL":
        no_trade.append("BIAS_NEUTRAL")
    if daily_st != 0 and h4_st != 0 and daily_st != h4_st:
        no_trade.append("TIMEFRAMES_DISAGREE")
    if health.status == "DEGRADED":
        no_trade.append("MODEL_HEALTH_DEGRADED")
    allow = len(no_trade) == 0 and bias in {"BULLISH", "BEARISH"}
    return BiasSnapshot(
        score=score,
        bias=bias,
        reasons=tuple(reasons),
        volatility=vol,
        atr=atr_last,
        atr_percentile=atr_pct,
        atr_zscore=atr_z,
        atr_roc=atr_roc,
        range_atr=range_atr,
        model_health=health.status,
        model_sample_count=health.sample_count,
        model_accuracy=health.accuracy,
        model_recent_accuracy=health.recent_accuracy,
        allow_entry=allow,
        no_trade_reasons=tuple(no_trade),
        daily_st=daily_st,
        h4_st=h4_st,
        h1_st=h1_st,
    )


def prediction_from_snapshot(h4: Sequence[Bar], snapshot: BiasSnapshot) -> DirectionPrediction | None:
    done = completed_bars(h4)
    if not done or snapshot.atr is None or snapshot.atr <= 0:
        return None
    if snapshot.bias == "NEUTRAL":
        return None
    return DirectionPrediction(
        bar_start=done[-1].start,
        direction=1 if snapshot.bias == "BULLISH" else -1,
        close=done[-1].close,
        atr=snapshot.atr,
    )


def merge_predictions(
    stored: Sequence[DirectionPrediction],
    incoming: Sequence[DirectionPrediction],
) -> tuple[list[DirectionPrediction], list[DirectionPrediction]]:
    """Keep the first prediction for each 4H bar. Past rows are never rewritten."""
    by_start = {item.bar_start: item for item in stored}
    added: list[DirectionPrediction] = []
    for item in incoming:
        if item.bar_start in by_start:
            continue
        by_start[item.bar_start] = item
        added.append(item)
    merged = [by_start[key] for key in sorted(by_start)]
    return merged, added


def collect_predictions(
    *,
    daily: Sequence[Bar],
    h4: Sequence[Bar],
    h1: Sequence[Bar],
    settings: Settings,
    existing: Sequence[DirectionPrediction] = (),
    session_close: time | None = None,
) -> list[DirectionPrediction]:
    """Causal walk: predict at completed 4H bar t using only bars up to t."""
    close_at = session_close or settings.parsed_expiry_time()
    daily_c = completed_bars(daily)
    h4_c = completed_bars(h4)
    h1_c = completed_bars(h1)
    known = {item.bar_start for item in existing}
    fresh: list[DirectionPrediction] = []
    min_len = max(settings.atr_period + 2, 20)
    for i in range(min_len - 1, len(h4_c)):
        bar = h4_c[i]
        if bar.start in known:
            continue
        end = session_4h_end(bar.start, close_at)
        daily_asof = _daily_asof(daily_c, bar_end=end, session_close=close_at)
        h1_asof = [item for item in h1_c if item.start < end]
        snapshot = evaluate_bias(
            daily=daily_asof,
            h4=h4_c[: i + 1],
            h1=h1_asof,
            settings=settings,
            predictions=(),
        )
        pred = prediction_from_snapshot(h4_c[: i + 1], snapshot)
        if pred is not None:
            fresh.append(pred)
    merged, _added = merge_predictions(existing, fresh)
    return merged


def _daily_asof(
    daily: Sequence[Bar],
    *,
    bar_end: datetime,
    session_close: time,
) -> list[Bar]:
    """Include a daily bar only once that futures session has closed."""
    end_local = bar_end.astimezone(IST)
    out: list[Bar] = []
    for bar in daily:
        session_day = bar.start.astimezone(IST).date()
        close_at = datetime.combine(session_day, session_close, tzinfo=IST)
        if close_at <= end_local:
            out.append(bar)
    return out
