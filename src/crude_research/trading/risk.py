"""Position sizing and live-arm / scaling gates. No martingale."""

from __future__ import annotations

from crude_research.config import Settings
from crude_research.strategy import reasons
from crude_research.trading.state import Mode


def lots_for_defined_loss(*, max_defined_loss_per_lot: float, equity: float, settings: Settings) -> int:
    if max_defined_loss_per_lot <= 0 or equity <= 0:
        return 0
    budget = settings.max_defined_loss_pct * equity
    raw = int(budget // max_defined_loss_per_lot)
    return max(0, raw)


def live_lot_cap(settings: Settings, computed: int) -> int:
    return min(computed, settings.live_max_lots)


def arm_blockers(
    settings: Settings,
    *,
    mode: Mode,
    authenticated: bool,
    market_data_ok: bool,
    quotes_fresh: bool,
    profile_approved: bool,
    static_ip: bool,
    broker_clear: bool,
    daily_halt: bool,
    weekly_halt: bool,
    model_health: str | None = None,
) -> list[str]:
    codes: list[str] = []
    if not authenticated:
        codes.append(reasons.AUTH_REQUIRED)
    if not market_data_ok or not quotes_fresh:
        codes.append(reasons.STALE_DATA)
    if not profile_approved:
        codes.append(reasons.UNAPPROVED_PROFILE)
    if not settings.live_trading_enabled:
        codes.append(reasons.LIVE_DISARMED)
    if mode != Mode.LIVE_ARMED and not settings.live_trading_enabled:
        codes.append(reasons.LIVE_DISARMED)
    if not static_ip or not settings.live_static_ip_configured:
        codes.append(reasons.STATIC_IP_NOT_CONFIRMED)
    if not broker_clear:
        codes.append(reasons.BROKER_STATE_UNRESOLVED)
    if daily_halt or weekly_halt:
        codes.append(reasons.RISK_LIMIT)
    if model_health == "WARMING_UP":
        codes.append(reasons.MODEL_HEALTH_WARMING_UP)
    if model_health == "DEGRADED":
        codes.append(reasons.MODEL_HEALTH_DEGRADED)
    return codes


def scale_allowed(
    *,
    settings: Settings,
    trades: int,
    expiries: int,
    profit_factor: float,
    expectancy: float,
    cost_ratio: float,
    max_dd: float,
    safety_incidents: int,
    same_profile: bool,
) -> bool:
    if safety_incidents > 0 or not same_profile:
        return False
    if trades < settings.scale_min_trades or expiries < settings.scale_min_expiries:
        return False
    if expectancy <= 0 or profit_factor < settings.scale_min_profit_factor:
        return False
    if cost_ratio > settings.scale_max_cost_ratio:
        return False
    if max_dd > settings.weekly_drawdown_pct:
        return False
    return True
