"""CRUDEOIL / CRUDEOILM option-chain reconstruction and ATM / straddle metrics."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Literal

from crude_research.market.contracts import (
    list_options,
    parse_underlying,
    resolve_underlying_future,
)
from crude_research.market.models import (
    ExpiryTimeSource,
    FutureMappingRule,
    Instrument,
    OptionChainSnapshot,
    OptionGreeks,
    OptionSideSnapshot,
    PriceSource,
    Quote,
    SnapshotQuality,
    StraddlePriceSource,
    StrikeRow,
    Underlying,
)
from crude_research.market.quote_quality import assess_quote
from crude_research.quant.implied_vol import greeks_from_solve, solve_implied_vol

log = logging.getLogger(__name__)


def derive_strike_interval(strikes: Sequence[float]) -> float | None:
    """Return the unique adjacent-strike gap, or None if the ladder is irregular.

    No ₹50 (or any other) interval is assumed.
    """
    unique = sorted({round(float(strike), 10) for strike in strikes})
    if len(unique) < 2:
        return None
    diffs = [round(unique[i + 1] - unique[i], 10) for i in range(len(unique) - 1)]
    first = diffs[0]
    if all(abs(diff - first) < 1e-9 for diff in diffs) and first > 0:
        return first
    return None


def select_atm_strike(future_price: float, strikes: Sequence[float]) -> float:
    """Closest listed strike to the mapped futures price.

    Tie-break: if two strikes are equally distant, the **lower** strike is chosen.
    Example: F=8025, strikes {8000, 8050} → 8000.
    """
    if not strikes:
        raise ValueError("Cannot select ATM from an empty strike list")
    return min(strikes, key=lambda strike: (abs(strike - future_price), strike))


def _side_snapshot(
    instrument: Instrument | None,
    quote: Quote | None,
    *,
    stale_after_seconds: float,
    now: datetime,
    future_price: float | None,
    atm_straddle_mid: float | None,
    rate: float | None,
    time_years: float | None,
    vol_lower: float,
    vol_upper: float,
    compute_greeks: bool,
) -> OptionSideSnapshot:
    if instrument is None:
        return OptionSideSnapshot.missing_side()

    quality = assess_quote(quote, stale_after_seconds=stale_after_seconds, now=now) if quote else None
    bid = quality.best_bid if quality else None
    ask = quality.best_ask if quality else None
    mid = quality.mid_price if quality else None
    ltp = quote.last_price if quote else None
    distance_points = None
    distance_pct = None
    straddle_distance_ratio = None
    if future_price is not None and future_price != 0:
        distance_points = abs(instrument.strike - future_price)
        distance_pct = distance_points / future_price
        if atm_straddle_mid is not None and atm_straddle_mid > 0:
            straddle_distance_ratio = distance_points / atm_straddle_mid

    greeks: OptionGreeks | None = None
    if compute_greeks:
        option_type: Literal["CE", "PE"] = "CE" if instrument.is_call else "PE"
        if (
            rate is None
            or time_years is None
            or time_years <= 0
            or future_price is None
            or future_price <= 0
            or quality is None
            or quality.research_price is None
            or quality.price_source == PriceSource.UNAVAILABLE
        ):
            from crude_research.market.models import IVStatus

            status = (
                IVStatus.INVALID_INPUT
                if rate is None or time_years is None or time_years <= 0 or future_price is None
                else IVStatus.NO_PRICE
            )
            greeks = OptionGreeks(
                iv=None,
                iv_status=status,
                iv_price=quality.research_price if quality else None,
                iv_price_source=quality.price_source if quality else None,
                risk_free_rate=rate,
                time_to_expiry=time_years,
                futures_price=future_price,
                strike=instrument.strike,
            )
        else:
            solved = solve_implied_vol(
                option_type,
                quality.research_price,
                future_price,
                instrument.strike,
                time_years,
                rate,
                vol_lower=vol_lower,
                vol_upper=vol_upper,
                price_source=quality.price_source,
                is_stale=quality.is_stale,
            )
            greeks = greeks_from_solve(
                solved,
                rate=rate,
                time_to_expiry=time_years,
                forward=future_price,
                strike=instrument.strike,
            )

    return OptionSideSnapshot(
        missing=False,
        token=instrument.instrument_token,
        symbol=instrument.tradingsymbol,
        raw_bid=bid,
        raw_ask=ask,
        raw_ltp=ltp,
        derived_mid=mid,
        volume=quote.volume if quote else None,
        oi=quote.oi if quote else None,
        depth=quote.depth if quote else None,
        exchange_timestamp=quote.exchange_timestamp if quote else None,
        last_trade_timestamp=quote.last_trade_timestamp if quote else None,
        received_at=quote.received_at if quote else None,
        quote_quality=quality,
        distance_points=distance_points,
        distance_pct=distance_pct,
        straddle_distance_ratio=straddle_distance_ratio,
        greeks=greeks,
    )


def _straddle_source(ce: OptionSideSnapshot, pe: OptionSideSnapshot) -> tuple[StraddlePriceSource, float | None, float | None, float | None]:
    ce_mid = ce.derived_mid
    pe_mid = pe.derived_mid
    ce_ltp = ce.raw_ltp if ce.raw_ltp is not None and ce.raw_ltp > 0 else None
    pe_ltp = pe.raw_ltp if pe.raw_ltp is not None and pe.raw_ltp > 0 else None
    if ce_mid is not None and pe_mid is not None:
        return StraddlePriceSource.MID, ce_mid, pe_mid, ce_mid + pe_mid
    if (ce_mid is not None and pe_ltp is not None) or (pe_mid is not None and ce_ltp is not None):
        ce_leg = ce_mid if ce_mid is not None else ce_ltp
        pe_leg = pe_mid if pe_mid is not None else pe_ltp
        if ce_leg is None or pe_leg is None:
            return StraddlePriceSource.UNAVAILABLE, ce_mid, pe_mid, None
        return StraddlePriceSource.MIXED, ce_mid, pe_mid, ce_leg + pe_leg
    if ce_ltp is not None and pe_ltp is not None:
        return StraddlePriceSource.LTP_FALLBACK, ce_mid, pe_mid, ce_ltp + pe_ltp
    return StraddlePriceSource.UNAVAILABLE, ce_mid, pe_mid, None


def _snapshot_quality(
    *,
    future_source: PriceSource,
    future_stale: bool,
    straddle_source: StraddlePriceSource,
    atm_strike: float | None,
    rows: Sequence[StrikeRow],
) -> SnapshotQuality:
    if future_source == PriceSource.UNAVAILABLE or atm_strike is None:
        return SnapshotQuality.UNAVAILABLE
    stale_count = 0
    missing_mids = 0
    for row in rows:
        for side in (row.ce, row.pe):
            if side.missing or side.quote_quality is None:
                missing_mids += 1
                continue
            if side.quote_quality.is_stale:
                stale_count += 1
            if not side.quote_quality.valid_bid_ask:
                missing_mids += 1
    if (
        future_source == PriceSource.MID
        and not future_stale
        and straddle_source == StraddlePriceSource.MID
        and stale_count == 0
    ):
        return SnapshotQuality.GOOD
    if straddle_source == StraddlePriceSource.UNAVAILABLE or future_stale or stale_count > max(4, len(rows)):
        return SnapshotQuality.POOR
    return SnapshotQuality.DEGRADED


def build_option_chain(
    underlying: str,
    expiry: date,
    quotes: Mapping[int, Quote],
    instruments: Sequence[Instrument],
    *,
    now: datetime,
    stale_after_seconds: float,
    rate: float | None,
    time_years: float | None,
    expiry_timestamp: datetime | None,
    expiry_time_source: ExpiryTimeSource,
    vol_lower: float,
    vol_upper: float,
    compute_greeks: bool = True,
) -> OptionChainSnapshot:
    target: Underlying = parse_underlying(underlying)
    future = resolve_underlying_future(
        instruments, underlying=target.value, option_expiry=expiry
    )
    options = list_options(instruments, target.value, expiry)
    future_quote = quotes.get(future.instrument_token)
    future_quality = (
        assess_quote(future_quote, stale_after_seconds=stale_after_seconds, now=now)
        if future_quote
        else None
    )
    future_price = future_quality.research_price if future_quality else None
    future_source = future_quality.price_source if future_quality else PriceSource.UNAVAILABLE
    future_stale = future_quality.is_stale if future_quality else True

    by_strike: dict[float, dict[str, Instrument]] = {}
    for option in options:
        by_strike.setdefault(option.strike, {})
        key = "CE" if option.is_call else "PE"
        by_strike[option.strike][key] = option
    strikes = sorted(by_strike)
    atm_strike = select_atm_strike(future_price, strikes) if future_price is not None and strikes else None

    # First pass: sides without distance/straddle ratios or greeks that need ATM straddle.
    prelim_rows: list[tuple[float, OptionSideSnapshot, OptionSideSnapshot]] = []
    for strike in strikes:
        pair = by_strike[strike]
        ce = _side_snapshot(
            pair.get("CE"),
            quotes.get(pair["CE"].instrument_token) if "CE" in pair else None,
            stale_after_seconds=stale_after_seconds,
            now=now,
            future_price=future_price,
            atm_straddle_mid=None,
            rate=None,
            time_years=None,
            vol_lower=vol_lower,
            vol_upper=vol_upper,
            compute_greeks=False,
        )
        pe = _side_snapshot(
            pair.get("PE"),
            quotes.get(pair["PE"].instrument_token) if "PE" in pair else None,
            stale_after_seconds=stale_after_seconds,
            now=now,
            future_price=future_price,
            atm_straddle_mid=None,
            rate=None,
            time_years=None,
            vol_lower=vol_lower,
            vol_upper=vol_upper,
            compute_greeks=False,
        )
        prelim_rows.append((strike, ce, pe))

    atm_ce = next((ce for strike, ce, _pe in prelim_rows if strike == atm_strike), None)
    atm_pe = next((pe for strike, _ce, pe in prelim_rows if strike == atm_strike), None)
    if atm_ce is not None and atm_pe is not None:
        straddle_source, atm_ce_mid, atm_pe_mid, atm_straddle_mid = _straddle_source(atm_ce, atm_pe)
        atm_ce_ltp = atm_ce.raw_ltp if atm_ce.raw_ltp and atm_ce.raw_ltp > 0 else None
        atm_pe_ltp = atm_pe.raw_ltp if atm_pe.raw_ltp and atm_pe.raw_ltp > 0 else None
        atm_straddle_ltp = (
            atm_ce_ltp + atm_pe_ltp if atm_ce_ltp is not None and atm_pe_ltp is not None else None
        )
    else:
        straddle_source = StraddlePriceSource.UNAVAILABLE
        atm_ce_mid = atm_pe_mid = atm_straddle_mid = None
        atm_ce_ltp = atm_pe_ltp = atm_straddle_ltp = None

    rows: list[StrikeRow] = []
    for strike, _ce0, _pe0 in prelim_rows:
        pair = by_strike[strike]
        ce = _side_snapshot(
            pair.get("CE"),
            quotes.get(pair["CE"].instrument_token) if "CE" in pair else None,
            stale_after_seconds=stale_after_seconds,
            now=now,
            future_price=future_price,
            atm_straddle_mid=atm_straddle_mid,
            rate=rate,
            time_years=time_years,
            vol_lower=vol_lower,
            vol_upper=vol_upper,
            compute_greeks=compute_greeks,
        )
        pe = _side_snapshot(
            pair.get("PE"),
            quotes.get(pair["PE"].instrument_token) if "PE" in pair else None,
            stale_after_seconds=stale_after_seconds,
            now=now,
            future_price=future_price,
            atm_straddle_mid=atm_straddle_mid,
            rate=rate,
            time_years=time_years,
            vol_lower=vol_lower,
            vol_upper=vol_upper,
            compute_greeks=compute_greeks,
        )
        rows.append(StrikeRow(strike=strike, ce=ce, pe=pe))

    quality = _snapshot_quality(
        future_source=future_source,
        future_stale=future_stale,
        straddle_source=straddle_source,
        atm_strike=atm_strike,
        rows=rows,
    )
    notes: list[str] = [
        f"future_mapping={FutureMappingRule.CONTRACT_MONTH_FROM_TRADINGSYMBOL.value}",
        f"expiry_time_source={expiry_time_source.value}",
    ]
    if future_source == PriceSource.LTP_FALLBACK:
        notes.append("FUTURE_LTP_FALLBACK")
    if straddle_source != StraddlePriceSource.MID:
        notes.append(f"STRADDLE_{straddle_source.value}")
    if derive_strike_interval(strikes) is None and len(strikes) >= 2:
        notes.append("IRREGULAR_STRIKE_LADDER")

    return OptionChainSnapshot(
        underlying=target,
        option_expiry=expiry,
        underlying_future_symbol=future.tradingsymbol,
        underlying_future_token=future.instrument_token,
        future_price=future_price,
        future_price_source=future_source,
        future_mapping_rule=FutureMappingRule.CONTRACT_MONTH_FROM_TRADINGSYMBOL,
        snapshot_timestamp=now,
        strike_interval=derive_strike_interval(strikes),
        available_strikes=strikes,
        atm_strike=atm_strike,
        atm_ce_mid=atm_ce_mid,
        atm_pe_mid=atm_pe_mid,
        atm_straddle_mid=atm_straddle_mid,
        atm_ce_ltp=atm_ce_ltp,
        atm_pe_ltp=atm_pe_ltp,
        atm_straddle_ltp=atm_straddle_ltp,
        straddle_price_source=straddle_source,
        snapshot_quality=quality,
        risk_free_rate=rate,
        expiry_timestamp=expiry_timestamp,
        expiry_time_source=expiry_time_source,
        time_to_expiry=time_years,
        rows=rows,
        notes=tuple(notes),
    )
