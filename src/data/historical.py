"""Historical market data fetcher for NIFTY, SENSEX, and Crude Oil."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
from loguru import logger

from src.config import get_data_dir, get_yaml_config
from src.data.database import HistoricalCandle, get_session, init_db

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class HistoricalDataFetcher:
    """Fetches and caches historical data from multiple sources."""

    INDEX_MAP = {
        "nifty50": {"yfinance": "^NSEI", "nse_symbol": "NIFTY 50"},
        "sensex": {"yfinance": "^BSESN", "nse_symbol": "SENSEX"},
        "crude_oil": {"yfinance": "CL=F", "nse_symbol": "CRUDEOIL"},
    }

    def __init__(self):
        self.config = get_yaml_config()
        self.cache_dir = get_data_dir()
        self.session = requests.Session()
        self.session.headers.update(NSE_HEADERS)
        init_db()

    def _nse_session_init(self):
        """Initialize NSE session cookies."""
        try:
            self.session.get("https://www.nseindia.com", timeout=10)
            time.sleep(0.5)
        except requests.RequestException as e:
            logger.warning(f"NSE session init failed: {e}")

    def fetch_index_history(
        self, instrument_key: str, days: Optional[int] = None
    ) -> pd.DataFrame:
        """Fetch daily OHLCV history for an instrument."""
        days = days or self.config["data"]["history_days"]
        cache_file = self.cache_dir / f"{instrument_key}_daily_{days}d.parquet"

        if cache_file.exists():
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            if datetime.now() - mtime < timedelta(hours=6):
                return pd.read_parquet(cache_file)

        mapping = self.INDEX_MAP.get(instrument_key)
        if not mapping:
            raise ValueError(f"Unknown instrument: {instrument_key}")

        df = self._fetch_yfinance(mapping["yfinance"], days)
        if df.empty:
            df = self._fetch_nse_index(mapping["nse_symbol"], days)

        if not df.empty:
            df.to_parquet(cache_file)
            self._persist_candles(instrument_key, df)

        return df

    def _fetch_yfinance(self, symbol: str, days: int) -> pd.DataFrame:
        try:
            end = datetime.now()
            start = end - timedelta(days=days)
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=start, end=end, interval="1d")
            if hist.empty:
                return pd.DataFrame()

            df = hist.reset_index()
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]
            if "date" in df.columns:
                df = df.rename(columns={"date": "timestamp"})
            elif "datetime" in df.columns:
                df = df.rename(columns={"datetime": "timestamp"})

            return df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        except Exception as e:
            logger.warning(f"yfinance fetch failed for {symbol}: {e}")
            return pd.DataFrame()

    def _fetch_nse_index(self, index_name: str, days: int) -> pd.DataFrame:
        self._nse_session_init()
        end = datetime.now()
        start = end - timedelta(days=days)

        url = "https://www.nseindia.com/api/historicalOR/foCPV"
        params = {
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
            "instrumentType": "FUTIDX",
            "symbol": "NIFTY" if "NIFTY" in index_name else index_name,
            "year": str(end.year),
        }

        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return pd.DataFrame()
            data = resp.json()
            rows = data.get("data", [])
            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows)
            rename_map = {
                "FH_TIMESTAMP": "timestamp",
                "FH_OPENING_PRICE": "open",
                "FH_TRADE_HIGH_PRICE": "high",
                "FH_TRADE_LOW_PRICE": "low",
                "FH_CLOSING_PRICE": "close",
                "FH_TOT_TRADED_QTY": "volume",
                "FH_OPEN_INT": "oi",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            return df
        except Exception as e:
            logger.warning(f"NSE fetch failed for {index_name}: {e}")
            return pd.DataFrame()

    def fetch_option_chain_snapshot(self, underlying: str = "NIFTY") -> dict:
        """Fetch live option chain from NSE."""
        self._nse_session_init()
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={underlying}"

        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"Option chain fetch failed: {e}")
        return {}

    def fetch_fii_dii_data(self) -> dict:
        """Fetch FII/DII activity data."""
        self._nse_session_init()
        url = "https://www.nseindia.com/api/fiidiiTradeReact"

        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                return {"data": resp.json()}
        except Exception as e:
            logger.warning(f"FII/DII fetch failed: {e}")
        return {"data": []}

    def fetch_india_vix(self) -> float:
        """Get current India VIX value."""
        try:
            ticker = yf.Ticker("^INDIAVIX")
            hist = ticker.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    def _persist_candles(self, symbol: str, df: pd.DataFrame):
        db = get_session()
        try:
            for _, row in df.iterrows():
                candle = HistoricalCandle(
                    symbol=symbol,
                    exchange="NSE",
                    timestamp=row["timestamp"],
                    open=float(row.get("open", 0)),
                    high=float(row.get("high", 0)),
                    low=float(row.get("low", 0)),
                    close=float(row.get("close", 0)),
                    volume=float(row.get("volume", 0)),
                    oi=float(row.get("oi", 0)),
                )
                db.merge(candle)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to persist candles: {e}")
        finally:
            db.close()

    def download_all_instruments(self) -> dict[str, pd.DataFrame]:
        """Download history for all configured instruments."""
        results = {}
        for key in self.config["instruments"]:
            if self.config["instruments"][key].get("enabled", True):
                logger.info(f"Downloading history for {key}...")
                results[key] = self.fetch_index_history(key)
                time.sleep(1)
        return results

    def get_option_historical(
        self,
        symbol: str,
        option_type: str,
        strike: float,
        expiry: str,
        days: int = 30,
    ) -> pd.DataFrame:
        """Fetch historical option price data from NSE archives."""
        self._nse_session_init()
        end = datetime.now()
        start = end - timedelta(days=days)

        url = "https://www.nseindia.com/api/historicalOR/foCPV"
        params = {
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
            "instrumentType": "OPTIDX",
            "symbol": symbol,
            "year": str(end.year),
            "expiryDate": expiry,
            "optionType": option_type,
            "strikePrice": str(int(strike)),
        }

        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    return pd.DataFrame(data)
        except Exception as e:
            logger.warning(f"Option history fetch failed: {e}")
        return pd.DataFrame()
