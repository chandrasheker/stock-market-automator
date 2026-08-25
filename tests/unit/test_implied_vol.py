"""Implied-volatility inversion: recover known σ and reject impossible prices."""

from __future__ import annotations

import pytest

from crude_research.market.models import IVStatus, PriceSource
from crude_research.quant.black76 import call_price, put_price
from crude_research.quant.implied_vol import solve_implied_vol

RATE = 0.05
T = 0.25
VOLS = (0.20, 0.40, 0.80)


def _solve(option_type: str, price: float, f: float, k: float, t: float = T, *, stale: bool = False):
    return solve_implied_vol(
        option_type,  # type: ignore[arg-type]
        price,
        f,
        k,
        t,
        RATE,
        vol_lower=1e-6,
        vol_upper=5.0,
        price_source=PriceSource.MID,
        is_stale=stale,
    )


@pytest.mark.parametrize("vol", VOLS)
@pytest.mark.parametrize("kind,f,k", [
    ("ATM", 8000.0, 8000.0),
    ("deep_itm_call", 8000.0, 5000.0),
    ("deep_otm_call", 8000.0, 11000.0),
])
def test_recover_known_call_vol(vol: float, kind: str, f: float, k: float) -> None:
    price = call_price(f, k, T, RATE, vol)
    result = _solve("CE", price, f, k)
    assert result.status == IVStatus.OK
    assert result.iv is not None
    assert abs(result.iv - vol) < 1e-6


@pytest.mark.parametrize("vol", VOLS)
def test_recover_known_put_vol(vol: float) -> None:
    f, k = 8000.0, 7900.0
    price = put_price(f, k, T, RATE, vol)
    result = _solve("PE", price, f, k)
    assert result.status == IVStatus.OK
    assert result.iv is not None
    assert abs(result.iv - vol) < 1e-6


def test_very_small_premium() -> None:
    f, k, vol = 8000.0, 12000.0, 0.20
    price = call_price(f, k, T, RATE, vol)
    assert price < 1.0
    result = _solve("CE", price, f, k)
    assert result.status == IVStatus.OK
    assert result.iv is not None
    assert abs(result.iv - vol) < 1e-4


def test_near_expiry_recovery() -> None:
    f, k, vol, t = 8000.0, 8000.0, 0.40, 2.0 / 365.25
    price = call_price(f, k, t, RATE, vol)
    result = _solve("CE", price, f, k, t)
    assert result.status == IVStatus.OK
    assert result.iv is not None
    assert abs(result.iv - vol) < 1e-5


def test_below_intrinsic() -> None:
    result = _solve("CE", 1.0, 8000.0, 7000.0)
    assert result.status == IVStatus.BELOW_INTRINSIC_BOUND
    assert result.iv is None


def test_above_model_bound() -> None:
    result = _solve("CE", 10_000.0, 8000.0, 8000.0)
    assert result.status == IVStatus.ABOVE_MODEL_BOUND
    assert result.iv is None


def test_no_price() -> None:
    result = _solve("CE", 0.0, 8000.0, 8000.0)
    assert result.status == IVStatus.NO_PRICE
    assert result.iv is None


def test_invalid_inputs() -> None:
    result = solve_implied_vol(
        "CE",
        50.0,
        0.0,
        8000.0,
        T,
        RATE,
        vol_lower=1e-6,
        vol_upper=5.0,
        price_source=PriceSource.MID,
        is_stale=False,
    )
    assert result.status == IVStatus.INVALID_INPUT
    assert result.iv is None


def test_stale_price_is_not_ok() -> None:
    price = call_price(8000, 8000, T, RATE, 0.4)
    result = _solve("CE", price, 8000, 8000, stale=True)
    assert result.status == IVStatus.STALE_PRICE
    assert result.iv is not None


def test_ltp_fallback_is_not_ok() -> None:
    price = call_price(8000, 8000, T, RATE, 0.4)
    result = solve_implied_vol(
        "CE",
        price,
        8000,
        8000,
        T,
        RATE,
        vol_lower=1e-6,
        vol_upper=5.0,
        price_source=PriceSource.LTP_FALLBACK,
        is_stale=False,
    )
    assert result.status == IVStatus.STALE_PRICE
    assert result.iv is not None
