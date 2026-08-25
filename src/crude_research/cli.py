"""Research CLI. Market data and Black-76 only — no order placement."""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer

from crude_research import __version__
from crude_research.bias.engine import BiasSnapshot
from crude_research.bias.live import build_futures_bias
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
bias_app = typer.Typer(no_args_is_help=True, help="Mapped-futures bias / volatility / model-health.")
app.add_typer(instruments_app, name="instruments")
app.add_typer(chain_app, name="chain")
app.add_typer(bias_app, name="bias")
kite_app = typer.Typer(no_args_is_help=True, help="Kite session helpers (no order placement).")
app.add_typer(kite_app, name="kite")

log = logging.getLogger(__name__)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"crude-research {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show package version and exit.",
        ),
    ] = False,
) -> None:
    """MCX CRUDEOIL/CRUDEOILM research CLI (market data + Black-76 only)."""
    del version


def _settings() -> Settings:
    settings = get_settings()
    setup_logging(settings.log_level)
    return settings


def _parse_expiry(value: str) -> date:
    return date.fromisoformat(value)


def _echo_crude_error(exc: BaseException) -> None:
    from crude_research.diagnostics.kite_auth import (
        is_market_data_permission_error,
        permission_exception_hints,
    )

    typer.echo(f"{type(exc).__name__}: {exc}", err=True)
    from crude_research.exceptions import AuthenticationRequiredError

    if isinstance(exc, AuthenticationRequiredError):
        typer.echo("Authenticate for today's session: python -m crude_research.cli kite login-url", err=True)
        return
    if is_market_data_permission_error(exc):
        typer.echo("", err=True)
        typer.echo("How to fix PermissionException:", err=True)
        for hint in permission_exception_hints():
            typer.echo(f"  - {hint}", err=True)


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


def format_bias_snapshot(
    snapshot: BiasSnapshot,
    *,
    future_symbol: str,
    option_expiry: date,
) -> str:
    """Compact diagnostic dump. Not a trading recommendation."""
    lines = [
        f"Mapped future: {future_symbol}  (option expiry {option_expiry.isoformat()})",
        f"Bias: {snapshot.bias}  score={snapshot.score:+.0f}",
        f"allow_entry: {snapshot.allow_entry}  allow_live_entry: {snapshot.allow_live_entry}",
        (
            f"session_close_rule: {snapshot.session_close_rule}"
            if snapshot.session_close_rule
            else "session_close_rule: —"
        ),
        (
            "no_trade: "
            + (", ".join(snapshot.no_trade_reasons) if snapshot.no_trade_reasons else "—")
        ),
        (
            f"volatility: {snapshot.volatility}  ATR={_fmt(snapshot.atr)}  "
            f"pct={_fmt(snapshot.atr_percentile, 1)}  z={_fmt(snapshot.atr_zscore, 2)}  "
            f"range/ATR={_fmt(snapshot.range_atr, 2)}  ROC={_fmt(snapshot.atr_roc, 3)}"
        ),
        (
            f"model_health: {snapshot.model_health}  samples={snapshot.model_sample_count}  "
            f"accuracy={_fmt(snapshot.model_accuracy, 3)}  "
            f"recent={_fmt(snapshot.model_recent_accuracy, 3)}"
        ),
        f"supertrend daily/4H/1H: {snapshot.daily_st:+d}/{snapshot.h4_st:+d}/{snapshot.h1_st:+d}",
        "reasons:",
    ]
    if snapshot.reasons:
        lines.extend(f"  - {code}" for code in snapshot.reasons)
    else:
        lines.append("  - (none)")
    lines.append("This is diagnostic/research output, not a trading recommendation.")
    return "\n".join(lines)


def _load_master(settings: Settings, *, force: bool = False) -> list[Instrument]:
    from crude_research.auth.token import has_current_access_token

    broker = KiteMarketDataClient(settings) if has_current_access_token(settings) else None
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
    from crude_research.auth.token import require_access_token

    require_access_token(settings)
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
        if check.name == "kite_debug":
            typer.echo(f"[INFO] {check.detail}")
            continue
        mark = "OK" if check.ok else "FAIL"
        typer.echo(f"[{mark}] {check.name}: {check.detail}")
    if report.hints:
        typer.echo("")
        header = "How to fix:"
        if any("paid Kite Connect" in hint for hint in report.hints):
            header = "How to fix PermissionException:"
        elif any("access_token" in hint for hint in report.hints):
            header = "How to fix TokenException:"
        typer.echo(header)
        for hint in report.hints:
            typer.echo(f"  - {hint}")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
) -> None:
    """Run SMA HTTP: official Kite browser login, callback, and status."""
    import uvicorn

    from crude_research.http.app import create_app

    settings = _settings()
    if not settings.kite_api_key or not settings.kite_api_secret:
        typer.echo("KITE_API_KEY and KITE_API_SECRET are required to serve.", err=True)
        raise typer.Exit(code=1)
    if not settings.sma_base_url:
        typer.echo(
            "SMA_BASE_URL is required. Register "
            "${SMA_BASE_URL}/auth/zerodha/callback at https://developers.kite.trade",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"SMA listening on http://{host}:{port}")
    typer.echo(f"Zerodha callback: {settings.sma_base_url}/auth/zerodha/callback")
    uvicorn.run(create_app(settings), host=host, port=port)


@kite_app.command("login-url")
def kite_login_url() -> None:
    """Print the official Kite Connect browser login URL. Does not open a browser."""
    from crude_research.diagnostics.kite_auth import ensure_env_key

    settings = _settings()
    if not settings.kite_api_key:
        typer.echo("Set KITE_API_KEY in .env first.", err=True)
        raise typer.Exit(code=1)
    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        ensure_env_key(env_path, "KITE_API_SECRET")
    if not settings.kite_api_secret:
        typer.echo("KITE_API_SECRET is empty. git pull does not copy keys into .env.", err=True)
        typer.echo("Set it before exchanging a request_token (tokens expire in minutes):", err=True)
        typer.echo("  python -m crude_research.cli kite set-secret", err=True)
        typer.echo(
            "  or edit .env: KITE_API_SECRET=<api_secret from https://developers.kite.trade>",
            err=True,
        )
        typer.echo("If KITE_ACCESS_TOKEN is the 32-char console secret, copy that value into KITE_API_SECRET.", err=True)
        typer.echo("", err=True)
    from crude_research.zerodha.session import login_url

    url = login_url(settings.kite_api_key)
    typer.echo(url)
    typer.echo("")
    typer.echo("Run the next commands one at a time (do not paste this whole block):")
    typer.echo("1. Open that URL, log in with Kite (password/PIN/TOTP in the browser).")
    typer.echo("2. After redirect, copy the value of request_token from the URL query string.")
    typer.echo("3. python -m crude_research.cli kite session --request-token <that-value>")


@kite_app.command("set-secret")
def kite_set_secret() -> None:
    """Write KITE_API_SECRET to .env from a hidden prompt. Never prints the value."""
    from crude_research.config import clear_settings_cache
    from crude_research.diagnostics.kite_auth import mask_secret, upsert_env_value

    settings = _settings()
    secret = typer.prompt(
        "Paste api_secret from https://developers.kite.trade (input hidden)",
        hide_input=True,
    ).strip()
    if not secret:
        typer.echo("Empty secret; nothing written.", err=True)
        raise typer.Exit(code=1)
    env_path = Path.cwd() / ".env"
    upsert_env_value(env_path, "KITE_API_SECRET", secret)
    clear_settings_cache()
    typer.echo(f"Wrote KITE_API_SECRET to {env_path} fingerprint={mask_secret(secret)}")
    if settings.kite_access_token and secret == settings.kite_access_token:
        typer.echo(
            "That value matches KITE_ACCESS_TOKEN. If ACCESS_TOKEN was the console secret, "
            "that mix-up is now fixed; kite session will replace ACCESS_TOKEN after login."
        )
    typer.echo("Next, one command at a time:")
    typer.echo("  python -m crude_research.cli kite login-url")


@kite_app.command("session")
def kite_session(
    request_token: Annotated[str, typer.Option("--request-token", help="From the Kite login redirect URL.")],
    write_env: Annotated[bool, typer.Option("--write-env/--no-write-env", help="Update .env KITE_ACCESS_TOKEN.")] = True,
) -> None:
    """Exchange request_token + api_secret for today's access_token. No orders."""
    from crude_research.config import clear_settings_cache
    from crude_research.diagnostics.kite_auth import (
        is_placeholder_request_token,
        mask_secret,
        upsert_env_value,
    )
    from crude_research.zerodha.session import exchange_request_token

    settings = _settings()
    failed = False
    if is_placeholder_request_token(request_token):
        typer.echo(
            f"{request_token!r} is a documentation placeholder, not a token from Kite.",
            err=True,
        )
        typer.echo(
            "Open kite login-url, finish login, then pass request_token from the redirect URL.",
            err=True,
        )
        failed = True
    if not settings.kite_api_key:
        typer.echo("KITE_API_KEY is missing.", err=True)
        failed = True
    if not settings.kite_api_secret:
        typer.echo("KITE_API_SECRET is missing. git pull does not copy keys into .env.", err=True)
        typer.echo("  python -m crude_research.cli kite set-secret", err=True)
        typer.echo(
            "or add KITE_API_SECRET=<api_secret from https://developers.kite.trade> to .env (no quotes).",
            err=True,
        )
        if settings.kite_access_token and len(settings.kite_access_token) == 32:
            typer.echo(
                "KITE_ACCESS_TOKEN is 32 chars — that is also the length of api_secret. "
                "If that field is the console secret, copy it into KITE_API_SECRET.",
                err=True,
            )
        failed = True
    if failed:
        raise typer.Exit(code=1)
    try:
        payload = exchange_request_token(
            api_key=settings.kite_api_key or "",
            api_secret=settings.kite_api_secret or "",
            request_token=request_token.strip(),
        )
    except Exception as exc:
        from crude_research.diagnostics.kite_auth import format_kite_exception

        typer.echo(format_kite_exception(exc), err=True)
        raise typer.Exit(code=1) from exc
    access_token = str(payload["access_token"])
    user_id = payload.get("user_id")
    typer.echo(f"user_id: {user_id}")
    typer.echo(f"access_token fingerprint: {mask_secret(access_token)}")
    from crude_research.auth.token import default_store

    default_store(settings).save(access_token)
    if write_env:
        env_path = Path.cwd() / ".env"
        upsert_env_value(env_path, "KITE_ACCESS_TOKEN", access_token)
        clear_settings_cache()
        typer.echo(f"Wrote KITE_ACCESS_TOKEN to {env_path} (file is gitignored).")
        typer.echo("Re-run: python -m crude_research.cli doctor")
    else:
        typer.echo("Session stored for today's SMA process (token not printed).")


@instruments_app.command("sync")
def instruments_sync(
    force: Annotated[bool, typer.Option("--force", help="Re-download even if today's cache exists.")] = False,
) -> None:
    """Download the MCX instrument master and cache it as Parquet."""
    settings = _settings()
    from crude_research.auth.token import has_current_access_token

    if not has_current_access_token(settings):
        typer.echo("AUTHENTICATION_REQUIRED: Kite session is missing or expired; cannot sync.")
        raise typer.Exit(code=1)
    try:
        records = _load_master(settings, force=force)
    except Exception as exc:
        from crude_research.diagnostics.kite_auth import (
            format_kite_exception,
            token_exception_hints,
        )

        typer.echo(format_kite_exception(exc), err=True)
        if type(exc).__name__ == "TokenException" or type(exc).__name__ == "AuthenticationRequiredError":
            for hint in token_exception_hints(
                settings.kite_api_key,
                settings.kite_access_token,
                settings.kite_api_secret,
            ):
                typer.echo(f"  - {hint}", err=True)
        raise typer.Exit(code=1) from exc
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


@bias_app.command("show")
def bias_show(
    underlying: Annotated[str, typer.Option("--underlying", help="CRUDEOIL or CRUDEOILM")],
    expiry: Annotated[str, typer.Option("--expiry", help="Option expiry YYYY-MM-DD")],
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
) -> None:
    """Score mapped-futures bias, volatility regime, and model health. No orders."""
    settings = _settings()
    exp = _parse_expiry(expiry)
    try:
        master = _load_master(settings)
        from crude_research.auth.token import require_access_token
        from crude_research.market.contracts import resolve_underlying_future

        require_access_token(settings)
        future = resolve_underlying_future(master, underlying=underlying, option_expiry=exp)
        client = KiteMarketDataClient(settings)
        snapshot, written = build_futures_bias(client, future, settings, persist=persist)
    except CrudeResearchError as exc:
        _echo_crude_error(exc)
        raise typer.Exit(code=1) from exc
    typer.echo(format_bias_snapshot(snapshot, future_symbol=future.tradingsymbol, option_expiry=exp))
    if written:
        typer.echo("")
        for label, path in written.items():
            typer.echo(f"Persisted {label}: {path}")


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
    try:
        snapshot, path = _build_live_chain(
            settings,
            underlying=underlying,
            expiry=_parse_expiry(expiry),
            rate=risk_free_rate,
            persist=persist,
        )
    except CrudeResearchError as exc:
        _echo_crude_error(exc)
        raise typer.Exit(code=1) from exc
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
    try:
        snapshot, _path = _build_live_chain(
            settings, underlying=underlying, expiry=exp, rate=risk_free_rate, persist=False
        )
    except CrudeResearchError as exc:
        _echo_crude_error(exc)
        raise typer.Exit(code=1) from exc
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
