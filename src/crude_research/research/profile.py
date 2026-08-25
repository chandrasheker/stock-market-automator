"""Immutable strategy-parameter snapshot. LIVE requires explicit approval."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from crude_research.config import Settings


class StrategyProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    approved: bool = False
    approved_at: str | None = None
    dte_min: int
    dte_max: int
    distance_ratio_min: float
    distance_ratio_max: float
    delta_min: float
    delta_max: float
    premium_margin_max: float
    short_premium_capture_target: float
    max_holding_days: int
    premium_stop_mult: float
    delta_stop: float
    fingerprint: str = ""

    def compute_fingerprint(self) -> str:
        payload = self.model_dump(exclude={"approved", "approved_at", "fingerprint", "name"})
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def profile_from_settings(settings: Settings, *, name: str = "v1") -> StrategyProfile:
    base = StrategyProfile(
        name=name,
        approved=False,
        dte_min=settings.dte_min,
        dte_max=settings.dte_max,
        distance_ratio_min=settings.distance_ratio_min,
        distance_ratio_max=settings.distance_ratio_max,
        delta_min=settings.delta_min,
        delta_max=settings.delta_max,
        premium_margin_max=settings.premium_margin_max,
        short_premium_capture_target=settings.short_premium_capture_target,
        max_holding_days=settings.max_holding_days,
        premium_stop_mult=settings.premium_stop_mult,
        delta_stop=settings.delta_stop,
    )
    return base.model_copy(update={"fingerprint": base.compute_fingerprint()})


def profile_path(data_dir: Path) -> Path:
    return Path(data_dir) / "profiles" / "strategy_profile.json"


def save_profile(profile: StrategyProfile, data_dir: Path) -> Path:
    path = profile_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = profile.model_copy(update={"fingerprint": profile.compute_fingerprint()})
    path.write_text(stamped.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_profile(data_dir: Path) -> StrategyProfile | None:
    path = profile_path(data_dir)
    if not path.exists():
        return None
    return StrategyProfile.model_validate_json(path.read_text(encoding="utf-8"))


def approve_profile(profile: StrategyProfile, data_dir: Path) -> StrategyProfile:
    """Explicit operator approval. Never auto-approve max-profit backtests."""
    approved = profile.model_copy(
        update={
            "approved": True,
            "approved_at": datetime.now(tz=UTC).isoformat(),
            "fingerprint": profile.compute_fingerprint(),
        }
    )
    save_profile(approved, data_dir)
    return approved


def settings_match_profile(settings: Settings, profile: StrategyProfile) -> bool:
    current = profile_from_settings(settings, name=profile.name)
    return current.fingerprint == profile.fingerprint


def profile_summary(profile: StrategyProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"approved": False, "fingerprint": None}
    return {
        "name": profile.name,
        "approved": profile.approved,
        "fingerprint": profile.fingerprint,
        "approved_at": profile.approved_at,
    }
