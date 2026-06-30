"""Block trading near major scheduled market events."""

from __future__ import annotations

import re

from src.config import get_yaml_config
from src.utils.clock import ist_now, ist_today

# Precise multi-word phrases that indicate an actual scheduled event,
# not routine market commentary. Matched as whole phrases (word boundaries).
DEFAULT_EVENT_PHRASES = [
    "rbi policy",
    "rbi monetary policy",
    "monetary policy committee",
    "mpc meeting",
    "mpc decision",
    "repo rate decision",
    "rate decision",
    "union budget",
    "interim budget",
    "fomc meeting",
    "fomc decision",
    "fed rate decision",
    "fed policy",
    "fed decision",
    "election results",
    "election result",
    "exit poll",
]


class EventFilter:
    """Avoid new premium selling before RBI, Fed, budget, elections, etc."""

    def __init__(self):
        self.cfg = get_yaml_config().get("events", {})

    @property
    def enabled(self) -> bool:
        return self.cfg.get("enabled", True)

    def _phrases(self) -> list[str]:
        return [p.lower() for p in self.cfg.get("keywords", DEFAULT_EVENT_PHRASES)]

    def blocking_event(self, headlines: list[str] | None = None) -> tuple[bool, str]:
        if not self.enabled:
            return False, ""

        today = ist_today().isoformat()
        for blocked in self.cfg.get("blocked_dates", []):
            if str(blocked) == today:
                return True, f"Manual event block for {today}"

        if not self.cfg.get("block_on_news_keywords", True):
            return False, ""

        if headlines:
            phrases = self._phrases()
            for headline in headlines:
                text = headline.lower()
                for phrase in phrases:
                    # Whole-phrase, word-boundary match to avoid false positives
                    if re.search(r"\b" + re.escape(phrase) + r"\b", text):
                        return True, f"Scheduled event in news: '{headline[:80]}'"

        return False, ""

    def is_expiry_day(self, instrument: str) -> bool:
        """Block new index sells on weekly expiry day (opt-in, off by default)."""
        if not self.cfg.get("block_expiry_day_sells", False):
            return False
        now = ist_now()
        # NIFTY weekly expiry is Tuesday; only caution in the final hours
        block_after_hour = int(self.cfg.get("expiry_block_after_hour", 14))
        if instrument in ("nifty50", "sensex") and now.weekday() == 1 and now.hour >= block_after_hour:
            return True
        return False

    def allows_selling(self, instrument: str, headlines: list[str] | None = None) -> tuple[bool, str]:
        blocked, reason = self.blocking_event(headlines)
        if blocked:
            return False, reason
        if self.is_expiry_day(instrument):
            return False, "Expiry-day final hours — avoid new short premium"
        return True, "No blocking events"
