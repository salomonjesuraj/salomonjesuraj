"""Market structure — fractal swing pivots, trend state, BOS / CHOCH events.

Mirrors `simple_structure_pivot_ma_plan_v6.pine`'s structure engine so the
backend's stated reason for a signal and the TradingView chart the user
manually confirms against never disagree:

  - A bar is a confirmed swing high/low once `right` bars have closed after
    it and it was the local extreme over the `left + right` bar window
    (Pine's `ta.pivothigh(left, right)` / `ta.pivotlow(left, right)`).
  - The two most recent confirmed swing highs/lows are kept, matching Pine's
    persistent `swingHigh1/swingHigh2`, `swingLow1/swingLow2`.
  - trend_state: 1 = uptrend (HH/HL), -1 = downtrend (LH/LL), 0 = range.
  - A close beyond the last swing high/low (plus an ATR buffer) fires a
    break event, labeled "Bullish/Bearish BOS" (continuation, same-direction
    trend) or "Bullish/Bearish CHOCH" (reversal, opposite-direction trend) —
    exactly Pine's `lastEventLabel` logic.

This module is a pure function operating on `SymbolState` — no I/O, no
Redis, matching the feature engine's existing update_* functions in
features/price.py, features/momentum.py, etc.

Note on break-event timing: Pine fires on `ta.crossover(close, level)`, a
true one-bar cross. Here we dedupe by remembering which swing-level value
last triggered a break (`state.last_break_high`/`last_break_low`) so a
break only fires once per distinct swing level rather than every bar price
stays beyond it — equivalent in effect for a live incremental system,
without needing a repaint-free crossover primitive.
"""

from __future__ import annotations

from typing import Any

from feature_engine.state import SymbolState

DEFAULT_LEFT = 2
DEFAULT_RIGHT = 2
BREAK_BUFFER_ATR = 0.10


def update_structure(
    state: SymbolState, left: int = DEFAULT_LEFT, right: int = DEFAULT_RIGHT, rsi: float = 50.0
) -> None:
    """Advance the fractal pivot / BOS-CHOCH state machine by one completed bar.

    Reads `state.recent_1m_bars` (already maintained by the engine on every
    completed 1m bar) and `state.atr`. No-ops until enough bars exist to
    confirm a pivot. Sets `state.structure_event = True` only on the bar a
    break just fired, so callers can tell "state changed this tick" apart
    from "state is still whatever it was."

    `rsi` (optional, Phase 13.11) is recorded alongside any swing point
    confirmed this call into state.rsi_swing_points -- see that field's
    docstring in state.py for why this lives in a separate deque rather
    than widening swing_points' own tuples.
    """
    items = list(state.recent_1m_bars)
    window = left + right + 1
    state.structure_event = False
    if len(items) < window:
        return

    surrounding = items[-window:]
    candidate = surrounding[-(right + 1)]
    highs = [b["h"] for b in surrounding]
    lows = [b["l"] for b in surrounding]
    cand_high = candidate["h"]
    cand_low = candidate["l"]

    if cand_high == max(highs) and highs.count(cand_high) == 1:
        state.swing_high_2 = state.swing_high_1
        state.swing_high_1 = cand_high
        state.swing_points.append((cand_high, "high", state.completed_1m_bars))
        state.rsi_swing_points.append((cand_high, "high", state.completed_1m_bars, rsi))

    if cand_low == min(lows) and lows.count(cand_low) == 1:
        state.swing_low_2 = state.swing_low_1
        state.swing_low_1 = cand_low
        state.swing_points.append((cand_low, "low", state.completed_1m_bars))
        state.rsi_swing_points.append((cand_low, "low", state.completed_1m_bars, rsi))

    close = items[-1]["c"]
    buf = max(state.atr, 0.0) * BREAK_BUFFER_ATR

    bullish_break = state.swing_high_1 is not None and close > state.swing_high_1 + buf
    bearish_break = state.swing_low_1 is not None and close < state.swing_low_1 - buf

    if bullish_break and state.last_break_high != state.swing_high_1:
        state.last_event_label = "Bullish CHOCH" if state.trend_state == -1 else "Bullish BOS"
        state.trend_state = 1
        state.last_break_high = state.swing_high_1
        state.structure_event = True
    elif bearish_break and state.last_break_low != state.swing_low_1:
        state.last_event_label = "Bearish CHOCH" if state.trend_state == 1 else "Bearish BOS"
        state.trend_state = -1
        state.last_break_low = state.swing_low_1
        state.structure_event = True


def trend_text(trend_state: int) -> str:
    """Human-readable trend label — matches Pine's `trendText`."""
    if trend_state == 1:
        return "UPTREND (HH/HL)"
    if trend_state == -1:
        return "DOWNTREND (LH/LL)"
    return "RANGE / UNDEFINED"


def structure_snapshot(state: SymbolState) -> dict[str, Any]:
    """Compact dict for FeatureVectorV1.ml_features — everything a consumer
    (scanner strategies, pine_confidence.py, dashboard) needs to render the
    same structure story TradingView shows."""
    return {
        "trend_state": state.trend_state,
        "trend_text": trend_text(state.trend_state),
        "last_event_label": state.last_event_label,
        "structure_event": state.structure_event,
        "swing_high_1": state.swing_high_1,
        "swing_high_2": state.swing_high_2,
        "swing_low_1": state.swing_low_1,
        "swing_low_2": state.swing_low_2,
    }
