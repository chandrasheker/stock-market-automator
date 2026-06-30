"""Time-of-day filters for entries and forced exits."""

from __future__ import annotations

from datetime import time as dt_time

from src.config import get_yaml_config
from src.utils.clock import ist_now, ist_time


class TimeWindowFilter:
    """Block entries during opening volatility and late session."""

    PHASES = {
        "opening": ("09:15", "09:30"),
        "trend": ("09:30", "11:30"),
        "range": ("11:30", "14:00"),
        "late": ("14:00", "15:15"),
        "close_only": ("15:15", "15:30"),
    }

    def __init__(self):
        self.cfg = get_yaml_config().get("time_filters", {})
        self.trading = get_yaml_config().get("trading", {})

    @property
    def enabled(self) -> bool:
        return self.cfg.get("enabled", True)

    def _parse_time(self, value: str) -> dt_time:
        h, m = map(int, value.split(":"))
        return dt_time(h, m)

    def _now(self) -> dt_time:
        return ist_time()

    def is_market_open(self, instrument: str = "nifty50") -> bool:
        now = ist_now()
        if now.weekday() >= 5:
            return False
        if instrument == "crude_oil":
            return dt_time(9, 0) <= now.time() <= dt_time(23, 30)
        open_t = self._parse_time(self.trading.get("market_open", "09:15"))
        close_t = self._parse_time(self.trading.get("market_close", "15:30"))
        return open_t <= now.time() <= close_t

    def get_session_phase(self) -> str:
        now = self._now()
        for phase, (start, end) in self.PHASES.items():
            if self._parse_time(start) <= now < self._parse_time(end):
                return phase
        return "closed"

    def allows_new_entry(self, instrument: str = "nifty50") -> tuple[bool, str]:
        if not self.enabled:
            return True, "Time filter disabled"

        if not self.is_market_open(instrument):
            return False, "Market closed"

        now = self._now()
        no_before = self._parse_time(self.cfg.get("no_entry_before", "09:30"))
        no_after = self._parse_time(self.cfg.get("no_entry_after", "15:15"))

        if now < no_before:
            return False, f"Opening volatility window — no entries before {no_before.strftime('%H:%M')} IST"

        if now >= no_after:
            return False, f"Late session — no new intraday entries after {no_after.strftime('%H:%M')} IST"

        phase = self.get_session_phase()
        if phase == "opening" and self.cfg.get("avoid_opening_volatility", True):
            return False, "Avoid 9:15–9:30 opening volatility"

        return True, f"OK ({phase} session)"

    def requires_force_exit(self, instrument: str = "nifty50") -> bool:
        if not self.is_market_open(instrument):
            return True
        force_before = self.cfg.get(
            "force_exit_before",
            get_yaml_config().get("exit_rules", {}).get("force_close_before", "15:20"),
        )
        return self._now() >= self._parse_time(force_before)

    def preferred_strategy_hint(self) -> str:
        """Suggest trend vs range selling based on time of day."""
        phase = self.get_session_phase()
        if phase == "trend":
            return "trend"
        if phase == "range":
            return "range"
        return "neutral"
