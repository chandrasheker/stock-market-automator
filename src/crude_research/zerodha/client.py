"""Read-only Kite Connect wrapper. Order and GTT methods are intentionally absent."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from crude_research.config import Settings
from crude_research.exceptions import CredentialsMissingError

log = logging.getLogger(__name__)


class MarketDataBroker(Protocol):
    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]: ...

    def quote(self, instruments: list[str]) -> dict[str, Any]: ...

    def profile(self) -> dict[str, Any]: ...


class KiteMarketDataClient:
    """Thin authenticated client exposing only market-data endpoints."""

    def __init__(self, settings: Settings) -> None:
        api_key, access_token = settings.require_kite_credentials()
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:  # pragma: no cover
            raise CredentialsMissingError(
                "kiteconnect is not installed. pip install -e '.[dev]'"
            ) from exc
        self._kite = KiteConnect(api_key=api_key)
        self._kite.set_access_token(access_token)

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        log.info("Downloading instrument master exchange=%s", exchange or "ALL")
        payload = self._kite.instruments(exchange=exchange) if exchange else self._kite.instruments()
        return list(payload)

    def quote(self, instruments: list[str]) -> dict[str, Any]:
        if not instruments:
            return {}
        return dict(self._kite.quote(instruments))

    def profile(self) -> dict[str, Any]:
        """Authenticated connectivity check. Does not touch orders."""
        log.info("Kite profile() request (read-only; credentials not logged)")
        try:
            return dict(self._kite.profile())
        except Exception as exc:
            from crude_research.diagnostics.kite_auth import format_kite_exception

            log.error("Kite profile() failed: %s", format_kite_exception(exc))
            raise
