"""Black-76 prices and put-call parity. Reference CDF uses math.erf, not scipy."""

from __future__ import annotations

import math

from crude_research.quant.black76 import call_price, put_price


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _ref_call(f: float, k: float, t: float, r: float, vol: float) -> float:
    d1 = (math.log(f / k) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    return math.exp(-r * t) * (f * _norm_cdf(d1) - k * _norm_cdf(d2))


def _ref_put(f: float, k: float, t: float, r: float, vol: float) -> float:
    d1 = (math.log(f / k) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    return math.exp(-r * t) * (k * _norm_cdf(-d2) - f * _norm_cdf(-d1))


def test_call_put_against_erf_reference() -> None:
    f, k, t, r, vol = 8000.0, 8000.0, 0.25, 0.06, 0.40
    assert call_price(f, k, t, r, vol) == pytest_approx(_ref_call(f, k, t, r, vol))
    assert put_price(f, k, t, r, vol) == pytest_approx(_ref_put(f, k, t, r, vol))


def pytest_approx(value: float, rel: float = 1e-10) -> object:
    import pytest

    return pytest.approx(value, rel=rel)


def test_known_atm_value() -> None:
    # F=K=100, T=1, r=0.05, σ=0.20 → C=P=exp(-0.05)*100*(N(0.1)-N(-0.1))
    f = k = 100.0
    t, r, vol = 1.0, 0.05, 0.20
    call = call_price(f, k, t, r, vol)
    put = put_price(f, k, t, r, vol)
    expected = _ref_call(f, k, t, r, vol)
    assert call == pytest_approx(expected, rel=1e-12)
    assert put == pytest_approx(call, rel=1e-12)
    assert 7.0 < call < 8.0


def test_put_call_parity() -> None:
    f, k, t, r, vol = 8012.0, 7950.0, 0.08, 0.065, 0.35
    call = call_price(f, k, t, r, vol)
    put = put_price(f, k, t, r, vol)
    parity = math.exp(-r * t) * (f - k)
    assert call - put == pytest_approx(parity, rel=1e-10)


def test_otm_call_cheaper_than_itm() -> None:
    t, r, vol = 0.2, 0.05, 0.5
    itm = call_price(8000, 7000, t, r, vol)
    otm = call_price(8000, 9000, t, r, vol)
    assert itm > otm > 0
