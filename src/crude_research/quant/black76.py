"""Black-76 futures-option pricing.

MCX CRUDEOIL / CRUDEOILM options are options on futures. This module implements
Black (1976), not equity Black-Scholes.

Call:
    C = exp(-rT) [ F N(d1) - K N(d2) ]
Put:
    P = exp(-rT) [ K N(-d2) - F N(-d1) ]

where
    d1 = [ln(F/K) + 0.5 σ² T] / (σ √T)
    d2 = d1 - σ √T

All calculations are implemented here directly (scipy is used only for N(·) / n(·)).
"""

from __future__ import annotations

import math
from typing import Literal

from scipy.stats import norm

OptionType = Literal["CE", "PE"]


def discount_factor(rate: float, time_to_expiry: float) -> float:
    return math.exp(-rate * time_to_expiry)


def d1_d2(forward: float, strike: float, time_to_expiry: float, vol: float) -> tuple[float, float]:
    if forward <= 0 or strike <= 0 or time_to_expiry <= 0 or vol <= 0:
        raise ValueError("F, K, T, σ must all be strictly positive to compute d1/d2")
    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(forward / strike) + 0.5 * vol * vol * time_to_expiry) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    return d1, d2


def call_price(forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    df = discount_factor(rate, time_to_expiry)
    d1, d2 = d1_d2(forward, strike, time_to_expiry, vol)
    return df * (forward * float(norm.cdf(d1)) - strike * float(norm.cdf(d2)))


def put_price(forward: float, strike: float, time_to_expiry: float, rate: float, vol: float) -> float:
    df = discount_factor(rate, time_to_expiry)
    d1, d2 = d1_d2(forward, strike, time_to_expiry, vol)
    return df * (strike * float(norm.cdf(-d2)) - forward * float(norm.cdf(-d1)))


def price(
    option_type: OptionType,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    vol: float,
) -> float:
    if option_type == "CE":
        return call_price(forward, strike, time_to_expiry, rate, vol)
    if option_type == "PE":
        return put_price(forward, strike, time_to_expiry, rate, vol)
    raise ValueError(f"option_type must be CE or PE, got {option_type!r}")


def undiscounted_intrinsic(option_type: OptionType, forward: float, strike: float) -> float:
    if option_type == "CE":
        return max(0.0, forward - strike)
    return max(0.0, strike - forward)


def discounted_intrinsic(
    option_type: OptionType,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
) -> float:
    return discount_factor(rate, time_to_expiry) * undiscounted_intrinsic(option_type, forward, strike)


def model_upper_bound(
    option_type: OptionType,
    forward: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
) -> float:
    df = discount_factor(rate, time_to_expiry)
    if option_type == "CE":
        return df * forward
    return df * strike
