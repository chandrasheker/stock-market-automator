from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from crude_research.auth.token import TokenStore
from crude_research.config import Settings
from crude_research.http.app import create_app


def _client(tmp_path: Path, monkeypatch) -> TestClient:  # type: ignore[no-untyped-def]
    settings = Settings(
        kite_api_key="testkey123",
        kite_api_secret="testsecret",
        sma_base_url="https://sma.example",
        data_dir=tmp_path,
        _env_file=None,
    )
    store = TokenStore(tmp_path / "secrets" / "kite_session.json")
    monkeypatch.setattr("crude_research.http.app.exchange_request_token", _fake_exchange)
    monkeypatch.setattr("crude_research.http.app._validate_profile", lambda api_key, access_token: {"user_id": "AB"})
    return TestClient(create_app(settings, store))


def _fake_exchange(*, api_key: str, api_secret: str, request_token: str) -> dict[str, str]:
    assert api_key == "testkey123"
    assert api_secret == "testsecret"
    assert request_token == "real_request_token"
    return {"access_token": "daily_access_token_xx", "user_id": "AB"}


def test_home_and_status_unauthenticated(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path, monkeypatch)
    home = client.get("/")
    assert home.status_code == 200
    assert "SMA" in home.text
    assert "Authentication Required" in home.text
    assert "Authenticate Zerodha" in home.text
    assert "daily_access_token" not in home.text
    status = client.get("/auth/status")
    assert status.json() == {
        "authenticated": False,
        "authenticated_at": None,
        "broker": "ZERODHA",
    }


def test_login_redirects_to_kite(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path, monkeypatch)
    response = client.get("/auth/zerodha", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://kite.zerodha.com/connect/login")
    assert "api_key=testkey123" in response.headers["location"]


def test_callback_rejects_placeholder_and_missing_token(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path, monkeypatch)
    failed = client.get("/auth/zerodha/callback", params={"status": "failure"})
    assert failed.status_code == 400
    assert "failed" in failed.text.lower()
    missing = client.get("/auth/zerodha/callback", params={"status": "success"})
    assert missing.status_code == 400
    assert "request_token" in missing.text


def test_callback_stores_token_without_exposing_it(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = _client(tmp_path, monkeypatch)
    response = client.get(
        "/auth/zerodha/callback",
        params={"status": "success", "request_token": "real_request_token", "action": "login"},
    )
    assert response.status_code == 200
    assert "Zerodha authenticated successfully." in response.text
    assert "SMA is ready for today's session." in response.text
    assert "daily_access_token_xx" not in response.text
    assert "real_request_token" not in response.text
    assert "testsecret" not in response.text
    status = client.get("/auth/status").json()
    assert status["authenticated"] is True
    assert status["broker"] == "ZERODHA"
    assert status["authenticated_at"]
    assert "daily_access_token_xx" not in str(status)
