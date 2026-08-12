"""Signal alignment -- Phase 13.9.

Counts how many of Infusion's existing, independently-computed signal
families agree with a candidate's direction -- informational evidence
surfaced alongside a trade, NOT a suppression gate. Inspired by
olaxbt/ai-market-maker's weighted-arbitrator "N independent factors must
agree" pattern (researched this session), but unlike the F&O ban gate
(Phase 13.13, a hard legal/exchange constraint), breadth-of-agreement is
an unvalidated strategy hypothesis -- it follows the SAME "informational
only until feature-ablation earns it" governance as every other Phase
1-13.x field, not the F&O ban's exception.

Every family here is read from data each strategy's evaluate() already
has in hand at the point it builds features_snapshot -- no new detection
runs, purely a new aggregation over signals Infusion already computes.
Each family reports True (agrees with the candidate's direction), False
(disagrees), or is left out of the count entirely when that family has no
opinion right now (e.g. no active zone, ATR trend is NEUTRAL) -- an
absent family is not evidence against the trade, it's simply silent.
"""

from __future__ import annotations

BULLISH_CANDLES = {
    "Bullish Engulfing", "Hammer", "Bullish Marubozu", "Bullish Harami",
    "Bullish Pin Bar", "Three White Soldiers", "Piercing Line",
    "Morning Star", "Dragonfly Doji", "Strong Bull Candle", "Tweezer Bottom",
}
BEARISH_CANDLES = {
    "Bearish Engulfing", "Shooting Star", "Bearish Marubozu", "Bearish Harami",
    "Bearish Pin Bar", "Three Black Crows", "Dark Cloud Cover",
    "Evening Star", "Gravestone Doji", "Strong Bear Candle", "Tweezer Top",
}


def compute_signal_alignment(
    *,
    bullish: bool,
    ml: dict,
    ma_regime: dict | None,
    donchian: dict | None,
    wyckoff_sos_sow: dict | None,
    atr_trend: str,
    candle_pattern: str,
) -> dict:
    """8 independent families: market structure, candlestick pattern,
    supply/demand zone, ICT (FVG/order-block/liquidity-sweep combined),
    ATR/Supertrend regime, daily MA regime (Golden/Death Cross), Donchian
    fresh breakout, Wyckoff SOS/SOW trigger bar."""
    families: dict[str, bool | None] = {}

    trend_state = ml.get("trend_state")
    families["structure"] = (
        None if trend_state is None or trend_state == 0
        else (trend_state == 1) == bullish
    )

    if candle_pattern in BULLISH_CANDLES:
        families["candlestick"] = bullish
    elif candle_pattern in BEARISH_CANDLES:
        families["candlestick"] = not bullish
    else:
        families["candlestick"] = None

    has_demand = ml.get("demand_zone_top") is not None
    has_supply = ml.get("supply_zone_top") is not None
    if has_demand and not has_supply:
        families["zone"] = bullish
    elif has_supply and not has_demand:
        families["zone"] = not bullish
    else:
        families["zone"] = None  # neither active, or both at once -- no clean read

    ict_bull = bool(ml.get("fvg_bullish_ce")) or bool(ml.get("order_block_bullish_validated")) or ml.get("last_liquidity_sweep") == "sellside"
    ict_bear = bool(ml.get("fvg_bearish_ce")) or bool(ml.get("order_block_bearish_validated")) or ml.get("last_liquidity_sweep") == "buyside"
    if ict_bull and not ict_bear:
        families["ict"] = bullish
    elif ict_bear and not ict_bull:
        families["ict"] = not bullish
    else:
        families["ict"] = None

    at = str(atr_trend or "").upper()
    families["regime"] = (at == "BULL") == bullish if at in ("BULL", "BEAR") else None

    regime = (ma_regime or {}).get("regime")
    if regime == "golden_cross":
        families["ma_regime"] = bullish
    elif regime == "death_cross":
        families["ma_regime"] = not bullish
    else:
        families["ma_regime"] = None

    fresh_high = bool((donchian or {}).get("fresh_high_breakout"))
    fresh_low = bool((donchian or {}).get("fresh_low_breakout"))
    if fresh_high and not fresh_low:
        families["donchian"] = bullish
    elif fresh_low and not fresh_high:
        families["donchian"] = not bullish
    else:
        families["donchian"] = None

    sos_sow_type = (wyckoff_sos_sow or {}).get("type") if wyckoff_sos_sow else None
    if sos_sow_type == "SOS":
        families["wyckoff"] = bullish
    elif sos_sow_type == "SOW":
        families["wyckoff"] = not bullish
    else:
        families["wyckoff"] = None

    checked = {k: v for k, v in families.items() if v is not None}
    agree = [k for k, v in checked.items() if v]
    disagree = [k for k, v in checked.items() if not v]
    return {
        "alignment_agree_count": len(agree),
        "alignment_disagree_count": len(disagree),
        "alignment_checked_count": len(checked),
        "alignment_total_families": len(families),
        "alignment_agreeing_families": agree,
        "alignment_disagreeing_families": disagree,
    }
