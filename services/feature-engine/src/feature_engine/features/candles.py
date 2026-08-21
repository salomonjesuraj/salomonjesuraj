"""Candlestick pattern detection — full set, matching
`simple_structure_pivot_ma_plan_v6.pine`'s pattern library exactly (names,
thresholds, and the EMA(14)-of-body sizing baseline for Marubozu / Three
Soldiers-Crows) so `candle_pattern` reads the same on the chart and in the
backend.

Replaces the old `_detect_candle_pattern()` in feature_engine/engine.py,
which only covered Inside Bar / Bullish-Bearish Engulfing / Doji / Hammer /
Shooting Star with flat range-ratio thresholds.
"""

from __future__ import annotations

MINTICK = 0.01
STRONG_BODY_PCT = 0.65  # Pine's candleBodyPct
DOJI_BODY_PCT = 0.08  # Pine's dojiBodyPct
MARUBOZU_BODY_EMA_MULT = 1.3
SOLDIER_CROW_BODY_EMA_MULT = 0.8
BODY_EMA_PERIOD = 14

# Phase 13.7 additions -- not in the source Pine script, hand-implemented
# in the same style/thresholds as the patterns above rather than pulled
# from TA-Lib (no TA-Lib dependency exists anywhere in this codebase, and
# its per-pattern-independent-signal model doesn't fit this function's
# single-highest-priority-match design -- adding a new C-library Docker
# dependency for 5 patterns wasn't worth breaking that consistency).
HARAMI_BODY_RATIO = 0.6  # current body must be <= this fraction of prior body
TWEEZER_TOLERANCE_PCT = 0.10  # highs/lows "nearly equal" within 10% of the larger candle's range
PIN_BAR_WICK_PCT = 0.66  # dominant wick >= this fraction of total range
PIN_BAR_BODY_PCT = 0.33  # body <= this fraction of total range
PIN_BAR_OPPOSITE_WICK_PCT = 0.15  # opposite wick must stay small


def body_pct(bars) -> float:
    """Latest bar's body/range ratio — matches Pine's `bodyPct`. Used by the
    strength meter's candle-body component (see scanner/pine_confidence.py)."""
    items = list(bars)
    if not items:
        return 0.0
    cur = items[-1]
    o, h, low, c = (float(cur.get(k, 0.0)) for k in ("o", "h", "l", "c"))
    rng = max(h - low, MINTICK)
    return abs(c - o) / rng


def update_body_ema(state, open_: float, close: float) -> None:
    """Advance the EMA(14)-of-body-size baseline. Call once per completed bar,
    same contract as the other update_* functions."""
    body = abs(close - open_)
    k = 2.0 / (BODY_EMA_PERIOD + 1)
    if not state.body_size_ema_initialized:
        state.body_size_ema = body
        state.body_size_ema_initialized = True
    else:
        state.body_size_ema = body * k + state.body_size_ema * (1 - k)


def detect_candle_pattern(bars, body_size_ema: float = 0.0) -> str:
    """Return the single most-significant pattern name for the latest bar,
    or "" if none. `bars` is the recent completed-1m-bar window (o/h/l/c
    dicts, newest last — same shape the engine already maintains).
    `body_size_ema` is the EMA(14)-of-body baseline (see `update_body_ema`),
    used for Marubozu / Three Soldiers-Crows sizing.
    """
    items = list(bars)
    if not items:
        return ""
    cur = items[-1]
    o, h, low, c = (float(cur.get(k, 0.0)) for k in ("o", "h", "l", "c"))
    rng = max(h - low, MINTICK)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - low
    body_pct = body / rng

    prev = items[-2] if len(items) >= 2 else None
    prev2 = items[-3] if len(items) >= 3 else None

    def bo(bar):
        return float(bar["o"]), float(bar["c"])

    bull_engulf = bear_engulf = piercing = dark_cloud = False
    morning_star = evening_star = False
    three_soldiers = three_crows = False

    if prev is not None:
        po, pc = bo(prev)
        bull_engulf = c > o and pc < po and o <= pc and c >= po
        bear_engulf = c < o and pc > po and o >= pc and c <= po
        piercing = pc < po and c > o and o < pc and c > (po + pc) / 2 and c < po
        dark_cloud = pc > po and c < o and o > pc and c < (po + pc) / 2 and c > po

    hammer = lower >= body * 2 and upper <= max(body * 0.60, MINTICK) and c >= low + rng * 0.60
    shooting_star = (
        upper >= body * 2 and lower <= max(body * 0.60, MINTICK) and c <= low + rng * 0.40
    )

    if prev is not None and prev2 is not None:
        po, pc = bo(prev)
        po2, pc2 = bo(prev2)
        prev_body = abs(pc - po)
        prev2_body = abs(pc2 - po2)
        morning_star = (
            pc2 < po2 and prev_body <= prev2_body * 0.55 and c > o and c >= (po2 + pc2) / 2
        )
        evening_star = (
            pc2 > po2 and prev_body <= prev2_body * 0.55 and c < o and c <= (po2 + pc2) / 2
        )

        ema = max(body_size_ema, MINTICK)
        three_soldiers = (
            c > o
            and pc > po
            and pc2 > po2
            and c > pc
            and pc > pc2
            and body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev2_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
        )
        three_crows = (
            c < o
            and pc < po
            and pc2 < po2
            and c < pc
            and pc < pc2
            and body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev2_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
        )

    ema = max(body_size_ema, MINTICK)
    marubozu_bull = (
        c > o
        and body >= ema * MARUBOZU_BODY_EMA_MULT
        and upper <= rng * 0.05
        and lower <= rng * 0.05
    )
    marubozu_bear = (
        c < o
        and body >= ema * MARUBOZU_BODY_EMA_MULT
        and upper <= rng * 0.05
        and lower <= rng * 0.05
    )

    bull_strong = c > o and body_pct >= STRONG_BODY_PCT and upper <= rng * 0.12
    bear_strong = c < o and body_pct >= STRONG_BODY_PCT and lower <= rng * 0.12
    is_doji = body <= rng * DOJI_BODY_PCT

    # ── Phase 13.7 additions ────────────────────────────
    bullish_harami = bearish_harami = False
    if prev is not None:
        po, pc = bo(prev)
        prev_body = abs(pc - po)
        prev_lo, prev_hi = (po, pc) if po < pc else (pc, po)
        contained = prev_lo < min(o, c) and max(o, c) < prev_hi
        small_enough = body <= prev_body * HARAMI_BODY_RATIO and prev_body > MINTICK
        bullish_harami = (
            pc < po and c > o and contained and small_enough
        )  # prior bearish, current small bullish inside it
        bearish_harami = (
            pc > po and c < o and contained and small_enough
        )  # prior bullish, current small bearish inside it

    tweezer_top = tweezer_bottom = False
    if prev is not None:
        po, pc = bo(prev)
        prev_h, prev_l = float(prev.get("h", 0.0)), float(prev.get("l", 0.0))
        prev_rng = max(prev_h - prev_l, MINTICK)
        tol = max(rng, prev_rng) * TWEEZER_TOLERANCE_PCT
        tweezer_top = pc > po and c < o and abs(h - prev_h) <= tol
        tweezer_bottom = pc < po and c > o and abs(low - prev_l) <= tol

    # Pin Bar requires a small-but-real body -- a near-zero body with the
    # same long-wick/short-opposite-wick shape is a Dragonfly/Gravestone
    # Doji instead (checked separately below), not a looser version of the
    # same pattern. Without this floor, every true doji-shaped rejection
    # candle also satisfies Pin Bar's looser body cap and -- since Pin Bar
    # sits earlier in this priority chain -- would always win, making the
    # Doji variants below unreachable (caught and fixed via a live test).
    bullish_pin_bar = (
        lower >= rng * PIN_BAR_WICK_PCT
        and rng * DOJI_BODY_PCT < body <= rng * PIN_BAR_BODY_PCT
        and upper <= rng * PIN_BAR_OPPOSITE_WICK_PCT
    )
    bearish_pin_bar = (
        upper >= rng * PIN_BAR_WICK_PCT
        and rng * DOJI_BODY_PCT < body <= rng * PIN_BAR_BODY_PCT
        and lower <= rng * PIN_BAR_OPPOSITE_WICK_PCT
    )

    dragonfly_doji = is_doji and lower >= rng * 0.6 and upper <= rng * 0.10
    gravestone_doji = is_doji and upper >= rng * 0.6 and lower <= rng * 0.10

    # Priority order matches Pine's `patternName` ternary chain exactly for
    # the original patterns; the 5 additions above are slotted in at the
    # tier that matches their signal strength (2-candle reversal patterns
    # near Piercing/Dark-Cloud, single-candle rejection near Hammer/
    # Shooting-Star-tier but after them since Pin Bar is a looser, more
    # general definition, Doji variants right before the generic Doji they'd
    # otherwise be swallowed by).
    if bull_engulf:
        return "Bullish Engulfing"
    if hammer:
        return "Hammer"
    if morning_star:
        return "Morning Star"
    if piercing:
        return "Piercing Line"
    if bullish_harami:
        return "Bullish Harami"
    if marubozu_bull:
        return "Bullish Marubozu"
    if three_soldiers:
        return "Three White Soldiers"
    if bullish_pin_bar:
        return "Bullish Pin Bar"
    if bull_strong:
        return "Strong Bull Candle"
    if bear_engulf:
        return "Bearish Engulfing"
    if shooting_star:
        return "Shooting Star"
    if evening_star:
        return "Evening Star"
    if dark_cloud:
        return "Dark Cloud Cover"
    if bearish_harami:
        return "Bearish Harami"
    if marubozu_bear:
        return "Bearish Marubozu"
    if three_crows:
        return "Three Black Crows"
    if bearish_pin_bar:
        return "Bearish Pin Bar"
    if bear_strong:
        return "Strong Bear Candle"
    if dragonfly_doji:
        return "Dragonfly Doji"
    if gravestone_doji:
        return "Gravestone Doji"
    if tweezer_bottom:
        return "Tweezer Bottom"
    if tweezer_top:
        return "Tweezer Top"
    if is_doji:
        return "Doji (indecision)"
    return ""
