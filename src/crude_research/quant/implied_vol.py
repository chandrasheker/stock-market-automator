"""Implied-volatility inversion for Black-76 via Brent's method.

Volatility search bounds are configurable. A failed solve never returns 0.0 as a
fake IV; it returns status + iv=None.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq

from crude_research.market.models import IVStatus, OptionGreeks, PriceSource
from crude_research.quant.black76 import (
    OptionType,
    discounted_intrinsic,
    model_upper_bound,
)
from crude_research.quant.black76 import (
    price as black76_price,
)
from crude_research.quant.greeks import greeks as black76_greeks

_BOUND_EPS_ABS = 1e-8


@dataclass(frozen=True)
class IVSolveResult:
    status: IVStatus
    iv: float | None
    iv_price: float | None
    iv_price_source: PriceSource | None
    greeks: dict[str, float] | None
    detail: str


def _bound_epsilon(forward: float) -> float:
    return max(_BOUND_EPS_ABS, 1e-8 * max(forward, 1.0))


def solve_implied_vol(
    option_type: OptionType,
    market_price: float | None,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    *,
    vol_lower: float,
    vol_upper: float,
    price_source: PriceSource,
    is_stale: bool,
) -> IVSolveResult:
    """Invert Black-76 for σ. Greeks are computed only on a successful numerical solve."""
    if market_price is None or market_price <= 0:
        return IVSolveResult(IVStatus.NO_PRICE, None, market_price, price_source, None, "market_price missing or <= 0")
    if (
        forward <= 0
        or strike <= 0
        or time_to_expiry <= 0
        or vol_lower <= 0
        or vol_upper <= vol_lower
        or not math.isfinite(rate)
    ):
        return IVSolveResult(
            IVStatus.INVALID_INPUT,
            None,
            market_price,
            price_source,
            None,
            f"invalid F={forward} K={strike} T={time_to_expiry} r={rate} bounds=({vol_lower},{vol_upper})",
        )

    eps = _bound_epsilon(forward)
    intrinsic = discounted_intrinsic(option_type, forward, strike, time_to_expiry, rate)
    upper = model_upper_bound(option_type, forward, strike, time_to_expiry, rate)
    if market_price < intrinsic - eps:
        return IVSolveResult(
            IVStatus.BELOW_INTRINSIC_BOUND,
            None,
            market_price,
            price_source,
            None,
            f"price {market_price} < discounted intrinsic {intrinsic}",
        )
    if market_price > upper + eps:
        return IVSolveResult(
            IVStatus.ABOVE_MODEL_BOUND,
            None,
            market_price,
            price_source,
            None,
            f"price {market_price} > model upper bound {upper}",
        )

    def objective(vol: float) -> float:
        return black76_price(option_type, forward, strike, time_to_expiry, rate, vol) - market_price

    try:
        f_low = objective(vol_lower)
        f_high = objective(vol_upper)
    except (ValueError, OverflowError) as exc:
        return IVSolveResult(IVStatus.INVALID_INPUT, None, market_price, price_source, None, str(exc))

    if f_low == 0:
        vol = vol_lower
    elif f_high == 0:
        vol = vol_upper
    elif f_low * f_high > 0:
        return IVSolveResult(
            IVStatus.NO_CONVERGENCE,
            None,
            market_price,
            price_source,
            None,
            f"no sign change on [{vol_lower}, {vol_upper}] (f_low={f_low}, f_high={f_high})",
        )
    else:
        try:
            vol = float(
                brentq(
                    objective,
                    vol_lower,
                    vol_upper,
                    xtol=1e-12,
                    rtol=1e-12,
                    maxiter=200,
                )
            )
        except (ValueError, RuntimeError) as exc:
            return IVSolveResult(
                IVStatus.NO_CONVERGENCE,
                None,
                market_price,
                price_source,
                None,
                f"brentq failed: {exc}",
            )

    greek_values = black76_greeks(option_type, forward, strike, time_to_expiry, rate, vol)
    status = IVStatus.STALE_PRICE if is_stale or price_source != PriceSource.MID else IVStatus.OK
    return IVSolveResult(status, vol, market_price, price_source, greek_values, "ok")


def greeks_from_solve(
    result: IVSolveResult,
    *,
    rate: float,
    time_to_expiry: float,
    forward: float,
    strike: float,
) -> OptionGreeks:
    g = result.greeks or {}
    return OptionGreeks(
        iv=result.iv,
        iv_status=result.status,
        iv_price=result.iv_price,
        iv_price_source=result.iv_price_source,
        delta=g.get("delta"),
        gamma=g.get("gamma"),
        theta=g.get("theta"),
        theta_per_day=g.get("theta_per_day"),
        vega=g.get("vega"),
        vega_1pct=g.get("vega_1pct"),
        d1=g.get("d1"),
        d2=g.get("d2"),
        risk_free_rate=rate,
        time_to_expiry=time_to_expiry,
        futures_price=forward,
        strike=strike,
    )
