"""Phase 3B validation — scanner strategy, scoring, and suppression logic.

Offline tests: no Redis required, no Docker required.
Tests the pure logic before integration.

Usage:
    python -X utf8 scripts/validate_3b.py
"""

import os
import sys

# Add lib and service paths
base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "scanner", "src"))

errors = []


def check(label, fn):
    try:
        fn()
        print(f"  ✓ {label}")
    except Exception as e:
        errors.append(f"{label}: {e}")
        print(f"  ✗ {label}: {e}")
        import traceback

        traceback.print_exc()


# ═══════════════════════════════════════════════════
# 1. Config
# ═══════════════════════════════════════════════════
print("\n--- CONFIG ---")


def test_config():
    from scanner.config import ScannerSettings

    s = ScannerSettings()
    assert s.signal_ttl_sec == 300
    assert s.cooldown_sec == 900
    assert s.min_conviction_score == 40.0
    assert s.vvb_min_rel_vol == 2.0
    assert s.warmup_ticks == 5


check("ScannerSettings defaults", test_config)


# ═══════════════════════════════════════════════════
# 2. State
# ═══════════════════════════════════════════════════
print("\n--- STATE ---")


def test_state():
    from scanner.state import StateManager

    mgr = StateManager()
    s = mgr.get_or_create("RELIANCE")
    assert s.symbol == "RELIANCE"
    assert s.tick_count == 0
    assert s.prev_ltp == 0.0

    features = {"ltp": 2500.0, "vwap": 2490.0, "rsi_14": 55.0}
    s.update_from_features(features)
    assert s.prev_ltp == 2500.0
    assert s.prev_vwap == 2490.0
    assert s.tick_count == 1

    # Same reference returned
    s2 = mgr.get_or_create("RELIANCE")
    assert s2.tick_count == 1
    assert mgr.symbol_count == 1


check("StateManager and SymbolState", test_state)


# ═══════════════════════════════════════════════════
# 3. Strategy: vol_vwap_breakout
# ═══════════════════════════════════════════════════
print("\n--- STRATEGY: vol_vwap_breakout ---")


def _make_features(**overrides):
    """Build a feature dict with sensible defaults."""
    base = {
        "symbol": "RELIANCE",
        "timestamp_us": 1700000000000000,
        "ltp": 2500.0,
        "vwap": 2490.0,
        "rel_vol_20d": 3.0,
        "rsi_14": 58.0,
        "ema_9": 2495.0,
        "ema_20": 2480.0,
        "ema_50": 2460.0,
        "atr_14": 25.0,
        "bb_width": 0.025,
        "bb_upper": 2510.0,
        "bb_lower": 2470.0,
        "order_imbalance": 0.2,
        "spread_bps": 8.0,
        "change_pct": 1.5,
        "prev_close": 2462.0,
    }
    base.update(overrides)
    return base


def _make_state(symbol="RELIANCE", prev_ltp=2488.0, prev_vwap=2490.0):
    from scanner.state import ScannerSymbolState

    s = ScannerSymbolState(symbol=symbol)
    s.prev_ltp = prev_ltp
    s.prev_vwap = prev_vwap
    s.tick_count = 10
    return s


def test_strategy_all_conditions_met():
    """All 7 conditions met → should produce a candidate."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features()
    state = _make_state(prev_ltp=2488.0, prev_vwap=2490.0)
    # prev_ltp=2488 <= prev_vwap=2490, ltp=2500 > vwap=2490 → crossover ✓

    candidate = strategy.evaluate(features, state)
    assert candidate is not None, "Expected a signal candidate"
    assert candidate.strategy_id == "vol_vwap_breakout"
    assert candidate.signal_type == "bullish"
    assert all(candidate.conditions_met.values()), (
        f"Not all conditions met: {candidate.conditions_met}"
    )
    assert len(candidate.explanation) == 7
    assert candidate.entry_price == 2500.0
    assert candidate.invalidation_price == 2490.0 - 25.0 * 0.5  # vwap - 0.5*atr
    assert candidate.target_price == 2500.0 + 25.0 * 2.0  # entry + 2*atr


def test_strategy_no_vwap_crossover():
    """Price already above VWAP on previous tick → no crossover → no signal."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features()
    state = _make_state(prev_ltp=2495.0, prev_vwap=2490.0)
    # prev_ltp=2495 > prev_vwap=2490 → was already above → no crossover

    candidate = strategy.evaluate(features, state)
    assert candidate is None, "Should not trigger — no VWAP crossover"


def test_strategy_low_volume():
    """Volume below threshold → no signal."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features(rel_vol_20d=1.2)
    state = _make_state()

    candidate = strategy.evaluate(features, state)
    assert candidate is None, "Should not trigger — low volume"


def test_strategy_rsi_overbought():
    """RSI above max → no signal."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features(rsi_14=80.0)
    state = _make_state()

    candidate = strategy.evaluate(features, state)
    assert candidate is None, "Should not trigger — RSI overbought"


def test_strategy_high_spread():
    """Wide spread → no signal."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features(spread_bps=60.0)
    state = _make_state()

    candidate = strategy.evaluate(features, state)
    assert candidate is None, "Should not trigger — high spread"


def test_strategy_warmup():
    """State with 0 prev_ltp → no signal (guard)."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features()
    state = _make_state(prev_ltp=0.0)

    candidate = strategy.evaluate(features, state)
    assert candidate is None, "Should not trigger — no prior price"


def test_strategy_determinism():
    """Same inputs → same output."""
    from scanner.config import ScannerSettings
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    strategy = VolVwapBreakout(ScannerSettings())
    features = _make_features()
    state1 = _make_state()
    state2 = _make_state()

    c1 = strategy.evaluate(features, state1)
    c2 = strategy.evaluate(features, state2)

    assert c1 is not None and c2 is not None
    assert c1.conditions_met == c2.conditions_met
    assert c1.explanation == c2.explanation
    assert c1.entry_price == c2.entry_price
    assert c1.invalidation_price == c2.invalidation_price


check("All conditions met → candidate", test_strategy_all_conditions_met)
check("No VWAP crossover → None", test_strategy_no_vwap_crossover)
check("Low volume → None", test_strategy_low_volume)
check("RSI overbought → None", test_strategy_rsi_overbought)
check("High spread → None", test_strategy_high_spread)
check("No prior price → None", test_strategy_warmup)
check("Determinism: same inputs → same output", test_strategy_determinism)


# ═══════════════════════════════════════════════════
# 4. Scoring
# ═══════════════════════════════════════════════════
print("\n--- SCORING ---")


def test_scoring_high():
    from scanner.scoring import compute_conviction, grade_conviction

    features = _make_features()
    score, subs = compute_conviction(features)
    assert score > 0
    assert "volume" in subs
    assert "vwap" in subs
    assert "rsi" in subs
    grade = grade_conviction(score)
    assert grade in ("A+", "A", "B", "C", "D")


def test_scoring_grading():
    from scanner.scoring import grade_conviction

    assert grade_conviction(90.0) == "A+"
    assert grade_conviction(85.0) == "A+"
    assert grade_conviction(75.0) == "A"
    assert grade_conviction(60.0) == "B"
    assert grade_conviction(45.0) == "C"
    assert grade_conviction(30.0) == "D"


def test_scoring_determinism():
    from scanner.scoring import compute_conviction

    features = _make_features()
    s1, sub1 = compute_conviction(features)
    s2, sub2 = compute_conviction(features)
    assert s1 == s2
    assert sub1 == sub2


def test_risk_reward():
    from scanner.scoring import compute_risk_reward

    rr = compute_risk_reward(entry=2500.0, invalidation=2475.0, target=2550.0)
    assert rr == 2.0
    rr_zero = compute_risk_reward(entry=2500.0, invalidation=2500.0, target=2550.0)
    assert rr_zero == 0.0


check("Scoring with valid features", test_scoring_high)
check("Grade mapping correctness", test_scoring_grading)
check("Scoring determinism", test_scoring_determinism)
check("Risk/reward computation", test_risk_reward)


# ═══════════════════════════════════════════════════
# 5. Strategy registry
# ═══════════════════════════════════════════════════
print("\n--- STRATEGY REGISTRY ---")


def test_registry():
    from scanner.config import ScannerSettings
    from scanner.strategies import get_strategies, get_strategy, register_strategy
    from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

    register_strategy(VolVwapBreakout(ScannerSettings()))
    strategies = get_strategies()
    assert len(strategies) >= 1
    s = get_strategy("vol_vwap_breakout")
    assert s is not None
    assert s.strategy_id == "vol_vwap_breakout"


check("Strategy registration and lookup", test_registry)


# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
print("\n" + "=" * 60)
total = len(errors)
if total == 0:
    print("ALL CHECKS PASSED — Phase 3B offline validation complete")
else:
    print(f"FAILURES: {total}")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
