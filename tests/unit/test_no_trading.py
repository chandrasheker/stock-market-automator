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
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                offenders.append(f"{path.name}:{needle}")
    assert offenders == []
