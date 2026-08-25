from __future__ import annotations

from datetime import date
from pathlib import Path

from crude_research.market.chain import build_option_chain
from crude_research.market.models import ExpiryTimeSource
from crude_research.market.storage import persist_chain_snapshot, read_chain_snapshot
from crude_research.zerodha.instruments import (
    frame_to_instruments,
    instruments_to_frame,
    read_instrument_master,
    write_instrument_master,
)
from tests.fixtures.builders import crude_master
from tests.unit.test_chain import _mini_quotes, _now


def test_instrument_master_roundtrip(tmp_path: Path) -> None:
    master = crude_master()
    path = tmp_path / "mcx.parquet"
    write_instrument_master(master, path)
    loaded = read_instrument_master(path)
    symbols = {row.tradingsymbol for row in loaded}
    assert "CRUDEOILM26OCTFUT" in symbols
    assert "CRUDEOIL26OCTFUT" in symbols
    mini = [row for row in loaded if row.tradingsymbol.startswith("CRUDEOILM")]
    crude = [row for row in loaded if row.tradingsymbol.startswith("CRUDEOIL") and not row.tradingsymbol.startswith("CRUDEOILM")]
    assert mini and crude


def test_frame_preserves_raw_gold() -> None:
    master = crude_master()
    frame = instruments_to_frame(master)
    gold = frame[frame["tradingsymbol"] == "GOLD26OCTFUT"]
    assert len(gold) == 1
    restored = frame_to_instruments(gold)
    assert restored[0].underlying is None


def test_chain_snapshot_append_only_roundtrip(tmp_path: Path) -> None:
    snapshot = build_option_chain(
        "CRUDEOILM",
        date(2026, 10, 16),
        _mini_quotes(),
        crude_master(),
        now=_now(),
        stale_after_seconds=120,
        rate=0.07,
        time_years=0.14,
        expiry_timestamp=None,
        expiry_time_source=ExpiryTimeSource.CONFIGURED_ASSUMPTION,
        vol_lower=1e-6,
        vol_upper=5.0,
    )
    path1 = persist_chain_snapshot(snapshot, tmp_path, timezone_name="Asia/Kolkata")
    path2 = persist_chain_snapshot(snapshot, tmp_path, timezone_name="Asia/Kolkata")
    assert path1 != path2
    assert path1.exists() and path2.exists()
    frame = read_chain_snapshot(path1)
    assert len(frame) == len(snapshot.rows)
    assert float(frame["atm_strike"].iloc[0]) == 8000
    assert float(frame["risk_free_rate"].iloc[0]) == 0.07
    assert "ce_iv" in frame.columns
    assert "ce_raw_bid" in frame.columns
    assert "ce_derived_mid" in frame.columns
    assert frame["future_mapping_rule"].iloc[0] == "CONTRACT_MONTH_FROM_TRADINGSYMBOL"
