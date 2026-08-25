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


def test_permission_hints_mention_connect_app() -> None:
    from crude_research.diagnostics.kite_auth import (
        is_market_data_permission_error,
        permission_exception_hints,
    )

    class PermissionException(Exception):
        pass

    assert is_market_data_permission_error(PermissionException("Insufficient permission for that call."))
    hints = permission_exception_hints()
    assert any("Personal" in h and "quote()" in h for h in hints)
    assert any("Connect" in h for h in hints)


def test_wrapped_quote_error_counts_as_permission() -> None:
    from crude_research.diagnostics.kite_auth import is_market_data_permission_error
    from crude_research.exceptions import QuoteRequestError

    exc = QuoteRequestError(
        "Kite full-quote request failed for 355 instruments: "
        "PermissionException: Insufficient permission for that call. (code=403)"
    )
    assert is_market_data_permission_error(exc)


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


def test_kite_login_url_cli(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from crude_research.cli import app
    from crude_research.config import Settings

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KITE_API_KEY=testkey123\n", encoding="utf-8")
    monkeypatch.setattr(
        "crude_research.cli.get_settings",
        lambda: Settings(kite_api_key="testkey123", kite_api_secret=None, _env_file=None),
    )
    result = CliRunner().invoke(app, ["kite", "login-url"])
    assert result.exit_code == 0
    assert "api_key=testkey123" in result.stdout
    assert "request-token" in result.stdout
    assert "KITE_API_SECRET is empty" in result.output
    assert "KITE_API_SECRET=" in (tmp_path / ".env").read_text(encoding="utf-8")


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


def test_placeholder_request_token() -> None:
    from crude_research.diagnostics.kite_auth import is_placeholder_request_token

    assert is_placeholder_request_token("REQUEST_TOKEN")
    assert is_placeholder_request_token("PASTE_TOKEN_HERE")
    assert is_placeholder_request_token("  <token_from_redirect_url>  ")
    assert not is_placeholder_request_token("abc123real")


def test_ensure_env_key_appends_once(tmp_path: Path) -> None:
    from crude_research.diagnostics.kite_auth import ensure_env_key

    path = tmp_path / ".env"
    path.write_text("KITE_API_KEY=abc\n", encoding="utf-8")
    assert ensure_env_key(path, "KITE_API_SECRET") is True
    assert ensure_env_key(path, "KITE_API_SECRET") is False
    text = path.read_text(encoding="utf-8")
    assert text.count("KITE_API_SECRET=") == 1


def test_kite_session_rejects_placeholder_and_missing_secret(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from crude_research.cli import app
    from crude_research.config import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "crude_research.cli.get_settings",
        lambda: Settings(
            kite_api_key="k",
            kite_api_secret=None,
            kite_access_token="b" * 32,
            _env_file=None,
        ),
    )
    result = CliRunner().invoke(app, ["kite", "session", "--request-token", "PASTE_TOKEN_HERE"])
    assert result.exit_code == 1
    assert "placeholder" in result.output
    assert "KITE_API_SECRET is missing" in result.output
    assert "32 chars" in result.output


def test_kite_set_secret_writes_env(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from crude_research.cli import app
    from crude_research.config import Settings

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KITE_API_KEY=k\nKITE_ACCESS_TOKEN=old\n", encoding="utf-8")
    monkeypatch.setattr(
        "crude_research.cli.get_settings",
        lambda: Settings(kite_api_key="k", kite_access_token="old", _env_file=None),
    )
    result = CliRunner().invoke(app, ["kite", "set-secret"], input="console_secret_value\n")
    assert result.exit_code == 0, result.output
    assert "console_secret_value" not in result.output
    assert "KITE_API_SECRET=console_secret_value" in (tmp_path / ".env").read_text(encoding="utf-8")
