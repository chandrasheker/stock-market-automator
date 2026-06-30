"""Live option chain fetcher with Kite WebSocket overlay."""

from __future__ import annotations

import threading
import time
from datetime import datetime, time as dt_time
from typing import Callable, Optional

import pandas as pd
from loguru import logger

from src.analysis.options_analyzer import OptionsAnalyzer
from src.config import get_yaml_config
from src.data.historical import HistoricalDataFetcher


class LiveOptionChainService:
    """Fetches and caches option chains; overlays Kite ticks when available."""

    CHAIN_SOURCES = {
        "nifty50": {"api_symbol": "NIFTY", "source": "nse"},
        "sensex": {"api_symbol": "SENSEX", "source": "nse"},
        "crude_oil": {"api_symbol": "CRUDEOIL", "source": "kite"},
    }

    def __init__(self):
        self.config = get_yaml_config()
        self.fetcher = HistoricalDataFetcher()
        self.analyzer = OptionsAnalyzer()
        self._cache: dict[str, dict] = {}
        self._kite_feed = None
        self._token_map: dict[int, dict] = {}
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: list[Callable] = []

    def set_kite_feed(self, feed):
        self._kite_feed = feed

    def on_update(self, callback: Callable):
        self._callbacks.append(callback)

    def is_market_open(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        return dt_time(9, 15) <= now.time() <= dt_time(15, 30)

    def start_polling(self, interval_sec: float = 3.0):
        if self._running:
            return
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(interval_sec,), daemon=True
        )
        self._poll_thread.start()
        logger.info(f"Option chain polling started ({interval_sec}s)")

    def stop_polling(self):
        self._running = False

    def _poll_loop(self, interval: float):
        while self._running:
            for inst in self.CHAIN_SOURCES:
                if self.config["instruments"].get(inst, {}).get("enabled", True):
                    try:
                        self.fetch_chain(inst)
                    except Exception as e:
                        logger.debug(f"Chain poll failed {inst}: {e}")
            time.sleep(interval)

    def fetch_chain(self, instrument_key: str) -> dict:
        cfg = self.config["instruments"].get(instrument_key, {})
        underlying = cfg.get("underlying", "NIFTY")
        source = self.CHAIN_SOURCES.get(instrument_key, {}).get("source", "nse")

        raw = {}
        if source == "nse":
            api_sym = self.CHAIN_SOURCES[instrument_key]["api_symbol"]
            raw = self.fetcher.fetch_option_chain_snapshot(api_sym)
        elif source == "kite" and self._kite_feed:
            raw = self._fetch_via_kite(instrument_key, underlying, cfg.get("exchange", "MCX"))

        if not raw:
            return self._cache.get(instrument_key, {"valid": False, "instrument": instrument_key})

        df = self.analyzer.parse_option_chain(raw)
        analysis = self.analyzer.analyze_chain(raw)

        # Overlay live ticks from Kite WebSocket
        if self._kite_feed and self._kite_feed.latest_ticks:
            df = self._overlay_kite_ticks(df)

        chain_view = self._build_chain_table(df, analysis)
        result = {
            "valid": not df.empty,
            "instrument": instrument_key,
            "underlying": analysis.get("underlying", 0),
            "pcr": analysis.get("pcr", 0),
            "max_pain": analysis.get("max_pain", 0),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "expiry": raw.get("records", {}).get("expiryDates", [""])[0] if raw else "",
            "chain_df": df,
            "chain_view": chain_view,
            "analysis": analysis,
        }
        self._cache[instrument_key] = result

        for cb in self._callbacks:
            try:
                cb(instrument_key, result)
            except Exception:
                pass

        return result

    def get_cached(self, instrument_key: str) -> dict:
        return self._cache.get(instrument_key, {"valid": False})

    def _overlay_kite_ticks(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or not self._kite_feed:
            return df

        df = df.copy()
        for idx, row in df.iterrows():
            token = row.get("instrument_token")
            if token and token in self._kite_feed.latest_ticks:
                tick = self._kite_feed.latest_ticks[token]
                if tick.get("last_price"):
                    df.at[idx, "ltp"] = tick["last_price"]
                if tick.get("oi"):
                    df.at[idx, "oi"] = tick["oi"]
        return df

    def _fetch_via_kite(self, instrument_key: str, underlying: str, exchange: str) -> dict:
        """Build chain-like structure from Kite instruments + quotes."""
        if not self._kite_feed or not self._kite_feed.kite:
            return {}

        try:
            kite = self._kite_feed.kite
            instruments = kite.instruments(exchange)
            opts = [
                i for i in instruments
                if i.get("name") == underlying and i.get("instrument_type") in ("CE", "PE")
            ]
            if not opts:
                return {}

            # Nearest expiry only
            expiries = sorted(set(i["expiry"] for i in opts))
            nearest = expiries[0]
            opts = [i for i in opts if i["expiry"] == nearest]

            keys = [f"{exchange}:{i['tradingsymbol']}" for i in opts[:80]]
            quotes = kite.ltp(keys) if keys else {}

            records_data = []
            strikes = sorted(set(i["strike"] for i in opts))
            underlying_ltp = kite.ltp([f"{exchange}:{underlying}"]) if underlying else {}
            spot = 0
            for k, v in underlying_ltp.items():
                spot = v.get("last_price", 0)

            for strike in strikes:
                ce = next((i for i in opts if i["strike"] == strike and i["instrument_type"] == "CE"), None)
                pe = next((i for i in opts if i["strike"] == strike and i["instrument_type"] == "PE"), None)
                row = {"strikePrice": strike}
                for opt, label in [(ce, "CE"), (pe, "PE")]:
                    if opt:
                        key = f"{exchange}:{opt['tradingsymbol']}"
                        q = quotes.get(key, {})
                        row[label] = {
                            "lastPrice": q.get("last_price", 0),
                            "openInterest": 0,
                            "changeinOpenInterest": 0,
                            "totalTradedVolume": 0,
                            "impliedVolatility": 0,
                            "bidprice": 0,
                            "askPrice": 0,
                        }
                records_data.append(row)

            return {
                "records": {
                    "underlyingValue": spot,
                    "expiryDates": [nearest.strftime("%d-%b-%Y") if hasattr(nearest, "strftime") else str(nearest)],
                    "data": records_data,
                }
            }
        except Exception as e:
            logger.warning(f"Kite chain fetch failed for {instrument_key}: {e}")
            return {}

    def _build_chain_table(self, df: pd.DataFrame, analysis: dict) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()

        underlying = analysis.get("underlying", df["underlying"].iloc[0] if "underlying" in df.columns else 0)
        strikes = sorted(df["strike"].unique())

        rows = []
        for strike in strikes:
            ce = df[(df["strike"] == strike) & (df["type"] == "CE")]
            pe = df[(df["strike"] == strike) & (df["type"] == "PE")]
            ce_row = ce.iloc[0] if len(ce) > 0 else None
            pe_row = pe.iloc[0] if len(pe) > 0 else None

            rows.append({
                "Strike": strike,
                "CE LTP": ce_row["ltp"] if ce_row is not None else 0,
                "CE OI": int(ce_row["oi"]) if ce_row is not None else 0,
                "CE IV": ce_row["iv"] if ce_row is not None else 0,
                "CE Vol": int(ce_row["volume"]) if ce_row is not None else 0,
                "PE LTP": pe_row["ltp"] if pe_row is not None else 0,
                "PE OI": int(pe_row["oi"]) if pe_row is not None else 0,
                "PE IV": pe_row["iv"] if pe_row is not None else 0,
                "PE Vol": int(pe_row["volume"]) if pe_row is not None else 0,
                "ATM": abs(strike - underlying) < (strikes[1] - strikes[0] if len(strikes) > 1 else 50) / 2,
            })

        view = pd.DataFrame(rows)
        if underlying:
            atm_idx = (view["Strike"] - underlying).abs().idxmin()
            view.loc[atm_idx, "ATM"] = True
        return view
