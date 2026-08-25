"""M4 money-critical: no unfinished bars, extreme vol veto, causal health/structure."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from crude_research.bias.engine import (
    collect_predictions,
    evaluate_bias,
    merge_predictions,
    prediction_from_snapshot,
)
from crude_research.bias.health import DirectionPrediction, evaluate_health
from crude_research.bias.store import load_predictions, persist_predictions
from crude_research.bias.structure import classify_structure, confirmed_pivots
from crude_research.cli import format_bias_snapshot
from crude_research.config import Settings
from crude_research.market.candles import IST, Bar
from tests.unit.test_candles import _h


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def _starts(n: int, day0: datetime) -> list[datetime]:
    hours = (9, 13, 17, 21)
    out: list[datetime] = []
    for i in range(n):
        day = day0 + timedelta(days=i // 4)
        hour = hours[i % 4]
        out.append(datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST))
    return out


def _trend(n: int, day0: datetime, *, base: float = 5000.0, step: float = 8.0) -> list[Bar]:
    bars: list[Bar] = []
    for i, ts in enumerate(_starts(n, day0)):
        price = base + i * step
        bars.append(
            Bar(
                start=ts,
                open=price - 2,
                high=price + 3,
                low=price - 3,
                close=price,
                volume=50,
                oi=1_000 + i * 10,
                complete=True,
            )
        )
    return bars


def _daily_trend(n: int, day0: datetime, *, base: float = 5000.0, step: float = 12.0) -> list[Bar]:
    bars: list[Bar] = []
    for i in range(n):
        day = day0 + timedelta(days=i)
        price = base + i * step
        ts = datetime(day.year, day.month, day.day, 0, 0, tzinfo=IST)
        bars.append(
            Bar(
                start=ts,
                open=price - 5,
                high=price + 6,
                low=price - 6,
                close=price,
                volume=200,
                oi=10_000 + i * 50,
                complete=True,
            )
        )
    return bars


def test_confirmed_pivots_need_right_side_bars() -> None:
    day0 = datetime(2026, 1, 5, tzinfo=IST)
    highs = [10, 11, 15, 11, 10, 12, 18, 12, 11, 13, 20, 13, 12]
    lows = [9, 8, 10, 8.5, 7, 9, 11, 9, 8, 10, 12, 10, 9]
    bars = [
        Bar(
            start=day0 + timedelta(hours=4 * i),
            open=lows[i] + 0.5,
            high=highs[i],
            low=lows[i],
            close=lows[i] + 0.5,
            complete=True,
        )
        for i in range(len(highs))
    ]
    with_partial = confirmed_pivots(bars[:-2])
    with_full = confirmed_pivots(bars)
    assert with_partial[0] != with_full[0]
    last_index = len(bars) - 1
    for idx, _price in with_full[0] + with_full[1]:
        assert idx <= last_index - 2
    assert classify_structure(bars) == "HH_HL"


def test_structure_does_not_use_unfinished_last_bar() -> None:
    day0 = datetime(2026, 1, 5, tzinfo=IST)
    bars = _trend(30, day0)
    forming = Bar(
        start=bars[-1].start + timedelta(hours=4),
        open=9_999,
        high=20_000,
        low=1,
        close=20_000,
        complete=False,
    )
    assert classify_structure(bars) == classify_structure([*bars, forming])


def test_evaluate_bias_ignores_unfinished_4h() -> None:
    settings = _settings()
    day0 = datetime(2026, 1, 2, tzinfo=IST)
    daily = _daily_trend(80, day0)
    h4 = _trend(80, day0)
    h1 = _trend(80, day0, step=2.0)
    baseline = evaluate_bias(daily=daily, h4=h4, h1=h1, settings=settings, predictions=())
    spike = Bar(
        start=h4[-1].start + timedelta(hours=4),
        open=h4[-1].close,
        high=h4[-1].close + 400,
        low=h4[-1].close - 400,
        close=h4[-1].close + 350,
        complete=False,
    )
    with_open = evaluate_bias(
        daily=daily, h4=[*h4, spike], h1=h1, settings=settings, predictions=()
    )
    assert with_open.score == baseline.score
    assert with_open.bias == baseline.bias
    assert with_open.atr == baseline.atr
    assert with_open.volatility == baseline.volatility


def test_extreme_volatility_blocks_even_if_bullish() -> None:
    settings = _settings()
    day0 = datetime(2026, 1, 2, tzinfo=IST)
    daily = _daily_trend(80, day0)
    h4 = _trend(80, day0)
    last = h4[-1]
    h4[-1] = Bar(
        start=last.start,
        open=last.close,
        high=last.close + 250,
        low=last.close - 250,
        close=last.close + 20,
        volume=last.volume,
        oi=last.oi,
        complete=True,
    )
    h1 = _trend(80, day0, step=2.0)
    snap = evaluate_bias(daily=daily, h4=h4, h1=h1, settings=settings, predictions=())
    assert snap.bias == "BULLISH"
    assert snap.score >= settings.bias_bullish_threshold
    assert snap.volatility == "EXTREME"
    assert snap.allow_entry is False
    assert "EXTREME_VOLATILITY" in snap.no_trade_reasons


def test_neutral_score_is_no_trade() -> None:
    settings = _settings()
    day0 = datetime(2026, 3, 2, tzinfo=IST)
    daily = [_h(day0 + timedelta(days=i), 5000.0 + (i % 3) - 1) for i in range(40)]
    h4 = [_h(_starts(40, day0)[i], 5000.0 + (i % 3) - 1) for i in range(40)]
    snap = evaluate_bias(daily=daily, h4=h4, h1=h4, settings=settings, predictions=())
    assert snap.bias == "NEUTRAL"
    assert settings.bias_bearish_threshold < snap.score < settings.bias_bullish_threshold
    assert snap.allow_entry is False
    assert "BIAS_NEUTRAL" in snap.no_trade_reasons


def test_health_waits_for_horizon_completed_bars() -> None:
    day0 = datetime(2026, 1, 5, 9, 0, tzinfo=IST)
    h4 = _trend(10, day0)
    pred = DirectionPrediction(bar_start=h4[0].start, direction=1, close=h4[0].close, atr=10.0)
    early = evaluate_health([pred], h4[:5], horizon=5, min_samples=20, deterioration=0.2)
    assert early.status == "WARMING_UP"
    assert early.sample_count == 0
    ready = evaluate_health([pred], h4, horizon=5, min_samples=20, deterioration=0.2)
    assert ready.sample_count == 1


def test_health_degraded_vetoes_entry() -> None:
    settings = _settings(model_health_min_samples=20, model_health_deterioration=0.20)
    day0 = datetime(2026, 1, 2, tzinfo=IST)
    daily = _daily_trend(80, day0)
    h4 = _trend(80, day0)
    h1 = _trend(80, day0, step=2.0)
    preds: list[DirectionPrediction] = []
    for i, bar in enumerate(h4[:-6]):
        preds.append(
            DirectionPrediction(
                bar_start=bar.start,
                direction=1,
                close=bar.close,
                atr=5.0,
            )
        )
        if i >= 25:
            preds[-1] = DirectionPrediction(
                bar_start=bar.start,
                direction=-1,
                close=bar.close,
                atr=5.0,
            )
    snap = evaluate_bias(daily=daily, h4=h4, h1=h1, settings=settings, predictions=preds)
    assert snap.model_health == "DEGRADED"
    assert snap.allow_entry is False
    assert "MODEL_HEALTH_DEGRADED" in snap.no_trade_reasons


def test_predictions_are_not_rewritten(tmp_path: Path) -> None:
    ts = datetime(2026, 8, 25, 9, 0, tzinfo=IST)
    original = DirectionPrediction(bar_start=ts, direction=1, close=5000.0, atr=10.0)
    later = DirectionPrediction(bar_start=ts, direction=-1, close=4990.0, atr=12.0)
    merged, added = merge_predictions([original], [later])
    assert added == []
    assert merged[0].direction == 1
    assert merged[0].close == 5000.0
    path = tmp_path / "predictions.parquet"
    persist_predictions(merged, path)
    other = DirectionPrediction(bar_start=ts, direction=-1, close=1.0, atr=1.0)
    persist_predictions(merge_predictions(load_predictions(path), [other])[0], path)
    loaded = load_predictions(path)
    assert loaded[0].direction == 1
    assert loaded[0].close == 5000.0


def test_collect_predictions_skips_existing_keys() -> None:
    settings = _settings()
    day0 = datetime(2026, 1, 2, tzinfo=IST)
    daily = _daily_trend(80, day0)
    h4 = _trend(80, day0)
    h1 = _trend(80, day0, step=2.0)
    first = collect_predictions(daily=daily, h4=h4, h1=h1, settings=settings)
    assert first
    flipped = [
        DirectionPrediction(bar_start=item.bar_start, direction=-item.direction, close=0.0, atr=1.0)
        for item in first
    ]
    again = collect_predictions(
        daily=daily, h4=h4, h1=h1, settings=settings, existing=flipped
    )
    by_start = {item.bar_start: item.direction for item in again}
    assert by_start[first[0].bar_start] == flipped[0].direction


def test_format_bias_mentions_not_a_recommendation() -> None:
    settings = _settings()
    day0 = datetime(2026, 1, 2, tzinfo=IST)
    snap = evaluate_bias(
        daily=_daily_trend(80, day0),
        h4=_trend(80, day0),
        h1=_trend(80, day0, step=2.0),
        settings=settings,
        predictions=(),
    )
    text = format_bias_snapshot(
        snap, future_symbol="CRUDEOILM26OCTFUT", option_expiry=datetime(2026, 10, 15).date()
    )
    assert "not a trading recommendation" in text.lower()
    assert "CRUDEOILM26OCTFUT" in text
    assert snap.bias in text
    pred = prediction_from_snapshot(_trend(80, day0), snap)
    if snap.bias != "NEUTRAL":
        assert pred is not None
        assert pred.direction in {-1, 1}
