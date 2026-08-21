"""Pine-script style confidence and rejection helpers for options setups.

The user's TradingView workflow was useful because it answered four practical
questions quickly:
  1) Which side has control?
  2) Do multiple timeframes agree?
  3) Am I chasing a stretched candle?
  4) Where are entry, stop, and targets?

This module keeps that logic deterministic and lightweight so scanner output,
dashboard rows, and Telegram alerts all speak the same language.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

MTF_CACHE_STALE_SEC = 900  # beyond this, prefer the synthetic live proxy


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _f(features: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(features.get(key) or default)
    except (TypeError, ValueError):
        return default


def compute_strength_meter(features: dict, bullish: bool) -> float:
    """0-100 continuous conviction read, matching
    simple_structure_pivot_ma_plan_v6.pine's Strength Meter exactly:
    ADX level (40) + EMA-stack-spread-vs-ATR (25) + Supertrend/VWAP
    agreement (25) + candle body ratio (10).

    Reads the ADX/Supertrend/candle-body values feature_engine now computes
    (services/feature-engine/src/feature_engine/features/{momentum,volatility,
    candles}.py) via the FeatureVectorV1.ml_features free-form dict.
    """
    ml = features.get("ml_features") or {}
    ltp = _f(features, "ltp")
    ema5 = _f(features, "ema_5")
    ema20 = _f(features, "ema_20")
    atr = _f(features, "atr_14")
    vwap = _f(features, "vwap")
    adx = _f(ml, "adx")
    supertrend_bullish = bool(ml.get("supertrend_bullish", True))
    body_ratio = _f(ml, "candle_body_pct")

    atr_safe = atr if atr > 0 else max(ltp * 0.004, 0.05)

    adx_part = clamp(adx, 0.0, 50.0) / 50.0 * 40.0

    ema_spread = abs(ema5 - ema20) / (atr_safe * 2.0) if atr_safe > 0 else 0.0
    ema_part = clamp(ema_spread, 0.0, 1.0) * 25.0

    above_vwap = ltp > vwap > 0
    below_vwap = 0 < ltp < vwap
    st_vwap_agree = (
        (supertrend_bullish and above_vwap) if bullish else (not supertrend_bullish and below_vwap)
    )
    st_vwap_part = 25.0 if st_vwap_agree else 12.0

    body_part = clamp(body_ratio, 0.0, 1.0) * 10.0

    return round(clamp(adx_part + ema_part + st_vwap_part + body_part), 1)


def _state_from_score(score: float) -> str:
    if score >= 58:
        return "BULL"
    if score <= 42:
        return "BEAR"
    return "MIXED"


def _dot(state: str) -> str:
    return {"BULL": "G", "BEAR": "R", "MIXED": "Y"}.get(state, "Y")


def practical_option_targets(
    *,
    bullish: bool,
    entry: float,
    invalidation: float,
    atr: float,
    ltp: float,
) -> tuple[float, float, float, float, str]:
    """Return practical option-trading targets for the underlying: (t1, t2,
    t3, effective_risk, method).

    The scanner trades stock options, so tiny theoretical targets are not
    useful. This floors target distance using ATR and a minimum underlying
    percentage move, while preserving the actual invalidation line. T3 is
    the "runner" target — matches Pine v6's Target 3 tier (intradayT3R=3.0
    / swingT3R=4.0), scaled the same way T1/T2 already are here.
    """
    if entry <= 0:
        return 0.0, 0.0, 0.0, 0.0, "No valid entry"
    risk = abs(entry - invalidation)
    atr_floor = max(float(atr or 0.0), entry * 0.0045, 0.50)
    effective_risk = max(risk, atr_floor * 0.75, entry * 0.0035, 0.50)
    t1_dist = max(effective_risk * 1.7, atr_floor * 1.25, entry * 0.0060, 1.00)
    t2_dist = max(effective_risk * 3.0, atr_floor * 2.20, entry * 0.0110, 1.50)
    t3_dist = max(effective_risk * 4.5, atr_floor * 3.20, entry * 0.0160, 2.00)
    if bullish:
        t1 = entry + t1_dist
        t2 = entry + t2_dist
        t3 = entry + t3_dist
    else:
        t1 = entry - t1_dist
        t2 = entry - t2_dist
        t3 = entry - t3_dist
    rr1 = round(t1_dist / effective_risk, 2) if effective_risk > 0 else 0.0
    method = (
        f"Practical option target floor: T1 {t1_dist:.2f}, risk {effective_risk:.2f}, R:R {rr1:.2f}"
    )
    return t1, t2, t3, effective_risk, method


def compute_fib_targets(ml_features: dict, *, bullish: bool, entry: float) -> dict | None:
    """Alternate T1/T2/T3 target mode using Boroden's confluence-cluster
    extension rule (1.272 / 1.618 / 2.618 of the swing leading into the
    cluster) alongside the existing ATR-based practical_option_targets().

    ml_features is FeatureVectorV1.ml_features (structure_snapshot +
    fib_snapshot already merged in by feature-engine). Returns None when no
    confluence cluster exists near current price yet -- thin swing history
    early in a symbol's session, or price just isn't near a genuine
    convergence zone right now -- callers should keep using the ATR-based
    targets in that case rather than force a Fib target that doesn't exist.
    """
    cluster_center = ml_features.get("fib_cluster_center")
    cluster_hits = ml_features.get("fib_cluster_hits") or 0
    swing_high = ml_features.get("swing_high_1")
    swing_low = ml_features.get("swing_low_1")
    if cluster_center is None or cluster_hits < 3:
        return None
    if not swing_high or not swing_low or swing_high <= swing_low or entry <= 0:
        return None

    span = swing_high - swing_low
    sign = 1.0 if bullish else -1.0
    t1 = cluster_center + sign * span * 1.272
    t2 = cluster_center + sign * span * 1.618
    t3 = cluster_center + sign * span * 2.618

    # The cluster has to actually sit ahead of entry in the trade direction
    # -- otherwise it's behind price (already-passed S/R, not a target zone
    # for this specific trade) and the ATR-based mode is the honest fallback.
    if bullish and t1 <= entry:
        return None
    if not bullish and t1 >= entry:
        return None

    return {
        "fib_t1_price": round(t1, 2),
        "fib_t2_price": round(t2, 2),
        "fib_t3_price": round(t3, 2),
        "fib_cluster_center": round(float(cluster_center), 2),
        "fib_cluster_hits": int(cluster_hits),
        "fib_target_method": (
            f"Fibonacci confluence: {cluster_hits} levels near {cluster_center:.2f}, "
            f"extensions off swing {swing_low:.2f}-{swing_high:.2f}"
        ),
    }


ROCKET_ADX_THRESHOLD = 32.0  # Pine's rocketAdxThreshold default
ROCKET_BODY_PCT = 0.65  # Pine's rocketBodyPct default


def compute_chaseable(features: dict, *, bullish: bool) -> bool:
    """Mirrors Pine v6's "Rocket" chaseable marker: ADX >= 32, Supertrend
    and VWAP both agree with the trade direction, and the trigger candle's
    body is a strong >= 65% of its range (not a doji/indecisive bar).

    This is the single "should I actually chase this right now" flag the
    dashboard/Telegram show instead of a wall of anti-chase prose.
    """
    ml = features.get("ml_features") or {}
    adx = float(ml.get("adx") or 0.0)
    supertrend_bullish = bool(ml.get("supertrend_bullish", True))
    body_pct = float(ml.get("candle_body_pct") or 0.0)
    ltp = float(features.get("ltp") or 0.0)
    vwap = float(features.get("vwap") or 0.0)
    above_vwap = ltp > vwap > 0
    below_vwap = 0 < ltp < vwap

    if adx < ROCKET_ADX_THRESHOLD or body_pct < ROCKET_BODY_PCT:
        return False
    if bullish:
        return supertrend_bullish and above_vwap
    return (not supertrend_bullish) and below_vwap


@dataclass(frozen=True)
class PineDecision:
    bull_confidence: float
    bear_confidence: float
    dominant_confidence: float
    dominant_side: str
    mtf: dict[str, str]
    mtf_dots: dict[str, str]
    mtf_text: str
    mtf_source: str  # "historical_cache" | "live_proxy"
    strength_score: float  # 0-100, Pine v6 Strength Meter equivalent
    chaseable: bool  # Pine v6 "Rocket" marker equivalent
    anti_chase_ok: bool
    anti_chase_reasons: list[str]
    rejection_reasons: list[str]
    t1_price: float
    t2_price: float
    t3_price: float
    risk_per_share: float
    target_method: str
    signal_candle_atr: float
    vwap_distance_atr: float
    stop_distance_atr: float
    fib_targets: (
        dict | None
    )  # alternate target mode, see compute_fib_targets(); None if no cluster yet

    def as_snapshot(self) -> dict:
        return {
            "bull_confidence": round(self.bull_confidence, 1),
            "bear_confidence": round(self.bear_confidence, 1),
            "dominant_confidence": round(self.dominant_confidence, 1),
            "dominant_side": self.dominant_side,
            "mtf": self.mtf,
            "mtf_dots": self.mtf_dots,
            "mtf_text": self.mtf_text,
            "mtf_source": self.mtf_source,
            "strength_score": self.strength_score,
            "chaseable": self.chaseable,
            "anti_chase_ok": self.anti_chase_ok,
            "anti_chase_reasons": self.anti_chase_reasons,
            "rejection_reasons": self.rejection_reasons,
            "t1_price": round(self.t1_price, 2),
            "t2_price": round(self.t2_price, 2),
            "t3_price": round(self.t3_price, 2),
            "risk_per_share": round(self.risk_per_share, 2),
            "target_method": self.target_method,
            "signal_candle_atr": round(self.signal_candle_atr, 2),
            "vwap_distance_atr": round(self.vwap_distance_atr, 2),
            "stop_distance_atr": round(self.stop_distance_atr, 2),
            "fib_targets": self.fib_targets,
        }


def compute_pine_decision(
    features: dict,
    *,
    bullish: bool,
    entry: float,
    invalidation: float,
) -> PineDecision:
    ltp = _f(features, "ltp")
    vwap = _f(features, "vwap")
    ema5 = _f(features, "ema_5")
    ema9 = _f(features, "ema_9")
    ema20 = _f(features, "ema_20")
    ema50 = _f(features, "ema_50")
    rsi = _f(features, "rsi_14", 50.0)
    macd = _f(features, "macd")
    macd_signal = _f(features, "macd_signal")
    macd_hist = _f(features, "macd_hist")
    rel_vol = _f(features, "rel_vol_20d")
    atr = _f(features, "atr_14")
    spread = _f(features, "spread_bps", 999.0)
    change_pct = _f(features, "change_pct")
    _f(features, "bb_width")
    atr_trend = str(features.get("atr_trend") or "NEUTRAL").upper()
    candle = str(features.get("candle_pattern") or "")
    squeeze = str(features.get("squeeze_state") or "").upper()
    nr = str(features.get("nr_pattern") or "")

    atr_safe = atr if atr > 0 else max(ltp * 0.004, 0.05)
    vwap_distance_atr = abs(ltp - vwap) / atr_safe if vwap > 0 else 9.99
    # If OHLC range is not available in the feature snapshot, use the movement
    # proxy. It is intentionally conservative for options because gap candles
    # can destroy entry quality.
    candle_range = abs(change_pct) / 100.0 * ltp
    signal_candle_atr = candle_range / atr_safe if atr_safe > 0 else 0.0
    risk = abs(entry - invalidation)
    stop_distance_atr = risk / atr_safe if atr_safe > 0 else 0.0

    ema_bull = ltp > ema9 > ema20 > 0
    ema_bear = ltp < ema9 < ema20 and ema20 > 0
    ema_stack_bull = ltp > ema5 > ema9 > ema20 > 0
    ema_stack_bear = ltp < ema5 < ema9 < ema20 and ema20 > 0
    above_vwap = ltp > vwap > 0
    below_vwap = vwap > ltp > 0
    macd_bull = macd > macd_signal and macd_hist > 0
    macd_bear = macd < macd_signal and macd_hist < 0
    atr_bull = atr_trend == "BULL"
    atr_bear = atr_trend == "BEAR"
    bull_pattern = candle in {"Bullish Engulfing", "Hammer"}
    bear_pattern = candle in {"Bearish Engulfing", "Shooting Star"}
    trigger = (
        bull_pattern
        or bear_pattern
        or squeeze in {"EXTREME", "COILED", "BUILDING"}
        or nr in {"NR4", "NR7"}
    )

    bull = 0.0
    bull += 12.5 if ema_stack_bull else 8.0 if ema_bull else 0.0
    bull += 12.5 if atr_bull else 6.0 if atr_trend == "NEUTRAL" else 0.0
    bull += 10.0 if macd_bull else 3.0 if macd_hist >= 0 else 0.0
    bull += 10.0 if 52 <= rsi <= 68 else 6.0 if 45 <= rsi < 52 else 0.0
    bull += 7.5 if above_vwap else 0.0
    bull += 7.5 if ltp > ema20 > 0 else 0.0
    bull += min(rel_vol / 3.0, 1.0) * (10.0 if change_pct >= 0 else 4.0)
    bull += 7.0 if trigger and bullish else 3.5 if trigger else 0.0
    bull += 3.0 if bull_pattern else 0.0
    bull += 5.0 if spread <= 35 else 2.0 if spread <= 70 else 0.0

    bear = 0.0
    bear += 12.5 if ema_stack_bear else 8.0 if ema_bear else 0.0
    bear += 12.5 if atr_bear else 6.0 if atr_trend == "NEUTRAL" else 0.0
    bear += 10.0 if macd_bear else 3.0 if macd_hist <= 0 else 0.0
    bear += 10.0 if 32 <= rsi <= 48 else 6.0 if 48 < rsi <= 55 else 0.0
    bear += 7.5 if below_vwap else 0.0
    bear += 7.5 if ltp < ema20 and ema20 > 0 else 0.0
    bear += min(rel_vol / 3.0, 1.0) * (10.0 if change_pct <= 0 else 4.0)
    bear += 7.0 if trigger and not bullish else 3.5 if trigger else 0.0
    bear += 3.0 if bear_pattern else 0.0
    bear += 5.0 if spread <= 35 else 2.0 if spread <= 70 else 0.0

    # MTF alignment. Prefer the real historical-candle engine
    # (api/routes/mtf.py:compute_mtf(), cached at infusion:mtf:{symbol} and
    # kept warm by mtf_queue.py) when the scanner engine has fetched it fresh
    # for this tick — see scanner/engine.py's _fetch_mtf_cache(). Falls back
    # to the synthetic live-feature proxy below when no cache entry exists
    # yet for this symbol (e.g. it hasn't reached the priority refresh queue)
    # or the cached entry is stale.
    mtf_cache = features.get("mtf_cache")
    cache_age = time.time() - float(mtf_cache.get("updated_at") or 0) if mtf_cache else None
    use_cache = bool(
        mtf_cache
        and mtf_cache.get("dots")
        and cache_age is not None
        and cache_age < MTF_CACHE_STALE_SEC
    )

    if use_cache:
        mtf = dict(mtf_cache.get("mtf") or {})
        dots = dict(mtf_cache.get("dots") or {})
        mtf_text = str(mtf_cache.get("mtf_text") or mtf_cache.get("alignment") or "Mixed alignment")
    else:
        # Synthetic proxy from live tick-derived features. We separate fast
        # momentum from slower trend so the display still catches conflict.
        mtf_scores = {
            "1M": 50
            + (12 if above_vwap else -12 if below_vwap else 0)
            + (12 if macd_bull else -12 if macd_bear else 0)
            + (rsi - 50) * 0.7,
            "5M": 50
            + (16 if ema_bull else -16 if ema_bear else 0)
            + (10 if macd_bull else -10 if macd_bear else 0),
            "15M": 50
            + (18 if ema_stack_bull else -18 if ema_stack_bear else 0)
            + (8 if atr_bull else -8 if atr_bear else 0),
            "1H": 50
            + (18 if ltp > ema50 > 0 else -18 if ltp < ema50 and ema50 > 0 else 0)
            + (8 if change_pct > 0 else -8 if change_pct < 0 else 0),
            "4H": 50
            + (
                15
                if ltp > ema50 > 0 and ema20 > ema50 > 0
                else -15
                if ltp < ema50 and ema20 < ema50 and ema50 > 0
                else 0
            ),
            "1D": 50
            + (14 if change_pct > 0 else -14 if change_pct < 0 else 0)
            + (8 if above_vwap else -8 if below_vwap else 0),
        }
        mtf = {tf: _state_from_score(score) for tf, score in mtf_scores.items()}
        dots = {tf: _dot(state) for tf, state in mtf.items()}
        bull_count = sum(1 for x in mtf.values() if x == "BULL")
        bear_count = sum(1 for x in mtf.values() if x == "BEAR")
        if bull_count >= 5:
            mtf_text = "Strong CE alignment"
        elif bear_count >= 5:
            mtf_text = "Strong PE alignment"
        elif bull_count >= 4:
            mtf_text = "CE focus; wait for clean trigger"
        elif bear_count >= 4:
            mtf_text = "PE focus; wait for clean trigger"
        elif mtf.get("1M") == mtf.get("5M") and mtf.get("15M") != mtf.get("1H"):
            mtf_text = "Fast scalp only; higher timeframe conflict"
        else:
            mtf_text = "Mixed alignment; wait for better location"

    anti_reasons: list[str] = []
    if vwap_distance_atr > 1.5:
        anti_reasons.append(f"VWAP stretch {vwap_distance_atr:.1f} ATR")
    if signal_candle_atr > 2.0:
        anti_reasons.append(f"Large signal candle {signal_candle_atr:.1f} ATR")
    if bullish and rsi > 75:
        anti_reasons.append(f"CE chase risk: RSI {rsi:.1f}")
    if not bullish and rsi < 25:
        anti_reasons.append(f"PE chase risk: RSI {rsi:.1f}")
    if stop_distance_atr > 2.5:
        anti_reasons.append(f"Stop too wide {stop_distance_atr:.1f} ATR")

    rejection = list(anti_reasons)
    selected_conf = bull if bullish else bear
    opposite_conf = bear if bullish else bull
    if selected_conf < 60:
        rejection.append(f"Confidence below 60 ({selected_conf:.0f})")
    if abs(selected_conf - opposite_conf) < 8:
        rejection.append("CE/PE evidence too close")
    if bullish and mtf.get("15M") == "BEAR" and mtf.get("1H") == "BEAR":
        rejection.append("15M and 1H oppose CE")
    if not bullish and mtf.get("15M") == "BULL" and mtf.get("1H") == "BULL":
        rejection.append("15M and 1H oppose PE")

    if bullish:
        t1, t2, t3, effective_risk, target_method = practical_option_targets(
            bullish=True,
            entry=entry,
            invalidation=invalidation,
            atr=atr_safe,
            ltp=ltp,
        )
        dominant = "CE" if bull >= bear else "PE"
    else:
        t1, t2, t3, effective_risk, target_method = practical_option_targets(
            bullish=False,
            entry=entry,
            invalidation=invalidation,
            atr=atr_safe,
            ltp=ltp,
        )
        dominant = "PE" if bear >= bull else "CE"

    ml_features = features.get("ml_features") or {}
    fib_targets = compute_fib_targets(ml_features, bullish=bullish, entry=entry)

    return PineDecision(
        bull_confidence=clamp(bull),
        bear_confidence=clamp(bear),
        dominant_confidence=clamp(max(bull, bear)),
        dominant_side=dominant,
        mtf=mtf,
        mtf_dots=dots,
        mtf_text=mtf_text,
        mtf_source="historical_cache" if use_cache else "live_proxy",
        strength_score=compute_strength_meter(features, bullish),
        chaseable=compute_chaseable(features, bullish=bullish),
        anti_chase_ok=not anti_reasons,
        anti_chase_reasons=anti_reasons,
        rejection_reasons=rejection,
        t1_price=t1,
        t2_price=t2,
        t3_price=t3,
        risk_per_share=effective_risk,
        target_method=target_method,
        signal_candle_atr=signal_candle_atr,
        vwap_distance_atr=vwap_distance_atr,
        stop_distance_atr=stop_distance_atr,
        fib_targets=fib_targets,
    )
