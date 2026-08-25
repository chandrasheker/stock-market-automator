from __future__ import annotations

from pathlib import Path

from crude_research.diagnostics.kite_auth import format_kite_exception, mask_secret


def test_format_kite_exception_includes_message() -> None:
    class TokenException(Exception):
        def __init__(self) -> None:
            super().__init__("Incorrect `api_key` or `access_token`.")
            self.message = "Incorrect `api_key` or `access_token`."
            self.code = 403

    text = format_kite_exception(TokenException())
    assert "TokenException" in text
    assert "api_key" in text
    assert "403" in text


def test_mask_secret_hides_middle() -> None:
    assert "SECRETVALUE99" not in mask_secret("SECRETVALUE99")
    assert "len=" in mask_secret("SECRETVALUE99")


def test_hints_flag_16_and_32_char_mixup() -> None:
    from crude_research.diagnostics.kite_auth import token_exception_hints

    hints = token_exception_hints("a" * 16, "b" * 32)
    assert any("16" in h and "32" in h for h in hints)
    assert any("KITE_API_SECRET is empty" in h for h in hints)


def test_hints_flag_secret_copied_into_access_token() -> None:
    from crude_research.diagnostics.kite_auth import token_exception_hints

    secret = "s" * 32
    hints = token_exception_hints("a" * 16, secret, secret)
    assert any("identical" in h for h in hints)


def test_login_url_contains_api_key() -> None:
    from crude_research.zerodha.session import login_url

    url = login_url("abc123")
    assert "api_key=abc123" in url
    assert url.startswith("https://kite.zerodha.com/connect/login")


def test_upsert_env_value(tmp_path: Path) -> None:
    from crude_research.diagnostics.kite_auth import upsert_env_value

    path = tmp_path / ".env"
    path.write_text("KITE_API_KEY=abc\nKITE_ACCESS_TOKEN=old\n", encoding="utf-8")
    upsert_env_value(path, "KITE_ACCESS_TOKEN", "newtoken")
    text = path.read_text(encoding="utf-8")
    assert "KITE_ACCESS_TOKEN=newtoken" in text
    assert "old" not in text
    assert "KITE_API_KEY=abc" in text


def test_exchange_request_token_does_not_log_secrets(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    class FakeKite:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def generate_session(self, request_token: str, api_secret: str) -> dict[str, str]:
            assert request_token == "one_time_request_token"
            assert api_secret == "console_api_secret_value"
            return {"access_token": "daily_access_token_xx", "user_id": "AB1234"}

    monkeypatch.setattr("kiteconnect.KiteConnect", FakeKite)
    from crude_research.zerodha.session import exchange_request_token

    caplog.set_level("INFO")
    payload = exchange_request_token(
        api_key="api_key_value_ok",
        api_secret="console_api_secret_value",
        request_token="one_time_request_token",
    )
    assert payload["access_token"] == "daily_access_token_xx"
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "console_api_secret_value" not in joined
    assert "one_time_request_token" not in joined
    assert "daily_access_token_xx" not in joined


def test_kite_login_url_cli(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from crude_research.cli import app
    from crude_research.config import Settings

    monkeypatch.setattr(
        "crude_research.cli.get_settings",
        lambda: Settings(kite_api_key="testkey123", _env_file=None),
    )
    result = CliRunner().invoke(app, ["kite", "login-url"])
    assert result.exit_code == 0
    assert "api_key=testkey123" in result.stdout
    assert "request-token" in result.stdout


def test_kite_session_writes_env_without_printing_token(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from crude_research.cli import app
    from crude_research.config import Settings

    env_path = tmp_path / ".env"
    env_path.write_text("KITE_API_KEY=k\nKITE_API_SECRET=s\nKITE_ACCESS_TOKEN=old\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakeKite:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def generate_session(self, request_token: str, api_secret: str) -> dict[str, str]:
            return {"access_token": "fresh_daily_token_xx", "user_id": "AB"}

    monkeypatch.setattr("kiteconnect.KiteConnect", FakeKite)
    monkeypatch.setattr(
        "crude_research.cli.get_settings",
        lambda: Settings(kite_api_key="k", kite_api_secret="s", _env_file=None),
    )
    result = CliRunner().invoke(app, ["kite", "session", "--request-token", "req123"])
    assert result.exit_code == 0, result.output
    assert "fresh_daily_token_xx" not in result.output
    assert "req123" not in result.output
    text = env_path.read_text(encoding="utf-8")
    assert "KITE_ACCESS_TOKEN=fresh_daily_token_xx" in text
    assert "old" not in text
