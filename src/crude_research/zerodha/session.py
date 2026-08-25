"""Official Kite session exchange. Does not automate password/PIN/TOTP login."""

from __future__ import annotations

import logging
from typing import Any

from crude_research.diagnostics.kite_auth import format_kite_exception, mask_secret
from crude_research.exceptions import CredentialsMissingError

log = logging.getLogger(__name__)

LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"


def login_url(api_key: str) -> str:
    return LOGIN_URL_TEMPLATE.format(api_key=api_key)


def exchange_request_token(*, api_key: str, api_secret: str, request_token: str) -> dict[str, Any]:
    """Call Kite generate_session. Never logs api_secret, request_token, or access_token."""
    try:
        from kiteconnect import KiteConnect
    except ImportError as exc:  # pragma: no cover
        raise CredentialsMissingError("kiteconnect is not installed") from exc
    kite = KiteConnect(api_key=api_key)
    log.info(
        "Exchanging request_token %s for access_token (api_key %s)",
        mask_secret(request_token),
        mask_secret(api_key),
    )
    try:
        payload = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as exc:
        log.error("generate_session failed: %s", format_kite_exception(exc))
        raise
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise CredentialsMissingError("Kite generate_session returned no access_token")
    log.info(
        "Session ok user_id=%s access_token=%s",
        payload.get("user_id"),
        mask_secret(str(payload["access_token"])),
    )
    return dict(payload)
