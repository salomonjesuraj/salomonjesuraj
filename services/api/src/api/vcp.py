"""VCP (Volatility Contraction Pattern) / Minervini Stage-2 composite score.

Phase 13.12, from ajeeshworkspace/indian-trading-skills's specified
weighting: Trend Template 25%, Contraction Quality 25%, Volume Dry-Up 20%,
Pivot Proximity 15%, Relative Strength vs Nifty 50 15%.

Deliberately built here, not in feature-engine as the original research-
follow-through plan guessed: every ingredient (SMA50/150/200, fractal swing
pivots, multi-month volume history, a 52-week high/low) is a DAILY-bar
computation, and feature-engine's SymbolState carries no daily-bar history
at all -- only live tick-session state that resets every day. All of this
already lives in api/routes/mtf.py's domain (same `infusion:ohlc:{symbol}
:daily` Redis zset _ma_regime/_donchian_channel/_week52_stats already read),
so this module is a peer of those, not a new subsystem.

Informational only -- NOT wired into live scoring or any suppression gate,
same governance as every Phase 13 field: emitted into the mtf payload
(and from there into features_snapshot, same as ma_regime/donchian/week52),
evaluated later via /api/backtest/feature-ablation and /api/backtest/
feature-ic before it's ever allowed to move a live score.
"""

from __future__ import annotations

import itertools

from api.chart_patterns import fractal_pivots_indexed

TREND_TEMPLATE_WEIGHT = 25.0
CONTRACTION_WEIGHT = 25.0
VOLUME_DRYUP_WEIGHT = 20.0
PIVOT_PROXIMITY_WEIGHT = 15.0
RELATIVE_STRENGTH_WEIGHT = 15.0

# ~3 trading months. Not the classic 12-month IBD relative-strength window:
# NIFTY50's own cached daily history (bootstrapped the same way every
# equity's is, via scheduler/historical.py) has been observed to run
# noticeably shorter than individual stocks' (~120 vs ~250 trading days) --
# Upstox's historical-candle endpoint appears to cap index lookback more
# tightly than equity lookback. 63 days is comfortably inside that shorter
# window so RS stays computable rather than silently degrading whenever the
# index cache happens to be thin.
RS_LOOKBACK_DAYS = 63
RS_MIN_LOOKBACK_DAYS = 20

BASE_LOOKBACK_DAYS = 90  # how far back to look for the current base's contraction legs
MIN_CONTRACTIONS = 2  # Minervini's own stated minimum for a "VCP" label (2-4 typical)
CONTRACTION_TOLERANCE = 0.80  # each leg's depth should be <= this fraction of the prior leg's (Minervini's ~0.75 heuristic, with slack)


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _trend_template(daily_bars: list[dict], ltp: float) -> dict:
    """Minervini's 7-point Stage-2 trend test, decomposed into 9 finer
    boolean checks (his own point 1 is really "price > 150-SMA AND price >
    200-SMA" -- split here for a more granular partial-credit score, not a
    reinterpretation). Each check that's actually computable (enough daily
    history) contributes equally; an uncomputable check is excluded from
    both the numerator and denominator rather than counted as a fail -- an
    unknown isn't a no.
    """
    closes = [float(b["close"]) for b in daily_bars if b.get("close")]
    if len(closes) < 30 or ltp <= 0:
        return {
            "available": False,
            "score": 0.0,
            "checks_passed": 0,
            "checks_total": 0,
            "reason": "Not enough daily history for any trend-template check (need 30+ days, 200+ for a full read)",
        }

    sma50 = _sma(closes, 50)
    sma150 = _sma(closes, 150)
    sma200 = _sma(closes, 200)
    # ~1 trading month earlier, so "is the 200-SMA still rising" is a real
    # slope check, not just today's snapshot.
    sma200_prev = _sma(closes[:-21], 200) if len(closes) >= 221 else None
    window = daily_bars[-252:]
    week_high = max(float(b["high"]) for b in window if b.get("high"))
    week_low = min(float(b["low"]) for b in window if b.get("low"))

    checks: list[bool | None] = [
        (ltp > sma150) if sma150 is not None else None,
        (ltp > sma200) if sma200 is not None else None,
        (sma150 > sma200) if (sma150 is not None and sma200 is not None) else None,
        (sma200 > sma200_prev) if (sma200 is not None and sma200_prev is not None) else None,
        (sma50 > sma150) if (sma50 is not None and sma150 is not None) else None,
        (sma50 > sma200) if (sma50 is not None and sma200 is not None) else None,
        (ltp > sma50) if sma50 is not None else None,
        (ltp >= week_low * 1.25) if week_low > 0 else None,
        (ltp >= week_high * 0.75) if week_high > 0 else None,
    ]
    computable = [c for c in checks if c is not None]
    if not computable:
        return {
            "available": False,
            "score": 0.0,
            "checks_passed": 0,
            "checks_total": 0,
            "reason": "Not enough daily history to compute any trend-template check",
        }
    passed = sum(1 for c in computable if c)
    fraction = passed / len(computable)
    return {
        "available": True,
        "score": round(fraction * TREND_TEMPLATE_WEIGHT, 2),
        "checks_passed": passed,
        "checks_total": len(computable),
        "full_read": len(computable) >= 9,  # all 9 checks had enough history
        "sma50": round(sma50, 2) if sma50 is not None else None,
        "sma150": round(sma150, 2) if sma150 is not None else None,
        "sma200": round(sma200, 2) if sma200 is not None else None,
    }


def _avg_volume(daily_bars: list[dict], start_idx: int, end_idx: int) -> float:
    span = daily_bars[start_idx : end_idx + 1]
    vols = [float(b.get("volume") or 0) for b in span]
    return sum(vols) / len(vols) if vols else 0.0


def _contraction_legs(daily_bars: list[dict], lookback: int = BASE_LOOKBACK_DAYS) -> list[dict]:
    """Sequential high->low pullback legs within the recent lookback window,
    oldest first -- the unit VCP's "successive pullbacks tightening" language
    is built on. Uses the same fractal pivot rule (and the same
    api.chart_patterns.fractal_pivots_indexed helper) as the daily
    chart-pattern detectors, not a new pivot definition.
    """
    window = daily_bars[-lookback:] if len(daily_bars) > lookback else daily_bars
    offset = len(daily_bars) - len(window)
    pivots = fractal_pivots_indexed(window)
    legs: list[dict] = []
    for i in range(len(pivots) - 1):
        price_a, kind_a, idx_a = pivots[i]
        price_b, kind_b, idx_b = pivots[i + 1]
        if kind_a == "high" and kind_b == "low" and price_a > 0:
            depth_pct = (price_a - price_b) / price_a * 100.0
            legs.append(
                {
                    "high": round(price_a, 2),
                    "low": round(price_b, 2),
                    "depth_pct": round(depth_pct, 2),
                    "volume_avg": _avg_volume(daily_bars, idx_a + offset, idx_b + offset),
                }
            )
    return legs


def _contraction_quality(legs: list[dict]) -> dict:
    if len(legs) < MIN_CONTRACTIONS:
        return {
            "available": False,
            "score": 0.0,
            "contractions_found": len(legs),
            "reason": f"Need at least {MIN_CONTRACTIONS} successive pullback legs in the recent base, found {len(legs)}",
        }
    pairs = list(itertools.pairwise(legs))
    tightening = 0
    for prior, nxt in pairs:
        if prior["depth_pct"] <= 0:
            continue
        if (nxt["depth_pct"] / prior["depth_pct"]) <= CONTRACTION_TOLERANCE:
            tightening += 1
    fraction = tightening / len(pairs) if pairs else 0.0
    return {
        "available": True,
        "score": round(fraction * CONTRACTION_WEIGHT, 2),
        "contractions_found": len(legs),
        "tightening_pairs": tightening,
        "pairs_checked": len(pairs),
        "depths_pct": [leg["depth_pct"] for leg in legs],
    }


def _volume_dryup(legs: list[dict]) -> dict:
    if len(legs) < MIN_CONTRACTIONS:
        return {
            "available": False,
            "score": 0.0,
            "reason": "Not enough contraction legs to compare volume",
        }
    first_vol = legs[0]["volume_avg"]
    last_vol = legs[-1]["volume_avg"]
    if first_vol <= 0:
        return {
            "available": False,
            "score": 0.0,
            "reason": "No real volume on the earliest leg (thinly-traded symbol, or an index has no per-bar volume)",
        }
    ratio = last_vol / first_vol
    # Full points at ratio <= 0.5 (volume into the latest/tightest leg has
    # halved vs. the base's earliest leg); zero points at ratio >= 1.0 (no
    # dry-up at all); linear between. Both bounds are Infusion's own
    # calibration -- the source states the *direction* (volume should
    # contract alongside price) without giving an exact ratio.
    fraction = max(0.0, min(1.0, (1.0 - ratio) / 0.5))
    return {
        "available": True,
        "score": round(fraction * VOLUME_DRYUP_WEIGHT, 2),
        "earliest_leg_avg_volume": round(first_vol, 0),
        "latest_leg_avg_volume": round(last_vol, 0),
        "volume_ratio": round(ratio, 3),
    }


def _pivot_proximity(legs: list[dict], ltp: float) -> dict:
    if not legs or ltp <= 0:
        return {"available": False, "score": 0.0, "reason": "No base pivot identified yet"}
    pivot = max(leg["high"] for leg in legs)
    if pivot <= 0:
        return {"available": False, "score": 0.0, "reason": "No base pivot identified yet"}
    distance_pct = (pivot - ltp) / pivot * 100.0
    if distance_pct >= 0:
        # Still below the pivot -- Minervini enters as price approaches/
        # breaks it, so closer (smaller distance) scores higher. Zero
        # points once 15%+ away (not a live setup yet).
        fraction = max(0.0, 1.0 - distance_pct / 15.0)
    else:
        # Already through the pivot -- fine right at/just above it,
        # penalize being extended (chase risk). Zero points 10%+ past it.
        fraction = max(0.0, 1.0 - abs(distance_pct) / 10.0)
    return {
        "available": True,
        "score": round(fraction * PIVOT_PROXIMITY_WEIGHT, 2),
        "pivot": round(pivot, 2),
        "distance_pct": round(distance_pct, 2),
    }


def _relative_strength(daily_bars: list[dict], nifty_bars: list[dict] | None) -> dict:
    if not nifty_bars:
        return {"available": False, "score": 0.0, "reason": "Nifty 50 daily history not cached"}
    stock_closes = [float(b["close"]) for b in daily_bars if b.get("close")]
    nifty_closes = [float(b["close"]) for b in nifty_bars if b.get("close")]
    lookback = min(RS_LOOKBACK_DAYS, len(stock_closes) - 1, len(nifty_closes) - 1)
    if lookback < RS_MIN_LOOKBACK_DAYS:
        return {
            "available": False,
            "score": 0.0,
            "reason": "Not enough overlapping daily history for stock vs Nifty 50",
        }
    stock_return = (stock_closes[-1] / stock_closes[-1 - lookback] - 1.0) * 100.0
    nifty_return = (nifty_closes[-1] / nifty_closes[-1 - lookback] - 1.0) * 100.0
    rs_diff = stock_return - nifty_return
    # 0 points at rs_diff <= -10 (badly lagging the index), full points at
    # rs_diff >= +20 (strong outperformance). Infusion's own calibration.
    fraction = max(0.0, min(1.0, (rs_diff + 10.0) / 30.0))
    return {
        "available": True,
        "score": round(fraction * RELATIVE_STRENGTH_WEIGHT, 2),
        "lookback_days": lookback,
        "stock_return_pct": round(stock_return, 2),
        "nifty_return_pct": round(nifty_return, 2),
        "rs_diff_pct": round(rs_diff, 2),
    }


def compute_vcp(daily_bars: list[dict], nifty_bars: list[dict] | None, ltp: float) -> dict:
    """Composite 0-100 VCP score across the 5 weighted components above.

    `reliable` is True only when every component was actually computable
    (enough history + at least MIN_CONTRACTIONS legs found) -- with any
    component missing, `score` is still returned (partial credit from
    whatever was computable) but should be read as provisional, same
    honesty convention as Feature-IC's n_present/n_absent gate.
    """
    if not daily_bars or ltp <= 0:
        return {"available": False, "score": None, "reason": "No daily bar history"}

    legs = _contraction_legs(daily_bars)
    components = {
        "trend_template": _trend_template(daily_bars, ltp),
        "contraction_quality": _contraction_quality(legs),
        "volume_dryup": _volume_dryup(legs),
        "pivot_proximity": _pivot_proximity(legs, ltp),
        "relative_strength": _relative_strength(daily_bars, nifty_bars),
    }
    total_score = round(sum(c["score"] for c in components.values()), 1)
    all_available = all(c["available"] for c in components.values())

    if all_available and total_score >= 80:
        grade = "tight_vcp"
    elif total_score >= 55:
        grade = "developing_base"
    else:
        grade = "no_clear_base"

    return {
        "available": True,
        "score": total_score,
        "grade": grade,
        "reliable": all_available,
        "components": components,
    }
