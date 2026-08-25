"""One-time Zerodha callback binding. Does not automate login. Never stores tokens in cookies."""

from __future__ import annotations

import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Lock

from fastapi import Request
from fastapi.responses import Response

from crude_research.config import Settings

log = logging.getLogger(__name__)

COOKIE = "sma_kite_nonce"
OPERATOR_COOKIE = "sma_operator"
TTL_SECONDS = 600


@dataclass
class PendingKiteLogin:
    nonce: str
    expires_at: datetime
    operator_bound: bool


class KiteLoginGuard:
    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: dict[str, PendingKiteLogin] = {}

    def issue(self, *, operator_bound: bool) -> str:
        nonce = secrets.token_urlsafe(32)
        item = PendingKiteLogin(
            nonce=nonce,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=TTL_SECONDS),
            operator_bound=operator_bound,
        )
        with self._lock:
            self._pending[nonce] = item
        return nonce

    def consume(self, nonce: str) -> PendingKiteLogin | None:
        with self._lock:
            item = self._pending.pop(nonce, None)
        if item is None:
            return None
        if item.expires_at <= datetime.now(tz=UTC):
            return None
        return item


def _signing_secret(settings: Settings) -> str:
    return (
        settings.sma_session_secret
        or settings.kite_api_secret
        or settings.kite_api_key
        or "sma-dev-signing-key"
    )


def _sign(value: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), value.encode(), sha256).hexdigest()
    return f"{value}.{digest}"


def _unsign(cookie: str, secret: str) -> str | None:
    if "." not in cookie:
        return None
    value, digest = cookie.rsplit(".", 1)
    expected = hmac.new(secret.encode(), value.encode(), sha256).hexdigest()
    if not hmac.compare_digest(expected, digest):
        return None
    return value


def cookie_secure(request: Request, settings: Settings) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded.lower() == "https":
        return True
    del settings
    return False


def attach_nonce_cookie(
    response: Response, nonce: str, request: Request, settings: Settings
) -> None:
    response.set_cookie(
        COOKIE,
        _sign(nonce, _signing_secret(settings)),
        max_age=TTL_SECONDS,
        httponly=True,
        secure=cookie_secure(request, settings),
        samesite="lax",
        path="/auth",
    )


def clear_nonce_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/auth")


def read_nonce(request: Request, settings: Settings) -> str | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    return _unsign(raw, _signing_secret(settings))


def operator_authenticated(request: Request, settings: Settings) -> bool:
    if not settings.sma_operator_token:
        return True
    raw = request.cookies.get(OPERATOR_COOKIE)
    if not raw:
        return False
    return _unsign(raw, _signing_secret(settings)) == "ok"


def attach_operator_cookie(response: Response, request: Request, settings: Settings) -> None:
    response.set_cookie(
        OPERATOR_COOKIE,
        _sign("ok", _signing_secret(settings)),
        max_age=12 * 3600,
        httponly=True,
        secure=cookie_secure(request, settings),
        samesite="lax",
        path="/",
    )
