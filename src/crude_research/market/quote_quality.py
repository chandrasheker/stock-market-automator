"""Explicit market-data quality assessment. LTP is never silently used as mid."""

from __future__ import annotations

from datetime import datetime

from crude_research.market.models import PriceSource, Quote, QuoteQuality


def _age_seconds(received_at: datetime, other: datetime | None) -> float | None:
    if other is None:
        return None
    stamp = other
    if received_at.tzinfo is not None and stamp.tzinfo is not None:
        stamp = stamp.astimezone(received_at.tzinfo)
    return max(0.0, (received_at - stamp).total_seconds())


def assess_quote(quote: Quote, *, stale_after_seconds: float, now: datetime | None = None) -> QuoteQuality:
    received_at = now or quote.received_at
    best_bid_level = quote.depth.best_bid()
    best_ask_level = quote.depth.best_ask()
    best_bid = best_bid_level.price if best_bid_level else None
    best_ask = best_ask_level.price if best_ask_level else None
    has_bid = best_bid is not None and best_bid > 0
    has_ask = best_ask is not None and best_ask > 0
    zero_bid = (best_bid_level is None) or (best_bid_level.price <= 0)
    crossed = bool(has_bid and has_ask and best_bid is not None and best_ask is not None and best_bid > best_ask)
    valid_bid_ask = bool(has_bid and has_ask and not crossed)
    spread = (best_ask - best_bid) if valid_bid_ask and best_bid is not None and best_ask is not None else None
    mid = (best_bid + best_ask) / 2.0 if valid_bid_ask and best_bid is not None and best_ask is not None else None
    spread_pct = (spread / mid) if spread is not None and mid not in (None, 0) else None

    quote_age = _age_seconds(received_at, quote.exchange_timestamp)
    last_trade_age = _age_seconds(received_at, quote.last_trade_timestamp)
    # Freshness of the book uses exchange timestamp. A missing last trade is illiquidity,
    # not automatically staleness, if the exchange packet itself is recent.
    age_for_stale = quote_age if quote_age is not None else last_trade_age
    is_stale = age_for_stale is not None and age_for_stale > stale_after_seconds
    if quote.exchange_timestamp is None and quote.last_trade_timestamp is None:
        is_stale = True

    notes: list[str] = []
    if zero_bid:
        notes.append("ZERO_BID")
    if not has_ask:
        notes.append("NO_ASK")
    if crossed:
        notes.append("CROSSED_MARKET")
    if is_stale:
        notes.append("STALE")
    if not valid_bid_ask:
        notes.append("NO_VALID_MID")

    ltp = quote.last_price if quote.last_price is not None and quote.last_price > 0 else None
    if mid is not None:
        research_price = mid
        price_source = PriceSource.MID
    elif ltp is not None:
        research_price = ltp
        price_source = PriceSource.LTP_FALLBACK
        notes.append("LTP_FALLBACK")
    else:
        research_price = None
        price_source = PriceSource.UNAVAILABLE

    has_oi = quote.oi is not None and quote.oi > 0
    has_volume = quote.volume is not None and quote.volume > 0
    if not has_oi:
        notes.append("MISSING_OI")
    if not has_volume:
        notes.append("MISSING_VOLUME")

    return QuoteQuality(
        has_bid=has_bid,
        has_ask=has_ask,
        valid_bid_ask=valid_bid_ask,
        crossed_market=crossed,
        zero_bid=zero_bid,
        spread=spread,
        spread_pct=spread_pct,
        mid_price=mid,
        best_bid=best_bid if has_bid else None,
        best_ask=best_ask if has_ask else None,
        quote_age_seconds=quote_age,
        last_trade_age_seconds=last_trade_age,
        is_stale=is_stale,
        has_oi=has_oi,
        has_volume=has_volume,
        research_price=research_price,
        price_source=price_source,
        notes=tuple(notes),
    )
