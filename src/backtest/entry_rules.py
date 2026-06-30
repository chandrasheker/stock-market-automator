"""Shared entry rules used by backtest and live signal engine."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import get_yaml_config


class EntryRuleEngine:
    """Validates trade entries using backtest-optimized filters."""

    def __init__(self):
        self.config = get_yaml_config()

    def get_instrument_config(self, instrument: str) -> dict:
        bt_cfg = self.config.get("backtest", {})
        per_inst = bt_cfg.get("instruments", {}).get(instrument, {})
        defaults = bt_cfg.get("defaults", {})
        return {**defaults, **per_inst}

    def evaluate(
        self,
        instrument: str,
        trend: dict,
        breakout: dict,
        latest: pd.Series,
        window: pd.DataFrame,
    ) -> Optional[str]:
        """Return BULLISH/BEARISH if entry criteria met, else None."""
        cfg = self.get_instrument_config(instrument)
        strategy = cfg.get("strategy", "trend_continuation")

        if strategy == "pullback_entry":
            return self._pullback_entry(trend, breakout, latest, window, cfg)
        return self._trend_continuation(trend, breakout, latest, window, cfg)

    def _trend_continuation(
        self, trend: dict, breakout: dict, latest: pd.Series, window: pd.DataFrame, cfg: dict
    ) -> Optional[str]:
        if trend["direction"] not in ("BULLISH", "BEARISH"):
            return None
        if trend["strength"] < cfg.get("min_strength", 0.4):
            return None
        if latest["adx"] < cfg.get("min_adx", 22):
            return None

        confirms = 0
        if trend["direction"] == "BULLISH":
            if latest["ema_9"] > latest["ema_21"] > latest["sma_50"]:
                confirms += 2
            elif latest["ema_9"] > latest["ema_21"]:
                confirms += 1
            rsi = trend.get("rsi", 50)
            if cfg.get("rsi_bull_min", 50) < rsi < cfg.get("rsi_bull_max", 65):
                confirms += 1
            if breakout.get("breakout") and breakout["direction"] == "UP":
                confirms += 1
        else:
            if latest["ema_9"] < latest["ema_21"] < latest["sma_50"]:
                confirms += 2
            elif latest["ema_9"] < latest["ema_21"]:
                confirms += 1
            rsi = trend.get("rsi", 50)
            if cfg.get("rsi_bear_min", 35) < rsi < cfg.get("rsi_bear_max", 50):
                confirms += 1
            if breakout.get("breakout") and breakout["direction"] == "DOWN":
                confirms += 1

        if confirms < cfg.get("min_confirms", 3):
            return None

        atr_pct = latest["atr"] / latest["close"] if latest["close"] > 0 else 0
        if atr_pct > cfg.get("max_atr_pct", 0.03):
            return None

        return trend["direction"]

    def _pullback_entry(
        self, trend: dict, breakout: dict, latest: pd.Series, window: pd.DataFrame, cfg: dict
    ) -> Optional[str]:
        if latest["adx"] < cfg.get("min_adx", 20):
            return None

        rsi = float(latest["rsi"])

        # Buy CE on pullback in established uptrend
        if (
            latest["ema_9"] > latest["ema_21"] > latest["sma_50"]
            and latest["close"] > latest["sma_50"]
        ):
            if cfg.get("rsi_pullback_bull_min", 40) <= rsi <= cfg.get("rsi_pullback_bull_max", 55):
                if latest["close"] <= latest["ema_9"] * 1.005:
                    if trend["macd_bullish"] or latest["stoch_k"] < 30:
                        return "BULLISH"

        # Buy PE on rally fade in established downtrend
        if (
            latest["ema_9"] < latest["ema_21"] < latest["sma_50"]
            and latest["close"] < latest["sma_50"]
        ):
            if cfg.get("rsi_pullback_bear_min", 45) <= rsi <= cfg.get("rsi_pullback_bear_max", 60):
                if latest["close"] >= latest["ema_9"] * 0.995:
                    if not trend["macd_bullish"] or latest["stoch_k"] > 70:
                        return "BEARISH"

        return None

    def compute_confidence(
        self,
        instrument: str,
        trend: dict,
        breakout: dict,
        latest: pd.Series,
        chain_analysis: dict,
        news_sentiment: dict,
        direction: str,
    ) -> float:
        """Score confidence 0-1 based on how many factors align."""
        cfg = self.get_instrument_config(instrument)
        score = 0.55

        if trend["direction"] == direction:
            score += trend["strength"] * 0.15

        if breakout.get("breakout"):
            aligned = (
                (breakout["direction"] == "UP" and direction == "BULLISH")
                or (breakout["direction"] == "DOWN" and direction == "BEARISH")
            )
            if aligned:
                score += 0.1

        if latest["adx"] >= cfg.get("min_adx", 20) + 5:
            score += 0.05

        if chain_analysis.get("valid"):
            oi_dir = chain_analysis.get("oi_signal", "NEUTRAL")
            if (oi_dir == "BULLISH" and direction == "BULLISH") or (
                oi_dir == "BEARISH" and direction == "BEARISH"
            ):
                score += 0.08

        news_dir = "BULLISH" if news_sentiment.get("score", 0) > 0.1 else (
            "BEARISH" if news_sentiment.get("score", 0) < -0.1 else "NEUTRAL"
        )
        if news_dir == direction:
            score += 0.05

        if trend.get("volume_spike"):
            score += 0.05

        return min(0.95, round(score, 3))
