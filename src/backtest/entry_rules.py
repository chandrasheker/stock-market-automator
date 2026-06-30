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
        vix: float = 0.0,
        news_sentiment: Optional[dict] = None,
    ) -> Optional[dict]:
        """Return trade setup dict or None. Includes BUY and SELL modes."""
        cfg = self.get_instrument_config(instrument)
        news_sentiment = news_sentiment or {}

        # Try premium selling first — backtest-proven profitable edge
        sell_setup = self._evaluate_sell(instrument, trend, breakout, latest, window, cfg, vix, news_sentiment)
        if sell_setup:
            return sell_setup

        # Profit mode: skip buying unless trend is exceptionally strong
        profit_cfg = self.config.get("profit_mode", {})
        if profit_cfg.get("enabled") and profit_cfg.get("sell_only_default"):
            buy_adx = profit_cfg.get("buy_min_adx", 28)
            if float(latest["adx"]) < buy_adx:
                return None

        strategy = cfg.get("strategy", "trend_continuation")
        if strategy == "pullback_entry":
            direction = self._pullback_entry(trend, breakout, latest, window, cfg)
        else:
            direction = self._trend_continuation(trend, breakout, latest, window, cfg)

        if not direction:
            return None

        if profit_cfg.get("enabled"):
            min_confirms = profit_cfg.get("buy_min_confirms", 4)
            if not self._has_confirms(trend, breakout, latest, direction, min_confirms, cfg):
                return None

        if not self._news_allows(direction, news_sentiment, cfg):
            return None

        return {
            "mode": "BUY_OPTION",
            "direction": direction,
            "opt_type": "CE" if direction == "BULLISH" else "PE",
        }

    def evaluate_direction_only(
        self,
        instrument: str,
        trend: dict,
        breakout: dict,
        latest: pd.Series,
        window: pd.DataFrame,
    ) -> Optional[str]:
        """Backward-compatible direction-only evaluation for simple backtest."""
        result = self.evaluate(instrument, trend, breakout, latest, window)
        if result and result["mode"] == "BUY_OPTION":
            return result["direction"]
        return None

    def _evaluate_sell(
        self,
        instrument: str,
        trend: dict,
        breakout: dict,
        latest: pd.Series,
        window: pd.DataFrame,
        cfg: dict,
        vix: float,
        news_sentiment: dict,
    ) -> Optional[dict]:
        sell_cfg = cfg.get("option_selling", self.config.get("option_selling", {}))
        if not sell_cfg.get("enabled", True):
            return None

        adx = float(latest["adx"])
        atr_pct = latest["atr"] / latest["close"] if latest["close"] > 0 else 0
        rsi = float(latest["rsi"])

        # India VIX only applies to index options; commodities use their own ATR vol
        is_index = instrument in ("nifty50", "sensex")

        # Sell premium when trend is weak and volatility elevated
        max_adx = sell_cfg.get("max_adx", 22)
        min_vix = sell_cfg.get("min_vix", 14)
        if is_index and vix > 0:
            vix_ok = vix >= min_vix
        else:
            vix_ok = atr_pct >= sell_cfg.get("min_atr_pct", 0.012)

        if adx > max_adx or not vix_ok:
            return None

        # IV percentile gate — index only (uses India VIX history)
        if is_index and sell_cfg.get("min_iv_percentile"):
            from src.filters.iv_analysis import IVAnalyzer
            iv_ok, _ = IVAnalyzer().allows_selling(vix)
            if not iv_ok:
                return None

        # Event / expiry gate
        if sell_cfg.get("block_near_events", True):
            from src.filters.events import EventFilter
            headlines = news_sentiment.get("headlines", [])
            ev_ok, _ = EventFilter().allows_selling(instrument, headlines)
            if not ev_ok:
                return None

        # Avoid selling into strong breakouts
        if breakout.get("breakout") and breakout.get("strength", 0) > 0.004:
            return None

        near_upper = latest["close"] >= latest["bb_mid"] and latest["close"] >= latest["bb_upper"] * 0.98
        near_lower = latest["close"] <= latest["bb_mid"] and latest["close"] <= latest["bb_lower"] * 1.02

        news_score = news_sentiment.get("score", 0)

        # Sell OTM call in neutral-to-bearish range
        if near_upper or (45 <= rsi <= 60 and trend["direction"] != "BULLISH"):
            if news_score < sell_cfg.get("max_bullish_news", 0.25):
                return {"mode": "SELL_OPTION", "direction": "BEARISH", "opt_type": "CE"}

        # Sell OTM put in neutral-to-bullish range
        if near_lower or (40 <= rsi <= 55 and trend["direction"] != "BEARISH"):
            if news_score > sell_cfg.get("min_bearish_news", -0.25):
                return {"mode": "SELL_OPTION", "direction": "BULLISH", "opt_type": "PE"}

        return None

    @staticmethod
    def _news_allows(direction: str, news_sentiment: dict, cfg: dict) -> bool:
        if not cfg.get("use_news_filter", True):
            return True
        score = news_sentiment.get("score", 0)
        threshold = cfg.get("news_block_threshold", 0.35)
        if direction == "BULLISH" and score < -threshold:
            return False
        if direction == "BEARISH" and score > threshold:
            return False
        return True

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

    def _has_confirms(
        self, trend: dict, breakout: dict, latest: pd.Series, direction: str, min_confirms: int, cfg: dict
    ) -> bool:
        confirms = 0
        if direction == "BULLISH":
            if latest["ema_9"] > latest["ema_21"] > latest["sma_50"]:
                confirms += 2
            if breakout.get("breakout") and breakout["direction"] == "UP":
                confirms += 1
        else:
            if latest["ema_9"] < latest["ema_21"] < latest["sma_50"]:
                confirms += 2
            if breakout.get("breakout") and breakout["direction"] == "DOWN":
                confirms += 1
        return confirms >= min_confirms

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
