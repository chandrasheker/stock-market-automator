"""IV percentile using India VIX history."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from loguru import logger

from src.config import get_data_dir, get_yaml_config


class IVAnalyzer:
    """Compute VIX percentile for sell-timing decisions."""

    def __init__(self):
        self.cfg = get_yaml_config().get("iv_analysis", {})
        self.cache_dir = get_data_dir()

    def get_vix_percentile(self, lookback_days: int | None = None) -> float:
        lookback = lookback_days or self.cfg.get("lookback_days", 252)
        cache_file = self.cache_dir / f"vix_history_{lookback}d.parquet"

        try:
            if cache_file.exists():
                mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
                if datetime.now() - mtime < timedelta(hours=6):
                    hist = pd.read_parquet(cache_file)
                else:
                    hist = self._fetch_vix_history(lookback)
                    if not hist.empty:
                        hist.to_parquet(cache_file)
            else:
                hist = self._fetch_vix_history(lookback)
                if not hist.empty:
                    hist.to_parquet(cache_file)

            if hist.empty or len(hist) < 20:
                return 50.0

            current = float(hist["close"].iloc[-1])
            series = hist["close"].dropna()
            percentile = (series <= current).sum() / len(series) * 100
            return round(float(percentile), 1)
        except Exception as e:
            logger.debug(f"VIX percentile failed: {e}")
            return 50.0

    @staticmethod
    def _fetch_vix_history(days: int) -> pd.DataFrame:
        try:
            end = datetime.now()
            start = end - timedelta(days=days + 30)
            ticker = yf.Ticker("^INDIAVIX")
            hist = ticker.history(start=start, end=end, interval="1d")
            if hist.empty:
                return pd.DataFrame()
            df = hist.reset_index()
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]
            if "close" not in df.columns and "Close" in hist.columns:
                df["close"] = hist["Close"].values
            return df[["close"]].dropna()
        except Exception as e:
            logger.warning(f"VIX history fetch failed: {e}")
            return pd.DataFrame()

    def allows_selling(self, vix: float = 0.0) -> tuple[bool, str]:
        sell_cfg = get_yaml_config().get("option_selling", {})
        min_pct = sell_cfg.get("min_iv_percentile", 50)
        percentile = self.get_vix_percentile()

        if percentile < min_pct:
            return False, f"IV percentile {percentile:.0f}% below minimum {min_pct}% for selling"

        min_vix = sell_cfg.get("min_vix", 14)
        if vix > 0 and vix < min_vix:
            return False, f"VIX {vix:.1f} below minimum {min_vix} for selling"

        return True, f"IV percentile {percentile:.0f}% OK for selling"
