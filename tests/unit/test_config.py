from __future__ import annotations

from pathlib import Path

from crude_research.config import Settings


def test_missing_credentials_still_construct(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("KITE_API_SECRET", raising=False)
    monkeypatch.delenv("RISK_FREE_RATE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.has_kite_credentials() is False
    assert settings.risk_free_rate is None
    assert settings.quote_batch_size == 500
    assert settings.timezone == "Asia/Kolkata"


def test_empty_rate_is_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("RISK_FREE_RATE", "")
    settings = Settings(_env_file=None)
    assert settings.risk_free_rate is None


def test_data_dir_path() -> None:
    settings = Settings(data_dir=Path("./data"), _env_file=None)
    assert settings.data_dir == Path("./data")


def test_strips_quoted_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KITE_API_KEY", '  "abcKEY12xyz"  ')
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "'tok_value_here_ok'")
    monkeypatch.setenv("KITE_API_SECRET", '  "sec_value_here_ok"  ')
    settings = Settings(_env_file=None)
    assert settings.kite_api_key == "abcKEY12xyz"
    assert settings.kite_access_token == "tok_value_here_ok"
    assert settings.kite_api_secret == "sec_value_here_ok"
