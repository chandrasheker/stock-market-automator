"""Application configuration loader."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


class EnvSettings(BaseSettings):
    kite_api_key: str = Field(default="", alias="KITE_API_KEY")
    kite_api_secret: str = Field(default="", alias="KITE_API_SECRET")
    kite_access_token: str = Field(default="", alias="KITE_ACCESS_TOKEN")
    trading_mode: str = Field(default="paper", alias="TRADING_MODE")
    capital: float = Field(default=500_000.0, alias="CAPITAL")
    max_risk_per_trade_pct: float = Field(default=2.0, alias="MAX_RISK_PER_TRADE_PCT")
    max_daily_loss_pct: float = Field(default=5.0, alias="MAX_DAILY_LOSS_PCT")
    max_open_positions: int = Field(default=3, alias="MAX_OPEN_POSITIONS")
    profit_target_pct: float = Field(default=20.0, alias="PROFIT_TARGET_PCT")
    stop_loss_pct: float = Field(default=15.0, alias="STOP_LOSS_PCT")
    news_api_key: str = Field(default="", alias="NEWS_API_KEY")
    dashboard_port: int = Field(default=8501, alias="DASHBOARD_PORT")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")

    model_config = {"populate_by_name": True, "extra": "ignore"}


@lru_cache
def get_env() -> EnvSettings:
    return EnvSettings()


@lru_cache
def get_yaml_config() -> dict[str, Any]:
    config_path = ROOT_DIR / "config" / "settings.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_data_dir() -> Path:
    cfg = get_yaml_config()
    data_dir = ROOT_DIR / cfg["data"]["cache_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_db_path() -> Path:
    cfg = get_yaml_config()
    db_path = ROOT_DIR / cfg["data"]["db_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path
