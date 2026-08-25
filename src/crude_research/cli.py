"""Research CLI. Market data and Black-76 only — no order placement."""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from crude_research.config import Settings, get_settings
from crude_research.diagnostics.doctor import run_doctor
from crude_research.exceptions import CrudeResearchError
from crude_research.logging_setup import setup_logging
from crude_research.market.chain import build_option_chain
from crude_research.market.contracts import list_futures, list_option_expiries, list_options
from crude_research.market.models import Instrument, IVStatus, OptionChainSnapshot, Quote
from crude_research.market.storage import persist_chain_snapshot, read_chain_snapshot
from crude_research.quant.time import assume_expiry_timestamp, parse_timezone, time_to_expiry
from crude_research.zerodha.client import KiteMarketDataClient
from crude_research.zerodha.instruments import load_or_sync_instruments
from crude_research.zerodha.quotes import fetch_full_quotes

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="MCX CRUDEOIL/CRUDEOILM research CLI (market data + Black-76 only).",
)
instruments_app = typer.Typer(no_args_is_help=True, help="Instrument master commands.")
chain_app = typer.Typer(no_args_is_help=True, help="Option-chain research commands.")
app.add_typer(instruments_app, name="instruments")
app.add_typer(chain_app, name="chain")

log = logging.getLogger(__name__)


def _settings() -> Settings:
    settings = get_settings()
    setup_logging(settings.log_level)
    return settings


def _parse_expiry(value: str) -> date:
    return date.fromisoformat(value)


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _iv_cell(status: IVStatus | None, iv: float | None) -> str:
    if iv is not None and status in {IVStatus.OK, IVStatus.STALE_PRICE}:
        flag = "" if status == IVStatus.OK else "*"
        return f"{iv * 100:.1f}{flag}"
    if status is None:
        return "—"
    return status.value[:6]


def format_chain_table(snapshot: OptionChainSnapshot) -> str:
    """Compact diagnostic table. Not a trading recommendation."""
    lines: list[str] = [
        f"Future: {snapshot.underlying_future_symbol}",
        (
            f"Future price: {_fmt(snapshot.future_price)} "
            f"({snapshot.future_price_source.value})"
        ),
        f"ATM: {_fmt(snapshot.atm_strike, 0)}",
        (
            f"ATM straddle mid: {_fmt(snapshot.atm_straddle_mid)} "
            f"({snapshot.straddle_price_source.value})"
        ),
        f"Snapshot quality: {snapshot.snapshot_quality.value}",
        (
            f"Risk-free rate: {_fmt(snapshot.risk_free_rate, 4)}  "
            f"T: {_fmt(snapshot.time_to_expiry, 6)} years  "
            f"expiry_time_source: {snapshot.expiry_time_source.value}"
        ),
        "This is diagnostic/research output, not a trading recommendation.",
        "",
        (
            f"{'CE IV':>8} {'Δ':>7} {'Mid':>8} {'OI':>10} {'Q':>6} "
            f"{'Strike':>8} "
            f"{'Q':<6} {'OI':<10} {'Mid':<8} {'Δ':<7} {'PE IV':<8} "
            f"{'dist':>7} {'d/str':>6}"
        ),
        "-" * 110,
    ]
    for row in snapshot.rows:
        ce_g = row.ce.greeks
        pe_g = row.pe.greeks
        ce_q = (
            "MISS"
            if row.ce.missing
            else (
                "STALE"
                if row.ce.quote_quality and row.ce.quote_quality.is_stale
                else (row.ce.quote_quality.price_source.value[:4] if row.ce.quote_quality else "—")
            )
        )
        pe_q = (
            "MISS"
            if row.pe.missing
            else (
                "STALE"
                if row.pe.quote_quality and row.pe.quote_quality.is_stale
                else (row.pe.quote_quality.price_source.value[:4] if row.pe.quote_quality else "—")
            )
        )
        marker = " *" if snapshot.atm_strike == row.strike else "  "
        lines.append(
            
                f"{_iv_cell(ce_g.iv_status if ce_g else None, ce_g.iv if ce_g else None):>8} "
                f"{_fmt(ce_g.delta if ce_g else None, 3):>7} "
                f"{_fmt(row.ce.derived_mid):>8} "
                f"{_fmt(row.ce.oi, 0):>10} "
                f"{ce_q:>6} "
                f"{row.strike:8.0f}{marker}"
                f"{pe_q:<6} "
                f"{_fmt(row.pe.oi, 0):<10} "
                f"{_fmt(row.pe.derived_mid):<8} "
                f"{_fmt(pe_g.delta if pe_g else None, 3):<7} "
                f"{_iv_cell(pe_g.iv_status if pe_g else None, pe_g.iv if pe_g else None):<8} "
                f"{_fmt(row.ce.distance_points or row.pe.distance_points, 0):>7} "
                f"{_fmt(row.ce.straddle_distance_ratio or row.pe.straddle_distance_ratio, 2):>6}"
            
        )
    valid_iv = sum(
        1
        for row in snapshot.rows
        for side in (row.ce, row.pe)
        if side.greeks and side.greeks.iv_status == IVStatus.OK and side.greeks.iv is not None
    )
    lines.append("")
    lines.append(f"Valid IV calculations (status=OK, mid, not stale): {valid_iv}")
    if snapshot.notes:
        lines.append("Notes: " + "; ".join(snapshot.notes))
    return "\n".join(lines)


def _load_master(settings: Settings, *, force: bool = False) -> list[Instrument]:
    broker = KiteMarketDataClient(settings) if settings.has_kite_credentials() else None
    return load_or_sync_instruments(
        data_dir=settings.data_dir,
        timezone_name=settings.timezone,
        broker=broker,
        force=force,
    )


def _build_live_chain(
    settings: Settings,
    *,
    underlying: str,
    expiry: date,
    rate: float | None,
    persist: bool,
) -> tuple[OptionChainSnapshot, str | None]:
    tz = parse_timezone(settings.timezone)
    now = datetime.now(tz=tz)
    master = _load_master(settings)
    from crude_research.market.contracts import resolve_underlying_future

    future = resolve_underlying_future(master, underlying=underlying, option_expiry=expiry)
    options = list_options(master, underlying, expiry)
    wanted = [future, *options]
    keys = [inst.kite_quote_key for inst in wanted]
    symbols = {inst.kite_quote_key: inst.tradingsymbol for inst in wanted}
    if not settings.has_kite_credentials():
        raise CrudeResearchError("KITE_API_KEY and KITE_ACCESS_TOKEN are required to snapshot live quotes.")
    client = KiteMarketDataClient(settings)
    quotes = fetch_full_quotes(
        client,
        keys,
        tradingsymbol_by_key=symbols,
        batch_size=settings.quote_batch_size,
        tz=tz,
        received_at=datetime.now(tz=UTC),
    )
    expiry_ts, expiry_source = assume_expiry_timestamp(
        expiry, tz=tz, expiry_time=settings.parsed_expiry_time()
    )
    t_years = time_to_expiry(now, expiry_ts, seconds_per_year=settings.seconds_per_year)
    used_rate = rate if rate is not None else settings.risk_free_rate
    snapshot = build_option_chain(
        underlying,
        expiry,
        quotes,
        master,
        now=now,
        stale_after_seconds=settings.stale_quote_seconds,
        rate=used_rate,
        time_years=t_years,
        expiry_timestamp=expiry_ts,
        expiry_time_source=expiry_source,
        vol_lower=settings.iv_vol_lower,
        vol_upper=settings.iv_vol_upper,
        compute_greeks=True,
    )
    path = None
    if persist:
        path = str(
            persist_chain_snapshot(
                snapshot, Path(settings.data_dir), timezone_name=settings.timezone
            )
        )
    return snapshot, path


@app.command("doctor")
def doctor() -> None:
    """Verify Python, config, data dir, credentials, and MCX instrument access."""
    settings = _settings()
    report = run_doctor(settings, try_network=True)
    for check in report.checks:
        mark = "OK" if check.ok else "FAIL"
        typer.echo(f"[{mark}] {check.name}: {check.detail}")
    if not report.ok:
        raise typer.Exit(code=1)


@instruments_app.command("sync")
def instruments_sync(
    force: Annotated[bool, typer.Option("--force", help="Re-download even if today's cache exists.")] = False,
) -> None:
    """Download the MCX instrument master and cache it as Parquet."""
    settings = _settings()
    if not settings.has_kite_credentials():
        typer.echo("KITE_API_KEY / KITE_ACCESS_TOKEN missing; cannot sync.")
        raise typer.Exit(code=1)
    records = _load_master(settings, force=force)
    crude = sum(1 for row in records if row.underlying and row.underlying.value == "CRUDEOIL")
    mini = sum(1 for row in records if row.underlying and row.underlying.value == "CRUDEOILM")
    typer.echo(f"MCX instruments: {len(records)} (CRUDEOIL={crude}, CRUDEOILM={mini})")


@instruments_app.command("expiries")
def instruments_expiries(underlying: str) -> None:
    """List option expiries and futures for CRUDEOIL or CRUDEOILM."""
    settings = _settings()
    records = _load_master(settings)
    typer.echo(f"Underlying: {underlying.upper()}")
    typer.echo("Futures:")
    for fut in list_futures(records, underlying):
        typer.echo(f"  {fut.tradingsymbol}  expiry={fut.expiry}  token={fut.instrument_token}  lot={fut.lot_size}")
    typer.echo("Option expiries:")
    for exp in list_option_expiries(records, underlying):
        n = len(list_options(records, underlying, exp))
        typer.echo(f"  {exp.isoformat()}  options={n}")


@chain_app.command("snapshot")
def chain_snapshot(
    underlying: Annotated[str, typer.Option("--underlying", help="CRUDEOIL or CRUDEOILM")],
    expiry: Annotated[str, typer.Option("--expiry", help="Option expiry YYYY-MM-DD")],
    risk_free_rate: Annotated[
        float | None,
        typer.Option("--risk-free-rate", help="Continuously compounded rate, decimal."),
    ] = None,
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
) -> None:
    """Fetch full quotes, reconstruct the chain, compute IV/Greeks, optionally persist."""
    settings = _settings()
    snapshot, path = _build_live_chain(
        settings,
        underlying=underlying,
        expiry=_parse_expiry(expiry),
        rate=risk_free_rate,
        persist=persist,
    )
    typer.echo(format_chain_table(snapshot))
    if path:
        typer.echo(f"\nPersisted: {path}")
        read_back = read_chain_snapshot(Path(path))
        typer.echo(f"Read-back rows: {len(read_back)}")


@chain_app.command("watch")
def chain_watch(
    underlying: Annotated[str, typer.Option("--underlying")],
    expiry: Annotated[str, typer.Option("--expiry", help="Option expiry YYYY-MM-DD")],
    interval: Annotated[float, typer.Option("--interval", help="Reprint interval seconds.")] = 5.0,
    risk_free_rate: Annotated[float | None, typer.Option("--risk-free-rate")] = None,
) -> None:
    """Subscribe FULL-mode ticks and reprint the research chain table."""
    settings = _settings()
    tz = parse_timezone(settings.timezone)
    exp = _parse_expiry(expiry)
    snapshot, _path = _build_live_chain(
        settings, underlying=underlying, expiry=exp, rate=risk_free_rate, persist=False
    )
    typer.echo(format_chain_table(snapshot))
    from crude_research.market.contracts import resolve_underlying_future
    from crude_research.zerodha.quotes import fetch_full_quotes as _fetch
    from crude_research.zerodha.websocket import MarketDataStream

    master = _load_master(settings)
    future = resolve_underlying_future(master, underlying=underlying, option_expiry=exp)
    options = list_options(master, underlying, exp)

    client = KiteMarketDataClient(settings)
    wanted = [future, *options]
    keys = [inst.kite_quote_key for inst in wanted]
    symbols = {inst.kite_quote_key: inst.tradingsymbol for inst in wanted}
    quotes = _fetch(
        client,
        keys,
        tradingsymbol_by_key=symbols,
        batch_size=settings.quote_batch_size,
        tz=tz,
    )
    by_token = {inst.instrument_token: inst.tradingsymbol for inst in wanted}

    def on_quote(quote: Quote) -> None:
        quotes[quote.instrument_token] = quote

    stream = MarketDataStream(
        settings,
        tz=tz,
        tradingsymbol_by_token=by_token,
        on_quote=on_quote,
    )
    stream.connect(threaded=True)
    if not stream.wait_connected(20):
        typer.echo("Websocket did not connect; showing REST snapshot only.")
        raise typer.Exit(code=1)
    stream.subscribe(list(by_token), mode="full")
    used_rate = risk_free_rate if risk_free_rate is not None else settings.risk_free_rate
    expiry_ts, expiry_source = assume_expiry_timestamp(
        exp, tz=tz, expiry_time=settings.parsed_expiry_time()
    )
    typer.echo("Watching FULL-mode ticks. Ctrl-C to stop. Illiquid strikes may not tick.")
    try:
        while True:
            time.sleep(max(1.0, interval))
            now = datetime.now(tz=tz)
            t_years = time_to_expiry(now, expiry_ts, seconds_per_year=settings.seconds_per_year)
            live = build_option_chain(
                underlying,
                exp,
                quotes,
                master,
                now=now,
                stale_after_seconds=settings.stale_quote_seconds,
                rate=used_rate,
                time_years=t_years,
                expiry_timestamp=expiry_ts,
                expiry_time_source=expiry_source,
                vol_lower=settings.iv_vol_lower,
                vol_upper=settings.iv_vol_upper,
            )
            typer.echo("\n" + format_chain_table(live))
    except KeyboardInterrupt:
        stream.shutdown()
        typer.echo("Stopped.")


if __name__ == "__main__":
    app()
