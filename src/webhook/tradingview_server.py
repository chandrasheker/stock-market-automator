"""FastAPI server for TradingView alert webhooks."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from src.config import get_env
from src.integrations.tradingview import parse_alert_payload
from src.webhook.executor import WebhookExecutor, WebhookResult

app = FastAPI(title="TradingView Webhook", version="1.0.0")
_executor: Optional[WebhookExecutor] = None


class WebhookResponse(BaseModel):
    ok: bool
    message: str
    action: str = ""
    instrument: str = ""
    direction: str = ""
    executed: bool = False
    trade_id: Optional[int] = None


def get_executor() -> WebhookExecutor:
    global _executor
    if _executor is None:
        _executor = WebhookExecutor()
    return _executor


def _validate_secret(
    provided: Optional[str],
    header_secret: Optional[str],
    query_secret: Optional[str],
) -> None:
    env = get_env()
    expected = env.tradingview_webhook_secret
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="TRADINGVIEW_WEBHOOK_SECRET not configured in .env",
        )

    token = provided or header_secret or query_secret
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _to_response(result: WebhookResult) -> WebhookResponse:
    return WebhookResponse(
        ok=result.ok,
        message=result.message,
        action=result.action,
        instrument=result.instrument,
        direction=result.direction,
        executed=result.executed,
        trade_id=result.trade_id,
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "tradingview-webhook"}


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    secret: Optional[str] = Query(None),
    x_webhook_secret: Optional[str] = Header(None, alias="X-Webhook-Secret"),
):
    body = await request.body()
    payload: dict | str | bytes = body
    secret_in_payload = None

    if body:
        try:
            import json
            payload = json.loads(body.decode())
            if isinstance(payload, dict):
                secret_in_payload = payload.get("secret")
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = body

    try:
        alert = parse_alert_payload(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _validate_secret(secret_in_payload, x_webhook_secret, secret)

    logger.info(
        f"TradingView webhook: {alert.instrument} {alert.action} "
        f"(source={alert.source})"
    )

    result = get_executor().handle(alert)
    status = 200 if result.ok else 422
    return JSONResponse(status_code=status, content=_to_response(result).model_dump())


def run_server(host: str = "0.0.0.0", port: int = 8765):
    import uvicorn

    logger.info(f"Starting TradingView webhook server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
