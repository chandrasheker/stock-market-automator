"""Minimal SMA HTTP server: official Kite browser login and token lifecycle."""

from __future__ import annotations

import html
import logging
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from crude_research.auth.token import TokenStore, default_store, session_status
from crude_research.config import Settings, get_settings
from crude_research.diagnostics.kite_auth import format_kite_exception
from crude_research.logging_setup import setup_logging
from crude_research.zerodha.session import exchange_request_token, login_url

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, store: TokenStore | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or default_store(settings)
    setup_logging(settings.log_level)
    app = FastAPI(title="SMA", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.token_store = store

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        status = session_status(request.app.state.settings)
        label = "Authenticated" if status["authenticated"] else "Authentication Required"
        body = (
            "<h1>SMA</h1>"
            f"<p>Zerodha: {html.escape(label)}</p>"
            '<p><a href="/auth/zerodha">Authenticate Zerodha</a></p>'
        )
        return _page("SMA", body)

    @app.get("/auth/status")
    def auth_status(request: Request) -> JSONResponse:
        payload = session_status(request.app.state.settings)
        return JSONResponse(payload)

    @app.get("/auth/zerodha", response_model=None)
    def auth_zerodha(request: Request) -> HTMLResponse | RedirectResponse:
        settings = request.app.state.settings
        if not settings.kite_api_key:
            return _error_page("KITE_API_KEY is not configured on the server.")
        return RedirectResponse(login_url(settings.kite_api_key), status_code=302)

    @app.get("/auth/zerodha/callback", response_class=HTMLResponse)
    def auth_callback(
        request: Request,
        request_token: str | None = Query(default=None),
        status: str | None = Query(default=None),
        action: str | None = Query(default=None),
    ) -> HTMLResponse:
        del action
        settings: Settings = request.app.state.settings
        store: TokenStore = request.app.state.token_store
        if status is not None and status.lower() != "success":
            log.info("Kite callback status is not success")
            return _error_page("Zerodha login was not completed.")
        token = (request_token or "").strip()
        if not token:
            return _error_page("Callback is missing request_token.")
        if not settings.kite_api_key or not settings.kite_api_secret:
            return _error_page("Server is missing KITE_API_KEY or KITE_API_SECRET.")
        try:
            payload = exchange_request_token(
                api_key=settings.kite_api_key,
                api_secret=settings.kite_api_secret,
                request_token=token,
            )
            access_token = str(payload["access_token"])
            _validate_profile(settings.kite_api_key, access_token)
            store.save(access_token)
        except Exception as exc:
            log.error("Kite callback failed: %s", format_kite_exception(exc))
            return _error_page("Could not exchange or validate the Kite session.")
        body = (
            "<h1>SMA</h1>"
            "<p>Zerodha authenticated successfully.</p>"
            "<p>SMA is ready for today's session.</p>"
            '<p><a href="/">Back</a></p>'
        )
        return _page("SMA", body)

    return app


def _validate_profile(api_key: str, access_token: str) -> dict[str, Any]:
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return dict(kite.profile())


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    document = (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )
    return HTMLResponse(document, status_code=status_code)


def _error_page(message: str) -> HTMLResponse:
    body = (
        "<h1>SMA</h1>"
        "<p>Zerodha authentication failed.</p>"
        f"<p>{html.escape(message)}</p>"
        '<p><a href="/auth/zerodha">Try again</a></p>'
    )
    return _page("SMA", body, status_code=400)


app = create_app()
