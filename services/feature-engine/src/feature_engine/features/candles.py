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
STRONG_BODY_PCT = 0.65        # Pine's candleBodyPct
DOJI_BODY_PCT = 0.08          # Pine's dojiBodyPct
MARUBOZU_BODY_EMA_MULT = 1.3
SOLDIER_CROW_BODY_EMA_MULT = 0.8
BODY_EMA_PERIOD = 14


def body_pct(bars) -> float:
    """Latest bar's body/range ratio — matches Pine's `bodyPct`. Used by the
    strength meter's candle-body component (see scanner/pine_confidence.py)."""
    items = list(bars)
    if not items:
        return 0.0
    cur = items[-1]
    o, h, l, c = (float(cur.get(k, 0.0)) for k in ("o", "h", "l", "c"))
    rng = max(h - l, MINTICK)
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
    o, h, l, c = (float(cur.get(k, 0.0)) for k in ("o", "h", "l", "c"))
    rng = max(h - l, MINTICK)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
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

    hammer = lower >= body * 2 and upper <= max(body * 0.60, MINTICK) and c >= l + rng * 0.60
    shooting_star = upper >= body * 2 and lower <= max(body * 0.60, MINTICK) and c <= l + rng * 0.40

    if prev is not None and prev2 is not None:
        po, pc = bo(prev)
        po2, pc2 = bo(prev2)
        prev_body = abs(pc - po)
        prev2_body = abs(pc2 - po2)
        morning_star = pc2 < po2 and prev_body <= prev2_body * 0.55 and c > o and c >= (po2 + pc2) / 2
        evening_star = pc2 > po2 and prev_body <= prev2_body * 0.55 and c < o and c <= (po2 + pc2) / 2

        ema = max(body_size_ema, MINTICK)
        three_soldiers = (
            c > o and pc > po and pc2 > po2
            and c > pc and pc > pc2
            and body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev2_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
        )
        three_crows = (
            c < o and pc < po and pc2 < po2
            and c < pc and pc < pc2
            and body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
            and prev2_body >= ema * SOLDIER_CROW_BODY_EMA_MULT
        )

    ema = max(body_size_ema, MINTICK)
    marubozu_bull = c > o and body >= ema * MARUBOZU_BODY_EMA_MULT and upper <= rng * 0.05 and lower <= rng * 0.05
    marubozu_bear = c < o and body >= ema * MARUBOZU_BODY_EMA_MULT and upper <= rng * 0.05 and lower <= rng * 0.05

    bull_strong = c > o and body_pct >= STRONG_BODY_PCT and upper <= rng * 0.12
    bear_strong = c < o and body_pct >= STRONG_BODY_PCT and lower <= rng * 0.12
    is_doji = body <= rng * DOJI_BODY_PCT

    # Priority order matches Pine's `patternName` ternary chain exactly.
    if bull_engulf:
        return "Bullish Engulfing"
    if hammer:
        return "Hammer"
    if morning_star:
        return "Morning Star"
    if piercing:
        return "Piercing Line"
    if marubozu_bull:
        return "Bullish Marubozu"
    if three_soldiers:
        return "Three White Soldiers"
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
    if marubozu_bear:
        return "Bearish Marubozu"
    if three_crows:
        return "Three Black Crows"
    if bear_strong:
        return "Strong Bear Candle"
    if is_doji:
        return "Doji (indecision)"
    return ""
