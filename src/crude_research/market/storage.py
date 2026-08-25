"""Append-only Parquet persistence for instrument masters and option-chain snapshots."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from crude_research.market.models import OptionChainSnapshot, OptionSideSnapshot
from crude_research.quant.time import parse_timezone


def _side_columns(prefix: str, side: OptionSideSnapshot) -> dict[str, object]:
    quality = side.quote_quality
    greeks = side.greeks
    return {
        f"{prefix}_missing": side.missing,
        f"{prefix}_token": side.token,
        f"{prefix}_symbol": side.symbol,
        f"{prefix}_raw_bid": side.raw_bid,
        f"{prefix}_raw_ask": side.raw_ask,
        f"{prefix}_raw_ltp": side.raw_ltp,
        f"{prefix}_derived_mid": side.derived_mid,
        f"{prefix}_volume": side.volume,
        f"{prefix}_oi": side.oi,
        f"{prefix}_exchange_timestamp": side.exchange_timestamp,
        f"{prefix}_last_trade_timestamp": side.last_trade_timestamp,
        f"{prefix}_received_at": side.received_at,
        f"{prefix}_has_bid": quality.has_bid if quality else None,
        f"{prefix}_has_ask": quality.has_ask if quality else None,
        f"{prefix}_valid_bid_ask": quality.valid_bid_ask if quality else None,
        f"{prefix}_crossed_market": quality.crossed_market if quality else None,
        f"{prefix}_spread": quality.spread if quality else None,
        f"{prefix}_spread_pct": quality.spread_pct if quality else None,
        f"{prefix}_is_stale": quality.is_stale if quality else None,
        f"{prefix}_has_oi": quality.has_oi if quality else None,
        f"{prefix}_has_volume": quality.has_volume if quality else None,
        f"{prefix}_price_source": quality.price_source.value if quality else None,
        f"{prefix}_quote_notes": "|".join(quality.notes) if quality else None,
        f"{prefix}_distance_points": side.distance_points,
        f"{prefix}_distance_pct": side.distance_pct,
        f"{prefix}_straddle_distance_ratio": side.straddle_distance_ratio,
        f"{prefix}_iv": greeks.iv if greeks else None,
        f"{prefix}_iv_status": greeks.iv_status.value if greeks else None,
        f"{prefix}_iv_price": greeks.iv_price if greeks else None,
        f"{prefix}_iv_price_source": greeks.iv_price_source.value if greeks and greeks.iv_price_source else None,
        f"{prefix}_delta": greeks.delta if greeks else None,
        f"{prefix}_gamma": greeks.gamma if greeks else None,
        f"{prefix}_theta": greeks.theta if greeks else None,
        f"{prefix}_theta_per_day": greeks.theta_per_day if greeks else None,
        f"{prefix}_vega": greeks.vega if greeks else None,
        f"{prefix}_vega_1pct": greeks.vega_1pct if greeks else None,
        f"{prefix}_d1": greeks.d1 if greeks else None,
        f"{prefix}_d2": greeks.d2 if greeks else None,
        f"{prefix}_top_bids": (
            ";".join(f"{lvl.price}:{lvl.quantity}:{lvl.orders}" for lvl in side.depth.buy)
            if side.depth
            else None
        ),
        f"{prefix}_top_asks": (
            ";".join(f"{lvl.price}:{lvl.quantity}:{lvl.orders}" for lvl in side.depth.sell)
            if side.depth
            else None
        ),
    }


def snapshot_to_frame(snapshot: OptionChainSnapshot) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in snapshot.rows:
        record: dict[str, object] = {
            "underlying": snapshot.underlying.value,
            "option_expiry": snapshot.option_expiry.isoformat(),
            "underlying_future_symbol": snapshot.underlying_future_symbol,
            "underlying_future_token": snapshot.underlying_future_token,
            "future_price": snapshot.future_price,
            "future_price_source": snapshot.future_price_source.value,
            "future_mapping_rule": snapshot.future_mapping_rule.value,
            "snapshot_timestamp": snapshot.snapshot_timestamp,
            "strike_interval": snapshot.strike_interval,
            "available_strikes": ",".join(str(s) for s in snapshot.available_strikes),
            "atm_strike": snapshot.atm_strike,
            "atm_ce_mid": snapshot.atm_ce_mid,
            "atm_pe_mid": snapshot.atm_pe_mid,
            "atm_straddle_mid": snapshot.atm_straddle_mid,
            "atm_ce_ltp": snapshot.atm_ce_ltp,
            "atm_pe_ltp": snapshot.atm_pe_ltp,
            "atm_straddle_ltp": snapshot.atm_straddle_ltp,
            "straddle_price_source": snapshot.straddle_price_source.value,
            "snapshot_quality": snapshot.snapshot_quality.value,
            "risk_free_rate": snapshot.risk_free_rate,
            "expiry_timestamp": snapshot.expiry_timestamp,
            "expiry_time_source": snapshot.expiry_time_source.value,
            "time_to_expiry": snapshot.time_to_expiry,
            "chain_notes": "|".join(snapshot.notes),
            "strike": row.strike,
        }
        record.update(_side_columns("ce", row.ce))
        record.update(_side_columns("pe", row.pe))
        rows.append(record)
    return pd.DataFrame(rows)


def chain_snapshot_dir(
    data_dir: Path,
    snapshot: OptionChainSnapshot,
    *,
    timezone_name: str,
) -> Path:
    tz = parse_timezone(timezone_name)
    session_date = snapshot.snapshot_timestamp.astimezone(tz).date().isoformat()
    return (
        Path(data_dir)
        / "chains"
        / f"date={session_date}"
        / f"underlying={snapshot.underlying.value}"
        / f"expiry={snapshot.option_expiry.isoformat()}"
    )


def persist_chain_snapshot(
    snapshot: OptionChainSnapshot,
    data_dir: Path,
    *,
    timezone_name: str,
) -> Path:
    """Write a new Parquet file. Existing observations are never overwritten."""
    folder = chain_snapshot_dir(data_dir, snapshot, timezone_name=timezone_name)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = snapshot.snapshot_timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"snapshot_{stamp}.parquet"
    suffix = 1
    while path.exists():
        suffix += 1
        path = folder / f"snapshot_{stamp}_{suffix}.parquet"
    snapshot_to_frame(snapshot).to_parquet(path, engine="pyarrow", index=False)
    return path


def read_chain_snapshot(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_parquet(str(path))
