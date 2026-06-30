"""Liquidity filter — skip illiquid option contracts."""

from __future__ import annotations

from typing import Any

from src.config import get_yaml_config


class LiquidityFilter:
    """Reject strikes with wide spreads, low OI/volume, or tiny LTP."""

    def __init__(self):
        self.cfg = get_yaml_config().get("liquidity", {})

    @property
    def enabled(self) -> bool:
        return self.cfg.get("enabled", True)

    def passes(self, strike_row: dict[str, Any], instrument: str) -> tuple[bool, str]:
        if not self.enabled:
            return True, "Liquidity filter disabled"

        ltp = float(strike_row.get("premium") or strike_row.get("ltp") or 0)
        bid = float(strike_row.get("bid") or 0)
        ask = float(strike_row.get("ask") or 0)
        oi = int(strike_row.get("oi") or 0)
        volume = int(strike_row.get("volume") or 0)

        min_ltp = float(self.cfg.get("min_ltp", 15.0))
        if ltp < min_ltp:
            return False, f"LTP ₹{ltp:.2f} below minimum ₹{min_ltp:.0f}"

        inst_cfg = self.cfg.get("instruments", {}).get(instrument, {})
        min_oi = int(inst_cfg.get("min_oi", self.cfg.get("default_min_oi", 10000)))
        if oi < min_oi:
            return False, f"OI {oi:,} below minimum {min_oi:,}"

        min_vol = int(inst_cfg.get("min_volume", self.cfg.get("min_volume", 500)))
        if volume > 0 and volume < min_vol:
            return False, f"Volume {volume:,} below minimum {min_vol:,}"

        if bid > 0 and ask > 0 and ltp > 0:
            spread = ask - bid
            spread_pct = (spread / ltp) * 100
            max_pct = float(self.cfg.get("max_spread_pct", 1.0))
            max_rupees = float(self.cfg.get("max_spread_rupees", 2.0))
            if spread_pct > max_pct and spread > max_rupees:
                return False, (
                    f"Bid-ask spread ₹{spread:.2f} ({spread_pct:.1f}%) too wide "
                    f"(max {max_pct}% or ₹{max_rupees:.0f})"
                )
        elif ltp > 0 and (bid <= 0 or ask <= 0):
            # No quote depth — only allow if OI is well above threshold
            if oi < min_oi * 2:
                return False, "Missing bid/ask and OI not high enough for safe fill"

        return True, "Liquid"

    def filter_chain_rows(self, rows: list[dict], instrument: str) -> list[dict]:
        """Return only rows passing liquidity checks."""
        liquid = []
        for row in rows:
            ok, _ = self.passes(row, instrument)
            if ok:
                liquid.append(row)
        return liquid
