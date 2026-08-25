"""KiteTicker market-data interface. No order/postback handling is implemented."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from crude_research.config import Settings
from crude_research.exceptions import AuthenticationRequiredError, CredentialsMissingError
from crude_research.market.models import Quote
from crude_research.zerodha.quotes import normalize_tick

log = logging.getLogger(__name__)

TickHandler = Callable[[Quote], None]


class MarketDataStream:
    """Subscribe to FULL-mode ticks for selected instrument tokens.

    This is a library interface, not a production daemon. Reconnect is conservative:
    KiteTicker auto-reconnect is enabled with bounded retries; `on_close` does not
    call `stop()` so reconnect remains possible until `shutdown()`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        tz: ZoneInfo,
        tradingsymbol_by_token: dict[int, str],
        on_quote: TickHandler | None = None,
    ) -> None:
        api_key = settings.kite_api_key
        if not api_key:
            raise AuthenticationRequiredError("KITE_API_KEY is missing")
        from crude_research.auth.token import require_access_token

        access_token = require_access_token(settings)
        try:
            from kiteconnect import KiteTicker
        except ImportError as exc:  # pragma: no cover
            raise CredentialsMissingError("kiteconnect is not installed") from exc
        self._ticker = KiteTicker(
            api_key,
            access_token,
            reconnect=True,
            reconnect_max_tries=settings.websocket_reconnect_max_tries,
            reconnect_max_delay=settings.websocket_reconnect_max_delay,
        )
        self._tz = tz
        self._symbols = dict(tradingsymbol_by_token)
        self._on_quote = on_quote
        self._tokens: set[int] = set()
        self._mode = "full"
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._ticker.on_connect = self._on_connect
        self._ticker.on_ticks = self._on_ticks
        self._ticker.on_close = self._on_close
        self._ticker.on_error = self._on_error
        self._ticker.on_reconnect = self._on_reconnect
        self._ticker.on_noreconnect = self._on_noreconnect

    def connect(self, *, threaded: bool = True) -> None:
        log.info("Connecting KiteTicker (threaded=%s)", threaded)
        self._ticker.connect(threaded=threaded)

    def subscribe(self, tokens: list[int], mode: str = "full") -> None:
        unique = [int(token) for token in dict.fromkeys(tokens)]
        with self._lock:
            self._tokens.update(unique)
            self._mode = mode
        if not unique:
            return
        log.info("Subscribe %s tokens mode=%s", len(unique), mode)
        self._ticker.subscribe(unique)
        if mode == "full":
            self._ticker.set_mode(self._ticker.MODE_FULL, unique)
        elif mode == "quote":
            self._ticker.set_mode(self._ticker.MODE_QUOTE, unique)
        elif mode == "ltp":
            self._ticker.set_mode(self._ticker.MODE_LTP, unique)
        else:
            raise ValueError(f"Unknown ticker mode {mode!r}")

    def unsubscribe(self, tokens: list[int]) -> None:
        unique = [int(token) for token in dict.fromkeys(tokens)]
        with self._lock:
            self._tokens.difference_update(unique)
        if unique:
            log.info("Unsubscribe %s tokens", len(unique))
            self._ticker.unsubscribe(unique)

    def reconnect(self) -> None:
        """Force a conservative reconnect and re-subscribe current tokens."""
        log.warning("Forcing websocket reconnect")
        try:
            self._ticker.close()
        except Exception:
            log.exception("Error while closing ticker before reconnect")
        self.connect(threaded=True)

    def shutdown(self) -> None:
        log.info("Shutting down KiteTicker")
        try:
            self._ticker.close()
        finally:
            try:
                self._ticker.stop()
            except Exception:
                log.debug("ticker.stop() raised during shutdown", exc_info=True)

    def wait_connected(self, timeout: float = 15.0) -> bool:
        return self._connected.wait(timeout)

    def _on_connect(self, ws: Any, _response: Any) -> None:
        self._connected.set()
        with self._lock:
            tokens = list(self._tokens)
            mode = self._mode
        if tokens:
            log.info("Resubscribing %s tokens after connect", len(tokens))
            ws.subscribe(tokens)
            if mode == "full":
                ws.set_mode(ws.MODE_FULL, tokens)

    def _on_ticks(self, _ws: Any, ticks: list[dict[str, Any]]) -> None:
        received_at = datetime.now(tz=UTC)
        for tick in ticks:
            token = int(tick.get("instrument_token") or 0)
            symbol = self._symbols.get(token, str(token))
            quote = normalize_tick(tick, tradingsymbol=symbol, received_at=received_at, tz=self._tz)
            if self._on_quote is not None:
                self._on_quote(quote)

    def _on_close(self, _ws: Any, code: Any, reason: Any) -> None:
        # Do not call stop() here: that would disable KiteTicker reconnect.
        log.warning("KiteTicker closed code=%s reason=%s", code, reason)
        self._connected.clear()

    def _on_error(self, _ws: Any, code: Any, reason: Any) -> None:
        log.error("KiteTicker error code=%s reason=%s", code, reason)

    def _on_reconnect(self, _ws: Any, attempts: Any) -> None:
        log.warning("KiteTicker reconnect attempt %s", attempts)

    def _on_noreconnect(self, _ws: Any) -> None:
        log.error("KiteTicker gave up reconnecting")
