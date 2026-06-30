"""13-step trade decision pipeline (pre-order checks)."""

from __future__ import annotations

from src.config import get_yaml_config
from src.filters.events import EventFilter
from src.filters.iv_analysis import IVAnalyzer
from src.filters.liquidity import LiquidityFilter
from src.filters.time_window import TimeWindowFilter


class TradePipeline:
    """
    Ordered checks before placing a trade:

    1. Trading hours  2. Time window  3. Liquidity (on strike)
    4. Trend/range    5. Volatility   6. News/events
    7. Strategy       8. Strike       9. Position size (risk mgr)
    10. Costs         11. Order       12. Manage       13. Exit
    """

    def __init__(self):
        self.time = TimeWindowFilter()
        self.liquidity = LiquidityFilter()
        self.events = EventFilter()
        self.iv = IVAnalyzer()
        self.config = get_yaml_config()

    def check_market_hours(self, instrument: str) -> tuple[bool, str]:
        if not self.time.is_market_open(instrument):
            return False, "Market closed"
        return True, "Market open"

    def check_entry_timing(self, instrument: str) -> tuple[bool, str]:
        return self.time.allows_new_entry(instrument)

    def check_sell_environment(
        self, instrument: str, vix: float, headlines: list[str] | None = None
    ) -> tuple[bool, str]:
        if self.config.get("option_selling", {}).get("block_near_events", True):
            ok, reason = self.events.allows_selling(instrument, headlines)
            if not ok:
                return False, reason

        # India VIX / IV percentile gate applies to index options only
        is_index = instrument in ("nifty50", "sensex")
        if is_index and self.config.get("option_selling", {}).get("min_iv_percentile"):
            ok, reason = self.iv.allows_selling(vix)
            if not ok:
                return False, reason

        return True, "Sell environment OK"

    def check_strike_liquidity(self, strike_info: dict, instrument: str) -> tuple[bool, str]:
        return self.liquidity.passes(strike_info, instrument)

    def run_pre_strategy_checks(
        self, instrument: str, trade_mode: str, vix: float = 0.0, headlines: list[str] | None = None
    ) -> tuple[bool, str]:
        ok, msg = self.check_market_hours(instrument)
        if not ok:
            return False, msg

        ok, msg = self.check_entry_timing(instrument)
        if not ok:
            return False, msg

        if trade_mode == "SELL_OPTION":
            ok, msg = self.check_sell_environment(instrument, vix, headlines)
            if not ok:
                return False, msg

        return True, f"Pre-checks passed ({self.time.get_session_phase()} session)"
