"""Conviction scoring engine.

Deterministic scoring: same features → same score → same grade.
No randomness, no ML, no opaque logic.

Score components (0-100 total):
  Volume:    0-25 (relative volume magnitude)
  VWAP:      0-25 (distance from VWAP — fresh reclaim scores highest)
  RSI:       0-15 (sweet spot 50-65)
  EMA:       0-10 (alignment: ltp > ema9 > ema20)
  Flow:      0-10 (order imbalance magnitude)
  Bollinger: 0-10 (breakout or healthy width)
  Spread:    0-5  (tighter = more liquid = better)

Grade mapping:
  85-100 → A+  (exceptional)
  70-84  → A   (strong)
  55-69  → B   (moderate)
  40-54  → C   (marginal)
  0-39   → D   (weak — typically suppressed)
"""

from __future__ import annotations


def compute_conviction(features: dict) -> tuple[float, dict[str, float]]:
    """Compute conviction score from features.

    Args:
        features: feature snapshot dict from SignalCandidate

    Returns:
        (total_score, sub_scores_dict)

    Contract: deterministic — same features → same score.
    """
    sub_scores: dict[str, float] = {}
    pine_confidence = features.get("pine_confidence")
    if pine_confidence is not None:
        try:
            pine_confidence = float(pine_confidence)
        except (TypeError, ValueError):
            pine_confidence = None

    # ── Volume component (0-25) ────────────────────
    rel_vol = features.get("rel_vol_20d", 1.0)
    if rel_vol >= 5.0:
        sub_scores["volume"] = 25.0
    elif rel_vol >= 3.0:
        sub_scores["volume"] = 20.0
    elif rel_vol >= 2.0:
        sub_scores["volume"] = 15.0
    else:
        sub_scores["volume"] = 5.0

    # ── VWAP reclaim component (0-25) ──────────────
    direction = str(features.get("direction") or "bullish").lower()
    bearish = direction == "bearish"

    ltp = features.get("ltp", 0.0)
    vwap = features.get("vwap", 0.0)
    if vwap > 0:
        vwap_distance_pct = ((vwap - ltp) if bearish else (ltp - vwap)) / vwap * 100
    else:
        vwap_distance_pct = 0.0

    if 0 < vwap_distance_pct <= 0.3:
        sub_scores["vwap"] = 25.0          # fresh reclaim, tight
    elif vwap_distance_pct <= 0.8:
        sub_scores["vwap"] = 20.0
    elif vwap_distance_pct <= 1.5:
        sub_scores["vwap"] = 15.0
    else:
        sub_scores["vwap"] = 8.0

    # ── RSI component (0-15) ──────────────────────
    rsi = features.get("rsi_14", 50.0)
    if (not bearish and 50 <= rsi <= 65) or (bearish and 35 <= rsi <= 50):
        sub_scores["rsi"] = 15.0           # sweet spot
    elif (not bearish and (40 < rsi < 50 or 65 < rsi < 75)) or (bearish and (28 < rsi < 35 or 50 < rsi < 60)):
        sub_scores["rsi"] = 10.0
    else:
        sub_scores["rsi"] = 3.0

    # ── EMA alignment component (0-10) ─────────────
    ema_9 = features.get("ema_9", 0.0)
    ema_20 = features.get("ema_20", 0.0)
    if (not bearish and ltp > ema_9 > ema_20 > 0) or (bearish and ltp < ema_9 < ema_20 and ema_20 > 0):
        sub_scores["ema_alignment"] = 10.0  # full directional alignment
    elif (not bearish and ltp > ema_9 > 0) or (bearish and ltp < ema_9 and ema_9 > 0):
        sub_scores["ema_alignment"] = 5.0   # short EMA aligned
    else:
        sub_scores["ema_alignment"] = 0.0

    # ── Order flow component (0-10) ────────────────
    oi = features.get("order_imbalance", 0.0)
    flow = -oi if bearish else oi
    if flow > 0.3:
        sub_scores["order_flow"] = 10.0
    elif flow > 0.1:
        sub_scores["order_flow"] = 7.0
    elif flow > 0:
        sub_scores["order_flow"] = 3.0
    else:
        sub_scores["order_flow"] = 0.0

    # ── Bollinger component (0-10) ─────────────────
    bb_upper = features.get("bb_upper", 0.0)
    bb_width = features.get("bb_width", 0.0)
    squeeze_state = str(features.get("squeeze_state", "")).upper()
    nr_pattern = str(features.get("nr_pattern", ""))
    if bb_upper > 0 and ltp > bb_upper:
        sub_scores["bollinger"] = 10.0      # breaking out
    elif squeeze_state in {"EXTREME", "COILED"} or nr_pattern in {"NR4", "NR7"}:
        sub_scores["bollinger"] = 8.0       # compression/NR setup can expand fast
    elif bb_width > 0.02:
        sub_scores["bollinger"] = 5.0       # healthy width
    elif bb_width > 0.01:
        sub_scores["bollinger"] = 3.0
    else:
        sub_scores["bollinger"] = 0.0

    # ── Spread/liquidity component (0-5) ───────────
    atr_trend = str(features.get("atr_trend", "")).upper()
    candle = str(features.get("candle_pattern", ""))
    atr_ok = (not bearish and atr_trend == "BULL") or (bearish and atr_trend == "BEAR")
    candle_ok = (
        (not bearish and candle in {"Bullish Engulfing", "Hammer"})
        or (bearish and candle in {"Bearish Engulfing", "Shooting Star"})
    )
    sub_scores["atr_trail"] = 7.0 if atr_ok else 2.0 if atr_trend in {"BULL", "BEAR"} else 0.0
    sub_scores["candle"] = 3.0 if candle_ok else 0.0

    spread = features.get("spread_bps", 0.0)
    if spread < 10:
        sub_scores["spread"] = 5.0
    elif spread < 30:
        sub_scores["spread"] = 3.0
    elif spread < 50:
        sub_scores["spread"] = 1.0
    else:
        sub_scores["spread"] = 0.0

    raw_total = min(sum(sub_scores.values()), 100.0)
    if pine_confidence is not None:
        # Options-first scanner should behave closer to the user's Pine
        # decision model: trend/volume/VWAP still matter, but MTF confidence and
        # anti-chase quality must influence the final grade.
        total = raw_total * 0.58 + pine_confidence * 0.42
        sub_scores["pine_confidence"] = round(pine_confidence * 0.42, 2)
        if features.get("anti_chase_ok") is False:
            total -= 8.0
            sub_scores["anti_chase_penalty"] = -8.0
        rejection_reasons = features.get("rejection_reasons") or []
        if isinstance(rejection_reasons, list) and rejection_reasons:
            total -= min(10.0, len(rejection_reasons) * 3.0)
            sub_scores["rejection_penalty"] = -min(10.0, len(rejection_reasons) * 3.0)
        total = min(max(total, 0.0), 100.0)
    else:
        total = raw_total
    return total, sub_scores


def grade_conviction(score: float) -> str:
    """Map conviction score to letter grade.

    Deterministic: same score → same grade.
    """
    if score >= 85:
        return "A+"
    elif score >= 70:
        return "A"
    elif score >= 55:
        return "B"
    elif score >= 40:
        return "C"
    else:
        return "D"


def compute_risk_reward(
    entry: float, invalidation: float, target: float
) -> float:
    """Compute risk/reward ratio. Returns 0.0 if invalid."""
    if target < entry and invalidation > entry:
        risk = invalidation - entry
        reward = entry - target
    else:
        risk = entry - invalidation
        reward = target - entry
    if risk <= 0:
        return 0.0
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)
