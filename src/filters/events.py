"""Block trading near major market events."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from src.config import get_yaml_config


class EventFilter:
    """Avoid new premium selling before RBI, Fed, budget, expiry, etc."""

    def __init__(self):
        self.cfg = get_yaml_config().get("events", {})

    @property
    def enabled(self) -> bool:
        return self.cfg.get("enabled", True)

    def blocking_event(self, headlines: list[str] | None = None) -> tuple[bool, str]:
        if not self.enabled:
            return False, ""

        today = date.today().isoformat()
        for blocked in self.cfg.get("blocked_dates", []):
            if str(blocked) == today:
                return True, f"Manual event block for {today}"

        if headlines:
            keywords = [k.lower() for k in self.cfg.get("keywords", [])]
            for headline in headlines:
                text = headline.lower()
                for kw in keywords:
                    if kw in text:
                        return True, f"Major event in news: '{headline[:80]}'"

        return False, ""

    def is_expiry_day(self, instrument: str) -> bool:
        """NIFTY weekly expiry Tue, monthly last Tue; approximate with Thursday caution."""
        if not self.cfg.get("block_expiry_day_sells", True):
            return False
        now = datetime.now()
        # NIFTY weekly expiry is Tuesday; block new sells on Tue afternoon
        if instrument in ("nifty50", "sensex") and now.weekday() == 1 and now.hour >= 12:
            return True
        return False

    def allows_selling(self, instrument: str, headlines: list[str] | None = None) -> tuple[bool, str]:
        blocked, reason = self.blocking_event(headlines)
        if blocked:
            return False, reason
        if self.is_expiry_day(instrument):
            return False, "Expiry day — avoid new short premium after noon"
        return True, "No blocking events"
