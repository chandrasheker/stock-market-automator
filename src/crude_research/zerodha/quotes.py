"""Full-quote retrieval and normalization. LTP-only endpoints are not used here."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from crude_research.exceptions import AuthenticationRequiredError, QuoteRequestError
from crude_research.market.models import DepthLevel, MarketDepth, Quote
from crude_research.quant.time import attach_timezone
from crude_research.zerodha.client import MarketDataBroker
from crude_research.zerodha.constants import KITE_QUOTE_MAX_INSTRUMENTS

log = logging.getLogger(__name__)


def chunked(items: Sequence[str], size: int) -> list[list[str]]:
    if size < 1:
        raise QuoteRequestError("quote batch size must be >= 1")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _as_optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _as_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _parse_depth_side(raw: object) -> list[DepthLevel]:
    if not isinstance(raw, list):
        return []
    levels: list[DepthLevel] = []
    for item in raw[:5]:
        if not isinstance(item, Mapping):
            continue
        levels.append(
            DepthLevel(
                price=float(item.get("price") or 0),
                quantity=int(item.get("quantity") or 0),
                orders=int(item.get("orders") or 0),
            )
        )
    return levels


def _as_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _parse_timestamp(value: object, tz: ZoneInfo) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return attach_timezone(value, tz)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return attach_timezone(parsed, tz)


def normalize_rest_quote(
    tradingsymbol: str,
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    tz: ZoneInfo,
) -> Quote:
    ohlc = _as_mapping(payload.get("ohlc"))
    depth_raw = _as_mapping(payload.get("depth"))
    depth = MarketDepth(
        buy=_parse_depth_side(depth_raw.get("buy")),
        sell=_parse_depth_side(depth_raw.get("sell")),
    )
    return Quote(
        instrument_token=int(payload.get("instrument_token") or 0),
        tradingsymbol=tradingsymbol,
        last_price=_as_optional_float(payload.get("last_price")),
        last_quantity=_as_optional_int(payload.get("last_quantity")),
        average_price=_as_optional_float(payload.get("average_price")),
        volume=_as_optional_int(payload.get("volume")),
        oi=_as_optional_float(payload.get("oi")),
        oi_day_high=_as_optional_float(payload.get("oi_day_high")),
        oi_day_low=_as_optional_float(payload.get("oi_day_low")),
        open=_as_optional_float(ohlc.get("open")),
        high=_as_optional_float(ohlc.get("high")),
        low=_as_optional_float(ohlc.get("low")),
        close=_as_optional_float(ohlc.get("close")),
        total_buy_quantity=_as_optional_int(payload.get("buy_quantity")),
        total_sell_quantity=_as_optional_int(payload.get("sell_quantity")),
        depth=depth,
        exchange_timestamp=_parse_timestamp(payload.get("timestamp") or payload.get("exchange_timestamp"), tz),
        last_trade_timestamp=_parse_timestamp(payload.get("last_trade_time"), tz),
        received_at=received_at if received_at.tzinfo else received_at.replace(tzinfo=UTC),
        source="rest_quote",
    )


def normalize_tick(
    tick: Mapping[str, Any],
    *,
    tradingsymbol: str,
    received_at: datetime,
    tz: ZoneInfo,
) -> Quote:
    """Normalize a KiteTicker FULL-mode tick. Field names differ from REST quotes."""
    ohlc = _as_mapping(tick.get("ohlc"))
    depth_raw = _as_mapping(tick.get("depth"))
    depth = MarketDepth(
        buy=_parse_depth_side(depth_raw.get("buy")),
        sell=_parse_depth_side(depth_raw.get("sell")),
    )
    # kiteconnect uses datetime.fromtimestamp(...) which is naive OS-local.
    # Treat naive values as the process local zone, then convert to configured tz.
    local_tz = datetime.now().astimezone().tzinfo or tz

    def tick_ts(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=local_tz).astimezone(tz)
            return value.astimezone(tz)
        return _parse_timestamp(value, tz)

    return Quote(
        instrument_token=int(tick.get("instrument_token") or 0),
        tradingsymbol=tradingsymbol,
        last_price=_as_optional_float(tick.get("last_price")),
        last_quantity=_as_optional_int(tick.get("last_traded_quantity") or tick.get("last_quantity")),
        average_price=_as_optional_float(tick.get("average_traded_price") or tick.get("average_price")),
        volume=_as_optional_int(tick.get("volume_traded") or tick.get("volume")),
        oi=_as_optional_float(tick.get("oi")),
        oi_day_high=_as_optional_float(tick.get("oi_day_high")),
        oi_day_low=_as_optional_float(tick.get("oi_day_low")),
        open=_as_optional_float(ohlc.get("open")),
        high=_as_optional_float(ohlc.get("high")),
        low=_as_optional_float(ohlc.get("low")),
        close=_as_optional_float(ohlc.get("close")),
        total_buy_quantity=_as_optional_int(
            tick.get("total_buy_quantity") if tick.get("total_buy_quantity") is not None else tick.get("buy_quantity")
        ),
        total_sell_quantity=_as_optional_int(
            tick.get("total_sell_quantity") if tick.get("total_sell_quantity") is not None else tick.get("sell_quantity")
        ),
        depth=depth,
        exchange_timestamp=tick_ts(tick.get("exchange_timestamp")),
        last_trade_timestamp=tick_ts(tick.get("last_trade_time")),
        received_at=received_at if received_at.tzinfo else received_at.replace(tzinfo=UTC),
        source="websocket_tick",
    )


def fetch_full_quotes(
    broker: MarketDataBroker,
    quote_keys: Iterable[str],
    *,
    tradingsymbol_by_key: Mapping[str, str],
    batch_size: int = KITE_QUOTE_MAX_INSTRUMENTS,
    tz: ZoneInfo,
    received_at: datetime | None = None,
) -> dict[int, Quote]:
    """Fetch `/quote` (full depth) in bounded batches. Never falls back to LTP."""
    keys = list(dict.fromkeys(quote_keys))
    size = min(batch_size, KITE_QUOTE_MAX_INSTRUMENTS)
    received = received_at or datetime.now(tz=UTC)
    merged: dict[int, Quote] = {}
    for batch in chunked(keys, size):
        log.info("Requesting full quotes batch_size=%s", len(batch))
        try:
            payload = broker.quote(batch)
        except AuthenticationRequiredError:
            raise
        except Exception as exc:
            from crude_research.diagnostics.kite_auth import format_kite_exception

            raise QuoteRequestError(
                f"Kite full-quote request failed for {len(batch)} instruments: {format_kite_exception(exc)}"
            ) from exc
        for key in batch:
            item = payload.get(key)
            if item is None:
                log.warning("Quote missing for %s (expired, halted, or unknown)", key)
                continue
            symbol = tradingsymbol_by_key.get(key, key.split(":", 1)[-1])
            quote = normalize_rest_quote(symbol, item, received_at=received, tz=tz)
            if quote.instrument_token:
                merged[quote.instrument_token] = quote
    return merged
