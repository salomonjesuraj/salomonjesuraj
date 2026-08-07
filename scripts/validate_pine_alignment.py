"""Pine v6 alignment — offline validation.

Verifies the backend logic added to match
simple_structure_pivot_ma_plan_v6.pine (market structure / candlestick
patterns / supply-demand zones / strength meter / MTF major blocker /
position sizing) behaves correctly, WITHOUT live services.

Usage:
    python -X utf8 scripts/validate_pine_alignment.py
"""

import os
import sys
import time

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "feature-engine", "src"))
sys.path.insert(0, os.path.join(base, "services", "scanner", "src"))
sys.path.insert(0, os.path.join(base, "services", "api", "src"))
sys.path.insert(0, os.path.join(base, "services", "alerter", "src"))

passed = 0
failed = 0
errors = []


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS]   {label}{' -- ' + detail if detail else ''}")
    else:
        failed += 1
        errors.append(label)
        print(f"  [FAIL]   {label}{' -- ' + detail if detail else ''}")


def main():
    print("=" * 70)
    print("INFUSION — PINE v6 ALIGNMENT VALIDATION")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    # ═══════════════════════════════════════════════
    # MODULE IMPORTS
    # ═══════════════════════════════════════════════
    print("\n--- MODULE IMPORTS ---")
    try:
        from feature_engine.state import SymbolState
        from feature_engine.features.structure import update_structure, structure_snapshot, trend_text
        from feature_engine.features.candles import detect_candle_pattern, update_body_ema, body_pct
        from feature_engine.features.zones import update_zones, zone_snapshot
        from feature_engine.features.momentum import update_adx, get_adx
        from feature_engine.features.volatility import update_supertrend, get_supertrend
        check("feature_engine Pine-alignment modules import", True)
    except Exception as e:
        check("feature_engine Pine-alignment modules import", False, str(e))
        return

    try:
        from scanner.pine_confidence import compute_pine_decision, compute_strength_meter, MTF_CACHE_STALE_SEC
        check("scanner.pine_confidence imports", True)
    except Exception as e:
        check("scanner.pine_confidence imports", False, str(e))
        return

    try:
        from api.routes.ticks import _classify_oi_buildup
        check("api.routes.ticks OI classification imports", True)
    except Exception as e:
        check("api.routes.ticks OI classification imports", False, str(e))
        return

    try:
        from scanner.scoring import compute_conviction, grade_conviction, CHASEABLE_GRADE_CAP
        check("scanner.scoring imports", True)
    except Exception as e:
        check("scanner.scoring imports", False, str(e))
        return

    try:
        from api.routes.mtf import _fractal_pivots, _major_blocker, compute_mtf
        check("api.routes.mtf blocker functions import", True)
    except Exception as e:
        check("api.routes.mtf blocker functions import", False, str(e))
        return

    try:
        from infusion_common.sizing import compute_position_size
        check("infusion_common.sizing imports", True)
    except Exception as e:
        check("infusion_common.sizing imports", False, str(e))
        return

    try:
        from alerter.formatter import format_signal
        check("alerter.formatter imports", True)
    except Exception as e:
        check("alerter.formatter imports", False, str(e))
        return

    # ═══════════════════════════════════════════════
    # PHASE 1 — MARKET STRUCTURE
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 1: MARKET STRUCTURE (swing pivots / BOS-CHOCH) ---")
    state = SymbolState(symbol="TEST1")
    state.atr = 1.0
    bars = [
        {"o": 100, "h": 101, "l": 99, "c": 100},
        {"o": 100, "h": 102, "l": 100, "c": 101},
        {"o": 101, "h": 105, "l": 101, "c": 104},
        {"o": 104, "h": 104, "l": 102, "c": 103},
        {"o": 103, "h": 103, "l": 101, "c": 102},
        {"o": 102, "h": 104, "l": 101, "c": 103},
        {"o": 103, "h": 108, "l": 103, "c": 107},
    ]
    for b in bars:
        state.recent_1m_bars.append(b)
        update_structure(state)
    snap = structure_snapshot(state)
    check("Swing high pivot detected (105)", state.swing_high_1 == 105, f"got {state.swing_high_1}")
    check("Bullish BOS fired on break", snap["last_event_label"] == "Bullish BOS", f"got {snap['last_event_label']}")
    check("trend_state flipped bullish", snap["trend_state"] == 1, f"got {snap['trend_state']}")
    check("trend_text() formats correctly", trend_text(1) == "UPTREND (HH/HL)")

    # ═══════════════════════════════════════════════
    # PHASE 2 — CANDLES + ZONES
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 2: CANDLESTICK PATTERNS + SUPPLY/DEMAND ZONES ---")
    state2 = SymbolState(symbol="TEST2")
    z_bars = [
        {"o": 100, "h": 100.5, "l": 98.0, "c": 99.0},
        {"o": 98.5, "h": 105.0, "l": 98.0, "c": 104.0},
    ]
    for b in z_bars:
        state2.recent_1m_bars.append(b)
        update_body_ema(state2, b["o"], b["c"])
        update_zones(state2, bar_start_ms=0)
    pattern = detect_candle_pattern(state2.recent_1m_bars, state2.body_size_ema)
    check("Bullish Engulfing detected", pattern == "Bullish Engulfing", f"got {pattern!r}")
    check("Demand zone carved", state2.demand_zone == (100.0, 98.0, 0), f"got {state2.demand_zone}")
    state2.recent_1m_bars.append({"o": 103, "h": 103, "l": 96, "c": 97})
    update_zones(state2, bar_start_ms=1)
    check("Demand zone self-cleans on close-through", state2.demand_zone is None)

    # ═══════════════════════════════════════════════
    # PHASE 3 — ADX / SUPERTREND / STRENGTH METER / MTF REWIRE
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 3: ADX / SUPERTREND / STRENGTH METER / MTF CACHE ---")
    state3 = SymbolState(symbol="TEST3")
    for i in range(20):
        h, l, c = 100 + i * 0.5, 99 + i * 0.5, 99.8 + i * 0.5
        update_adx(state3, h, l, c)
        update_supertrend(state3, h, l, c, atr=1.0)
    di_plus, di_minus, adx = get_adx(state3)
    st_level, st_bull = get_supertrend(state3)
    check("ADX computes a finite value", adx >= 0, f"adx={adx}")
    check("Supertrend flips bullish on sustained uptrend", st_bull is True)

    strong_features = {
        "ltp": 100.0, "vwap": 99.0, "ema_5": 100.5, "ema_20": 98.0, "atr_14": 1.0,
        "ml_features": {"adx": 35.0, "supertrend_bullish": True, "candle_body_pct": 0.7},
    }
    weak_features = {
        "ltp": 100.0, "vwap": 101.0, "ema_5": 100.0, "ema_20": 100.0, "atr_14": 1.0,
        "ml_features": {"adx": 5.0, "supertrend_bullish": False, "candle_body_pct": 0.1},
    }
    strong = compute_strength_meter(strong_features, bullish=True)
    weak = compute_strength_meter(weak_features, bullish=True)
    check("Strength meter separates strong setup", strong > 70, f"strong={strong}")
    check("Strength meter separates weak setup", weak < 30, f"weak={weak}")

    no_cache = compute_pine_decision(strong_features, bullish=True, entry=100.0, invalidation=98.0)
    check("Falls back to live proxy with no MTF cache", no_cache.mtf_source == "live_proxy")
    fresh = {**strong_features, "mtf_cache": {
        "dots": {"1M": "G", "5M": "G", "15M": "G", "1H": "G", "4H": "G", "1D": "G"},
        "mtf": {"1M": "BULL", "5M": "BULL", "15M": "BULL", "1H": "BULL", "4H": "BULL", "1D": "BULL"},
        "mtf_text": "Strong CE alignment", "updated_at": time.time(),
    }}
    with_cache = compute_pine_decision(fresh, bullish=True, entry=100.0, invalidation=98.0)
    check("Prefers fresh MTF cache over live proxy", with_cache.mtf_source == "historical_cache")
    stale = {**strong_features, "mtf_cache": {
        "dots": {"1M": "G"}, "mtf": {"1M": "BULL"}, "mtf_text": "x",
        "updated_at": time.time() - (MTF_CACHE_STALE_SEC + 100),
    }}
    stale_result = compute_pine_decision(stale, bullish=True, entry=100.0, invalidation=98.0)
    check("Falls back when MTF cache is stale", stale_result.mtf_source == "live_proxy")

    check("T1 < T2 < T3 ordering (bullish)", with_cache.t1_price < with_cache.t2_price < with_cache.t3_price,
          f"{with_cache.t1_price} < {with_cache.t2_price} < {with_cache.t3_price}")
    chase_features = {**strong_features, "ltp": 100.0, "vwap": 99.0}
    chase_features["ml_features"] = {"adx": 35.0, "supertrend_bullish": True, "candle_body_pct": 0.7}
    chaseable_decision = compute_pine_decision(chase_features, bullish=True, entry=100.0, invalidation=98.0)
    check("Chaseable flag true on strong aligned setup", chaseable_decision.chaseable is True)
    weak_chase = compute_pine_decision(weak_features | {"ltp": 100.0, "vwap": 101.0}, bullish=True, entry=100.0, invalidation=98.0)
    check("Chaseable flag false on weak setup", weak_chase.chaseable is False)

    # ═══════════════════════════════════════════════
    # UNIFIED CONVICTION SCORE (technical/pine/strength/structure blend
    # + chaseable hard cap) — folds the previously-orphaned strength_score
    # and chaseable flag into the one score the dashboard/Telegram show.
    # ═══════════════════════════════════════════════
    print("\n--- UNIFIED CONVICTION SCORE ---")
    base_technical = {
        "rel_vol_20d": 5.0, "direction": "bullish", "ltp": 101.0, "vwap": 100.9,
        "rsi_14": 58, "ema_9": 100, "ema_20": 99, "order_imbalance": 0.4,
        "bb_upper": 100.5, "bb_width": 0.03, "atr_trend": "BULL",
        "candle_pattern": "Bullish Engulfing", "spread_bps": 5,
    }
    strong = {
        **base_technical, "pine_confidence": 90.0, "strength_score": 88.0,
        "chaseable": True, "last_event_label": "Bullish BOS", "trend_state": 1,
        "anti_chase_ok": True, "rejection_reasons": [],
    }
    score_strong, _ = compute_conviction(strong)
    check("Strong aligned + chaseable setup reaches A/A+", grade_conviction(score_strong) in ("A", "A+"),
          f"score={score_strong}")

    capped = {**strong, "chaseable": False}
    score_capped, sub_capped = compute_conviction(capped)
    check("Not-chaseable setup hard-capped below A", score_capped == CHASEABLE_GRADE_CAP, f"score={score_capped}")
    check("Cap keeps grade at B even with strong raw inputs", grade_conviction(score_capped) == "B")

    opposing = {**strong, "last_event_label": "Bearish BOS", "trend_state": -1}
    score_opposed, _ = compute_conviction(opposing)
    check("Opposing structure scores lower than aligned structure", score_opposed < score_strong,
          f"opposed={score_opposed} vs aligned={score_strong}")

    no_pine_score, no_pine_sub = compute_conviction(base_technical)
    check("No pine_confidence -> unaffected legacy technical-only path",
          "pine_confidence_component" not in no_pine_sub)

    # ═══════════════════════════════════════════════
    # OI BUILDUP CLASSIFICATION (Long/Short Buildup, Short Covering,
    # Long Unwinding) -- was in the original design doc, never implemented.
    # ═══════════════════════════════════════════════
    print("\n--- OI BUILDUP CLASSIFICATION ---")
    check("Price up + OI up -> Long Buildup",
          _classify_oi_buildup(15.0, 1.2)["label"] == "LONG BUILDUP")
    check("Price down + OI up -> Short Buildup",
          _classify_oi_buildup(15.0, -1.2)["label"] == "SHORT BUILDUP")
    check("Price up + OI down -> Short Covering",
          _classify_oi_buildup(-15.0, 1.2)["label"] == "SHORT COVERING")
    check("Price down + OI down -> Long Unwinding",
          _classify_oi_buildup(-15.0, -1.2)["label"] == "LONG UNWINDING")
    check("Small OI move -> OI FLAT, not a false signal",
          _classify_oi_buildup(1.0, 2.0)["label"] == "OI FLAT")
    check("Missing data never raises",
          _classify_oi_buildup(None, None)["label"] == "NO_DATA")
    check("Long Buildup / Short Covering both tag BULLISH bias",
          _classify_oi_buildup(15.0, 1.2)["bias"] == "BULLISH" == _classify_oi_buildup(-15.0, 1.2)["bias"])

    # ═══════════════════════════════════════════════
    # PHASE 4 — MTF MAJOR BLOCKER
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 4: MTF MAJOR BLOCKER ---")

    def bar(o, h, l, c):
        return {"open": o, "high": h, "low": l, "close": c}

    h1_bars = [
        bar(100, 101, 99, 100), bar(100, 102, 100, 101), bar(101, 110, 101, 108),
        bar(108, 108, 105, 106), bar(106, 107, 104, 105), bar(105, 106, 103, 104),
        bar(104, 105, 100, 100),
    ]
    highs, lows = _fractal_pivots(h1_bars)
    check("Fractal pivot high found on 1H bars", 110 in highs, f"highs={highs}")
    blocker = _major_blocker({"1H": h1_bars, "1D": []}, ltp=100.0)
    check("Blocker reports nearest resistance above price", blocker["blocker_up_level"] == 110.0)
    check("Blocker tags the correct source timeframe", blocker["blocker_up_source"] == "1H")

    # ═══════════════════════════════════════════════
    # PHASE 5 — POSITION SIZING
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 5: POSITION SIZING ---")
    sizing = compute_position_size(risk_amount=5000, per_unit_risk=2, lot_size=50, max_lots=5)
    check("Sizing computes lots correctly", sizing["lot_count"] == 5 and sizing["quantity"] == 250, f"got {sizing}")
    zero_sizing = compute_position_size(risk_amount=0, per_unit_risk=2, lot_size=50)
    check("Sizing never raises on degenerate input", zero_sizing == {"quantity": 0, "lot_count": 0})

    # ═══════════════════════════════════════════════
    # PHASE 6 — TELEGRAM FORMATTER
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 6: TELEGRAM FORMATTER WIRING ---")
    payload = {
        "symbol": "RELIANCE", "strategy_id": "options_first_hybrid", "signal_type": "bullish",
        "conviction_score": 84, "conviction_grade": "A+", "risk_reward_ratio": 2.1,
        "entry_price": 2847.5, "invalidation_price": 2820.0, "target_price": 2900.0,
        "sub_scores": {"position_sizing": {"lot_count": 3, "lot_size": 250, "quantity": 750, "risk_amount": 500.0}},
        "features_snapshot": {
            "t1_price": 2880.0, "t2_price": 2900.0, "t3_price": 2920.0,
            "chaseable": True, "option_bias": "BUY CE",
            "mtf_dots": {"1M": "G", "5M": "G", "15M": "G", "1H": "G", "4H": "Y", "1D": "G"},
        },
    }
    text = format_signal(payload)
    # Short, score-first format per direct feedback: score/grade, MTF dots,
    # entry/SL/T1/T2/T3, chaseable flag, sizing -- no prose walls of text.
    check("Formatter includes score", "Score 84" in text)
    check("Formatter includes MTF colour dots line", any(line.startswith("MTF") for line in text.split("\n")))
    check("Formatter includes chaseable flag", "Chaseable" in text)
    check("Formatter includes T1/T2/T3", "T1" in text and "T2" in text and "T3" in text)
    check("Formatter includes Sizing line", "Sizing: 3 lot" in text)
    check("Formatter stays short (<= 10 lines)", text.count("\n") <= 9, f"lines={text.count(chr(10))+1}")
    check("Formatter does not crash on missing fields", format_signal({"symbol": "X"}) is not None)

    # ═══════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print(f"  Total: {passed + failed}  |  PASS: {passed}  |  FAIL: {failed}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'PINE V6 ALIGNMENT VALIDATED' if failed == 0 else 'NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
