"""Black-76 greeks.

Units (do not mix these up in downstream research):

* delta       – dV/dF, discounted. Call: exp(-rT) N(d1). Put: -exp(-rT) N(-d1).
                Range is roughly (-df, +df), not (-1, +1), unless r=0 or T=0.
* gamma       – d²V/dF² = exp(-rT) n(d1) / (F σ √T). Same for call and put.
* theta       – calendar decay of premium per YEAR (T decreasing).
* theta_per_day – theta / 365. Convenience only; 365 is not the pricing day count.
* vega        – dV/dσ for a +1.00 change in volatility (e.g. 20% -> 120%).
* vega_1pct   – vega / 100, premium change for +0.01 (one volatility point).

Premiums are in the same units as the quoted MCX option price (INR per barrel),
not rupee P&L per lot.
"""

from __future__ import annotations

import math

from scipy.stats import norm

from crude_research.quant.black76 import OptionType, d1_d2, discount_factor, price
from crude_research.quant.time import THETA_DAYS_PER_YEAR


def call_delta(forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    df = discount_factor(rate, time_to_expiry)
    d1, _ = d1_d2(forward, strike, time_to_expiry, vol)
    return df * float(norm.cdf(d1))


def put_delta(forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    df = discount_factor(rate, time_to_expiry)
    d1, _ = d1_d2(forward, strike, time_to_expiry, vol)
    return -df * float(norm.cdf(-d1))


def gamma(forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    df = discount_factor(rate, time_to_expiry)
    d1, _ = d1_d2(forward, strike, time_to_expiry, vol)
    return df * float(norm.pdf(d1)) / (forward * vol * math.sqrt(time_to_expiry))


def vega(forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    """dV/dσ for +1.00 volatility. Same for calls and puts."""
    df = discount_factor(rate, time_to_expiry)
    d1, _ = d1_d2(forward, strike, time_to_expiry, vol)
    return df * forward * float(norm.pdf(d1)) * math.sqrt(time_to_expiry)


def theta(option_type: OptionType, forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    """Calendar theta per year: dV/dt with T = T_expiry - t."""
    df = discount_factor(rate, time_to_expiry)
    d1, _ = d1_d2(forward, strike, time_to_expiry, vol)
    premium = price(option_type, forward, strike, time_to_expiry, rate, vol)
    diffusion = df * forward * float(norm.pdf(d1)) * vol / (2.0 * math.sqrt(time_to_expiry))
    return rate * premium - diffusion


def greeks(
    option_type: OptionType,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    vol: float,
) -> dict[str, float]:
    d1, d2 = d1_d2(forward, strike, time_to_expiry, vol)
    delta = (
        call_delta(forward, strike, time_to_expiry, rate, vol)
        if option_type == "CE"
        else put_delta(forward, strike, time_to_expiry, rate, vol)
    )
    vega_raw = vega(forward, strike, time_to_expiry, rate, vol)
    theta_year = theta(option_type, forward, strike, time_to_expiry, rate, vol)
    return {
        "delta": delta,
        "gamma": gamma(forward, strike, time_to_expiry, rate, vol),
        "theta": theta_year,
        "theta_per_day": theta_year / THETA_DAYS_PER_YEAR,
        "vega": vega_raw,
        "vega_1pct": vega_raw / 100.0,
        "d1": d1,
        "d2": d2,
    }
