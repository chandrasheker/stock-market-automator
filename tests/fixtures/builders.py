"""Shared instrument / quote factories for unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from crude_research.market.models import DepthLevel, Instrument, MarketDepth, Quote, Underlying


def utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=UTC)


def instrument(
    *,
    token: int,
    symbol: str,
    name: str,
    instrument_type: str,
    expiry: date | None,
    strike: float = 0.0,
    lot_size: int = 10,
    tick_size: float = 0.05,
    underlying: Underlying | None = None,
    contract_month: str | None = None,
) -> Instrument:
    from crude_research.zerodha.instruments import classify_underlying, parse_contract_month

    parsed = parse_contract_month(symbol)
    return Instrument(
        instrument_token=token,
        exchange_token=token,
        tradingsymbol=symbol,
        name=name,
        last_price=0.0,
        expiry=expiry,
        strike=strike,
        tick_size=tick_size,
        lot_size=lot_size,
        instrument_type=instrument_type,
        segment="MCX",
        exchange="MCX",
        retrieved_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
        underlying=underlying if underlying is not None else classify_underlying(name, symbol),
        contract_month=contract_month if contract_month is not None else (parsed[1] if parsed else None),
    )


def quote(
    *,
    token: int,
    symbol: str,
    bid: float | None,
    ask: float | None,
    ltp: float | None,
    oi: float | None = 100.0,
    volume: int | None = 50,
    exchange_ts: datetime | None = None,
    last_trade_ts: datetime | None = None,
    received_at: datetime | None = None,
    bid_qty: int = 2,
    ask_qty: int = 2,
) -> Quote:
    buy = [DepthLevel(price=bid, quantity=bid_qty, orders=1)] if bid is not None else []
    sell = [DepthLevel(price=ask, quantity=ask_qty, orders=1)] if ask is not None else []
    received = received_at or datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    return Quote(
        instrument_token=token,
        tradingsymbol=symbol,
        last_price=ltp,
        last_quantity=1,
        average_price=ltp,
        volume=volume,
        oi=oi,
        oi_day_high=oi,
        oi_day_low=oi,
        open=ltp,
        high=ltp,
        low=ltp,
        close=ltp,
        total_buy_quantity=bid_qty,
        total_sell_quantity=ask_qty,
        depth=MarketDepth(buy=buy, sell=sell),
        exchange_timestamp=exchange_ts or received,
        last_trade_timestamp=last_trade_ts or received,
        received_at=received,
        source="test",
    )


def crude_master() -> list[Instrument]:
    """CRUDEOIL + CRUDEOILM futures/options plus an unrelated GOLD row."""
    oct_fut_exp = date(2026, 10, 19)
    oct_opt_exp = date(2026, 10, 16)
    nov_fut_exp = date(2026, 11, 19)
    nov_opt_exp = date(2026, 11, 17)
    records = [
        instrument(token=1, symbol="CRUDEOIL26OCTFUT", name="CRUDEOIL", instrument_type="FUT", expiry=oct_fut_exp, lot_size=100),
        instrument(token=2, symbol="CRUDEOILM26OCTFUT", name="CRUDEOILM", instrument_type="FUT", expiry=oct_fut_exp, lot_size=10),
        instrument(token=3, symbol="CRUDEOIL26NOVFUT", name="CRUDEOIL", instrument_type="FUT", expiry=nov_fut_exp, lot_size=100),
        instrument(token=4, symbol="CRUDEOILM26NOVFUT", name="CRUDEOILM", instrument_type="FUT", expiry=nov_fut_exp, lot_size=10),
        instrument(token=9, symbol="GOLD26OCTFUT", name="GOLD", instrument_type="FUT", expiry=oct_fut_exp, lot_size=1),
    ]
    for strike in (7900, 7950, 8000, 8050, 8100):
        records.append(
            instrument(
                token=1000 + int(strike),
                symbol=f"CRUDEOIL26OCT{int(strike)}CE",
                name="CRUDEOIL",
                instrument_type="CE",
                expiry=oct_opt_exp,
                strike=float(strike),
                lot_size=100,
            )
        )
        records.append(
            instrument(
                token=2000 + int(strike),
                symbol=f"CRUDEOIL26OCT{int(strike)}PE",
                name="CRUDEOIL",
                instrument_type="PE",
                expiry=oct_opt_exp,
                strike=float(strike),
                lot_size=100,
            )
        )
        records.append(
            instrument(
                token=3000 + int(strike),
                symbol=f"CRUDEOILM26OCT{int(strike)}CE",
                name="CRUDEOILM",
                instrument_type="CE",
                expiry=oct_opt_exp,
                strike=float(strike),
                lot_size=10,
            )
        )
        records.append(
            instrument(
                token=4000 + int(strike),
                symbol=f"CRUDEOILM26OCT{int(strike)}PE",
                name="CRUDEOILM",
                instrument_type="PE",
                expiry=oct_opt_exp,
                strike=float(strike),
                lot_size=10,
            )
        )
    records.append(
        instrument(
            token=5001,
            symbol="CRUDEOILM26NOV7950CE",
            name="CRUDEOILM",
            instrument_type="CE",
            expiry=nov_opt_exp,
            strike=7950,
            lot_size=10,
        )
    )
    records.append(
        instrument(
            token=5002,
            symbol="CRUDEOILM26NOV7950PE",
            name="CRUDEOILM",
            instrument_type="PE",
            expiry=nov_opt_exp,
            strike=7950,
            lot_size=10,
        )
    )
    return records
