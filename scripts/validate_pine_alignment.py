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
        from feature_engine.features.ict import update_ict, ict_snapshot
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
        from scanner.sector import SectorEngine, SectorState, SymbolSnapshot
        check("scanner.sector imports", True)
    except Exception as e:
        check("scanner.sector imports", False, str(e))
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
        from api.routes.mtf import _ma_regime, _sma_series, _regime_at
        check("api.routes.mtf MA-regime functions import", True)
    except Exception as e:
        check("api.routes.mtf MA-regime functions import", False, str(e))
        return

    try:
        from api.chart_patterns import (
            detect_double_top, detect_double_bottom, detect_triple_top, detect_triple_bottom,
            detect_rectangle, detect_chart_patterns, fractal_pivots_indexed, _similar,
        )
        check("api.chart_patterns functions import", True)
    except Exception as e:
        check("api.chart_patterns functions import", False, str(e))
        return

    try:
        from infusion_common.sizing import compute_position_size
        check("infusion_common.sizing imports", True)
    except Exception as e:
        check("infusion_common.sizing imports", False, str(e))
        return

    try:
        from api.routes.mtf import _donchian_channel, DONCHIAN_PERIOD
        check("api.routes.mtf Donchian channel function imports", True)
    except Exception as e:
        check("api.routes.mtf Donchian channel function imports", False, str(e))
        return

    try:
        from api.wyckoff import detect_structural_failure, detect_shortening_of_thrust, detect_sos_sow_bar
        check("api.wyckoff functions import", True)
    except Exception as e:
        check("api.wyckoff functions import", False, str(e))
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
    # PHASE 4 (NEW) — GOLDEN/DEATH CROSS + MA-STACK REGIME
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 4 (NEW): GOLDEN/DEATH CROSS + MA-STACK REGIME ---")

    def flat_then_jump(base, target, jump_days):
        return [{"close": c} for c in [base] * 200 + [target] * jump_days]

    check("_sma_series returns None before enough history", _sma_series([1.0, 2.0, 3.0], 5) == [None, None, None])
    sma3 = _sma_series([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    check("_sma_series computes correctly once warmed up", sma3 == [None, None, 2.0, 3.0, 4.0], f"got {sma3}")
    check("_regime_at: sma50>sma200 -> golden_cross", _regime_at(110.0, 100.0) == "golden_cross")
    check("_regime_at: sma50<sma200 -> death_cross", _regime_at(90.0, 100.0) == "death_cross")
    check("_regime_at: equal -> neutral", _regime_at(100.0, 100.0) == "neutral")
    check("_regime_at: missing data -> unknown", _regime_at(None, 100.0) == "unknown")

    r_recent = _ma_regime(flat_then_jump(100.0, 200.0, 8))
    check("Golden cross detected on a clean crossover series", r_recent["regime"] == "golden_cross", f"got {r_recent}")
    check("Cross flagged recent when it happened 7 trading days ago", r_recent["cross_recent"] is True, f"got {r_recent}")
    check("Strong bull stack detected (price > 20 > 50 > 200 SMA)", r_recent["stack"] == "strong_bull_stack", f"got {r_recent}")

    r_old = _ma_regime(flat_then_jump(100.0, 200.0, 15))
    check("Same crossover NOT flagged recent once 14 days old", r_old["cross_recent"] is False, f"got {r_old}")
    check(
        "A real crossover passing through equal-SMA (neutral) still counts as a differing prior state",
        r_recent["cross_recent"] is True,
        "regression guard for the neutral-exclusion bug found during Phase 4 testing",
    )

    r_death = _ma_regime(flat_then_jump(200.0, 100.0, 8))
    check("Death cross detected on the mirror-image series", r_death["regime"] == "death_cross", f"got {r_death}")
    check("Death cross also flagged recent", r_death["cross_recent"] is True, f"got {r_death}")
    check("Strong bear stack detected on death-cross series", r_death["stack"] == "strong_bear_stack", f"got {r_death}")

    thin = _ma_regime([{"close": 100.0}] * 5)
    check("Thin history (<20 days) returns unknown regime, never crashes", thin["regime"] == "unknown" and thin["sma50"] is None, f"got {thin}")

    # ═══════════════════════════════════════════════
    # PHASE 5 (NEW) — CHART-PATTERN GEOMETRY (DAILY TIMEFRAME)
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 5 (NEW): CHART-PATTERN GEOMETRY (DOUBLE/TRIPLE TOP-BOTTOM, RECTANGLE) ---")

    check("_similar: within tolerance", _similar(150.0, 151.0) is True)
    check("_similar: outside tolerance", _similar(100.0, 120.0) is False)
    check("_similar: non-positive input never raises", _similar(0.0, 100.0) is False)

    piv_dt = [(100.0, "low", 0), (150.0, "high", 1), (110.0, "low", 2), (151.0, "high", 3)]
    dt = detect_double_top(piv_dt, current_price=105.0)
    check("Double top: height computed correctly", dt is not None and dt["height"] == 41.0, f"got {dt}")
    check("Double top: half-height target (Bulkowski correction)", dt is not None and dt["target"] == 89.5, f"got {dt}")
    check("Double top: confirmed when price closes below the valley", dt is not None and dt["confirmed"] is True)
    dt_unconfirmed = detect_double_top(piv_dt, current_price=130.0)
    check("Double top: NOT confirmed while price sits above the valley", dt_unconfirmed is not None and dt_unconfirmed["confirmed"] is False)

    piv_db = [(150.0, "high", 0), (100.0, "low", 1), (140.0, "high", 2), (101.0, "low", 3)]
    db = detect_double_bottom(piv_db, current_price=145.0)
    check("Double bottom: height + half-height target", db is not None and db["height"] == 40.0 and db["target"] == 160.0, f"got {db}")
    check("Double bottom: confirmed above the peak", db is not None and db["confirmed"] is True)

    piv_tt = [(150.0, "high", 0), (120.0, "low", 1), (151.0, "high", 2), (118.0, "low", 3), (149.0, "high", 4)]
    tt = detect_triple_top(piv_tt, current_price=110.0)
    check("Triple top: uses the LOWER intervening valley as confirmation line", tt is not None and tt["confirmation_line"] == 118.0, f"got {tt}")
    check("Triple top: full-height target (no half-height correction)", tt is not None and tt["target"] == 85.0, f"got {tt}")

    piv_tb = [(100.0, "low", 0), (130.0, "high", 1), (99.0, "low", 2), (132.0, "high", 3), (101.0, "low", 4)]
    tb = detect_triple_bottom(piv_tb, current_price=140.0)
    check("Triple bottom: uses the HIGHER intervening peak as confirmation line", tb is not None and tb["confirmation_line"] == 132.0, f"got {tb}")
    check("Triple bottom: full-height target", tb is not None and tb["target"] == 165.0, f"got {tb}")

    piv_rect = [(150.0, "high", 0), (100.0, "low", 1), (151.0, "high", 2), (99.0, "low", 3), (150.5, "high", 4)]
    rect_inside = detect_rectangle(piv_rect, current_price=125.0)
    check("Rectangle: no target while price sits inside the channel", rect_inside is not None and rect_inside["breakout"] == "inside" and rect_inside["target"] is None, f"got {rect_inside}")
    rect_up = detect_rectangle(piv_rect, current_price=160.0)
    check("Rectangle: full-height target on an upside breakout", rect_up is not None and rect_up["breakout"] == "up" and rect_up["target"] == 201.5, f"got {rect_up}")

    noise = [(100.0, "high", 0), (90.0, "low", 1), (120.0, "high", 2)]
    check("No pattern forced on dissimilar peaks (honest None, not a false positive)", detect_double_top(noise, 95.0) is None)
    check("Thin/empty pivot list never crashes", detect_chart_patterns([], 100.0) == [])

    overlap = detect_chart_patterns(piv_rect, current_price=160.0)
    overlap_names = sorted(m["pattern"] for m in overlap)
    check(
        "Known double/triple/rectangle overlap on the same swing sequence (documented, not a bug)",
        "rectangle" in overlap_names and "double_top" in overlap_names,
        f"got {overlap_names}",
    )

    idx_pivots = fractal_pivots_indexed([
        {"high": 101, "low": 99}, {"high": 102, "low": 100}, {"high": 110, "low": 101},
        {"high": 108, "low": 105}, {"high": 107, "low": 104},
    ])
    check("fractal_pivots_indexed returns chronologically ordered tuples", idx_pivots == sorted(idx_pivots, key=lambda t: t[2]), f"got {idx_pivots}")

    # ═══════════════════════════════════════════════
    # PHASE 6 (NEW) — ICT: FVG / LIQUIDITY SWEEP / ORDER BLOCK
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 6 (NEW): ICT (FVG, LIQUIDITY SWEEP, ORDER BLOCK) ---")

    def feed(state, bar):
        state.recent_1m_bars.append(bar)
        state.completed_1m_bars += 1
        update_ict(state)

    ict1 = SymbolState(symbol="ICT1")
    for b in [
        {"o": 99, "h": 100, "l": 98, "c": 99.5},
        {"o": 100, "h": 103, "l": 99, "c": 102},
        {"o": 103, "h": 106, "l": 105, "c": 105.5},
    ]:
        feed(ict1, b)
    check("FVG (BISI) detected: candle1.high < candle3.low", ict1.fvg_bullish == (100, 105, 3), f"got {ict1.fvg_bullish}")
    ict1_snap = ict_snapshot(ict1)
    check("FVG CE is the 50% midpoint", ict1_snap["fvg_bullish_ce"] == 102.5, f"got {ict1_snap['fvg_bullish_ce']}")
    for _ in range(3):
        feed(ict1, {"o": 102, "h": 103, "l": 101, "c": 102.5})  # closes inside the gap each time
    check("FVG fully rebalances on the 3rd touch (ICT's own rule)", ict1.fvg_bullish is None)

    ict2 = SymbolState(symbol="ICT2")
    ict2.swing_low_1 = 100.0
    for b in [{"o": 101, "h": 102, "l": 100.5, "c": 101.5}] * 3:
        feed(ict2, b)
    feed(ict2, {"o": 101, "h": 101.5, "l": 99.0, "c": 101.2})  # wicks below the swing low, closes back above
    check("Liquidity sweep: sellside detected on wick-through-close-back-above", ict2.last_liquidity_sweep == "sellside", f"got {ict2.last_liquidity_sweep}")

    ict3 = SymbolState(symbol="ICT3")
    ict3.swing_low_1 = 100.0
    for b in [{"o": 101, "h": 102, "l": 100.5, "c": 101.5}] * 3:
        feed(ict3, b)
    feed(ict3, {"o": 101.5, "h": 102.0, "l": 99.0, "c": 100.5})  # down-close candle sweeping sellside
    check("Order Block candidate forms on sweep + down-close", ict3.order_block_bullish == (99.0, 102.0, ict3.completed_1m_bars, False), f"got {ict3.order_block_bullish}")
    check("Order Block NOT validated on formation (needs a later close beyond its high)", ict3.order_block_bullish[3] is False)
    feed(ict3, {"o": 102.0, "h": 103.5, "l": 102.0, "c": 103.0})  # close above OB high
    check("Order Block validates once price closes above its high", ict3.order_block_bullish is not None and ict3.order_block_bullish[3] is True, f"got {ict3.order_block_bullish}")
    feed(ict3, {"o": 101.0, "h": 101.0, "l": 99.5, "c": 100.0})  # close below the 50% mean threshold (100.5)
    check("Order Block invalidates on a close below its mean threshold", ict3.order_block_bullish is None)

    ict4 = SymbolState(symbol="ICT4")
    ict4.swing_low_1 = 100.0
    for b in [{"o": 101, "h": 102, "l": 100.5, "c": 101.5}] * 3:
        feed(ict4, b)
    feed(ict4, {"o": 101.5, "h": 102.0, "l": 99.0, "c": 100.5})  # candidate forms (99, 102)
    feed(ict4, {"o": 100.0, "h": 100.0, "l": 97.0, "c": 98.0})   # closes below the candidate's own low before ever validating
    check("Order Block fails outright if price closes below its low before validating", ict4.order_block_bullish is None)

    ict5 = SymbolState(symbol="ICT5")
    ict5.swing_high_1 = 100.0
    for b in [{"o": 99, "h": 99.5, "l": 98.5, "c": 99.2}] * 3:
        feed(ict5, b)
    feed(ict5, {"o": 99.5, "h": 101.0, "l": 99.0, "c": 99.8})  # up-close candle sweeping buyside
    check("Bearish mirror: buyside sweep detected", ict5.last_liquidity_sweep == "buyside")
    check("Bearish Order Block candidate forms on the mirror conditions", ict5.order_block_bearish == (99.0, 101.0, ict5.completed_1m_bars, False), f"got {ict5.order_block_bearish}")

    ict6 = SymbolState(symbol="ICT6")
    check("update_ict never crashes on thin history (<3 bars)", update_ict(ict6) is None and ict6.fvg_bullish is None)

    # ═══════════════════════════════════════════════
    # PHASE 7 (NEW) — ATR-SCALED POSITION SIZING + DONCHIAN CHANNEL
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 7 (NEW): ATR-SCALED POSITION SIZING (TURTLE) + DONCHIAN CHANNEL ---")

    no_atr = compute_position_size(5000, 2, 50, max_lots=5)
    check("No-ATR call preserves the exact original 2-key result shape", no_atr == {"quantity": 250, "lot_count": 5}, f"got {no_atr}")
    check("No-ATR call never adds a sizing_method key (backward compat)", "sizing_method" not in no_atr)

    tight_stop = compute_position_size(5000, 2, 50, max_lots=100, atr=50)
    check("ATR cap kicks in when a tight stop implies oversizing", tight_stop.get("sizing_method") == "atr_capped", f"got {tight_stop}")
    check("ATR-capped size matches risk_amount/(multiplier*atr)/lot_size", tight_stop["lot_count"] == 1, f"got {tight_stop}")

    wide_stop = compute_position_size(5000, 100, 50, max_lots=100, atr=1.0)
    check("Risk-distance sizing still wins when it's the tighter constraint", wide_stop.get("sizing_method") == "risk_distance", f"got {wide_stop}")

    zero_atr = compute_position_size(5000, 2, 50, max_lots=5, atr=0.0)
    check("atr=0.0 is treated as not supplied, not a division by zero", zero_atr == {"quantity": 250, "lot_count": 5}, f"got {zero_atr}")
    none_input = compute_position_size(0, 2, 50, atr=10.0)
    check("Degenerate risk_amount never raises even with ATR supplied", none_input == {"quantity": 0, "lot_count": 0})

    donchian_bars = [{"high": 100 + i, "low": 90 + i, "close": 95 + i} for i in range(25)]
    dc = _donchian_channel(donchian_bars, period=20)
    check("Donchian channel high/low computed correctly over the window", dc["high"] == 124 and dc["low"] == 95, f"got {dc}")
    check("Fresh high breakout flagged when today set the window's extreme", dc["fresh_high_breakout"] is True, f"got {dc}")
    check("Fresh low breakout correctly false (today's low isn't the window min)", dc["fresh_low_breakout"] is False, f"got {dc}")

    pullback_bars = list(donchian_bars)
    pullback_bars[-1] = {"high": 110, "low": 108, "close": 109}
    dc_pullback = _donchian_channel(pullback_bars, period=20)
    check("A pullback day (not setting a new extreme) is NOT flagged as a fresh breakout", dc_pullback["fresh_high_breakout"] is False, f"got {dc_pullback}")

    thin_dc = _donchian_channel(donchian_bars[:5], period=DONCHIAN_PERIOD)
    check("Thin history returns None channel values, never crashes", thin_dc["high"] is None and thin_dc["low"] is None)

    # ═══════════════════════════════════════════════
    # PHASE 8 (NEW) — WYCKOFF (STRUCTURAL FAILURE, SOT, SOS/SOW)
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 8 (NEW): WYCKOFF STRUCTURAL FAILURE / SOT / SOS-SOW BAR ---")

    piv_weak = [(150.0, "high", 0), (100.0, "low", 1), (151.0, "high", 2), (99.0, "low", 3), (140.0, "high", 4)]
    weak = detect_structural_failure(piv_weak)
    check("Structural weakness: rally fails to reach validated range top", weak is not None and weak["type"] == "weakness", f"got {weak}")

    piv_strong = [(100.0, "low", 0), (150.0, "high", 1), (99.0, "low", 2), (151.0, "high", 3), (110.0, "low", 4)]
    strong = detect_structural_failure(piv_strong)
    check("Structural strength: decline fails to reach validated range bottom", strong is not None and strong["type"] == "strength", f"got {strong}")

    piv_no_range = [(100.0, "low", 0), (150.0, "high", 1), (105.0, "low", 2)]
    check("No structural failure without a validated (>=2 touch) range", detect_structural_failure(piv_no_range) is None)

    piv_reached = [(150.0, "high", 0), (100.0, "low", 1), (151.0, "high", 2), (99.0, "low", 3), (149.5, "high", 4)]
    check("No structural failure when the swing actually reaches the extreme", detect_structural_failure(piv_reached) is None)

    piv_sot = [
        (100.0, "low", 0), (150.0, "high", 1), (120.0, "low", 2), (155.0, "high", 3),
        (125.0, "low", 4), (145.0, "high", 5), (115.0, "low", 6),
    ]
    sot = detect_shortening_of_thrust(piv_sot)
    check("SOT: bullish exhaustion detected on 3 shrinking up-legs", sot is not None and sot["type"] == "bullish_exhaustion", f"got {sot}")
    check("SOT: leg sizes reported in shrinking order", sot is not None and sot["leg_sizes"] == [50.0, 35.0, 20.0], f"got {sot}")
    check("SOT: too few pivots returns None, not a crash", detect_shortening_of_thrust(piv_sot[:4]) is None)

    piv_no_sot = [
        (100.0, "low", 0), (150.0, "high", 1), (120.0, "low", 2), (148.0, "high", 3),
        (118.0, "low", 4), (151.0, "high", 5), (116.0, "low", 6),
    ]
    check("SOT: non-monotonic legs correctly return None (no forced match)", detect_shortening_of_thrust(piv_no_sot) is None)

    sos_window = [{"high": 110, "low": 100, "close": 105, "volume": 1000} for _ in range(20)]
    sos_bars = sos_window + [{"high": 125, "low": 100, "close": 122, "volume": 1500}]
    sos = detect_sos_sow_bar(sos_bars)
    check("SOS bar: wide range + high volume + close in upper third", sos is not None and sos["type"] == "SOS", f"got {sos}")

    sow_bars = sos_window + [{"high": 110, "low": 85, "close": 88, "volume": 1500}]
    sow = detect_sos_sow_bar(sow_bars)
    check("SOW bar: wide range + high volume + close in lower third", sow is not None and sow["type"] == "SOW", f"got {sow}")

    mid_bars = sos_window + [{"high": 125, "low": 100, "close": 112, "volume": 1500}]
    check("No SOS/SOW when wide/high-volume bar closes in the middle third", detect_sos_sow_bar(mid_bars) is None)

    normal_bars = sos_window + [{"high": 108, "low": 102, "close": 107, "volume": 1000}]
    check("No SOS/SOW on an ordinary (not wide, not high-volume) bar", detect_sos_sow_bar(normal_bars) is None)
    check("SOS/SOW: thin history returns None, never crashes", detect_sos_sow_bar(sos_bars[:5]) is None)

    # ═══════════════════════════════════════════════
    # PHASE 9 (NEW) — CROSS-INDEX CONFIRMATION (DOW-STYLE 2-OF-3)
    # ═══════════════════════════════════════════════
    print("\n--- PHASE 9 (NEW): CROSS-INDEX CONFIRMATION + CONCENTRATION FLAG ---")

    eng = SectorEngine.__new__(SectorEngine)  # bypass __init__ (needs a real redis/settings) -- pure unit test of the new method only
    eng._sectors = {}
    eng._index_snapshot = SymbolSnapshot(symbol="INDEX")

    genuine = SectorState(sector_id="IT")
    for i, chg in enumerate([1.0, 1.5, 0.8, 1.2, 0.9]):
        genuine.constituents[f"S{i}"] = SymbolSnapshot(symbol=f"S{i}", change_pct=chg)
    genuine.positive_change_pct = 100.0
    genuine.avg_change_pct = sum([1.0, 1.5, 0.8, 1.2, 0.9]) / 5
    eng._sectors["IT"] = genuine
    eng._index_snapshot.change_pct = 0.5

    r_genuine = eng.compute_cross_confirmation("IT", "S0", "bullish")
    check("Genuine broad sector move: all 3 measures confirm", r_genuine["confirmation_count"] == 3, f"got {r_genuine}")
    check("Genuine broad move: confirmed (>=2 of 3)", r_genuine["confirmed"] is True)
    check("Genuine broad move: no concentration flag", r_genuine["concentration_flag"] is False)

    masquerade = SectorState(sector_id="DEFENCE")
    changes = [8.0, -0.2, -0.3, 0.1]
    for i, chg in enumerate(changes):
        masquerade.constituents[f"D{i}"] = SymbolSnapshot(symbol=f"D{i}", change_pct=chg)
    masquerade.positive_change_pct = 25.0
    masquerade.avg_change_pct = sum(changes) / len(changes)  # still net-positive only because of the 8% outlier
    eng._sectors["DEFENCE"] = masquerade
    eng._index_snapshot.change_pct = -0.3

    r_mask = eng.compute_cross_confirmation("DEFENCE", "D0", "bullish")
    check(
        "Single-stock masquerade: concentration flag fires (avg flips sign once top mover excluded)",
        r_mask["concentration_flag"] is True, f"got {r_mask}",
    )
    check("Single-stock masquerade: top mover share correctly dominant", r_mask["top_mover_share_pct"] > 80, f"got {r_mask}")
    check(
        "Single-stock masquerade: NOT confirmed bullish once breadth/market/concentration are all checked",
        r_mask["confirmed"] is False, f"got {r_mask}",
    )

    r_unknown = eng.compute_cross_confirmation("NOPE", "X", "bullish")
    check("Unknown sector returns a safe empty result, never crashes", r_unknown["confirmed"] is False and r_unknown["confirmation_count"] == 0, f"got {r_unknown}")

    empty_sector = SectorState(sector_id="EMPTY")
    eng._sectors["EMPTY"] = empty_sector
    r_empty = eng.compute_cross_confirmation("EMPTY", "X", "bullish")
    check("Sector with zero constituents never crashes (division-by-zero guard)", r_empty["confirmed"] is False, f"got {r_empty}")

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
