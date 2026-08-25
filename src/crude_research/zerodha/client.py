"""Read-only Kite Connect wrapper. Order and GTT methods are intentionally absent."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from crude_research.auth.token import require_access_token
from crude_research.config import Settings
from crude_research.exceptions import AuthenticationRequiredError, CredentialsMissingError

log = logging.getLogger(__name__)


class MarketDataBroker(Protocol):
    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]: ...

    def quote(self, instruments: list[str]) -> dict[str, Any]: ...

    def profile(self) -> dict[str, Any]: ...

    def historical_data(
        self,
        instrument_token: int,
        from_dt: object,
        to_dt: object,
        interval: str,
        *,
        oi: bool = True,
    ) -> list[dict[str, Any]]: ...


def _raise_if_expired_token(exc: BaseException) -> None:
    name = type(exc).__name__
    if name == "TokenException" or "Incorrect `api_key` or `access_token`" in str(exc):
        raise AuthenticationRequiredError("Kite rejected the access token") from exc


class KiteMarketDataClient:
    """Thin authenticated client exposing only market-data endpoints."""

    def __init__(self, settings: Settings) -> None:
        api_key = settings.kite_api_key
        if not api_key:
            raise AuthenticationRequiredError("KITE_API_KEY is missing")
        access_token = require_access_token(settings)
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
        try:
            payload = self._kite.instruments(exchange=exchange) if exchange else self._kite.instruments()
        except Exception as exc:
            _raise_if_expired_token(exc)
            raise
        return list(payload)

    def quote(self, instruments: list[str]) -> dict[str, Any]:
        if not instruments:
            return {}
        try:
            return dict(self._kite.quote(instruments))
        except Exception as exc:
            _raise_if_expired_token(exc)
            raise

    def profile(self) -> dict[str, Any]:
        """Authenticated connectivity check. Does not touch orders."""
        log.info("Kite profile() request (read-only; credentials not logged)")
        try:
            return dict(self._kite.profile())
        except Exception as exc:
            from crude_research.diagnostics.kite_auth import format_kite_exception

            log.error("Kite profile() failed: %s", format_kite_exception(exc))
            _raise_if_expired_token(exc)
            raise

    def historical_data(
        self,
        instrument_token: int,
        from_dt: object,
        to_dt: object,
        interval: str,
        *,
        oi: bool = True,
    ) -> list[dict[str, Any]]:
        """Read-only Kite historical candles. Never places orders."""
        log.info("historical_data token=%s interval=%s", instrument_token, interval)
        try:
            payload = self._kite.historical_data(
                instrument_token,
                from_dt,
                to_dt,
                interval,
                oi=oi,
            )
        except Exception as exc:
            _raise_if_expired_token(exc)
            raise
        return list(payload)
