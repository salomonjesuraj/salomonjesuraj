"""RSI divergence -- Phase 13.11.

Regular and hidden divergence between price and RSI at confirmed swing
points, layered directly on the swing-pivot detection features/
structure.py already does (state.rsi_swing_points -- the exact same real
pivots as everything else that reads state.swing_points, just also
carrying the RSI value recorded at that instant). No new pivot detection
here, purely a comparison over the last two same-type pivots.

Four independent booleans, not a single forced verdict -- a bullish read
on the low side and a bearish read on the high side can both be true at
once (price chopping in a range), and collapsing that into one field
would hide real information rather than clarify it.

Standard definitions (unchanged from any TA reference):
  Regular bullish  -- price LOWER low,  RSI HIGHER low  -> reversal up
  Regular bearish  -- price HIGHER high, RSI LOWER high  -> reversal down
  Hidden bullish   -- price HIGHER low, RSI LOWER low   -> uptrend continues
  Hidden bearish   -- price LOWER high,  RSI HIGHER high -> downtrend continues
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def detect_rsi_divergence(
    rsi_swing_points: Iterable[tuple[float, str, int, float]],
) -> dict[str, Any]:
    points = list(rsi_swing_points)
    highs = [(price, rsi) for price, kind, _bar_idx, rsi in points if kind == "high"]
    lows = [(price, rsi) for price, kind, _bar_idx, rsi in points if kind == "low"]

    bearish_regular = bearish_hidden = False
    if len(highs) >= 2:
        (p1, r1), (p2, r2) = highs[-2], highs[-1]
        bearish_regular = p2 > p1 and r2 < r1
        bearish_hidden = p2 < p1 and r2 > r1

    bullish_regular = bullish_hidden = False
    if len(lows) >= 2:
        (p1, r1), (p2, r2) = lows[-2], lows[-1]
        bullish_regular = p2 < p1 and r2 > r1
        bullish_hidden = p2 > p1 and r2 < r1

    return {
        "rsi_divergence_bullish_regular": bullish_regular,
        "rsi_divergence_bullish_hidden": bullish_hidden,
        "rsi_divergence_bearish_regular": bearish_regular,
        "rsi_divergence_bearish_hidden": bearish_hidden,
    }
