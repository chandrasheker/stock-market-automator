"""MCX instrument master download, classification, and daily Parquet cache."""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from crude_research.exceptions import InstrumentMasterError
from crude_research.market.models import Instrument, Underlying
from crude_research.quant.time import parse_timezone
from crude_research.zerodha.client import MarketDataBroker
from crude_research.zerodha.constants import EXCHANGE_MCX, MONTH_CODES

log = logging.getLogger(__name__)

# CRUDEOILM is listed first so it cannot be swallowed by a CRUDEOIL prefix match.
_MONTH = "(?:" + "|".join(MONTH_CODES) + ")"
_FUT_RE = re.compile(rf"^(CRUDEOILM|CRUDEOIL)(\d{{2}}{_MONTH})FUT$")
_OPT_RE = re.compile(rf"^(CRUDEOILM|CRUDEOIL)(\d{{2}}{_MONTH})(\d+(?:\.\d+)?)(CE|PE)$")

_MASTER_COLUMNS = (
    "instrument_token",
    "exchange_token",
    "tradingsymbol",
    "name",
    "last_price",
    "expiry",
    "strike",
    "tick_size",
    "lot_size",
    "instrument_type",
    "segment",
    "exchange",
    "retrieved_at",
    "underlying",
    "contract_month",
)


def parse_contract_month(tradingsymbol: str) -> tuple[Underlying, str] | None:
    """Parse CRUDEOIL / CRUDEOILM contract month from a Zerodha tradingsymbol.

    Matching CRUDEOILM before CRUDEOIL is mandatory: `CRUDEOILM25OCTFUT` contains
    the substring CRUDEOIL.
    """
    for regex in (_FUT_RE, _OPT_RE):
        match = regex.match(tradingsymbol.strip().upper())
        if match:
            return Underlying(match.group(1)), match.group(2)
    return None


def classify_underlying(name: str, tradingsymbol: str) -> Underlying | None:
    """Classify an MCX row as CRUDEOIL, CRUDEOILM, or unrelated.

    Tradingsymbol parsing is the source of truth for the crude family. An exact
    `name` of CRUDEOIL / CRUDEOILM is corroboration only. `name.startswith("CRUDEOIL")`
    is never used.
    """
    parsed = parse_contract_month(tradingsymbol)
    if parsed is not None:
        symbol_underlying, _ = parsed
        name_upper = name.strip().upper()
        if name_upper in {Underlying.CRUDEOIL, Underlying.CRUDEOILM} and name_upper != symbol_underlying:
            log.warning(
                "Instrument name %s disagrees with tradingsymbol %s; using tradingsymbol classification %s",
                name,
                tradingsymbol,
                symbol_underlying,
            )
        return symbol_underlying
    name_upper = name.strip().upper()
    if name_upper == Underlying.CRUDEOILM:
        return Underlying.CRUDEOILM
    if name_upper == Underlying.CRUDEOIL:
        return Underlying.CRUDEOIL
    return None


def _parse_expiry(value: object) -> date | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    return date.fromisoformat(text[:10])


def _as_int(value: object, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(float(str(value)))


def _as_float(value: object, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def normalize_instrument(raw: dict[str, Any], *, retrieved_at: datetime) -> Instrument:
    tradingsymbol = str(raw.get("tradingsymbol", "")).strip()
    name = str(raw.get("name", "") or "").strip()
    parsed = parse_contract_month(tradingsymbol)
    contract_month = parsed[1] if parsed else None
    return Instrument(
        instrument_token=_as_int(raw.get("instrument_token")),
        exchange_token=_as_int(raw.get("exchange_token")),
        tradingsymbol=tradingsymbol,
        name=name,
        last_price=_as_float(raw.get("last_price")),
        expiry=_parse_expiry(raw.get("expiry")),
        strike=_as_float(raw.get("strike")),
        tick_size=_as_float(raw.get("tick_size")),
        lot_size=_as_int(raw.get("lot_size"), default=0),
        instrument_type=str(raw.get("instrument_type", "")).strip().upper(),
        segment=str(raw.get("segment", "")).strip(),
        exchange=str(raw.get("exchange", "")).strip().upper(),
        retrieved_at=retrieved_at,
        underlying=classify_underlying(name, tradingsymbol),
        contract_month=contract_month,
    )


def instruments_to_frame(records: list[Instrument]) -> pd.DataFrame:
    rows = [inst.model_dump() for inst in records]
    frame = pd.DataFrame(rows, columns=list(_MASTER_COLUMNS))
    if not frame.empty:
        frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
        frame["expiry"] = pd.to_datetime(frame["expiry"]).dt.date
        frame["underlying"] = frame["underlying"].apply(
            lambda value: value.value if isinstance(value, Underlying) else value
        )
    return frame


def frame_to_instruments(frame: pd.DataFrame) -> list[Instrument]:
    records: list[Instrument] = []
    for row in frame.to_dict(orient="records"):
        retrieved = row.get("retrieved_at")
        if isinstance(retrieved, pd.Timestamp):
            retrieved_at = retrieved.to_pydatetime()
            if retrieved_at.tzinfo is None:
                retrieved_at = retrieved_at.replace(tzinfo=UTC)
        elif isinstance(retrieved, datetime):
            retrieved_at = retrieved if retrieved.tzinfo else retrieved.replace(tzinfo=UTC)
        else:
            retrieved_at = datetime.now(tz=UTC)
        raw = {str(key): value for key, value in row.items()}
        raw["retrieved_at"] = retrieved_at
        if raw.get("underlying") in {None, "", float("nan")} or (isinstance(raw.get("underlying"), float) and pd.isna(raw.get("underlying"))):
            raw["underlying"] = None
        records.append(normalize_instrument(raw, retrieved_at=retrieved_at))
    return records


def cache_path(data_dir: Path, session_date: date) -> Path:
    return Path(data_dir) / "instruments" / f"mcx_instruments_{session_date.isoformat()}.parquet"


def history_path(data_dir: Path, session_date: date, retrieved_at: datetime) -> Path:
    stamp = retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        Path(data_dir)
        / "instruments"
        / "history"
        / f"mcx_instruments_{session_date.isoformat()}_{stamp}.parquet"
    )


def write_instrument_master(records: list[Instrument], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    instruments_to_frame(records).to_parquet(path, engine="pyarrow", index=False)


def read_instrument_master(path: Path) -> list[Instrument]:
    if not path.exists():
        raise InstrumentMasterError(f"Instrument master not found: {path}")
    frame = pd.read_parquet(str(path))
    return frame_to_instruments(frame)


def session_date_now(*, timezone_name: str, now: datetime | None = None) -> date:
    tz = parse_timezone(timezone_name)
    current = now if now is not None else datetime.now(tz=tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    return current.astimezone(tz).date()


def load_or_sync_instruments(
    *,
    data_dir: Path,
    timezone_name: str,
    broker: MarketDataBroker | None,
    force: bool = False,
    now: datetime | None = None,
) -> list[Instrument]:
    """Load today's cached MCX master, or download it when missing/forced."""
    session = session_date_now(timezone_name=timezone_name, now=now)
    path = cache_path(data_dir, session)
    if path.exists() and not force:
        log.info("Using cached MCX instrument master %s", path)
        return read_instrument_master(path)
    if broker is None:
        raise InstrumentMasterError(
            f"No cached MCX instrument master at {path} and no Kite credentials to download one."
        )
    retrieved_at = datetime.now(tz=UTC)
    raw_rows = broker.instruments(EXCHANGE_MCX)
    records = [normalize_instrument(row, retrieved_at=retrieved_at) for row in raw_rows]
    write_instrument_master(records, path)
    write_instrument_master(records, history_path(data_dir, session, retrieved_at))
    log.info("Wrote %s MCX instruments to %s", len(records), path)
    return records
