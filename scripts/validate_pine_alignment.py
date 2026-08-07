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
        from scanner.pine_confidence import compute_pine_decision, compute_strength_meter, MTF_CACHE_STALE_SEC, compute_fib_targets
        check("scanner.pine_confidence imports", True)
    except Exception as e:
        check("scanner.pine_confidence imports", False, str(e))
        return

    try:
        from feature_engine.features.fibonacci import (
            retracement_levels, resistance_retracement_levels, extension_levels,
            projection_levels, find_confluence_clusters, fib_snapshot,
        )
        check("feature_engine.features.fibonacci imports", True)
    except Exception as e:
        check("feature_engine.features.fibonacci imports", False, str(e))
        return

    try:
        from api.routes.ticks import _classify_oi_buildup
        check("api.routes.ticks OI classification imports", True)
    except Exception as e:
        check("api.routes.ticks OI classification imports", False, str(e))
        return

    try:
        from api.routes.ticks import _potential_upside_pct, _trade_horizon_label
        check("api.routes.ticks trade-card helpers import", True)
    except Exception as e:
        check("api.routes.ticks trade-card helpers import", False, str(e))
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
        from api.routes.mtf import _classic_pivots, _pivot_bias, _virgin_cpr_zones
        check("api.routes.mtf pivot/CPR functions import", True)
    except Exception as e:
        check("api.routes.mtf pivot/CPR functions import", False, str(e))
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
    # PHASE 1 (NEW) — FIBONACCI CONFLUENCE
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 1 (NEW): FIBONACCI CONFLUENCE + ALTERNATE T1/T2/T3 ---")

    # Formula checks against hand-computable values (Boroden's ratios).
    r = retracement_levels(100.0, 200.0)
    check("Retracement 61.8% formula", abs(r[0.618] - 138.2) < 1e-9, f"got {r[0.618]}")
    check("Retracement 50% formula", abs(r[0.5] - 150.0) < 1e-9, f"got {r[0.5]}")
    rr = resistance_retracement_levels(100.0, 200.0)
    check("Resistance retracement 61.8% formula", abs(rr[0.618] - 161.8) < 1e-9, f"got {rr[0.618]}")
    e_bull = extension_levels(100.0, 200.0, bullish=True)
    check("Extension 1.618 bullish formula", abs(e_bull[1.618] - 361.8) < 1e-9, f"got {e_bull[1.618]}")
    e_bear = extension_levels(100.0, 200.0, bullish=False)
    check("Extension 1.618 bearish formula", abs(e_bear[1.618] - (100.0 - 100.0 * 1.618)) < 1e-9, f"got {e_bear[1.618]}")
    p = projection_levels(100.0, 200.0, 150.0)
    check("Projection 1.0 (symmetry) formula", abs(p[1.0] - 250.0) < 1e-9, f"got {p[1.0]}")
    check("Projection 1.618 formula", abs(p[1.618] - (150.0 + 100.0 * 1.618)) < 1e-9, f"got {p[1.618]}")

    # Confluence cluster: repeated 100<->200 swings put a 50%-retracement
    # level at exactly 150 six times over -- must cluster with >=3 hits.
    convergent_swings = [
        (100.0, "low", 1), (200.0, "high", 2), (100.0, "low", 3),
        (200.0, "high", 4), (100.0, "low", 5), (200.0, "high", 6),
    ]
    clusters = find_confluence_clusters(convergent_swings, current_price=150.0, atr=2.0)
    check("Confluence cluster found near repeated 50% level", bool(clusters) and clusters[0]["hits"] >= 3, f"got {clusters}")
    if clusters:
        check("Nearest cluster centers on 150.0", abs(clusters[0]["center"] - 150.0) < 1.0, f"got {clusters[0]['center']}")

    # No swing history / too few points -> no crash, empty result.
    check("Confluence scan on empty history returns []", find_confluence_clusters([], 100.0, 1.0) == [])
    check("Confluence scan on single point returns []", find_confluence_clusters([(100.0, "low", 1)], 100.0, 1.0) == [])

    # Integration: feed a real bar sequence through update_structure and
    # confirm swing_points accumulates (not just swing_high_1/2) and
    # fib_snapshot() reads it without crashing.
    fib_state = SymbolState(symbol="TESTFIB")
    fib_state.atr = 1.5
    fib_bars = [
        {"o": 100, "h": 101, "l": 99, "c": 100}, {"o": 100, "h": 102, "l": 100, "c": 101},
        {"o": 101, "h": 110, "l": 101, "c": 108}, {"o": 108, "h": 108, "l": 105, "c": 106},
        {"o": 106, "h": 107, "l": 95, "c": 96}, {"o": 96, "h": 97, "l": 94, "c": 95},
        {"o": 95, "h": 105, "l": 95, "c": 104}, {"o": 104, "h": 104, "l": 100, "c": 101},
        {"o": 101, "h": 102, "l": 98, "c": 99}, {"o": 99, "h": 112, "l": 99, "c": 111},
        {"o": 111, "h": 111, "l": 107, "c": 108}, {"o": 108, "h": 109, "l": 104, "c": 105},
    ]
    for b in fib_bars:
        fib_state.recent_1m_bars.append(b)
        update_structure(fib_state)
    check("swing_points accumulates beyond the 2 BOS/CHOCH slots", len(fib_state.swing_points) >= 2, f"got {len(fib_state.swing_points)}")
    snap = fib_snapshot(fib_state, ltp=105.0)
    check("fib_snapshot returns expected keys", set(snap) == {
        "fib_cluster_center", "fib_cluster_low", "fib_cluster_high",
        "fib_cluster_hits", "fib_cluster_sources", "fib_swing_count",
    }, f"got {set(snap)}")
    check("fib_snapshot swing count matches state", snap["fib_swing_count"] == len(fib_state.swing_points))

    # compute_fib_targets: no cluster -> None (thin/no history), never crashes.
    check("compute_fib_targets None when no cluster in ml_features", compute_fib_targets({}, bullish=True, entry=100.0) is None)

    # compute_fib_targets: synthetic ml_features simulating a confirmed
    # cluster ahead of entry for a bullish trade -> valid, ordered T1<T2<T3.
    fib_ml = {
        "fib_cluster_center": 100.0, "fib_cluster_hits": 4,
        "swing_high_1": 120.0, "swing_low_1": 100.0,
    }
    ft = compute_fib_targets(fib_ml, bullish=True, entry=95.0)
    check("compute_fib_targets returns targets for a valid ahead-of-entry cluster", ft is not None, f"got {ft}")
    if ft:
        check("Fib targets ordered T1 < T2 < T3 (bullish)", ft["fib_t1_price"] < ft["fib_t2_price"] < ft["fib_t3_price"], f"got {ft}")
        check("Fib T1 matches 1.272 extension formula", abs(ft["fib_t1_price"] - (100.0 + 20.0 * 1.272)) < 1e-6, f"got {ft['fib_t1_price']}")

    # compute_fib_targets: entry already sits past where the bullish T1
    # would project to (target already behind price) -> None (honest
    # fallback to ATR-based targets), not a nonsense already-hit target.
    ft_behind = compute_fib_targets(fib_ml, bullish=True, entry=130.0)
    check("compute_fib_targets None when target is already behind entry", ft_behind is None, f"got {ft_behind}")

    # compute_pine_decision must not crash when ml_features has no fib data
    # at all (new symbol, thin swing history) -- fib_targets should be None.
    decision_features = {
        "ltp": 100.0, "vwap": 99.5, "ema_5": 100.5, "ema_9": 100.0, "ema_20": 99.0, "ema_50": 98.0,
        "rsi_14": 60.0, "macd": 0.5, "macd_signal": 0.3, "macd_hist": 0.2, "rel_vol_20d": 1.2,
        "atr_14": 1.0, "spread_bps": 20.0, "change_pct": 1.0, "bb_width": 0.02,
        "atr_trend": "BULL", "candle_pattern": "", "squeeze_state": "", "nr_pattern": "",
        "ml_features": {},
    }
    decision = compute_pine_decision(decision_features, bullish=True, entry=100.0, invalidation=98.0)
    check("PineDecision.fib_targets is None with no fib data", decision.fib_targets is None)
    check("PineDecision.as_snapshot includes fib_targets key", "fib_targets" in decision.as_snapshot())

    # ═══════════════════════════════════════════════
    # PHASE 2 (NEW) — CLASSIC PIVOTS / CPR / VIRGIN CPR
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 2 (NEW): CLASSIC PIVOTS + CPR + VIRGIN CPR ---")

    piv = _classic_pivots(110.0, 100.0, 108.0)
    check("Pivot formula P=(H+L+C)/3", piv.get("pivot") == 106.0, f"got {piv.get('pivot')}")
    check("R1 formula 2P-L", piv.get("r1") == 112.0, f"got {piv.get('r1')}")
    check("S1 formula 2P-H", piv.get("s1") == 102.0, f"got {piv.get('s1')}")
    check("R2 formula P+range", piv.get("r2") == 116.0, f"got {piv.get('r2')}")
    check("S2 formula P-range", piv.get("s2") == 96.0, f"got {piv.get('s2')}")
    check("R3 formula H+2(P-L)", piv.get("r3") == 122.0, f"got {piv.get('r3')}")
    check("S3 formula L-2(H-P)", piv.get("s3") == 92.0, f"got {piv.get('s3')}")
    check("CPR top/bottom from BCPR/TCPR", piv.get("cpr_top") == 107.0 and piv.get("cpr_bottom") == 105.0, f"got {piv}")
    check("Wide CPR classified correctly", piv.get("day_type") == "wide_range_bias", f"got {piv.get('day_type')}")
    check("Degenerate/invalid input returns {}", _classic_pivots(0, 0, 0) == {})
    check("Pivot bias bullish above pivot", _pivot_bias(110.0, piv) == "bullish")
    check("Pivot bias bearish below pivot", _pivot_bias(100.0, piv) == "bearish")
    check("Pivot bias neutral with no pivots", _pivot_bias(100.0, {}) == "neutral")

    virgin_bars = [
        {"time": 1, "open": 99, "high": 100, "low": 90, "close": 98, "volume": 0},
        {"time": 2, "open": 101, "high": 103, "low": 99, "close": 102, "volume": 0},
        {"time": 3, "open": 104, "high": 106, "low": 102, "close": 105, "volume": 0},
        {"time": 4, "open": 107, "high": 109, "low": 104, "close": 108, "volume": 0},
        {"time": 5, "open": 104.3, "high": 105, "low": 103, "close": 104.5, "volume": 0},  # touches the age=3 zone
    ]
    # Regression guard: age=1 must be TODAY's own CPR -- i.e. exactly what
    # _classic_pivots(daily[-1]...) returns, the same value the live `pivots`
    # field uses. An earlier version of this function was off-by-one here
    # (age=1 silently meant "yesterday's CPR" instead of "today's").
    live_pivots = _classic_pivots(virgin_bars[-1]["high"], virgin_bars[-1]["low"], virgin_bars[-1]["close"])
    vz = _virgin_cpr_zones(virgin_bars, current_ltp=0.0)
    age1 = next((z for z in vz if z["formed_days_ago"] == 1), None)
    check("Virgin CPR age=1 matches the live pivots CPR exactly", age1 is not None and age1["cpr_top"] == live_pivots["cpr_top"] and age1["cpr_bottom"] == live_pivots["cpr_bottom"], f"age1={age1} vs pivots={live_pivots}")
    ages = sorted(z["formed_days_ago"] for z in vz)
    check("Virgin CPR: subsequent-day body touch excludes that zone", ages == [1, 2, 4], f"got {ages}")
    vz_ltp = _virgin_cpr_zones(virgin_bars, current_ltp=107.0)
    ages_ltp = sorted(z["formed_days_ago"] for z in vz_ltp)
    check("Virgin CPR: current_ltp inside a zone also counts as touched", ages_ltp == [1, 4, 5], f"got {ages_ltp}")
    check("Virgin CPR: thin history (<2 bars) returns [] without crashing", _virgin_cpr_zones(virgin_bars[:1], 0.0) == [])
    check("Virgin CPR: zones sorted strongest-first", all(vz[i]["strength"] >= vz[i + 1]["strength"] for i in range(len(vz) - 1)), f"got {vz}")
    check("Virgin CPR: capped at 3 zones", len(vz) <= 3)

    # ═══════════════════════════════════════════════
    # PHASE 3 (NEW) — TRADE-CARD FIELDS
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 3 (NEW): POTENTIAL UPSIDE % + HORIZON LABEL ---")

    # Matches the user-supplied reference broker-call cards exactly:
    # Hindalco (current 1059.60, target 1105) -> 4.28%; Blue Star
    # (current 1514, target 1665.95) -> 10.04%.
    up1 = _potential_upside_pct("BUY CE", 1059.60, 1105.0)
    check("Potential upside % matches reference card 1 (4.28%)", up1 == 4.28, f"got {up1}")
    up2 = _potential_upside_pct("BUY CE", 1514.0, 1665.95)
    check("Potential upside % matches reference card 2 (10.04%)", up2 == 10.04, f"got {up2}")

    up_pe = _potential_upside_pct("BUY PE", 100.0, 90.0)
    check("PE upside is the downward move framed positive", up_pe == 10.0, f"got {up_pe}")
    up_pe_wrong_side = _potential_upside_pct("BUY PE", 100.0, 110.0)
    check("PE target above current price gives negative upside (honest, not clamped)", up_pe_wrong_side == -10.0, f"got {up_pe_wrong_side}")
    check("Zero/negative ltp never raises, returns 0.0", _potential_upside_pct("BUY CE", 0.0, 100.0) == 0.0)

    check("Horizon label: INTRADAY -> Intraday", _trade_horizon_label("INTRADAY") == "Intraday")
    check("Horizon label: BTST_1_2D -> Short Term", _trade_horizon_label("BTST_1_2D") == "Short Term")
    check("Horizon label: SWING -> Medium Term", _trade_horizon_label("SWING") == "Medium Term")
    check("Horizon label: AVOID -> Avoid", _trade_horizon_label("AVOID") == "Avoid")
    check("Horizon label: unknown value falls back to title-case, no crash", _trade_horizon_label("some_new_state") == "Some_New_State")

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
