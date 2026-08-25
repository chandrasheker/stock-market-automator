"""Black-76 greeks sign, range, ATM, and near-expiry behaviour."""

from __future__ import annotations

import math

from crude_research.quant.black76 import call_price, discount_factor, put_price
from crude_research.quant.greeks import call_delta, gamma, greeks, put_delta, theta, vega


def test_call_delta_sign_and_range() -> None:
    f, k, t, r, vol = 8000.0, 8000.0, 0.25, 0.05, 0.4
    delta = call_delta(f, k, t, r, vol)
    df = discount_factor(r, t)
    assert 0 < delta < df
    assert abs(delta - 0.5 * df) < 0.05  # ATM


def test_put_delta_sign_and_range() -> None:
    f, k, t, r, vol = 8000.0, 8000.0, 0.25, 0.05, 0.4
    delta = put_delta(f, k, t, r, vol)
    df = discount_factor(r, t)
    assert -df < delta < 0


def test_gamma_and_vega_positive() -> None:
    f, k, t, r, vol = 8000.0, 8100.0, 0.25, 0.05, 0.4
    assert gamma(f, k, t, r, vol) > 0
    assert vega(f, k, t, r, vol) > 0
    call_g = greeks("CE", f, k, t, r, vol)
    put_g = greeks("PE", f, k, t, r, vol)
    assert call_g["gamma"] == put_g["gamma"]
    assert call_g["vega"] == put_g["vega"]
    assert call_g["vega_1pct"] == call_g["vega"] / 100.0
    assert call_g["theta_per_day"] == call_g["theta"] / 365.0


def test_itm_call_delta_higher_than_otm() -> None:
    t, r, vol = 0.3, 0.04, 0.35
    itm = call_delta(8000, 7000, t, r, vol)
    otm = call_delta(8000, 9000, t, r, vol)
    assert itm > otm


def test_near_expiry_itm_call_approaches_discount() -> None:
    f, k, r, vol = 8000.0, 7000.0, 0.05, 0.4
    t = 1.0 / (365.25 * 24.0)  # one hour
    delta = call_delta(f, k, t, r, vol)
    df = discount_factor(r, t)
    assert abs(delta - df) < 1e-4
    otm_delta = call_delta(f, 9000.0, t, r, vol)
    assert otm_delta < 1e-6


def test_near_expiry_price_approaches_intrinsic() -> None:
    f, k, r, vol = 8000.0, 7900.0, 0.05, 0.3
    t = 1e-6
    df = math.exp(-r * t)
    assert abs(call_price(f, k, t, r, vol) - df * (f - k)) < 1e-4
    assert put_price(f, k, t, r, vol) < 1e-3


def test_theta_put_call_parity() -> None:
    f, k, t, r, vol = 8100.0, 8000.0, 0.2, 0.06, 0.4
    th_c = theta("CE", f, k, t, r, vol)
    th_p = theta("PE", f, k, t, r, vol)
    expected = r * math.exp(-r * t) * (f - k)
    assert abs((th_c - th_p) - expected) < 1e-8
