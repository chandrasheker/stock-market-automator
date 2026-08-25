"""Strongly typed market-data models. Raw broker fields stay distinct from derived fields."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


class Underlying(StrEnum):
    CRUDEOIL = "CRUDEOIL"
    CRUDEOILM = "CRUDEOILM"


class InstrumentType(StrEnum):
    FUT = "FUT"
    CE = "CE"
    PE = "PE"


class PriceSource(StrEnum):
    MID = "MID"
    LTP_FALLBACK = "LTP_FALLBACK"
    UNAVAILABLE = "UNAVAILABLE"


class StraddlePriceSource(StrEnum):
    MID = "MID"
    MIXED = "MIXED"
    LTP_FALLBACK = "LTP_FALLBACK"
    UNAVAILABLE = "UNAVAILABLE"


class IVStatus(StrEnum):
    OK = "OK"
    NO_PRICE = "NO_PRICE"
    STALE_PRICE = "STALE_PRICE"
    BELOW_INTRINSIC_BOUND = "BELOW_INTRINSIC_BOUND"
    ABOVE_MODEL_BOUND = "ABOVE_MODEL_BOUND"
    NO_CONVERGENCE = "NO_CONVERGENCE"
    INVALID_INPUT = "INVALID_INPUT"


class ExpiryTimeSource(StrEnum):
    EXPLICIT_TIMESTAMP = "EXPLICIT_TIMESTAMP"
    CONFIGURED_ASSUMPTION = "CONFIGURED_ASSUMPTION"


class SnapshotQuality(StrEnum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    POOR = "POOR"
    UNAVAILABLE = "UNAVAILABLE"


class FutureMappingRule(StrEnum):
    CONTRACT_MONTH_FROM_TRADINGSYMBOL = "CONTRACT_MONTH_FROM_TRADINGSYMBOL"


class DepthLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: float
    quantity: int
    orders: int = 0


class MarketDepth(BaseModel):
    model_config = ConfigDict(frozen=True)

    buy: list[DepthLevel] = Field(default_factory=list)
    sell: list[DepthLevel] = Field(default_factory=list)

    def best_bid(self) -> DepthLevel | None:
        live = [level for level in self.buy if level.price > 0 and level.quantity > 0]
        return live[0] if live else None

    def best_ask(self) -> DepthLevel | None:
        live = [level for level in self.sell if level.price > 0 and level.quantity > 0]
        return live[0] if live else None


class Instrument(BaseModel):
    """Normalized Zerodha instrument row plus conservative crude classification."""

    model_config = ConfigDict(frozen=True)

    instrument_token: int
    exchange_token: int
    tradingsymbol: str
    name: str
    last_price: float
    expiry: date | None
    strike: float
    tick_size: float
    lot_size: int
    instrument_type: str
    segment: str
    exchange: str
    retrieved_at: datetime
    underlying: Underlying | None = None
    contract_month: str | None = None

    @property
    def kite_quote_key(self) -> str:
        return f"{self.exchange}:{self.tradingsymbol}"

    @property
    def is_future(self) -> bool:
        return self.instrument_type.upper() == InstrumentType.FUT

    @property
    def is_call(self) -> bool:
        return self.instrument_type.upper() == InstrumentType.CE

    @property
    def is_put(self) -> bool:
        return self.instrument_type.upper() == InstrumentType.PE

    @property
    def is_option(self) -> bool:
        return self.is_call or self.is_put


class Quote(BaseModel):
    """Full-quote snapshot. Timestamps are stored separately and never mixed."""

    model_config = ConfigDict(frozen=True)

    instrument_token: int
    tradingsymbol: str
    last_price: float | None = None
    last_quantity: int | None = None
    average_price: float | None = None
    volume: int | None = None
    oi: float | None = None
    oi_day_high: float | None = None
    oi_day_low: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    total_buy_quantity: int | None = None
    total_sell_quantity: int | None = None
    depth: MarketDepth = Field(default_factory=MarketDepth)
    exchange_timestamp: datetime | None = None
    last_trade_timestamp: datetime | None = None
    received_at: datetime
    source: str = "rest_quote"


class QuoteQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    has_bid: bool
    has_ask: bool
    valid_bid_ask: bool
    crossed_market: bool
    zero_bid: bool
    spread: float | None
    spread_pct: float | None
    mid_price: float | None
    best_bid: float | None
    best_ask: float | None
    quote_age_seconds: float | None
    last_trade_age_seconds: float | None
    is_stale: bool
    has_oi: bool
    has_volume: bool
    research_price: float | None
    price_source: PriceSource
    notes: tuple[str, ...] = ()


class OptionGreeks(BaseModel):
    """Black-76 greeks. Units are documented on the fields."""

    model_config = ConfigDict(frozen=True)

    iv: float | None
    iv_status: IVStatus
    iv_price: float | None
    iv_price_source: PriceSource | None
    delta: float | None = Field(
        default=None,
        description="Discounted futures delta: dV/dF = exp(-rT) * N(d1) for calls.",
    )
    gamma: float | None = Field(
        default=None,
        description="d²V/dF². Same for calls and puts.",
    )
    theta: float | None = Field(
        default=None,
        description="Calendar decay of option premium per year (T in years decreasing).",
    )
    theta_per_day: float | None = Field(
        default=None,
        description="theta / 365. Display convenience, not a day-count convention.",
    )
    vega: float | None = Field(
        default=None,
        description="dV/dσ for a +1.00 move in volatility (e.g. 0.20 -> 1.20).",
    )
    vega_1pct: float | None = Field(
        default=None,
        description="vega / 100, i.e. premium change for +0.01 (one vol point).",
    )
    d1: float | None = None
    d2: float | None = None
    risk_free_rate: float | None = None
    time_to_expiry: float | None = None
    futures_price: float | None = None
    strike: float | None = None


class OptionSideSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    missing: bool = False
    token: int | None = None
    symbol: str | None = None
    raw_bid: float | None = None
    raw_ask: float | None = None
    raw_ltp: float | None = None
    derived_mid: float | None = None
    volume: int | None = None
    oi: float | None = None
    depth: MarketDepth | None = None
    exchange_timestamp: datetime | None = None
    last_trade_timestamp: datetime | None = None
    received_at: datetime | None = None
    quote_quality: QuoteQuality | None = None
    distance_points: float | None = None
    distance_pct: float | None = None
    straddle_distance_ratio: float | None = None
    greeks: OptionGreeks | None = None

    @classmethod
    def missing_side(cls) -> Self:
        return cls(missing=True)


class StrikeRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    ce: OptionSideSnapshot
    pe: OptionSideSnapshot


class OptionChainSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: Underlying
    option_expiry: date
    underlying_future_symbol: str
    underlying_future_token: int
    future_price: float | None
    future_price_source: PriceSource
    future_mapping_rule: FutureMappingRule
    snapshot_timestamp: datetime
    strike_interval: float | None
    available_strikes: list[float]
    atm_strike: float | None
    atm_ce_mid: float | None
    atm_pe_mid: float | None
    atm_straddle_mid: float | None
    atm_ce_ltp: float | None
    atm_pe_ltp: float | None
    atm_straddle_ltp: float | None
    straddle_price_source: StraddlePriceSource
    snapshot_quality: SnapshotQuality
    risk_free_rate: float | None
    expiry_timestamp: datetime | None
    expiry_time_source: ExpiryTimeSource
    time_to_expiry: float | None
    rows: list[StrikeRow]
    notes: tuple[str, ...] = ()
