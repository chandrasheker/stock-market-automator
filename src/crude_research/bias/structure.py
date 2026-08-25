"""Confirmed swing structure. Pivots need completed bars on both sides; no repaint."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from crude_research.market.candles import Bar

Structure = Literal["HH_HL", "LH_LL", "UNCLEAR"]


def confirmed_pivots(
    bars: Sequence[Bar],
    *,
    left: int = 2,
    right: int = 2,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (swing_highs, swing_lows) as (index, price) using only confirmed pivots.

    Index `i` is a pivot only when bars `i-left` through `i+right` all exist.
    """
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    n = len(bars)
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    last = n - right
    for i in range(left, last):
        window_h = highs[i - left : i + right + 1]
        window_l = lows[i - left : i + right + 1]
        if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
            swing_highs.append((i, highs[i]))
        if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def classify_structure(bars: Sequence[Bar], *, left: int = 2, right: int = 2) -> Structure:
    highs, lows = confirmed_pivots(bars, left=left, right=right)
    if len(highs) < 2 or len(lows) < 2:
        return "UNCLEAR"
    h1, h2 = highs[-2][1], highs[-1][1]
    l1, l2 = lows[-2][1], lows[-1][1]
    if h2 > h1 and l2 > l1:
        return "HH_HL"
    if h2 < h1 and l2 < l1:
        return "LH_LL"
    return "UNCLEAR"
