"""Option-chain reconstruction: ATM, straddle, missing sides, irregular ladder."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from crude_research.market.chain import (
    build_option_chain,
    derive_strike_interval,
    select_atm_strike,
)
from crude_research.market.models import (
    ExpiryTimeSource,
    IVStatus,
    PriceSource,
    StraddlePriceSource,
)
from crude_research.zerodha.quotes import chunked, normalize_rest_quote
from tests.fixtures.builders import crude_master, quote


def _now() -> datetime:
    return datetime(2026, 8, 25, 10, 0, tzinfo=UTC)


def test_atm_closest_and_lower_tiebreak() -> None:
    assert select_atm_strike(8018, [7950, 8000, 8050]) == 8000
    assert select_atm_strike(8025, [8000, 8050]) == 8000
    assert select_atm_strike(8025.0001, [8000, 8050]) == 8050


def test_irregular_strike_ladder() -> None:
    assert derive_strike_interval([7900, 7950, 8000, 8050]) == 50
    assert derive_strike_interval([7900, 8000, 8050]) is None


def _mini_quotes(*, future_bid: float = 8010, future_ask: float = 8014, stale: bool = False) -> dict:
    received = _now()
    exch = received - timedelta(hours=4) if stale else received
    q = {
        2: quote(token=2, symbol="CRUDEOILM26OCTFUT", bid=future_bid, ask=future_ask, ltp=8012, exchange_ts=exch, received_at=received),
    }
    # ATM ~ 8000. CE/PE mids 400/386 → straddle 786
    specs = {
        7900: (520, 310),
        7950: (460, 350),
        8000: (400, 386),
        8050: (350, 440),
        8100: (300, 500),
    }
    for strike, (ce_mid, pe_mid) in specs.items():
        q[3000 + strike] = quote(
            token=3000 + strike,
            symbol=f"CRUDEOILM26OCT{strike}CE",
            bid=ce_mid - 2,
            ask=ce_mid + 2,
            ltp=ce_mid,
            exchange_ts=exch,
            received_at=received,
        )
        q[4000 + strike] = quote(
            token=4000 + strike,
            symbol=f"CRUDEOILM26OCT{strike}PE",
            bid=pe_mid - 2,
            ask=pe_mid + 2,
            ltp=pe_mid,
            exchange_ts=exch,
            received_at=received,
        )
    return q


def test_chain_atm_straddle_and_distance_ratio() -> None:
    snapshot = build_option_chain(
        "CRUDEOILM",
        date(2026, 10, 16),
        _mini_quotes(),
        crude_master(),
        now=_now(),
        stale_after_seconds=120,
        rate=0.06,
        time_years=0.14,
        expiry_timestamp=datetime(2026, 10, 16, 23, 30, tzinfo=ZoneInfo("Asia/Kolkata")),
        expiry_time_source=ExpiryTimeSource.CONFIGURED_ASSUMPTION,
        vol_lower=1e-6,
        vol_upper=5.0,
    )
    assert snapshot.atm_strike == 8000
    assert snapshot.future_price == 8012
    assert snapshot.future_price_source == PriceSource.MID
    assert snapshot.atm_ce_mid == 400
    assert snapshot.atm_pe_mid == 386
    assert snapshot.atm_straddle_mid == 786
    assert snapshot.straddle_price_source == StraddlePriceSource.MID
    row_7000ish = next(r for r in snapshot.rows if r.strike == 7900)
    assert row_7000ish.ce.distance_points == abs(7900 - 8012)
    assert row_7000ish.ce.straddle_distance_ratio == pytest_approx(abs(7900 - 8012) / 786)
    assert snapshot.strike_interval == 50
    ok_ivs = [
        side.greeks
        for row in snapshot.rows
        for side in (row.ce, row.pe)
        if side.greeks and side.greeks.iv_status.value == "OK"
    ]
    assert ok_ivs
    assert snapshot.risk_free_rate == 0.06


def pytest_approx(value: float) -> object:
    import pytest

    return pytest.approx(value)


def test_missing_ce_and_pe() -> None:
    master = [
        inst
        for inst in crude_master()
        if inst.tradingsymbol
        in {
            "CRUDEOILM26OCTFUT",
            "CRUDEOILM26OCT8000CE",
            "CRUDEOILM26OCT8050PE",
        }
        or inst.tradingsymbol.startswith("CRUDEOILM26OCT79")
        or inst.tradingsymbol.startswith("CRUDEOILM26OCT81")
    ]
    # Drop 8000 PE and 8050 CE if present
    master = [m for m in master if m.tradingsymbol not in {"CRUDEOILM26OCT8000PE", "CRUDEOILM26OCT8050CE"}]
    quotes = _mini_quotes()
    snapshot = build_option_chain(
        "CRUDEOILM",
        date(2026, 10, 16),
        quotes,
        master,
        now=_now(),
        stale_after_seconds=120,
        rate=0.06,
        time_years=0.14,
        expiry_timestamp=None,
        expiry_time_source=ExpiryTimeSource.CONFIGURED_ASSUMPTION,
        vol_lower=1e-6,
        vol_upper=5.0,
        compute_greeks=False,
    )
    row_8000 = next(r for r in snapshot.rows if r.strike == 8000)
    assert row_8000.ce.missing is False
    assert row_8000.pe.missing is True
    row_8050 = next((r for r in snapshot.rows if r.strike == 8050), None)
    if row_8050:
        assert row_8050.ce.missing is True


def test_stale_and_zero_bid_and_crossed() -> None:
    quotes = _mini_quotes(stale=True)
    # zero bid on 8100 CE
    quotes[3000 + 8100] = quote(
        token=38100,
        symbol="CRUDEOILM26OCT8100CE",
        bid=0.0,
        ask=100.0,
        ltp=5.0,
        bid_qty=0,
        exchange_ts=_now() - timedelta(hours=5),
        last_trade_ts=_now() - timedelta(hours=5),
        received_at=_now(),
    )
    quotes[3000 + 8050] = quote(
        token=38050,
        symbol="CRUDEOILM26OCT8050CE",
        bid=20.0,
        ask=10.0,
        ltp=12.0,
        received_at=_now(),
        exchange_ts=_now() - timedelta(hours=5),
    )
    snapshot = build_option_chain(
        "CRUDEOILM",
        date(2026, 10, 16),
        quotes,
        crude_master(),
        now=_now(),
        stale_after_seconds=120,
        rate=0.06,
        time_years=0.14,
        expiry_timestamp=None,
        expiry_time_source=ExpiryTimeSource.CONFIGURED_ASSUMPTION,
        vol_lower=1e-6,
        vol_upper=5.0,
    )
    assert snapshot.rows
    stale_row = next(r for r in snapshot.rows if r.strike == 8000)
    assert stale_row.ce.quote_quality and stale_row.ce.quote_quality.is_stale
    assert stale_row.ce.greeks and stale_row.ce.greeks.iv_status == IVStatus.STALE_PRICE
    zero = next(r for r in snapshot.rows if r.strike == 8100)
    assert zero.ce.quote_quality and zero.ce.quote_quality.zero_bid
    assert zero.ce.derived_mid is None
    crossed = next(r for r in snapshot.rows if r.strike == 8050)
    assert crossed.ce.quote_quality and crossed.ce.quote_quality.crossed_market
    assert crossed.ce.derived_mid is None


def test_missing_oi_does_not_block_chain() -> None:
    quotes = _mini_quotes()
    quotes[3000 + 8000] = quote(
        token=38000,
        symbol="CRUDEOILM26OCT8000CE",
        bid=398,
        ask=402,
        ltp=400,
        oi=None,
        volume=None,
    )
    snapshot = build_option_chain(
        "CRUDEOILM",
        date(2026, 10, 16),
        quotes,
        crude_master(),
        now=_now(),
        stale_after_seconds=120,
        rate=0.06,
        time_years=0.14,
        expiry_timestamp=None,
        expiry_time_source=ExpiryTimeSource.CONFIGURED_ASSUMPTION,
        vol_lower=1e-6,
        vol_upper=5.0,
        compute_greeks=False,
    )
    row = next(r for r in snapshot.rows if r.strike == 8000)
    assert row.ce.quote_quality and not row.ce.quote_quality.has_oi
    assert snapshot.atm_straddle_mid is not None


def test_quote_batching_constant() -> None:
    assert chunked(["a"] * 3, 2) == [["a", "a"], ["a"]]


def test_rest_quote_normalization_preserves_depth() -> None:
    payload = {
        "instrument_token": 42,
        "last_price": 12.5,
        "last_quantity": 3,
        "average_price": 12.4,
        "volume": 100,
        "oi": 50,
        "oi_day_high": 60,
        "oi_day_low": 40,
        "buy_quantity": 10,
        "sell_quantity": 11,
        "timestamp": "2026-08-25 15:30:01",
        "last_trade_time": "2026-08-25 15:29:59",
        "ohlc": {"open": 11, "high": 13, "low": 10, "close": 11.5},
        "depth": {
            "buy": [{"price": 12.0, "quantity": 2, "orders": 1}] * 5,
            "sell": [{"price": 12.5, "quantity": 4, "orders": 2}] * 5,
        },
    }
    q = normalize_rest_quote(
        "CRUDEOILM26OCTFUT",
        payload,
        received_at=_now(),
        tz=ZoneInfo("Asia/Kolkata"),
    )
    assert q.instrument_token == 42
    assert len(q.depth.buy) == 5
    assert q.depth.best_bid() is not None
    assert q.exchange_timestamp is not None
    assert q.last_trade_timestamp is not None
    assert q.received_at.tzinfo is not None
