from __future__ import annotations

from datetime import date

import pytest

from crude_research.exceptions import AmbiguousFutureMappingError, UnknownOptionExpiryError
from crude_research.market.contracts import resolve_underlying_future
from tests.fixtures.builders import crude_master, instrument


def test_maps_by_contract_month_not_equal_expiry() -> None:
    master = crude_master()
    future = resolve_underlying_future(master, underlying="CRUDEOILM", option_expiry=date(2026, 10, 16))
    assert future.tradingsymbol == "CRUDEOILM26OCTFUT"
    assert future.expiry == date(2026, 10, 19)
    crude = resolve_underlying_future(master, underlying="CRUDEOIL", option_expiry=date(2026, 10, 16))
    assert crude.tradingsymbol == "CRUDEOIL26OCTFUT"


def test_november_maps_separately() -> None:
    master = crude_master()
    future = resolve_underlying_future(master, underlying="CRUDEOILM", option_expiry=date(2026, 11, 17))
    assert future.tradingsymbol == "CRUDEOILM26NOVFUT"


def test_ambiguous_two_futures_same_month() -> None:
    master = crude_master()
    duplicate = instrument(
        token=99,
        symbol="CRUDEOILM26OCTFUT",
        name="CRUDEOILM",
        instrument_type="FUT",
        expiry=date(2026, 10, 20),
        lot_size=10,
    )
    with pytest.raises(AmbiguousFutureMappingError) as exc:
        resolve_underlying_future(
            [*master, duplicate],
            underlying="CRUDEOILM",
            option_expiry=date(2026, 10, 16),
        )
    assert exc.value.candidates


def test_fails_when_month_unparseable() -> None:
    master = [
        instrument(token=1, symbol="CRUDEOILM26OCTFUT", name="CRUDEOILM", instrument_type="FUT", expiry=date(2026, 10, 19)),
        instrument(
            token=2,
            symbol="WEIRD",
            name="CRUDEOILM",
            instrument_type="CE",
            expiry=date(2026, 10, 16),
            strike=8000,
            contract_month=None,
        ),
    ]
    # Force unparseable month: name classifies as CRUDEOILM but symbol does not parse.
    weird = master[1].model_copy(update={"underlying": master[0].underlying, "contract_month": None})
    with pytest.raises(AmbiguousFutureMappingError):
        resolve_underlying_future([master[0], weird], underlying="CRUDEOILM", option_expiry=date(2026, 10, 16))


def test_rejects_future_expiring_before_option() -> None:
    master = [
        instrument(token=1, symbol="CRUDEOILM26OCTFUT", name="CRUDEOILM", instrument_type="FUT", expiry=date(2026, 10, 10)),
        instrument(
            token=2,
            symbol="CRUDEOILM26OCT8000CE",
            name="CRUDEOILM",
            instrument_type="CE",
            expiry=date(2026, 10, 16),
            strike=8000,
        ),
    ]
    with pytest.raises(AmbiguousFutureMappingError):
        resolve_underlying_future(master, underlying="CRUDEOILM", option_expiry=date(2026, 10, 16))


def test_unknown_expiry_lists_available() -> None:
    master = crude_master()
    with pytest.raises(UnknownOptionExpiryError) as exc:
        resolve_underlying_future(master, underlying="CRUDEOILM", option_expiry=date(2026, 10, 14))
    assert date(2026, 10, 16) in exc.value.available
    assert "2026-10-16" in str(exc.value)
    assert "Closest listed expiry: 2026-10-16" in str(exc.value)

