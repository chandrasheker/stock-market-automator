from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from crude_research.auth.token import (
    TokenStore,
    has_current_access_token,
    require_access_token,
    session_status,
)
from crude_research.config import Settings
from crude_research.exceptions import AuthenticationRequiredError


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(data_dir=tmp_path, _env_file=None, **kwargs)  # type: ignore[arg-type]


def test_store_roundtrip_and_permissions(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "secrets" / "kite_session.json")
    record = store.save("daily_access_token_xx")
    assert record.session_date == store.today()
    loaded = store.get_if_current()
    assert loaded is not None
    assert loaded.access_token == "daily_access_token_xx"
    assert store.path.stat().st_mode & 0o777 == 0o600
    status = store.public_status()
    assert status["authenticated"] is True
    assert status["broker"] == "ZERODHA"
    assert "daily_access_token_xx" not in str(status)


def test_stale_session_is_fail_closed(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "secrets" / "kite_session.json")
    store.save("old_token_value")
    yesterday = datetime.now(tz=ZoneInfo("Asia/Kolkata")).date() - timedelta(days=1)
    payload = store.path.read_text(encoding="utf-8").replace(
        f'"session_date": "{store.today().isoformat()}"',
        f'"session_date": "{yesterday.isoformat()}"',
    )
    store.path.write_text(payload, encoding="utf-8")
    assert store.get_if_current() is None
    settings = _settings(tmp_path, kite_api_key="k", kite_access_token="env_token_should_not_be_used")
    with pytest.raises(AuthenticationRequiredError, match="AUTHENTICATION_REQUIRED"):
        require_access_token(settings)
    assert has_current_access_token(settings) is False
    assert session_status(settings)["authenticated"] is False


def test_env_token_used_only_when_store_absent(tmp_path: Path) -> None:
    settings = _settings(tmp_path, kite_api_key="k", kite_access_token="env_daily_token")
    assert require_access_token(settings) == "env_daily_token"
    assert session_status(settings)["authenticated"] is True


def test_missing_session_raises_authentication_required(tmp_path: Path) -> None:
    settings = _settings(tmp_path, kite_api_key="k")
    with pytest.raises(AuthenticationRequiredError, match="AUTHENTICATION_REQUIRED"):
        require_access_token(settings)


def test_save_does_not_log_token(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = TokenStore(tmp_path / "secrets" / "kite_session.json")
    caplog.set_level("INFO")
    store.save("super_secret_access_token")
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "super_secret_access_token" not in joined
