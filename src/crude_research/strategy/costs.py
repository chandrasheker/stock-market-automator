"""Round-trip cost gates. Live uses broker charges when present; history is estimated."""

from __future__ import annotations

from dataclasses import dataclass

from crude_research.config import Settings


@dataclass(frozen=True)
class CostEstimate:
    expected_gross_target: float
    expected_costs: float
    cost_ratio: float
    profit_to_cost: float | None
    source: str


def estimate_round_trip(
    *,
    short_entry: float,
    long_entry: float,
    lot_quantity: float,
    capture: float,
    settings: Settings,
    live_charges: float | None,
    short_spread: float,
    long_spread: float,
) -> CostEstimate:
    """Conservative costs: half-spread + extra slippage on four legs, plus charges."""
    short_premium_value = short_entry * lot_quantity
    expected_gross = capture * short_premium_value
    slip = settings.extra_slippage_pct
    spread_cost = (short_spread + long_spread) * lot_quantity
    extra = slip * (short_entry + long_entry) * 2.0 * lot_quantity
    if live_charges is not None and live_charges > 0:
        charges = live_charges
        source = "LIVE_CHARGES"
    else:
        notion = (short_entry + long_entry) * 2.0 * lot_quantity
        charges = settings.estimated_round_trip_charge_pct * notion
        source = "ESTIMATED"
    costs = spread_cost + extra + charges
    ratio = costs / expected_gross if expected_gross > 0 else 999.0
    p2c = expected_gross / costs if costs > 0 else None
    return CostEstimate(expected_gross, costs, ratio, p2c, source)


def economics_ok(estimate: CostEstimate, settings: Settings) -> bool:
    if estimate.expected_gross_target <= 0:
        return False
    if estimate.cost_ratio > settings.cost_ratio_max:
        return False
    if estimate.profit_to_cost is None or estimate.profit_to_cost < settings.profit_to_cost_min:
        return False
    return True
