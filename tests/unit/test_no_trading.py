"""Guardrail: research code must not grow order / GTT methods."""

from __future__ import annotations

from pathlib import Path

_FORBIDDEN = (
    "place_order",
    "modify_order",
    "cancel_order",
    "place_gtt",
    "modify_gtt",
    "delete_gtt",
    "place_gtt_order",
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "crude_research"


def test_no_order_or_gtt_calls() -> None:
    """Research packages must not grow order APIs. Execution lives only in trading/."""
    offenders: list[str] = []
    trading = _SRC / "trading"
    for path in _SRC.rglob("*.py"):
        if path.is_relative_to(trading):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                offenders.append(f"{path.name}:{needle}")
    assert offenders == []
