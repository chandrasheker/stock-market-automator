"""Options chain analysis: OI, PCR, IV, and strike selection."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.config import get_yaml_config


class OptionsAnalyzer:
    """Analyzes option chains for trading opportunities."""

    def __init__(self, risk_free_rate: float = 0.065):
        self.r = risk_free_rate

    def parse_option_chain(self, chain_data: dict) -> pd.DataFrame:
        """Parse NSE option chain JSON into structured DataFrame."""
        records = []
        records_data = chain_data.get("records", {})
        underlying = records_data.get("underlyingValue", 0)
        expiry_dates = records_data.get("expiryDates", [])
        nearest_expiry = expiry_dates[0] if expiry_dates else None

        for item in chain_data.get("records", {}).get("data", []):
            strike = item.get("strikePrice", 0)
            for opt_type in ["CE", "PE"]:
                opt_data = item.get(opt_type, {})
                if opt_data:
                    records.append({
                        "strike": strike,
                        "type": opt_type,
                        "ltp": opt_data.get("lastPrice", 0),
                        "oi": opt_data.get("openInterest", 0),
                        "oi_change": opt_data.get("changeinOpenInterest", 0),
                        "volume": opt_data.get("totalTradedVolume", 0),
                        "iv": opt_data.get("impliedVolatility", 0),
                        "bid": opt_data.get("bidprice", 0),
                        "ask": opt_data.get("askPrice", 0),
                        "underlying": underlying,
                        "expiry": nearest_expiry,
                    })

        return pd.DataFrame(records)

    def calculate_pcr(self, df: pd.DataFrame) -> float:
        call_oi = df[df["type"] == "CE"]["oi"].sum()
        put_oi = df[df["type"] == "PE"]["oi"].sum()
        return put_oi / call_oi if call_oi > 0 else 0

    def find_max_pain(self, df: pd.DataFrame) -> float:
        strikes = sorted(df["strike"].unique())
        min_pain = float("inf")
        max_pain_strike = strikes[len(strikes) // 2]

        for strike in strikes:
            total_pain = 0
            for _, row in df.iterrows():
                if row["type"] == "CE" and strike > row["strike"]:
                    total_pain += (strike - row["strike"]) * row["oi"]
                elif row["type"] == "PE" and strike < row["strike"]:
                    total_pain += (row["strike"] - strike) * row["oi"]
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = strike

        return max_pain_strike

    def get_support_resistance(self, df: pd.DataFrame, top_n: int = 3) -> dict:
        calls = df[df["type"] == "CE"].nlargest(top_n, "oi")
        puts = df[df["type"] == "PE"].nlargest(top_n, "oi")

        return {
            "resistance_strikes": calls["strike"].tolist(),
            "support_strikes": puts["strike"].tolist(),
            "max_call_oi_strike": float(calls.iloc[0]["strike"]) if len(calls) > 0 else 0,
            "max_put_oi_strike": float(puts.iloc[0]["strike"]) if len(puts) > 0 else 0,
        }

    def select_strike(
        self,
        df: pd.DataFrame,
        direction: str,
        underlying_price: float,
        otm_distance: int = 1,
        trade_mode: str = "BUY_OPTION",
        days_to_expiry: float = 7 / 365,
    ) -> Optional[dict]:
        """Select strike — prefers delta-based selection when IV available."""
        if df.empty:
            return None

        cfg = get_yaml_config().get("strike_selection", {})
        if cfg.get("use_delta", True):
            delta_strike = self.select_strike_by_delta(
                df, direction, underlying_price, trade_mode, days_to_expiry
            )
            if delta_strike:
                return delta_strike

        if not cfg.get("fallback_otm_distance", True):
            return None

        return self._select_strike_otm(df, direction, underlying_price, otm_distance)

    def select_strike_by_delta(
        self,
        df: pd.DataFrame,
        direction: str,
        underlying_price: float,
        trade_mode: str = "BUY_OPTION",
        days_to_expiry: float = 7 / 365,
    ) -> Optional[dict]:
        """Pick strike closest to target delta band."""
        cfg = get_yaml_config().get("strike_selection", {})
        opt_type = "CE" if direction == "BULLISH" else "PE"

        if trade_mode == "SELL_OPTION":
            d_min = cfg.get("sell_delta_min", 0.15)
            d_max = cfg.get("sell_delta_max", 0.25)
        else:
            d_min = cfg.get("buy_delta_min", 0.30)
            d_max = cfg.get("buy_delta_max", 0.50)

        target_delta = (d_min + d_max) / 2
        options = df[df["type"] == opt_type].copy()
        if options.empty:
            return None

        candidates = []
        for _, row in options.iterrows():
            sigma = float(row.get("iv", 0) or 0) / 100
            if sigma <= 0:
                sigma = 0.18
            delta = abs(
                self.calculate_delta(
                    underlying_price, float(row["strike"]), days_to_expiry, sigma, opt_type
                )
            )
            if d_min <= delta <= d_max or abs(delta - target_delta) < 0.15:
                candidates.append((abs(delta - target_delta), row, delta))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        _, row, delta = candidates[0]
        premium = float(row["ltp"])
        if premium <= 0:
            return None

        return {
            "strike": float(row["strike"]),
            "type": opt_type,
            "premium": premium,
            "oi": int(row["oi"]),
            "iv": float(row.get("iv", 0)),
            "volume": int(row.get("volume", 0)),
            "expiry": row.get("expiry"),
            "bid": float(row.get("bid", 0)),
            "ask": float(row.get("ask", 0)),
            "delta": round(delta, 3),
        }

    def _select_strike_otm(
        self,
        df: pd.DataFrame,
        direction: str,
        underlying_price: float,
        otm_distance: int,
    ) -> Optional[dict]:
        """Legacy fixed OTM distance strike selection."""
        if df.empty:
            return None

        strike_interval = self._guess_strike_interval(df)
        opt_type = "CE" if direction == "BULLISH" else "PE"

        if direction == "BULLISH":
            atm = round(underlying_price / strike_interval) * strike_interval
            target_strike = atm + (strike_interval * otm_distance)
        else:
            atm = round(underlying_price / strike_interval) * strike_interval
            target_strike = atm - (strike_interval * otm_distance)

        options = df[(df["type"] == opt_type) & (df["strike"] == target_strike)]
        if options.empty:
            options = df[df["type"] == opt_type].copy()
            options["distance"] = abs(options["strike"] - target_strike)
            options = options.nsmallest(1, "distance")

        if options.empty:
            return None

        row = options.iloc[0]
        premium = row["ltp"]
        if premium <= 0:
            return None

        return {
            "strike": float(row["strike"]),
            "type": opt_type,
            "premium": float(premium),
            "oi": int(row["oi"]),
            "iv": float(row.get("iv", 0)),
            "volume": int(row.get("volume", 0)),
            "expiry": row.get("expiry"),
            "bid": float(row.get("bid", 0)),
            "ask": float(row.get("ask", 0)),
        }

    def calculate_delta(
        self, S: float, K: float, T: float, sigma: float, option_type: str = "CE"
    ) -> float:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0
        d1 = (np.log(S / K) + (self.r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        if option_type == "CE":
            return float(norm.cdf(d1))
        return float(norm.cdf(d1) - 1)

    def analyze_chain(self, chain_data: dict) -> dict:
        df = self.parse_option_chain(chain_data)
        if df.empty:
            return {"valid": False}

        pcr = self.calculate_pcr(df)
        max_pain = self.find_max_pain(df)
        sr = self.get_support_resistance(df)
        underlying = df["underlying"].iloc[0] if "underlying" in df.columns else 0

        oi_signal = "BULLISH" if pcr > 1.2 else "BEARISH" if pcr < 0.8 else "NEUTRAL"

        call_oi_change = df[df["type"] == "CE"]["oi_change"].sum()
        put_oi_change = df[df["type"] == "PE"]["oi_change"].sum()

        return {
            "valid": True,
            "underlying": underlying,
            "pcr": round(pcr, 3),
            "max_pain": max_pain,
            "oi_signal": oi_signal,
            "support_strikes": sr["support_strikes"],
            "resistance_strikes": sr["resistance_strikes"],
            "call_oi_change": int(call_oi_change),
            "put_oi_change": int(put_oi_change),
            "distance_to_max_pain": underlying - max_pain,
        }

    def black_scholes_price(
        self, S: float, K: float, T: float, sigma: float, option_type: str = "CE"
    ) -> float:
        if T <= 0 or sigma <= 0:
            return max(0, S - K) if option_type == "CE" else max(0, K - S)

        d1 = (np.log(S / K) + (self.r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        if option_type == "CE":
            return S * norm.cdf(d1) - K * np.exp(-self.r * T) * norm.cdf(d2)
        return K * np.exp(-self.r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    def calculate_iv_rank(self, current_iv: float, iv_history: list[float]) -> float:
        if not iv_history:
            return 50.0
        iv_min = min(iv_history)
        iv_max = max(iv_history)
        if iv_max == iv_min:
            return 50.0
        return ((current_iv - iv_min) / (iv_max - iv_min)) * 100

    @staticmethod
    def _guess_strike_interval(df: pd.DataFrame) -> int:
        strikes = sorted(df["strike"].unique())
        if len(strikes) < 2:
            return 50
        diffs = [strikes[i + 1] - strikes[i] for i in range(min(5, len(strikes) - 1))]
        return int(min(d for d in diffs if d > 0))
