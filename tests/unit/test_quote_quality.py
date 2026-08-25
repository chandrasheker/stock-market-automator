from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crude_research.market.models import PriceSource
from crude_research.market.quote_quality import assess_quote
from tests.fixtures.builders import quote


def test_mid_only_when_bid_ask_valid() -> None:
    q = quote(token=1, symbol="X", bid=10.0, ask=12.0, ltp=9.0)
    quality = assess_quote(q, stale_after_seconds=120)
    assert quality.valid_bid_ask
    assert quality.mid_price == 11.0
    assert quality.price_source == PriceSource.MID
    assert quality.research_price == 11.0


def test_does_not_substitute_ltp_as_mid() -> None:
    q = quote(token=1, symbol="X", bid=None, ask=None, ltp=5.0)
    quality = assess_quote(q, stale_after_seconds=120)
    assert quality.mid_price is None
    assert quality.price_source == PriceSource.LTP_FALLBACK
    assert quality.research_price == 5.0
    assert "LTP_FALLBACK" in quality.notes
    assert "NO_VALID_MID" in quality.notes


def test_zero_bid() -> None:
    q = quote(token=1, symbol="X", bid=0.0, ask=100.0, ltp=5.0, bid_qty=0)
    quality = assess_quote(q, stale_after_seconds=120)
    assert quality.zero_bid
    assert not quality.valid_bid_ask
    assert quality.mid_price is None
    assert quality.price_source == PriceSource.LTP_FALLBACK


def test_crossed_market() -> None:
    q = quote(token=1, symbol="X", bid=12.0, ask=10.0, ltp=11.0)
    quality = assess_quote(q, stale_after_seconds=120)
    assert quality.crossed_market
    assert not quality.valid_bid_ask
    assert quality.mid_price is None


def test_stale_quote() -> None:
    received = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    old = received - timedelta(hours=3)
    q = quote(token=1, symbol="X", bid=10.0, ask=12.0, ltp=11.0, exchange_ts=old, last_trade_ts=old, received_at=received)
    quality = assess_quote(q, stale_after_seconds=120, now=received)
    assert quality.is_stale
    assert "STALE" in quality.notes


def test_missing_oi_and_volume() -> None:
    q = quote(token=1, symbol="X", bid=10.0, ask=12.0, ltp=11.0, oi=0, volume=0)
    quality = assess_quote(q, stale_after_seconds=120)
    assert not quality.has_oi
    assert not quality.has_volume
    assert "MISSING_OI" in quality.notes
