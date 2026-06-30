"""Technical analysis indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta


class TechnicalAnalyzer:
    """Computes technical indicators for signal generation."""

    @staticmethod
    def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if len(df) < 30:
            return df

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df.get("volume", pd.Series([0] * len(df)))

        df["sma_20"] = ta.trend.sma_indicator(close, window=20)
        df["sma_50"] = ta.trend.sma_indicator(close, window=50)
        df["ema_9"] = ta.trend.ema_indicator(close, window=9)
        df["ema_21"] = ta.trend.ema_indicator(close, window=21)
        df["rsi"] = ta.momentum.rsi(close, window=14)
        df["macd"] = ta.trend.macd_diff(close)
        df["macd_signal"] = ta.trend.macd_signal(close)
        df["bb_upper"] = ta.volatility.bollinger_hband(close)
        df["bb_lower"] = ta.volatility.bollinger_lband(close)
        df["bb_mid"] = ta.volatility.bollinger_mavg(close)
        df["atr"] = ta.volatility.average_true_range(high, low, close, window=14)
        df["adx"] = ta.trend.adx(high, low, close, window=14)
        df["stoch_k"] = ta.momentum.stoch(high, low, close)
        df["stoch_d"] = ta.momentum.stoch_signal(high, low, close)
        df["obv"] = ta.volume.on_balance_volume(close, volume)
        df["volume_sma"] = volume.rolling(20).mean()

        return df

    @staticmethod
    def get_trend_signal(df: pd.DataFrame) -> dict:
        if len(df) < 50:
            return {"direction": "NEUTRAL", "strength": 0.0}

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        signals = []

        if latest["ema_9"] > latest["ema_21"]:
            signals.append(1)
        else:
            signals.append(-1)

        if latest["sma_20"] > latest["sma_50"]:
            signals.append(1)
        else:
            signals.append(-1)

        if latest["close"] > latest["sma_20"]:
            signals.append(1)
        else:
            signals.append(-1)

        if latest["macd"] > latest["macd_signal"]:
            signals.append(1)
        else:
            signals.append(-1)

        if 40 < latest["rsi"] < 60:
            signals.append(0)
        elif latest["rsi"] > 60:
            signals.append(1)
        else:
            signals.append(-1)

        avg = sum(signals) / len(signals)
        direction = "BULLISH" if avg > 0.2 else "BEARISH" if avg < -0.2 else "NEUTRAL"

        return {
            "direction": direction,
            "strength": abs(avg),
            "rsi": float(latest["rsi"]),
            "macd_bullish": latest["macd"] > latest["macd_signal"],
            "above_sma20": latest["close"] > latest["sma_20"],
            "volume_spike": (
                latest.get("volume", 0) > latest.get("volume_sma", 0) * 1.5
                if latest.get("volume_sma", 0) > 0 else False
            ),
            "atr": float(latest.get("atr", 0)),
        }

    @staticmethod
    def detect_breakout(df: pd.DataFrame, lookback: int = 20) -> dict:
        if len(df) < lookback + 1:
            return {"breakout": False, "direction": None}

        recent = df.tail(lookback + 1)
        prev_high = recent["high"].iloc[:-1].max()
        prev_low = recent["low"].iloc[:-1].min()
        current = recent.iloc[-1]

        if current["close"] > prev_high:
            return {
                "breakout": True,
                "direction": "UP",
                "level": prev_high,
                "strength": (current["close"] - prev_high) / prev_high,
            }
        if current["close"] < prev_low:
            return {
                "breakout": True,
                "direction": "DOWN",
                "level": prev_low,
                "strength": (prev_low - current["close"]) / prev_low,
            }
        return {"breakout": False, "direction": None}
