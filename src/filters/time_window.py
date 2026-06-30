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

    def _windows(self, instrument: str) -> dict:
        """Instrument-aware session/entry windows (MCX crude runs much later)."""
        if instrument == "crude_oil":
            mcx = self.cfg.get("crude", {})
            return {
                "open": mcx.get("market_open", "09:00"),
                "close": mcx.get("market_close", "23:30"),
                "no_entry_before": mcx.get("no_entry_before", "09:15"),
                "no_entry_after": mcx.get("no_entry_after", "23:00"),
                "force_exit_before": mcx.get("force_exit_before", "23:25"),
                "avoid_opening": mcx.get("avoid_opening_volatility", False),
            }
        return {
            "open": self.trading.get("market_open", "09:15"),
            "close": self.trading.get("market_close", "15:30"),
            "no_entry_before": self.cfg.get("no_entry_before", "09:30"),
            "no_entry_after": self.cfg.get("no_entry_after", "15:15"),
            "force_exit_before": self.cfg.get(
                "force_exit_before",
                get_yaml_config().get("exit_rules", {}).get("force_close_before", "15:20"),
            ),
            "avoid_opening": self.cfg.get("avoid_opening_volatility", True),
        }

    def is_market_open(self, instrument: str = "nifty50") -> bool:
        now = ist_now()
        if now.weekday() >= 5:
            return False
        w = self._windows(instrument)
        return self._parse_time(w["open"]) <= now.time() <= self._parse_time(w["close"])

    def get_session_phase(self, instrument: str = "nifty50") -> str:
        now = self._now()
        if instrument == "crude_oil":
            if not self.is_market_open(instrument):
                return "closed"
            return "commodity"
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
        w = self._windows(instrument)
        no_before = self._parse_time(w["no_entry_before"])
        no_after = self._parse_time(w["no_entry_after"])

        if now < no_before:
            return False, f"Opening volatility window — no entries before {no_before.strftime('%H:%M')} IST"

        if now >= no_after:
            return False, f"Late session — no new entries after {no_after.strftime('%H:%M')} IST"

        # Opening-volatility skip (first 15 min) — indices by default
        if w["avoid_opening"]:
            open_t = self._parse_time(w["open"])
            cutoff = self._parse_time(self._add_minutes(w["open"], 15))
            if open_t <= now < cutoff:
                return False, f"Avoid first 15 min after {open_t.strftime('%H:%M')} open"

        return True, f"OK ({self.get_session_phase(instrument)} session)"

    @staticmethod
    def _add_minutes(hhmm: str, minutes: int) -> str:
        h, m = map(int, hhmm.split(":"))
        total = h * 60 + m + minutes
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"

    def requires_force_exit(self, instrument: str = "nifty50") -> bool:
        if not self.is_market_open(instrument):
            return True
        w = self._windows(instrument)
        return self._now() >= self._parse_time(w["force_exit_before"])

    def preferred_strategy_hint(self, instrument: str = "nifty50") -> str:
        """Suggest trend vs range selling based on time of day."""
        phase = self.get_session_phase(instrument)
        if phase == "trend":
            return "trend"
        if phase == "range":
            return "range"
        return "neutral"
