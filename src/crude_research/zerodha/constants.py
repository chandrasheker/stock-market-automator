"""Zerodha/Kite API constraints kept in one place."""

from __future__ import annotations

# Official Kite Connect v3 /quote limit.
# https://kite.trade/docs/connect/v3/market-quotes/
KITE_QUOTE_MAX_INSTRUMENTS = 500
KITE_OHLC_MAX_INSTRUMENTS = 1000
KITE_LTP_MAX_INSTRUMENTS = 1000

# Official WebSocket subscription cap per connection.
KITE_WEBSOCKET_MAX_INSTRUMENTS = 3000

EXCHANGE_MCX = "MCX"

MONTH_CODES: tuple[str, ...] = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)
