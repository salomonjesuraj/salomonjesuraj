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

from dataclasses import dataclass


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _f(features: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(features.get(key) or default)
    except (TypeError, ValueError):
        return default


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
) -> tuple[float, float, float, str]:
    """Return practical option-trading targets for the underlying.

    The scanner trades stock options, so tiny theoretical targets are not
    useful. This floors target distance using ATR and a minimum underlying
    percentage move, while preserving the actual invalidation line.
    """
    if entry <= 0:
        return 0.0, 0.0, 0.0, "No valid entry"
    risk = abs(entry - invalidation)
    atr_floor = max(float(atr or 0.0), entry * 0.0045, 0.50)
    effective_risk = max(risk, atr_floor * 0.75, entry * 0.0035, 0.50)
    t1_dist = max(effective_risk * 1.7, atr_floor * 1.25, entry * 0.0060, 1.00)
    t2_dist = max(effective_risk * 3.0, atr_floor * 2.20, entry * 0.0110, 1.50)
    if bullish:
        t1 = entry + t1_dist
        t2 = entry + t2_dist
    else:
        t1 = entry - t1_dist
        t2 = entry - t2_dist
    rr1 = round(t1_dist / effective_risk, 2) if effective_risk > 0 else 0.0
    method = f"Practical option target floor: T1 {t1_dist:.2f}, risk {effective_risk:.2f}, R:R {rr1:.2f}"
    return t1, t2, effective_risk, method


@dataclass(frozen=True)
class PineDecision:
    bull_confidence: float
    bear_confidence: float
    dominant_confidence: float
    dominant_side: str
    mtf: dict[str, str]
    mtf_dots: dict[str, str]
    mtf_text: str
    anti_chase_ok: bool
    anti_chase_reasons: list[str]
    rejection_reasons: list[str]
    t1_price: float
    t2_price: float
    risk_per_share: float
    target_method: str
    signal_candle_atr: float
    vwap_distance_atr: float
    stop_distance_atr: float

    def as_snapshot(self) -> dict:
        return {
            "bull_confidence": round(self.bull_confidence, 1),
            "bear_confidence": round(self.bear_confidence, 1),
            "dominant_confidence": round(self.dominant_confidence, 1),
            "dominant_side": self.dominant_side,
            "mtf": self.mtf,
            "mtf_dots": self.mtf_dots,
            "mtf_text": self.mtf_text,
            "anti_chase_ok": self.anti_chase_ok,
            "anti_chase_reasons": self.anti_chase_reasons,
            "rejection_reasons": self.rejection_reasons,
            "t1_price": round(self.t1_price, 2),
            "t2_price": round(self.t2_price, 2),
            "risk_per_share": round(self.risk_per_share, 2),
            "target_method": self.target_method,
            "signal_candle_atr": round(self.signal_candle_atr, 2),
            "vwap_distance_atr": round(self.vwap_distance_atr, 2),
            "stop_distance_atr": round(self.stop_distance_atr, 2),
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
    bb_width = _f(features, "bb_width")
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
    trigger = bull_pattern or bear_pattern or squeeze in {"EXTREME", "COILED", "BUILDING"} or nr in {"NR4", "NR7"}

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

    # Pine-style MTF dots. True higher timeframe candles will replace these
    # approximations when a dedicated MTF feature worker is added. We separate
    # fast momentum from slower trend so the display still catches conflict.
    mtf_scores = {
        "1M": 50 + (12 if above_vwap else -12 if below_vwap else 0) + (12 if macd_bull else -12 if macd_bear else 0) + (rsi - 50) * 0.7,
        "5M": 50 + (16 if ema_bull else -16 if ema_bear else 0) + (10 if macd_bull else -10 if macd_bear else 0),
        "15M": 50 + (18 if ema_stack_bull else -18 if ema_stack_bear else 0) + (8 if atr_bull else -8 if atr_bear else 0),
        "1H": 50 + (18 if ltp > ema50 > 0 else -18 if ltp < ema50 and ema50 > 0 else 0) + (8 if change_pct > 0 else -8 if change_pct < 0 else 0),
        "4H": 50 + (15 if ltp > ema50 > 0 and ema20 > ema50 > 0 else -15 if ltp < ema50 and ema20 < ema50 and ema50 > 0 else 0),
        "1D": 50 + (14 if change_pct > 0 else -14 if change_pct < 0 else 0) + (8 if above_vwap else -8 if below_vwap else 0),
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
    elif mtf["1M"] == mtf["5M"] and mtf["15M"] != mtf["1H"]:
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
    if bullish and mtf["15M"] == "BEAR" and mtf["1H"] == "BEAR":
        rejection.append("15M and 1H oppose CE")
    if not bullish and mtf["15M"] == "BULL" and mtf["1H"] == "BULL":
        rejection.append("15M and 1H oppose PE")

    if bullish:
        t1, t2, effective_risk, target_method = practical_option_targets(
            bullish=True,
            entry=entry,
            invalidation=invalidation,
            atr=atr_safe,
            ltp=ltp,
        )
        dominant = "CE" if bull >= bear else "PE"
    else:
        t1, t2, effective_risk, target_method = practical_option_targets(
            bullish=False,
            entry=entry,
            invalidation=invalidation,
            atr=atr_safe,
            ltp=ltp,
        )
        dominant = "PE" if bear >= bull else "CE"

    return PineDecision(
        bull_confidence=clamp(bull),
        bear_confidence=clamp(bear),
        dominant_confidence=clamp(max(bull, bear)),
        dominant_side=dominant,
        mtf=mtf,
        mtf_dots=dots,
        mtf_text=mtf_text,
        anti_chase_ok=not anti_reasons,
        anti_chase_reasons=anti_reasons,
        rejection_reasons=rejection,
        t1_price=t1,
        t2_price=t2,
        risk_per_share=effective_risk,
        target_method=target_method,
        signal_candle_atr=signal_candle_atr,
        vwap_distance_atr=vwap_distance_atr,
        stop_distance_atr=stop_distance_atr,
    )
