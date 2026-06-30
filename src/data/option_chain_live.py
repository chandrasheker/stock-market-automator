"""Live option chain fetcher with Kite REST + WebSocket overlay."""

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
from src.utils.clock import ist_now

# NSE blocks most cloud VM IPs (403). Kite is the reliable source on Oracle Cloud.
SPOT_SYMBOLS = {
    "nifty50": "NSE:NIFTY 50",
    "sensex": "BSE:SENSEX",
}


class LiveOptionChainService:
    """Fetches and caches option chains; overlays Kite ticks when available."""

    CHAIN_SOURCES = {
        "nifty50": {"underlying": "NIFTY", "exchange": "NFO", "spot": "NSE:NIFTY 50"},
        "sensex": {"underlying": "SENSEX", "exchange": "BFO", "spot": "BSE:SENSEX"},
        "crude_oil": {"underlying": "CRUDEOIL", "exchange": "MCX", "spot": None},
    }

    def __init__(self):
        self.config = get_yaml_config()
        self.fetcher = HistoricalDataFetcher()
        self.analyzer = OptionsAnalyzer()
        self._cache: dict[str, dict] = {}
        self._kite_feed = None
        self._kite_client = None
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: list[Callable] = []
        self._instruments_cache: dict[str, tuple] = {}  # exchange -> (date, instruments)
        self._oi_baseline: dict = {}  # date -> {tradingsymbol: first-seen OI today}

    def _oi_change(self, symbol: str, current_oi: int) -> int:
        """Intraday OI change vs the first reading seen today (Kite has no prev-day OI)."""
        from src.utils.clock import ist_today

        day_map = self._oi_baseline.setdefault(ist_today(), {})
        if symbol not in day_map:
            day_map[symbol] = current_oi
        return int(current_oi - day_map[symbol])

    def _get_instruments(self, kite, exchange: str) -> list:
        """Cache the (large) instruments dump per exchange per day."""
        from datetime import date as _date

        cached = self._instruments_cache.get(exchange)
        if cached and cached[0] == _date.today():
            return cached[1]
        instruments = kite.instruments(exchange)
        self._instruments_cache[exchange] = (_date.today(), instruments)
        return instruments

    def set_kite_feed(self, feed):
        self._kite_feed = feed
        if feed and getattr(feed, "kite", None):
            self._kite_client = feed.kite

    def set_kite_client(self, kite):
        self._kite_client = kite

    def on_update(self, callback: Callable):
        self._callbacks.append(callback)

    def is_market_open(self, instrument_key: str = "nifty50") -> bool:
        now = ist_now()
        if now.weekday() >= 5:
            return False
        if instrument_key == "crude_oil":
            return dt_time(9, 0) <= now.time() <= dt_time(23, 30)
        trading = self.config.get("trading", {})
        open_parts = trading.get("market_open", "09:15").split(":")
        close_parts = trading.get("market_close", "15:30").split(":")
        market_open = dt_time(int(open_parts[0]), int(open_parts[1]))
        market_close = dt_time(int(close_parts[0]), int(close_parts[1]))
        return market_open <= now.time() <= market_close

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

    def _get_kite(self):
        if self._kite_client:
            return self._kite_client
        if self._kite_feed and getattr(self._kite_feed, "kite", None):
            return self._kite_feed.kite
        try:
            from src.auth.kite_auth import KiteAuth

            auth = KiteAuth()
            if auth.is_authenticated():
                self._kite_client = auth.get_client()
                return self._kite_client
        except Exception as e:
            logger.debug(f"Kite client unavailable: {e}")
        return None

    def fetch_chain(self, instrument_key: str, expiry: Optional[str] = None) -> dict:
        meta = self.CHAIN_SOURCES.get(instrument_key)
        if not meta:
            return self._error_result(instrument_key, "unknown_instrument", "Unknown instrument")

        if not self.is_market_open(instrument_key):
            cache_key = f"{instrument_key}:{expiry or 'nearest'}"
            cached = self._cache.get(cache_key)
            if cached and cached.get("valid"):
                cached = {**cached, "stale": True, "error": "market_closed"}
                return cached
            return self._error_result(
                instrument_key,
                "market_closed",
                "Market is closed. Chain loads during trading hours (NIFTY/SENSEX 9:15–15:30 IST).",
            )

        kite = self._get_kite()
        raw = None
        source = ""

        # Kite REST is reliable from cloud VMs; NSE often returns 403 on Oracle Cloud.
        if kite:
            raw = self._fetch_via_kite(instrument_key, meta, kite, expiry=expiry)
            source = "kite"

        if not raw and instrument_key == "nifty50":
            api_sym = meta["underlying"]
            raw = self.fetcher.fetch_option_chain_snapshot(api_sym)
            source = "nse"

        if not raw:
            if not kite:
                return self._error_result(
                    instrument_key,
                    "kite_login_required",
                    "Login to Zerodha Kite in Settings. NSE API is blocked from cloud servers; "
                    "Kite Connect is required for live option chain on your VM.",
                )
            return self._error_result(
                instrument_key,
                "no_chain_data",
                "Could not fetch option chain from Kite. Re-login if your token expired this morning.",
            )

        df = self.analyzer.parse_option_chain(raw)
        analysis = self.analyzer.analyze_chain(raw)

        if self._kite_feed and self._kite_feed.latest_ticks:
            df = self._overlay_kite_ticks(df)

        df = self._enrich_greeks(df)
        chain_view = self._build_chain_table(df, analysis)
        all_expiries = raw.get("_all_expiries") or raw.get("records", {}).get("expiryDates", [])
        result = {
            "valid": not df.empty,
            "instrument": instrument_key,
            "source": source,
            "underlying": analysis.get("underlying", 0),
            "pcr": analysis.get("pcr", 0),
            "max_pain": analysis.get("max_pain", 0),
            "timestamp": ist_now().strftime("%H:%M:%S"),
            "expiry": raw.get("records", {}).get("expiryDates", [""])[0] if raw else "",
            "expiries": all_expiries,
            "chain_df": df,
            "chain_view": chain_view,
            "analysis": analysis,
            "stale": False,
        }
        if df.empty:
            return self._error_result(
                instrument_key,
                "empty_chain",
                "Option chain returned no rows. Try again during market hours.",
            )

        self._cache[f"{instrument_key}:{expiry or 'nearest'}"] = result
        for cb in self._callbacks:
            try:
                cb(instrument_key, result)
            except Exception:
                pass
        return result

    def _error_result(self, instrument_key: str, error: str, detail: str) -> dict:
        return {
            "valid": False,
            "instrument": instrument_key,
            "error": error,
            "error_detail": detail,
            "chain_view": pd.DataFrame(),
        }

    def get_cached(self, instrument_key: str) -> dict:
        return self._cache.get(instrument_key, {"valid": False})

    def get_chain_for_scan(self, instrument_key: str) -> tuple:
        """Return (chain_df, analysis) for the signal engine.

        Uses Kite when available (works on cloud VMs); falls back to NSE only
        for NIFTY. Never raises — returns (empty df, {valid: False}) on failure.
        """
        import pandas as _pd

        meta = self.CHAIN_SOURCES.get(instrument_key)
        if not meta:
            return _pd.DataFrame(), {"valid": False}

        kite = self._get_kite()
        raw = None
        if kite:
            raw = self._fetch_via_kite(instrument_key, meta, kite)
        if not raw and instrument_key == "nifty50":
            raw = self.fetcher.fetch_option_chain_snapshot(meta["underlying"])

        if not raw:
            return _pd.DataFrame(), {"valid": False}

        df = self.analyzer.parse_option_chain(raw)
        analysis = self.analyzer.analyze_chain(raw)
        df = self._enrich_greeks(df)  # fill IV so delta-based strike selection is accurate
        return df, analysis

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

    def _fetch_via_kite(
        self, instrument_key: str, meta: dict, kite, expiry: Optional[str] = None
    ) -> dict:
        """Build NSE-compatible chain structure from Kite instruments + quotes."""
        underlying = meta["underlying"]
        exchange = meta["exchange"]
        strikes_side = self.config.get("option_chain", {}).get("strikes_each_side", 10)

        try:
            instruments = self._get_instruments(kite, exchange)
            opts = [
                i for i in instruments
                if i.get("name") == underlying and i.get("instrument_type") in ("CE", "PE")
            ]
            # Fallback: some segments (e.g. MCX) populate name differently —
            # match on tradingsymbol prefix as a backup.
            if not opts:
                opts = [
                    i for i in instruments
                    if i.get("instrument_type") in ("CE", "PE")
                    and str(i.get("tradingsymbol", "")).upper().startswith(underlying.upper())
                ]
            if not opts:
                logger.warning(
                    f"No options found for {underlying} on {exchange}. "
                    f"Check that the {exchange} segment is enabled on your Kite subscription."
                )
                return {}

            # Expiries that haven't passed (in IST)
            from src.utils.clock import ist_today
            today = ist_today()
            all_exp = sorted(
                e for e in set(i["expiry"] for i in opts)
                if not hasattr(e, "year") or e >= today
            ) or sorted(set(i["expiry"] for i in opts))

            def _fmt(e):
                return e.strftime("%d-%b-%Y") if hasattr(e, "strftime") else str(e)

            exp_strs = [_fmt(e) for e in all_exp]
            # Pick requested expiry if valid, else nearest
            if expiry and expiry in exp_strs:
                chosen = all_exp[exp_strs.index(expiry)]
            else:
                chosen = all_exp[0]
            opts = [i for i in opts if i["expiry"] == chosen]
            logger.debug(
                f"{instrument_key}: {len(opts)} options on {exchange}, expiry {chosen}"
            )

            spot = self._fetch_spot(kite, instrument_key, meta, exchange, instruments, underlying)
            if spot > 0:
                interval = self.config["instruments"][instrument_key].get("strike_interval", 50)
                opts = [
                    i for i in opts
                    if abs(i["strike"] - spot) <= strikes_side * interval * 1.5
                ]

            keys = [f"{exchange}:{i['tradingsymbol']}" for i in opts]
            quotes = self._batch_quote(kite, keys)

            records_data = []
            strikes = sorted(set(i["strike"] for i in opts))

            for strike in strikes:
                ce = next(
                    (i for i in opts if i["strike"] == strike and i["instrument_type"] == "CE"),
                    None,
                )
                pe = next(
                    (i for i in opts if i["strike"] == strike and i["instrument_type"] == "PE"),
                    None,
                )
                row = {"strikePrice": strike}
                for opt, label in [(ce, "CE"), (pe, "PE")]:
                    if opt:
                        key = f"{exchange}:{opt['tradingsymbol']}"
                        q = quotes.get(key, {})
                        cur_oi = q.get("oi", 0) or 0
                        row[label] = {
                            "lastPrice": q.get("last_price", 0) or 0,
                            "openInterest": cur_oi,
                            "changeinOpenInterest": self._oi_change(opt["tradingsymbol"], cur_oi),
                            "totalTradedVolume": q.get("volume", 0) or 0,
                            "impliedVolatility": 0,
                            "bidprice": q.get("depth", {}).get("buy", [{}])[0].get("price", 0) or 0,
                            "askPrice": q.get("depth", {}).get("sell", [{}])[0].get("price", 0) or 0,
                        }
                records_data.append(row)

            return {
                "records": {
                    "underlyingValue": spot,
                    "expiryDates": [_fmt(chosen)],
                    "data": records_data,
                },
                "_all_expiries": exp_strs,
            }
        except Exception as e:
            logger.warning(f"Kite chain fetch failed for {instrument_key}: {e}")
            return {}

    def _fetch_spot(
        self, kite, instrument_key: str, meta: dict, exchange: str, instruments: list, underlying: str
    ) -> float:
        spot_key = meta.get("spot") or SPOT_SYMBOLS.get(instrument_key)
        if spot_key:
            try:
                q = kite.ltp([spot_key])
                return float(q.get(spot_key, {}).get("last_price", 0) or 0)
            except Exception:
                pass

        if instrument_key == "crude_oil":
            futures = [
                i for i in instruments
                if i.get("name") == underlying and i.get("instrument_type") == "FUT"
            ]
            if futures:
                futures.sort(key=lambda x: x["expiry"])
                fut_key = f"{exchange}:{futures[0]['tradingsymbol']}"
                try:
                    q = kite.ltp([fut_key])
                    return float(q.get(fut_key, {}).get("last_price", 0) or 0)
                except Exception:
                    pass
        return 0.0

    @staticmethod
    def _batch_quote(kite, keys: list[str], chunk_size: int = 200) -> dict:
        quotes: dict = {}
        for i in range(0, len(keys), chunk_size):
            batch = keys[i : i + chunk_size]
            try:
                quotes.update(kite.quote(batch))
            except Exception as e:
                logger.debug(f"Quote batch failed ({len(batch)} symbols): {e}")
                try:
                    quotes.update(kite.ltp(batch))
                except Exception:
                    pass
        return quotes

    @staticmethod
    def _dte_years(raw_exp) -> float:
        from datetime import datetime as _dt
        from src.utils.clock import ist_today

        if not raw_exp:
            return 7 / 365
        if hasattr(raw_exp, "year"):
            return max(1, (raw_exp - ist_today()).days) / 365
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                exp = _dt.strptime(str(raw_exp), fmt).date()
                return max(1, (exp - ist_today()).days) / 365
            except (ValueError, TypeError):
                continue
        return 7 / 365

    def _enrich_greeks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fill IV (from price if missing) and compute Greeks per option."""
        if df.empty:
            return df
        df = df.copy()
        S = float(df["underlying"].iloc[0]) if "underlying" in df.columns else 0.0
        T = self._dte_years(df["expiry"].iloc[0] if "expiry" in df.columns else None)

        ivs, deltas, gammas, thetas, vegas = [], [], [], [], []
        for _, row in df.iterrows():
            otype = row["type"]
            K = float(row["strike"])
            ltp = float(row.get("ltp", 0) or 0)
            iv = float(row.get("iv", 0) or 0)
            if S <= 0 or T <= 0:
                ivs.append(iv); deltas.append(0); gammas.append(0); thetas.append(0); vegas.append(0)
                continue
            sigma = iv / 100 if iv > 0 else self.analyzer.implied_vol(ltp, S, K, T, otype)
            g = self.analyzer.calculate_greeks(S, K, T, sigma, otype)
            ivs.append(round(sigma * 100, 1))
            deltas.append(g["delta"]); gammas.append(g["gamma"])
            thetas.append(g["theta"]); vegas.append(g["vega"])

        df["iv"] = ivs
        df["delta"] = deltas
        df["gamma"] = gammas
        df["theta"] = thetas
        df["vega"] = vegas
        return df

    def _build_chain_table(self, df: pd.DataFrame, analysis: dict) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()

        underlying = analysis.get("underlying", df["underlying"].iloc[0] if "underlying" in df.columns else 0)
        strikes = sorted(df["strike"].unique())
        has_greeks = "delta" in df.columns

        def g(row, col):
            return row[col] if (row is not None and col in row) else 0

        rows = []
        for strike in strikes:
            ce = df[(df["strike"] == strike) & (df["type"] == "CE")]
            pe = df[(df["strike"] == strike) & (df["type"] == "PE")]
            ce_row = ce.iloc[0] if len(ce) > 0 else None
            pe_row = pe.iloc[0] if len(pe) > 0 else None

            entry = {
                "Strike": strike,
                "CE LTP": g(ce_row, "ltp"),
                "CE OI": int(g(ce_row, "oi")),
                "CE OIChg": int(g(ce_row, "oi_change")),
                "CE IV": g(ce_row, "iv"),
                "CE Vol": int(g(ce_row, "volume")),
                "PE LTP": g(pe_row, "ltp"),
                "PE OI": int(g(pe_row, "oi")),
                "PE OIChg": int(g(pe_row, "oi_change")),
                "PE IV": g(pe_row, "iv"),
                "PE Vol": int(g(pe_row, "volume")),
                "ATM": False,
            }
            if has_greeks:
                entry.update({
                    "CE Delta": g(ce_row, "delta"), "CE Gamma": g(ce_row, "gamma"),
                    "CE Theta": g(ce_row, "theta"), "CE Vega": g(ce_row, "vega"),
                    "PE Delta": g(pe_row, "delta"), "PE Gamma": g(pe_row, "gamma"),
                    "PE Theta": g(pe_row, "theta"), "PE Vega": g(pe_row, "vega"),
                })
            rows.append(entry)

        view = pd.DataFrame(rows)
        if underlying and not view.empty:
            atm_idx = (view["Strike"] - underlying).abs().idxmin()
            view.loc[atm_idx, "ATM"] = True
        return view
