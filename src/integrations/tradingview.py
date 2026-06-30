"""TradingView chart widgets and webhook alert parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from src.config import get_yaml_config

# TradingView index/futures symbols for embedded charts
DEFAULT_TV_SYMBOLS: dict[str, str] = {
    "nifty50": "NSE:NIFTY",
    "sensex": "BSE:SENSEX",
    "crude_oil": "MCX:CRUDEOIL1!",
}

INSTRUMENT_ALIASES: dict[str, str] = {
    "nifty": "nifty50",
    "nifty50": "nifty50",
    "nifty 50": "nifty50",
    "nifty_50": "nifty50",
    "nse:nifty": "nifty50",
    "sensex": "sensex",
    "bse:sensex": "sensex",
    "crude": "crude_oil",
    "crude_oil": "crude_oil",
    "crudeoil": "crude_oil",
    "mcx:crudeoil": "crude_oil",
    "mcx:crudeoil1!": "crude_oil",
}

VALID_ACTIONS = {"SCAN", "SELL_CE", "SELL_PE", "BUY_CE", "BUY_PE", "EXIT"}


@dataclass
class TradingViewAlert:
    instrument: str
    action: str
    message: str = ""
    source: str = "tradingview"
    raw: str = ""

    @property
    def direction_filter(self) -> Optional[str]:
        """Map alert action to bot direction label (e.g. SELL_CE)."""
        if self.action in {"SELL_CE", "SELL_PE", "BUY_CE", "BUY_PE"}:
            return self.action
        return None


def get_tv_symbols() -> dict[str, str]:
    cfg = get_yaml_config().get("tradingview", {})
    symbols = dict(DEFAULT_TV_SYMBOLS)
    symbols.update(cfg.get("symbols", {}))
    return symbols


def resolve_instrument(value: str) -> Optional[str]:
    if not value:
        return None
    key = value.strip().lower().replace("-", "_")
    if key in INSTRUMENT_ALIASES:
        return INSTRUMENT_ALIASES[key]
    cfg = get_yaml_config()
    if key in cfg.get("instruments", {}):
        return key
    return None


def parse_alert_payload(body: str | bytes | dict) -> TradingViewAlert:
    """Parse TradingView webhook body (JSON or key=value text)."""
    raw = ""
    data: dict[str, Any] = {}

    if isinstance(body, dict):
        data = body
        raw = json.dumps(body)
    else:
        raw = body.decode() if isinstance(body, bytes) else str(body)
        raw = raw.strip()
        if not raw:
            raise ValueError("Empty webhook body")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _parse_kv_message(raw)

    instrument_raw = (
        data.get("instrument")
        or data.get("symbol")
        or data.get("ticker")
        or ""
    )
    instrument = resolve_instrument(str(instrument_raw))
    if not instrument:
        raise ValueError(f"Unknown instrument: {instrument_raw!r}")

    action = str(data.get("action") or data.get("side") or "SCAN").upper().strip()
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action}. Use one of {sorted(VALID_ACTIONS)}")

    return TradingViewAlert(
        instrument=instrument,
        action=action,
        message=str(data.get("message") or data.get("msg") or ""),
        source=str(data.get("source") or "tradingview"),
        raw=raw,
    )


def _parse_kv_message(text: str) -> dict[str, str]:
    """Parse 'instrument=nifty50 action=SCAN' style alerts."""
    result: dict[str, str] = {}
    for part in re.split(r"[,\n;]+", text):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip().lower()] = v.strip()
    if not result and text:
        result["message"] = text
        result["action"] = "SCAN"
        result["instrument"] = "nifty50"
    return result


def build_chart_html(
    symbol: str,
    *,
    interval: str = "15",
    theme: str = "dark",
    height: int = 500,
    studies: Optional[list[str]] = None,
    container_id: str = "tradingview_chart",
) -> str:
    """Return HTML for TradingView Advanced Chart widget."""
    studies = studies or ["RSI@tv-basicstudies", "MACD@tv-basicstudies"]
    studies_json = json.dumps(studies)
    return f"""
<div class="tradingview-widget-container" style="height:{height}px;width:100%;">
  <div id="{container_id}" style="height:100%;width:100%;"></div>
  <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
  <script type="text/javascript">
  new TradingView.widget({{
    "autosize": true,
    "symbol": "{symbol}",
    "interval": "{interval}",
    "timezone": "Asia/Kolkata",
    "theme": "{theme}",
    "style": "1",
    "locale": "en",
    "toolbar_bg": "#131722",
    "enable_publishing": false,
    "hide_top_toolbar": false,
    "hide_legend": false,
    "save_image": false,
    "container_id": "{container_id}",
    "studies": {studies_json},
    "withdateranges": true,
    "allow_symbol_change": false
  }});
  </script>
</div>
"""


def get_webhook_message_template(secret_placeholder: str = "YOUR_SECRET") -> str:
    """JSON alert message for Pine Script / TradingView alert dialog."""
    return (
        '{{"secret":"'
        + secret_placeholder
        + '","instrument":"{{{{ticker}}}}","action":"SCAN","source":"tradingview"}}'
    )


def get_pine_script_example() -> str:
    return """// TradingView → Automator webhook example (Pine v5)
// Replace YOUR_SECRET and your VM public IP / domain.

//@version=5
indicator("Automator Webhook", overlay=true)

webhook_secret = input.string("YOUR_SECRET", "Webhook secret")
instrument     = input.string("nifty50", "Instrument key", options=["nifty50", "sensex", "crude_oil"])
action_on_bull = input.string("SELL_PE", "Bullish action", options=["SCAN", "SELL_CE", "SELL_PE"])
action_on_bear = input.string("SELL_CE", "Bearish action", options=["SCAN", "SELL_CE", "SELL_PE"])

rsi = ta.rsi(close, 14)
bull = ta.crossover(rsi, 30)
bear = ta.crossunder(rsi, 70)

msg_bull = '{"secret":"' + webhook_secret + '","instrument":"' + instrument + '","action":"' + action_on_bull + '","source":"tradingview"}'
msg_bear = '{"secret":"' + webhook_secret + '","instrument":"' + instrument + '","action":"' + action_on_bear + '","source":"tradingview"}'

if bull
    alert(msg_bull, alert.freq_once_per_bar_close)
if bear
    alert(msg_bear, alert.freq_once_per_bar_close)
"""
