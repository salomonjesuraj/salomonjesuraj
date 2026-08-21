"""Heiken-Ashi smoothed candles -- Phase 13.7.

Standard recursive formula:
  HA_close = (O + H + L + C) / 4
  HA_open  = (prev_HA_open + prev_HA_close) / 2   (first bar: (O + C) / 2)
  HA_high  = max(H, HA_open, HA_close)
  HA_low   = min(L, HA_open, HA_close)

A different noise-filtering lens on trend than Infusion's existing
Supertrend + MA-stack + ADX (which already cover the same ground their own
way) -- correlated confirmation, not new information, hence lower priority
than everything else in this session's research batch. Two derived signals
per the original Phase 13 scoping:
  - ha_trend_streak: consecutive same-color HA candles with "no opposite
    wick" (a clean HA_low==HA_open for a bull candle / HA_high==HA_open for
    a bear candle, within a small tolerance) -- >=3 reads as trend
    continuation.
  - ha_doji: current HA candle's body is small relative to its range --
    a potential reversal trigger, especially paired with a fresh color
    change from the prior bar (both surfaced; the "at a key level" part of
    the original idea needs S/R context this module doesn't have, so it's
    left for the caller/strategy to combine with zones.py/structure.py --
    not fabricated here).

Informational only -- not wired into scoring until feature-ablation earns
it, same governance as every other Phase 1-13.x field.
"""

from __future__ import annotations

MINTICK = 0.01
DOJI_BODY_PCT = 0.08  # same threshold candles.py uses for its own Doji
CLEAN_WICK_TOLERANCE = 0.05  # wick <= 5% of range counts as "no opposite wick"
TREND_STREAK_MIN = 3  # Phase 13 spec: "3+ consecutive... = continuation"


def update_heiken_ashi(state, o: float, h: float, low: float, c: float) -> None:
    """Advance HA state by one completed bar. Call once per completed 1m
    bar, same contract as update_body_ema/update_structure."""
    ha_close = (o + h + low + c) / 4.0
    if not state.ha_initialized:
        ha_open = (o + c) / 2.0
        state.ha_initialized = True
    else:
        ha_open = (state.ha_open + state.ha_close) / 2.0

    ha_high = max(h, ha_open, ha_close)
    ha_low = min(low, ha_open, ha_close)

    state.ha_open = ha_open
    state.ha_close = ha_close
    state.ha_high = ha_high
    state.ha_low = ha_low

    rng = max(ha_high - ha_low, MINTICK)
    body = abs(ha_close - ha_open)
    bullish = ha_close >= ha_open
    upper_wick = ha_high - max(ha_open, ha_close)
    lower_wick = min(ha_open, ha_close) - ha_low
    clean = (
        (lower_wick <= rng * CLEAN_WICK_TOLERANCE)
        if bullish
        else (upper_wick <= rng * CLEAN_WICK_TOLERANCE)
    )

    if state.ha_streak_bullish is not None and bullish == state.ha_streak_bullish and clean:
        state.ha_trend_streak += 1
    else:
        state.ha_trend_streak = 1 if clean else 0
    state.ha_streak_bullish = bullish
    state.ha_prev_bullish_for_flip = state.ha_last_bullish
    state.ha_last_bullish = bullish
    state.ha_doji = body <= rng * DOJI_BODY_PCT


def heiken_ashi_snapshot(state) -> dict:
    if not state.ha_initialized:
        return {
            "ha_open": None,
            "ha_close": None,
            "ha_high": None,
            "ha_low": None,
            "ha_bullish": None,
            "ha_trend_streak": 0,
            "ha_trend": "NA",
            "ha_doji": False,
            "ha_color_flip": False,
        }
    trend = "NA"
    if state.ha_trend_streak >= TREND_STREAK_MIN:
        trend = "BULL" if state.ha_streak_bullish else "BEAR"
    color_flip = (
        state.ha_prev_bullish_for_flip is not None
        and state.ha_prev_bullish_for_flip != state.ha_last_bullish
    )
    return {
        "ha_open": round(state.ha_open, 2),
        "ha_close": round(state.ha_close, 2),
        "ha_high": round(state.ha_high, 2),
        "ha_low": round(state.ha_low, 2),
        "ha_bullish": state.ha_last_bullish,
        "ha_trend_streak": state.ha_trend_streak,
        "ha_trend": trend,
        "ha_doji": state.ha_doji,
        # A doji right after a color flip is the reversal-trigger reading
        # from the original spec ("HA Doji ... + color change = reversal
        # trigger") -- "at a key level" is left to the caller to combine
        # with existing S/R (zones.py / structure.py), not asserted here.
        "ha_color_flip": color_flip,
    }
