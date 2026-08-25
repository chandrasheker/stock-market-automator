"""Owns the current day's Kite access token. Never logs the token value."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from crude_research.config import Settings
from crude_research.exceptions import AuthenticationRequiredError

log = logging.getLogger(__name__)

_BROKER = "ZERODHA"
_FILE_MODE = 0o600
_DIR_MODE = 0o700


@dataclass(frozen=True)
class TokenRecord:
    access_token: str
    session_date: date
    authenticated_at: datetime | None
    source: str


class TokenStore:
    """Restrictive local persistence so CLI and the SMA process can share today's token."""

    def __init__(self, path: Path, *, timezone_name: str = "Asia/Kolkata") -> None:
        self.path = path
        self.timezone_name = timezone_name
        self._lock = threading.Lock()

    def today(self) -> date:
        return datetime.now(tz=ZoneInfo(self.timezone_name)).date()

    def has_file(self) -> bool:
        return self.path.is_file()

    def get_if_current(self) -> TokenRecord | None:
        record = self._load()
        if record is None:
            return None
        today = self.today()
        if record.session_date != today:
            log.info(
                "Stored Kite session date=%s is not today=%s; refusing token (AUTHENTICATION_REQUIRED)",
                record.session_date.isoformat(),
                today.isoformat(),
            )
            return None
        return record

    def save(self, access_token: str, *, authenticated_at: datetime | None = None) -> TokenRecord:
        token = access_token.strip()
        if not token:
            raise AuthenticationRequiredError("refusing to store an empty access token")
        tz = ZoneInfo(self.timezone_name)
        now = authenticated_at or datetime.now(tz=tz)
        now = now.replace(tzinfo=UTC).astimezone(tz) if now.tzinfo is None else now.astimezone(tz)
        record = TokenRecord(
            access_token=token,
            session_date=now.date(),
            authenticated_at=now,
            source="store",
        )
        payload = {
            "broker": _BROKER,
            "session_date": record.session_date.isoformat(),
            "authenticated_at": now.isoformat(),
            "access_token": record.access_token,
        }
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, _DIR_MODE)
        except OSError:
            log.debug("Could not chmod token directory")
        tmp = self.path.with_name(self.path.name + ".tmp")
        with self._lock:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            try:
                os.chmod(tmp, _FILE_MODE)
            except OSError:
                log.debug("Could not chmod token temp file")
            tmp.replace(self.path)
            try:
                os.chmod(self.path, _FILE_MODE)
            except OSError:
                log.debug("Could not chmod token file")
        log.info(
            "Stored Kite session for %s (token not logged)",
            record.session_date.isoformat(),
        )
        return record

    def public_status(self) -> dict[str, object]:
        record = self.get_if_current()
        if record is None:
            return {"authenticated": False, "authenticated_at": None, "broker": _BROKER}
        stamped = record.authenticated_at.isoformat() if record.authenticated_at else None
        return {"authenticated": True, "authenticated_at": stamped, "broker": _BROKER}

    def _load(self) -> TokenRecord | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Kite session file unreadable; treating as AUTHENTICATION_REQUIRED")
            return None
        if not isinstance(raw, dict):
            return None
        token = raw.get("access_token")
        session_raw = raw.get("session_date")
        if not isinstance(token, str) or not token.strip() or not isinstance(session_raw, str):
            return None
        try:
            session_day = date.fromisoformat(session_raw)
        except ValueError:
            return None
        stamped = raw.get("authenticated_at")
        authenticated_at: datetime | None = None
        if isinstance(stamped, str) and stamped:
            try:
                authenticated_at = datetime.fromisoformat(stamped)
            except ValueError:
                authenticated_at = None
        return TokenRecord(
            access_token=token.strip(),
            session_date=session_day,
            authenticated_at=authenticated_at,
            source="store",
        )


def default_store(settings: Settings) -> TokenStore:
    return TokenStore(
        settings.data_dir / "secrets" / "kite_session.json",
        timezone_name=settings.timezone,
    )


def require_access_token(settings: Settings) -> str:
    """Return today's access token or fail closed with AUTHENTICATION_REQUIRED."""
    if not settings.kite_api_key:
        raise AuthenticationRequiredError("KITE_API_KEY is missing")
    store = default_store(settings)
    current = store.get_if_current()
    if current is not None:
        return current.access_token
    if store.has_file():
        raise AuthenticationRequiredError("stored Kite session is not valid for today's session")
    env_token = settings.kite_access_token
    if env_token:
        return env_token
    raise AuthenticationRequiredError("Kite session is missing or expired")


def has_current_access_token(settings: Settings) -> bool:
    try:
        require_access_token(settings)
    except AuthenticationRequiredError:
        return False
    return True


def session_status(settings: Settings) -> dict[str, object]:
    store = default_store(settings)
    current = store.get_if_current()
    if current is not None:
        return store.public_status()
    if store.has_file():
        return {"authenticated": False, "authenticated_at": None, "broker": _BROKER}
    if settings.kite_api_key and settings.kite_access_token:
        return {"authenticated": True, "authenticated_at": None, "broker": _BROKER}
    return {"authenticated": False, "authenticated_at": None, "broker": _BROKER}
