"""CRUDEOIL vs CRUDEOILM classification must never cross-match."""

from __future__ import annotations

from datetime import date

from crude_research.market.contracts import list_futures, list_option_expiries, list_options
from crude_research.zerodha.instruments import classify_underlying, parse_contract_month
from tests.fixtures.builders import crude_master, instrument


def test_parse_prefers_crudeoilm() -> None:
    assert parse_contract_month("CRUDEOILM26OCTFUT") == ("CRUDEOILM", "26OCT")
    assert parse_contract_month("CRUDEOIL26OCTFUT") == ("CRUDEOIL", "26OCT")
    assert parse_contract_month("CRUDEOILM26OCT8000CE") == ("CRUDEOILM", "26OCT")
    assert parse_contract_month("CRUDEOIL26OCT8000PE") == ("CRUDEOIL", "26OCT")
    assert parse_contract_month("GOLD26OCTFUT") is None


def test_classify_does_not_use_prefix() -> None:
    assert classify_underlying("CRUDEOIL", "CRUDEOILM26OCTFUT") == "CRUDEOILM"
    assert classify_underlying("CRUDEOILM", "CRUDEOIL26OCTFUT") == "CRUDEOIL"
    assert classify_underlying("GOLD", "GOLD26OCTFUT") is None
    assert classify_underlying("CRUDEOIL", "SOMETHING_ELSE") == "CRUDEOIL"


def test_list_futures_no_cross_match() -> None:
    master = crude_master()
    crude = list_futures(master, "CRUDEOIL")
    mini = list_futures(master, "CRUDEOILM")
    assert {f.tradingsymbol for f in crude} == {"CRUDEOIL26OCTFUT", "CRUDEOIL26NOVFUT"}
    assert {f.tradingsymbol for f in mini} == {"CRUDEOILM26OCTFUT", "CRUDEOILM26NOVFUT"}
    assert all("CRUDEOILM" not in f.tradingsymbol or False for f in crude)
    for fut in crude:
        assert "CRUDEOILM" not in fut.tradingsymbol
    for fut in mini:
        assert fut.tradingsymbol.startswith("CRUDEOILM")


def test_list_options_no_cross_match() -> None:
    master = crude_master()
    expiry = date(2026, 10, 16)
    crude = list_options(master, "CRUDEOIL", expiry)
    mini = list_options(master, "CRUDEOILM", expiry)
    assert crude and mini
    assert all(opt.tradingsymbol.startswith("CRUDEOIL") and not opt.tradingsymbol.startswith("CRUDEOILM") for opt in crude)
    assert all(opt.tradingsymbol.startswith("CRUDEOILM") for opt in mini)
    assert [opt.strike for opt in crude] == sorted(opt.strike for opt in crude)


def test_expiries_sorted() -> None:
    master = crude_master()
    expiries = list_option_expiries(master, "CRUDEOILM")
    assert expiries == sorted(expiries)
    assert date(2026, 10, 16) in expiries


def test_gold_not_classified_as_crude() -> None:
    gold = instrument(token=9, symbol="GOLD26OCTFUT", name="GOLD", instrument_type="FUT", expiry=date(2026, 10, 19))
    assert gold.underlying is None
    master = crude_master()
    assert all(row.tradingsymbol != "GOLD26OCTFUT" or row.underlying is None for row in master)
