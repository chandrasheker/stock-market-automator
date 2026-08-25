"""Environment-driven settings. Credentials are never logged."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from crude_research.exceptions import ConfigurationError


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    kite_api_key: str | None = None
    kite_access_token: str | None = None
    kite_api_secret: str | None = None
    sma_base_url: str | None = None
    data_dir: Path = Path("./data")
    timezone: str = "Asia/Kolkata"
    risk_free_rate: float | None = None

    # Isolated API / model constants (overridable, not scattered as magic numbers).
    quote_batch_size: int = Field(default=500, ge=1, le=500)
    stale_quote_seconds: float = Field(default=120.0, gt=0)
    iv_vol_lower: float = Field(default=1e-6, gt=0)
    iv_vol_upper: float = Field(default=5.0, gt=0)
    option_expiry_time: str = "23:30:00"
    seconds_per_year: float = Field(default=365.25 * 24.0 * 3600.0, gt=0)
    websocket_reconnect_max_tries: int = Field(default=10, ge=1)
    websocket_reconnect_max_delay: int = Field(default=60, ge=1)
    log_level: str = "INFO"

    live_trading_enabled: bool = False

    bias_bullish_threshold: float = 60.0
    bias_bearish_threshold: float = -60.0
    model_health_min_samples: int = 20
    model_health_deterioration: float = 0.20
    prediction_horizon_4h: int = 5
    atr_period: int = 14
    atr_percentile_lookback: int = 100
    atr_extreme_percentile: float = 95.0
    atr_extreme_zscore: float = 2.5
    atr_extreme_range_mult: float = 2.5

    @field_validator(
        "kite_api_key",
        "kite_access_token",
        "kite_api_secret",
        "sma_base_url",
        "risk_free_rate",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            cleaned = value.strip().strip("\ufeff")
            if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
                cleaned = cleaned[1:-1].strip()
            if cleaned == "":
                return None
            return cleaned
        return value

    @field_validator("sma_base_url", mode="after")
    @classmethod
    def _strip_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.rstrip("/")

    @model_validator(mode="after")
    def _vol_bounds_ordered(self) -> Self:
        if self.iv_vol_upper <= self.iv_vol_lower:
            raise ConfigurationError("IV_VOL_UPPER must be greater than IV_VOL_LOWER")
        return self

    def parsed_expiry_time(self) -> time:
        """Parse `OPTION_EXPIRY_TIME` (HH:MM:SS). This is an explicit assumption."""
        parts = self.option_expiry_time.strip().split(":")
        if len(parts) != 3:
            raise ConfigurationError(
                f"OPTION_EXPIRY_TIME must be HH:MM:SS, got {self.option_expiry_time!r}"
            )
        try:
            hour, minute, second = (int(p) for p in parts)
            return time(hour=hour, minute=minute, second=second)
        except ValueError as exc:
            raise ConfigurationError(
                f"OPTION_EXPIRY_TIME is not a valid clock time: {self.option_expiry_time!r}"
            ) from exc

    def has_kite_credentials(self) -> bool:
        return bool(self.kite_api_key) and bool(self.kite_access_token)

    def require_kite_credentials(self) -> tuple[str, str]:
        if not self.has_kite_credentials():
            from crude_research.exceptions import CredentialsMissingError

            raise CredentialsMissingError(
                "KITE_API_KEY and KITE_ACCESS_TOKEN are required for live market data. "
                "Offline instrument/cache and unit tests still work without them."
            )
        assert self.kite_api_key is not None
        assert self.kite_access_token is not None
        return self.kite_api_key, self.kite_access_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
