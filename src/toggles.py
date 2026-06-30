"""User toggles for instruments and trade actions (persisted locally)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import ROOT_DIR, get_yaml_config

TOGGLES_FILE = ROOT_DIR / "data" / "user_toggles.json"

ACTION_KEYS = ("buy_ce", "buy_pe", "sell_ce", "sell_pe")


def _default_toggles() -> dict[str, Any]:
    config = get_yaml_config()
    defaults = {}
    for inst, cfg in config.get("instruments", {}).items():
        actions = cfg.get("actions", {})
        defaults[inst] = {
            "enabled": cfg.get("enabled", True),
            "buy_ce": actions.get("buy_ce", True),
            "buy_pe": actions.get("buy_pe", True),
            "sell_ce": actions.get("sell_ce", True),
            "sell_pe": actions.get("sell_pe", True),
        }
    return defaults


def load_toggles() -> dict[str, Any]:
    defaults = _default_toggles()
    if not TOGGLES_FILE.exists():
        return defaults

    try:
        saved = json.loads(TOGGLES_FILE.read_text())
        for inst, vals in defaults.items():
            if inst in saved:
                vals.update(saved[inst])
        return defaults
    except Exception:
        return defaults


def save_toggles(toggles: dict[str, Any]):
    TOGGLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOGGLES_FILE.write_text(json.dumps(toggles, indent=2))


def is_instrument_enabled(instrument: str) -> bool:
    return load_toggles().get(instrument, {}).get("enabled", True)


def is_action_enabled(instrument: str, trade_mode: str, direction: str) -> bool:
    toggles = load_toggles().get(instrument, {})
    if not toggles.get("enabled", True):
        return False

    if trade_mode == "BUY_OPTION":
        key = "buy_ce" if direction == "BULLISH" else "buy_pe"
    else:
        key = "sell_ce" if direction == "BEARISH" else "sell_pe"

    return toggles.get(key, True)


def is_any_buy_enabled(instrument: str | None = None) -> bool:
    toggles = load_toggles()
    instruments = [instrument] if instrument else list(toggles.keys())
    for inst in instruments:
        t = toggles.get(inst, {})
        if t.get("enabled") and (t.get("buy_ce") or t.get("buy_pe")):
            return True
    return False


def is_any_sell_enabled(instrument: str | None = None) -> bool:
    toggles = load_toggles()
    instruments = [instrument] if instrument else list(toggles.keys())
    for inst in instruments:
        t = toggles.get(inst, {})
        if t.get("enabled") and (t.get("sell_ce") or t.get("sell_pe")):
            return True
    return False


def get_sell_recommendation_text() -> str:
    return (
        "Recommended: **SELL** OTM options (collect premium). "
        "Backtest: 97% win rate, net positive after taxes. "
        "Selling needs higher margin in your account but is the profitable strategy."
    )


def get_buy_warning_text() -> str:
    return (
        "Buy CE/PE is **not recommended** — backtest showed losses on NIFTY/SENSEX. "
        "Lower margin needed, but historically unprofitable. Enable only if you accept the risk."
    )


def apply_sell_only_defaults():
    """Reset toggles to recommended sell-only configuration."""
    toggles = _default_toggles()
    for inst in toggles:
        toggles[inst]["buy_ce"] = False
        toggles[inst]["buy_pe"] = False
        toggles[inst]["sell_ce"] = toggles[inst].get("enabled", True)
        toggles[inst]["sell_pe"] = toggles[inst].get("enabled", True)
    save_toggles(toggles)
    return toggles
