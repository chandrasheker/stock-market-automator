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


def direction_to_label(trade_mode: str, direction: str) -> str:
    if trade_mode == "BUY_OPTION":
        return f"BUY {'CE' if direction == 'BULLISH' else 'PE'}"
    return f"SELL {'CE' if direction == 'BEARISH' else 'PE'}"
