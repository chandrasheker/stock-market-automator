"""M5: hard-gated option candidate + defined-risk hedge. Does not place orders."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from crude_research.bias.engine import BiasSnapshot
from crude_research.config import Settings
from crude_research.market.models import OptionChainSnapshot, OptionSideSnapshot, StrikeRow
from crude_research.quant.time import parse_timezone
from crude_research.strategy import reasons
from crude_research.strategy.costs import CostEstimate, economics_ok, estimate_round_trip
from crude_research.strategy.margins import MarginProvider


@dataclass(frozen=True)
class HedgeSpec:
    symbol: str
    token: int
    strike: float
    side: str
    ask: float
    delta: float
    width: float
    net_credit: float
    max_defined_loss: float
    basket_final_margin: float
    net_credit_to_basket: float
    target_profit_to_basket: float


@dataclass(frozen=True)
class Candidate:
    symbol: str
    token: int
    strike: float
    side: str
    bid: float
    ask: float
    delta: float
    iv: float | None
    distance_points: float
    distance_ratio: float
    short_premium_value: float
    standalone_short_margin: float
    short_premium_margin_ratio: float
    lot_quantity: float
    lots: int
    hedge: HedgeSpec
    costs: CostEstimate
    expiry: date
    dte: int
    future_price: float
    atm_strike: float
    atm_straddle_mid: float


@dataclass
class CandidateDecision:
    status: str
    reasons: list[str] = field(default_factory=list)
    candidate: Candidate | None = None
    rejected: list[dict[str, object]] = field(default_factory=list)
    record: dict[str, object] = field(default_factory=dict)

    @property
    def qualified(self) -> bool:
        return self.status == reasons.QUALIFIED and self.candidate is not None


def calendar_dte(now: datetime, expiry: date, tz: ZoneInfo) -> int:
    return (expiry - now.astimezone(tz).date()).days


def _side_of_bias(bias: str) -> str | None:
    if bias == "BULLISH":
        return "PE"
    if bias == "BEARISH":
        return "CE"
    return None


def _pick_side(row: StrikeRow, side: str) -> OptionSideSnapshot:
    return row.pe if side == "PE" else row.ce


def _liquidity_fail(side: OptionSideSnapshot, settings: Settings, *, stale_seconds: float) -> str | None:
    quality = side.quote_quality
    if side.missing or quality is None:
        return reasons.POOR_LIQUIDITY
    if quality.is_stale or (quality.quote_age_seconds or 0) > stale_seconds:
        return reasons.STALE_DATA
    if not quality.valid_bid_ask or quality.crossed_market:
        return reasons.POOR_LIQUIDITY
    if quality.spread_pct is not None and quality.spread_pct > settings.max_spread_pct:
        return reasons.POOR_LIQUIDITY
    if (side.oi or 0) < settings.min_oi or (side.volume or 0) < settings.min_volume:
        return reasons.POOR_LIQUIDITY
    if quality.best_bid is None or quality.best_ask is None:
        return reasons.POOR_LIQUIDITY
    return None


def _farther_otm(side: str, short_strike: float, hedge_strike: float) -> bool:
    if side == "PE":
        return hedge_strike < short_strike
    return hedge_strike > short_strike


def evaluate_candidate(
    *,
    bias: BiasSnapshot,
    chain: OptionChainSnapshot,
    settings: Settings,
    now: datetime,
    margin: MarginProvider,
    lots: int = 1,
    equity: float | None = None,
    underlying: str = "CRUDEOILM",
    live_entry: bool = False,
) -> CandidateDecision:
    tz = parse_timezone(settings.timezone)
    rejected: list[dict[str, object]] = []
    codes: list[str] = []

    def finish(status: str, extra: list[str] | None = None, candidate: Candidate | None = None) -> CandidateDecision:
        why = list(codes)
        if extra:
            why.extend(extra)
        record = _base_record(bias, chain, now, why, status, candidate)
        return CandidateDecision(status=status, reasons=why, candidate=candidate, rejected=rejected, record=record)

    if underlying == "CRUDEOIL" and not settings.enable_crudeoil:
        codes.append(reasons.CRUDEOIL_DISABLED)
        return finish(reasons.CRUDEOIL_DISABLED)
    if bias.bias == "NEUTRAL" or "BIAS_NEUTRAL" in bias.no_trade_reasons:
        codes.append(reasons.BIAS_NEUTRAL)
        return finish(reasons.BIAS_NEUTRAL)
    if bias.volatility == "EXTREME" or "EXTREME_VOLATILITY" in bias.no_trade_reasons:
        codes.append(reasons.EXTREME_VOLATILITY)
        return finish(reasons.EXTREME_VOLATILITY)
    if bias.model_health == "DEGRADED" or "MODEL_HEALTH_DEGRADED" in bias.no_trade_reasons:
        codes.append(reasons.MODEL_HEALTH_DEGRADED)
        return finish(reasons.MODEL_HEALTH_DEGRADED)
    if live_entry and not bias.allow_live_entry:
        if bias.model_health == "WARMING_UP":
            codes.append(reasons.MODEL_HEALTH_WARMING_UP)
        codes.extend(r for r in bias.no_trade_reasons if r not in codes)
        return finish(codes[0] if codes else reasons.MODEL_HEALTH_WARMING_UP)
    if not bias.allow_entry:
        codes.extend(bias.no_trade_reasons or (reasons.BIAS_NEUTRAL,))
        return finish(codes[0] if codes else reasons.BIAS_NEUTRAL)

    dte = calendar_dte(now, chain.option_expiry, tz)
    if dte < settings.dte_min or dte > settings.dte_max:
        codes.append(reasons.NO_ELIGIBLE_EXPIRY)
        return finish(reasons.NO_ELIGIBLE_EXPIRY)

    side = _side_of_bias(bias.bias)
    if side is None:
        codes.append(reasons.BIAS_NEUTRAL)
        return finish(reasons.BIAS_NEUTRAL)

    stale_limit = min(settings.stale_quote_seconds, settings.live_stale_quote_seconds)
    if chain.snapshot_quality.value in {"POOR", "UNAVAILABLE"}:
        codes.append(reasons.STALE_DATA)
        return finish(reasons.STALE_DATA)
    if chain.atm_straddle_mid is None or chain.atm_straddle_mid <= 0 or chain.future_price is None:
        codes.append(reasons.NO_ATM_STRADDLE)
        return finish(reasons.NO_ATM_STRADDLE)
    if chain.atm_strike is None:
        codes.append(reasons.NO_ATM_STRADDLE)
        return finish(reasons.NO_ATM_STRADDLE)

    passed: list[tuple[StrikeRow, OptionSideSnapshot, float, float, float]] = []
    for row in chain.rows:
        opt = _pick_side(row, side)
        fail = _liquidity_fail(opt, settings, stale_seconds=stale_limit)
        if fail:
            rejected.append({"strike": row.strike, "reason": fail, "symbol": opt.symbol})
            continue
        delta = opt.greeks.delta if opt.greeks else None
        if delta is None:
            rejected.append({"strike": row.strike, "reason": reasons.NO_DELTA_MATCH, "symbol": opt.symbol})
            continue
        abs_delta = abs(delta)
        if abs_delta < settings.delta_min or abs_delta > settings.delta_max:
            rejected.append({"strike": row.strike, "reason": reasons.NO_DELTA_MATCH, "symbol": opt.symbol})
            continue
        ratio = opt.straddle_distance_ratio
        if ratio is None:
            rejected.append({"strike": row.strike, "reason": reasons.DISTANCE_TOO_SMALL, "symbol": opt.symbol})
            continue
        if ratio < settings.distance_ratio_min:
            rejected.append({"strike": row.strike, "reason": reasons.DISTANCE_TOO_SMALL, "symbol": opt.symbol})
            continue
        if ratio > settings.distance_ratio_max:
            rejected.append({"strike": row.strike, "reason": reasons.DISTANCE_TOO_LARGE, "symbol": opt.symbol})
            continue
        bid = opt.quote_quality.best_bid if opt.quote_quality else None
        ask = opt.quote_quality.best_ask if opt.quote_quality else None
        if bid is None or ask is None or opt.token is None or opt.symbol is None:
            rejected.append({"strike": row.strike, "reason": reasons.POOR_LIQUIDITY, "symbol": opt.symbol})
            continue
        lot_qty = float(lots) * 10.0
        # lot size from first option on the chain: use instrument lot via premium * lots * inferred
        lot_qty = _lot_quantity(chain, lots)
        premium_value = bid * lot_qty
        standalone = margin.standalone_short_margin(
            exchange="MCX", tradingsymbol=opt.symbol, quantity=lots, price=bid
        )
        if standalone <= 0:
            rejected.append({"strike": row.strike, "reason": reasons.PREMIUM_MARGIN_TOO_HIGH, "symbol": opt.symbol})
            continue
        prem_ratio = premium_value / standalone
        if prem_ratio > settings.premium_margin_max:
            rejected.append(
                {"strike": row.strike, "reason": reasons.PREMIUM_MARGIN_TOO_HIGH, "symbol": opt.symbol}
            )
            continue
        passed.append((row, opt, bid, prem_ratio, abs_delta))

    if not passed:
        why = _majority_reason(rejected) or reasons.NO_DELTA_MATCH
        codes.append(why)
        return finish(why)

    def rank_key(item: tuple[StrikeRow, OptionSideSnapshot, float, float, float]) -> tuple[float, float, float]:
        _row, opt, _bid, prem_ratio, abs_d = item
        dist = -(opt.straddle_distance_ratio or 0.0)
        preferred = 0.0
        low, high = settings.premium_margin_preferred_low, settings.premium_margin_preferred_high
        if not (low <= prem_ratio <= high):
            preferred = abs(prem_ratio - 0.075)
        return (abs_d, dist, preferred)

    passed.sort(key=rank_key)
    chosen_row, chosen, bid, prem_ratio, _abs_delta = passed[0]
    assert chosen.quote_quality is not None
    ask = chosen.quote_quality.best_ask
    assert ask is not None and chosen.symbol is not None and chosen.token is not None
    lot_qty = _lot_quantity(chain, lots)
    hedge = _select_hedge(
        chain,
        settings,
        margin,
        short=chosen,
        short_bid=bid,
        side=side,
        lots=lots,
        lot_qty=lot_qty,
        stale_limit=stale_limit,
        capture=settings.short_premium_capture_target,
    )
    if hedge is None:
        codes.append(reasons.NO_SUITABLE_HEDGE)
        return finish(reasons.NO_SUITABLE_HEDGE)

    short_spread = ask - bid
    long_spread = 0.0
    long_row = next(r for r in chain.rows if r.strike == hedge.strike)
    long_side = _pick_side(long_row, side)
    if long_side.quote_quality and long_side.quote_quality.spread is not None:
        long_spread = long_side.quote_quality.spread
    legs = [
        _leg("BUY", hedge.symbol, lots, hedge.ask),
        _leg("SELL", chosen.symbol, lots, bid),
    ]
    costs = estimate_round_trip(
        short_entry=bid,
        long_entry=hedge.ask,
        lot_quantity=lot_qty,
        capture=settings.short_premium_capture_target,
        settings=settings,
        live_charges=margin.live_charges(legs),
        short_spread=short_spread,
        long_spread=long_spread,
    )
    if not economics_ok(costs, settings):
        codes.append(reasons.ECONOMICS_UNATTRACTIVE)
        return finish(reasons.ECONOMICS_UNATTRACTIVE)

    cap = equity if equity is not None else settings.account_equity
    if cap is None or cap <= 0:
        # Cannot prove 0.5% defined-loss budget without equity. Fail closed for live-like eval.
        codes.append(reasons.RISK_LIMIT)
        return finish(reasons.RISK_LIMIT)
    if hedge.max_defined_loss > settings.max_defined_loss_pct * cap:
        codes.append(reasons.RISK_LIMIT)
        return finish(reasons.RISK_LIMIT)

    candidate = Candidate(
        symbol=chosen.symbol,
        token=chosen.token,
        strike=chosen_row.strike,
        side=side,
        bid=bid,
        ask=ask,
        delta=chosen.greeks.delta if chosen.greeks and chosen.greeks.delta is not None else 0.0,
        iv=chosen.greeks.iv if chosen.greeks else None,
        distance_points=chosen.distance_points or 0.0,
        distance_ratio=chosen.straddle_distance_ratio or 0.0,
        short_premium_value=bid * lot_qty,
        standalone_short_margin=margin.standalone_short_margin(
            exchange="MCX", tradingsymbol=chosen.symbol, quantity=lots, price=bid
        ),
        short_premium_margin_ratio=prem_ratio,
        lot_quantity=lot_qty,
        lots=lots,
        hedge=hedge,
        costs=costs,
        expiry=chain.option_expiry,
        dte=dte,
        future_price=chain.future_price or 0.0,
        atm_strike=chain.atm_strike,
        atm_straddle_mid=chain.atm_straddle_mid,
    )
    return finish(reasons.QUALIFIED, candidate=candidate)


def _lot_quantity(chain: OptionChainSnapshot, lots: int) -> float:
    for row in chain.rows:
        for side in (row.ce, row.pe):
            if side.symbol and "CRUDEOILM" in side.symbol:
                return float(lots * 10)
            if side.symbol and "CRUDEOIL" in side.symbol:
                return float(lots * 100)
    return float(lots * 10)


def _leg(txn: str, symbol: str, quantity: int, price: float) -> dict[str, object]:
    return {
        "exchange": "MCX",
        "tradingsymbol": symbol,
        "transaction_type": txn,
        "variety": "regular",
        "product": "NRML",
        "order_type": "LIMIT",
        "quantity": quantity,
        "price": price,
    }


def _majority_reason(rejected: Sequence[dict[str, object]]) -> str | None:
    counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("reason") or "")
        counts[reason] = counts.get(reason, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda key: counts[key])


def _select_hedge(
    chain: OptionChainSnapshot,
    settings: Settings,
    margin: MarginProvider,
    *,
    short: OptionSideSnapshot,
    short_bid: float,
    side: str,
    lots: int,
    lot_qty: float,
    stale_limit: float,
    capture: float,
) -> HedgeSpec | None:
    assert short.symbol is not None
    best: HedgeSpec | None = None
    short_k = _short_strike(chain, short)
    for row in chain.rows:
        opt = _pick_side(row, side)
        if opt.symbol is None or opt.token is None or opt.greeks is None or opt.greeks.delta is None:
            continue
        if opt.symbol == short.symbol:
            continue
        if not _farther_otm(side, short_k, row.strike):
            continue
        abs_d = abs(opt.greeks.delta)
        if abs_d < settings.hedge_delta_min or abs_d > settings.hedge_delta_max:
            continue
        if _liquidity_fail(opt, settings, stale_seconds=stale_limit):
            continue
        ask = opt.quote_quality.best_ask if opt.quote_quality else None
        if ask is None or ask <= 0:
            continue
        width = abs(row.strike - _short_strike(chain, short))
        net_credit = short_bid - ask
        if net_credit <= 0:
            continue
        max_loss = max(0.0, (width - net_credit) * lot_qty)
        basket = margin.basket_final_margin(
            [
                _leg("BUY", opt.symbol, lots, ask),
                _leg("SELL", short.symbol, lots, short_bid),
            ]
        )
        target = capture * short_bid * lot_qty
        spec = HedgeSpec(
            symbol=opt.symbol,
            token=opt.token,
            strike=row.strike,
            side=side,
            ask=ask,
            delta=opt.greeks.delta,
            width=width,
            net_credit=net_credit * lot_qty,
            max_defined_loss=max_loss,
            basket_final_margin=basket,
            net_credit_to_basket=net_credit * lot_qty / basket if basket else 0.0,
            target_profit_to_basket=target / basket if basket else 0.0,
        )
        if best is None or spec.max_defined_loss < best.max_defined_loss:
            best = spec
    return best


def _short_strike(chain: OptionChainSnapshot, short: OptionSideSnapshot) -> float:
    for row in chain.rows:
        if row.ce.symbol == short.symbol or row.pe.symbol == short.symbol:
            return row.strike
    return 0.0


def _base_record(
    bias: BiasSnapshot,
    chain: OptionChainSnapshot,
    now: datetime,
    why: list[str],
    status: str,
    candidate: Candidate | None,
) -> dict[str, object]:
    return {
        "timestamp": now.isoformat(),
        "future_price": chain.future_price,
        "expiry": chain.option_expiry.isoformat(),
        "dte": candidate.dte if candidate else None,
        "bias": bias.bias,
        "bias_score": bias.score,
        "volatility": bias.volatility,
        "model_health": bias.model_health,
        "atm": chain.atm_strike,
        "atm_straddle": chain.atm_straddle_mid,
        "candidate_symbol": candidate.symbol if candidate else None,
        "candidate_strike": candidate.strike if candidate else None,
        "distance_ratio": candidate.distance_ratio if candidate else None,
        "delta": candidate.delta if candidate else None,
        "iv": candidate.iv if candidate else None,
        "short_premium": candidate.bid if candidate else None,
        "standalone_short_margin": candidate.standalone_short_margin if candidate else None,
        "short_premium_margin_ratio": candidate.short_premium_margin_ratio if candidate else None,
        "hedge_symbol": candidate.hedge.symbol if candidate else None,
        "net_credit": candidate.hedge.net_credit if candidate else None,
        "basket_final_margin": candidate.hedge.basket_final_margin if candidate else None,
        "defined_max_loss": candidate.hedge.max_defined_loss if candidate else None,
        "estimated_costs": candidate.costs.expected_costs if candidate else None,
        "status": status,
        "reasons": "|".join(why),
    }
