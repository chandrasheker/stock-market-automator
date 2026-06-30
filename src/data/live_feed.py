"""Live market data feed via Kite WebSocket."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from src.config import ROOT_DIR, get_env


class LiveFeedManager:
    """Manages real-time tick data from Kite Connect WebSocket."""

    def __init__(self, kite_client=None):
        self.kite = kite_client
        self.env = get_env()
        self.ticker = None
        self.subscribed_tokens: list[int] = []
        self.tick_callbacks: list[Callable] = []
        self.latest_ticks: dict[int, dict] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def set_kite_client(self, kite_client):
        self.kite = kite_client

    def subscribe(self, instrument_tokens: list[int]):
        self.subscribed_tokens = instrument_tokens
        if self.ticker and self._running:
            self.ticker.subscribe(instrument_tokens)
            self.ticker.set_mode(self.ticker.MODE_FULL, instrument_tokens)

    def on_tick(self, callback: Callable):
        self.tick_callbacks.append(callback)

    def start(self):
        if not self.kite:
            logger.warning("Kite client not set. Live feed unavailable.")
            return False

        if self._running:
            return True

        try:
            from kiteconnect import KiteTicker

            self.ticker = KiteTicker(
                self.env.kite_api_key,
                self.kite.access_token if hasattr(self.kite, "access_token") else self.env.kite_access_token,
            )
            self.ticker.on_ticks = self._handle_ticks
            self.ticker.on_connect = self._on_connect
            self.ticker.on_close = self._on_close
            self.ticker.on_error = self._on_error

            self._running = True
            self._thread = threading.Thread(target=self.ticker.connect, daemon=True)
            self._thread.start()
            logger.info("Live feed started")
            return True
        except Exception as e:
            logger.error(f"Failed to start live feed: {e}")
            return False

    def stop(self):
        self._running = False
        if self.ticker:
            self.ticker.close()
        logger.info("Live feed stopped")

    def get_ltp(self, token: int) -> Optional[float]:
        tick = self.latest_ticks.get(token)
        return tick.get("last_price") if tick else None

    def _on_connect(self, ws, response):
        logger.info("WebSocket connected")
        if self.subscribed_tokens:
            ws.subscribe(self.subscribed_tokens)
            ws.set_mode(ws.MODE_FULL, self.subscribed_tokens)

    def _on_close(self, ws, code, reason):
        logger.warning(f"WebSocket closed: {code} - {reason}")
        self._running = False

    def _on_error(self, ws, code, reason):
        logger.error(f"WebSocket error: {code} - {reason}")

    def _handle_ticks(self, ws, ticks):
        for tick in ticks:
            token = tick["instrument_token"]
            self.latest_ticks[token] = {
                "last_price": tick.get("last_price"),
                "volume": tick.get("volume_traded", 0),
                "oi": tick.get("oi", 0),
                "bid": tick.get("depth", {}).get("buy", [{}])[0].get("price"),
                "ask": tick.get("depth", {}).get("sell", [{}])[0].get("price"),
                "timestamp": datetime.now().isoformat(),
            }

            for callback in self.tick_callbacks:
                try:
                    callback(token, self.latest_ticks[token])
                except Exception as e:
                    logger.error(f"Tick callback error: {e}")

    def save_access_token(self, token: str):
        token_file = ROOT_DIR / "data" / ".access_token"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token)
        self.env.kite_access_token = token

    def load_access_token(self) -> Optional[str]:
        token_file = ROOT_DIR / "data" / ".access_token"
        if token_file.exists():
            return token_file.read_text().strip()
        return self.env.kite_access_token or None
