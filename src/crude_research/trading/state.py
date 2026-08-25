"""Durable SMA trade states. Small set, restart-safe."""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    DISARMED = "DISARMED"
    PAPER = "PAPER"
    LIVE_ARMED = "LIVE_ARMED"
    SAFE_HALT = "SAFE_HALT"


class TradeState(StrEnum):
    ENTRY_PENDING = "ENTRY_PENDING"
    HEDGE_FILLED = "HEDGE_FILLED"
    SHORT_PENDING = "SHORT_PENDING"
    POSITION_OPEN = "POSITION_OPEN"
    GTT_PENDING = "GTT_PENDING"
    PROTECTED = "PROTECTED"
    EXIT_TRIGGERED = "EXIT_TRIGGERED"
    EXIT_PARTIAL = "EXIT_PARTIAL"
    EXIT_RECOVERY = "EXIT_RECOVERY"
    SHORT_FLAT = "SHORT_FLAT"
    HEDGE_EXIT = "HEDGE_EXIT"
    CLOSED = "CLOSED"
    ABORTED = "ABORTED"
    SAFE_HALT = "SAFE_HALT"
