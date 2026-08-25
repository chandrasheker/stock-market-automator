"""Read-only environment and market-data diagnostics. Never prints secrets."""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from crude_research.config import Settings
from crude_research.diagnostics.kite_auth import (
    describe_settings_load,
    format_kite_exception,
    token_exception_hints,
)
from crude_research.market.contracts import list_futures, list_option_expiries
from crude_research.market.models import Underlying
from crude_research.zerodha.client import KiteMarketDataClient
from crude_research.zerodha.instruments import (
    cache_path,
    load_or_sync_instruments,
    session_date_now,
)

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(CheckResult(name, ok, detail))


def _inspect_cached_master(report: DoctorReport, settings: Settings, *, required: bool) -> None:
    session = session_date_now(timezone_name=settings.timezone)
    path = cache_path(settings.data_dir, session)
    if not path.exists():
        report.add(
            "mcx_instrument_master",
            not required,
            f"no cache at {path}" if required else "skipped (no local cache)",
        )
        return
    try:
        records = load_or_sync_instruments(
            data_dir=settings.data_dir,
            timezone_name=settings.timezone,
            broker=None,
            force=False,
        )
        report.add(
            "mcx_instrument_master",
            True,
            f"local cache {path.name}: {len(records)} rows (live download skipped)",
        )
        crude = [row for row in records if row.underlying == Underlying.CRUDEOIL]
        mini = [row for row in records if row.underlying == Underlying.CRUDEOILM]
        report.add("CRUDEOIL_found", bool(crude), f"{len(crude)} instruments")
        report.add("CRUDEOILM_found", bool(mini), f"{len(mini)} instruments")
        fut_c = list_futures(records, "CRUDEOIL")
        fut_m = list_futures(records, "CRUDEOILM")
        report.add("CRUDEOIL_futures", bool(fut_c), ",".join(f.tradingsymbol for f in fut_c) or "none")
        report.add("CRUDEOILM_futures", bool(fut_m), ",".join(f.tradingsymbol for f in fut_m) or "none")
        exp_c = list_option_expiries(records, "CRUDEOIL")
        exp_m = list_option_expiries(records, "CRUDEOILM")
        report.add(
            "CRUDEOIL_option_expiries",
            bool(exp_c),
            ",".join(d.isoformat() for d in exp_c) or "none",
        )
        report.add(
            "CRUDEOILM_option_expiries",
            bool(exp_m),
            ",".join(d.isoformat() for d in exp_m) or "none",
        )
    except Exception as exc:
        report.add("mcx_instrument_master", False, f"{type(exc).__name__}: {exc}")


def run_doctor(settings: Settings, *, try_network: bool = True) -> DoctorReport:
    report = DoctorReport()
    version = sys.version_info
    report.add(
        "python_version",
        version >= (3, 12),
        f"{platform.python_version()} (require >= 3.12)",
    )
    report.add("config_loaded", True, f"timezone={settings.timezone} data_dir={settings.data_dir}")
    data_dir = Path(settings.data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        report.add("data_dir_writable", True, str(data_dir.resolve()))
    except OSError as exc:
        report.add("data_dir_writable", False, str(exc))

    creds = settings.has_kite_credentials()
    report.add(
        "kite_credentials",
        True,
        "present" if creds else "missing (offline/unit tests still work)",
    )
    for line in describe_settings_load(settings):
        report.add("kite_debug", True, line)
        log.info("doctor %s", line)

    report.add(
        "risk_free_rate",
        True,
        str(settings.risk_free_rate)
        if settings.risk_free_rate is not None
        else "unset (IV/Greeks disabled until provided)",
    )
    report.add(
        "expiry_time_assumption",
        True,
        f"{settings.option_expiry_time} {settings.timezone} (CONFIGURED_ASSUMPTION until official specs are wired)",
    )

    if not try_network or not creds:
        report.add("kite_connectivity", not try_network or not creds, "skipped")
        _inspect_cached_master(report, settings, required=False)
        return report

    try:
        log.info("Calling Kite profile() (read-only auth check; token values are not logged)")
        client = KiteMarketDataClient(settings)
        profile = client.profile()
        user_id = profile.get("user_id") or profile.get("user_name") or "connected"
        report.add("kite_connectivity", True, f"authenticated as {user_id}")
    except Exception as exc:
        detail = format_kite_exception(exc)
        report.add("kite_connectivity", False, detail)
        log.error("Kite connectivity failed: %s", detail)
        if type(exc).__name__ == "TokenException" or "token" in str(exc).lower():
            report.hints.extend(token_exception_hints(settings.kite_api_key, settings.kite_access_token))
        _inspect_cached_master(report, settings, required=False)
        return report

    try:
        records = load_or_sync_instruments(
            data_dir=settings.data_dir,
            timezone_name=settings.timezone,
            broker=client,
            now=datetime.now(tz=UTC),
        )
        report.add("mcx_instrument_master", True, f"{len(records)} rows")
        crude = [row for row in records if row.underlying == Underlying.CRUDEOIL]
        mini = [row for row in records if row.underlying == Underlying.CRUDEOILM]
        report.add("CRUDEOIL_found", bool(crude), f"{len(crude)} instruments")
        report.add("CRUDEOILM_found", bool(mini), f"{len(mini)} instruments")
        fut_c = list_futures(records, "CRUDEOIL")
        fut_m = list_futures(records, "CRUDEOILM")
        report.add("CRUDEOIL_futures", bool(fut_c), ",".join(f.tradingsymbol for f in fut_c) or "none")
        report.add("CRUDEOILM_futures", bool(fut_m), ",".join(f.tradingsymbol for f in fut_m) or "none")
        exp_c = list_option_expiries(records, "CRUDEOIL")
        exp_m = list_option_expiries(records, "CRUDEOILM")
        report.add(
            "CRUDEOIL_option_expiries",
            bool(exp_c),
            ",".join(d.isoformat() for d in exp_c) or "none",
        )
        report.add(
            "CRUDEOILM_option_expiries",
            bool(exp_m),
            ",".join(d.isoformat() for d in exp_m) or "none",
        )
    except Exception as exc:
        report.add("mcx_instrument_master", False, f"{type(exc).__name__}: {exc}")
    return report
