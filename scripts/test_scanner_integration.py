"""Phase 3B integration test — scanner signal pipeline.

Tests the full flow: inject synthetic features → scanner evaluates →
signals appear on Redis streams and hot state.

Requires: Redis running on localhost:6379

Usage:
    python -X utf8 scripts/test_scanner_integration.py
"""

import asyncio
import os
import sys
import time
import uuid

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "scanner", "src"))

import redis.asyncio as aioredis

from scanner.config import ScannerSettings
from scanner.engine import ScannerEngine
from scanner.state import StateManager
from scanner.strategies import register_strategy, _REGISTRY
from scanner.strategies.vol_vwap_breakout import VolVwapBreakout

from infusion_streams.constants import (
    STREAM_SCAN_SIGNALS,
    STREAM_SCAN_SUPPRESSED,
    KEY_SIGNAL_PREFIX,
    KEY_SIGNAL_ACTIVE,
    KEY_COOLDOWN_PREFIX,
)
from infusion_streams.codec import decode_event
from infusion_common.timing import now_us

REDIS_URL = os.environ.get("INFUSION_REDIS_URL", "redis://localhost:6379/0")

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


def _breakout_features(symbol, timestamp_us):
    """Features that should trigger vol_vwap_breakout."""
    return {
        "symbol": symbol,
        "timestamp_us": timestamp_us,
        "ltp": 2500.0,
        "vwap": 2490.0,
        "rel_vol_20d": 3.5,
        "rsi_14": 58.0,
        "ema_5": 2498.0,
        "ema_9": 2495.0,
        "ema_20": 2480.0,
        "ema_50": 2460.0,
        "atr_14": 25.0,
        "bb_width": 0.025,
        "bb_upper": 2510.0,
        "bb_lower": 2470.0,
        "order_imbalance": 0.25,
        "spread_bps": 8.0,
        "change_pct": 1.5,
        "prev_close": 2462.0,
        "gap_pct": 0.0,
        "day_high": 2505.0,
        "day_low": 2475.0,
        "macd": 2.0,
        "macd_signal": 1.5,
        "macd_hist": 0.5,
        "stochastic_k": 65.0,
        "stochastic_d": 60.0,
        "cci_20": 80.0,
        "obv": 1000000.0,
        "volume_sma_20": 500000,
    }


def _no_trigger_features(symbol, timestamp_us):
    """Features that should NOT trigger (volume too low)."""
    f = _breakout_features(symbol, timestamp_us)
    f["rel_vol_20d"] = 0.8  # below threshold
    return f


async def run_tests():
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    await r.ping()
    print(f"✓ Redis connected\n")

    settings = ScannerSettings()

    # Clean up any prior test state
    for key in await r.keys(b"infusion:signal:TEST_*"):
        await r.delete(key)
    for key in await r.keys(b"infusion:cooldown:TEST_*"):
        await r.delete(key)
    await r.zrem(KEY_SIGNAL_ACTIVE, "TEST_RELIANCE:vol_vwap_breakout")
    await r.zrem(KEY_SIGNAL_ACTIVE, "TEST_INFY:vol_vwap_breakout")

    # Note start positions on streams
    sig_before = await r.xlen(STREAM_SCAN_SIGNALS)
    sup_before = await r.xlen(STREAM_SCAN_SUPPRESSED)

    # Clear and re-register strategies
    _REGISTRY.clear()
    register_strategy(VolVwapBreakout(settings))

    # Create engine with test symbol sectors
    engine = ScannerEngine(
        redis=r,
        settings=settings,
        symbol_sectors={"TEST_RELIANCE": "NIFTY_50", "TEST_INFY": "NIFTY_IT"},
    )

    # Seed sector data so sector_weak suppression doesn't block test signals
    from infusion_streams.constants import KEY_SECTOR_PREFIX
    for sec in ("NIFTY_50", "NIFTY_IT"):
        await r.hset(
            f"{KEY_SECTOR_PREFIX}{sec}",
            mapping={
                "sector_id": sec,
                "strength_score": "65.0",
                "breadth_score": "70.0",
                "rank": "1",
                "trend": "stable",
            },
        )
        await r.expire(f"{KEY_SECTOR_PREFIX}{sec}", 600)

    # ═══════════════════════════════════════════════
    # TEST 1: Warmup — no signals during warmup period
    # ═══════════════════════════════════════════════
    print("--- WARMUP ---")

    for i in range(settings.warmup_ticks):
        features = _breakout_features("TEST_RELIANCE", now_us())
        await engine.process_feature(features)

    sig_after_warmup = await r.xlen(STREAM_SCAN_SIGNALS)
    check(
        "No signals during warmup",
        sig_after_warmup == sig_before,
        f"signals_before={sig_before}, after_warmup={sig_after_warmup}",
    )

    # ═══════════════════════════════════════════════
    # TEST 2: Signal trigger — VWAP crossover
    # ═══════════════════════════════════════════════
    print("\n--- SIGNAL TRIGGER ---")

    # Set prev state: price was below VWAP
    state = engine.state_mgr.get_or_create("TEST_RELIANCE")
    state.prev_ltp = 2488.0   # below VWAP
    state.prev_vwap = 2490.0

    # Now send a feature where ltp > vwap → crossover
    trigger_features = _breakout_features("TEST_RELIANCE", now_us())
    await engine.process_feature(trigger_features)

    sig_after_trigger = await r.xlen(STREAM_SCAN_SIGNALS)
    check(
        "Signal emitted on crossover",
        sig_after_trigger > sig_before,
        f"signals_before={sig_before}, after_trigger={sig_after_trigger}",
    )

    # Verify hot state
    hot_key = f"{KEY_SIGNAL_PREFIX}TEST_RELIANCE"
    hot_data = await r.hgetall(hot_key)
    check(
        "Signal hot state written",
        len(hot_data) > 0,
        f"fields={len(hot_data)}",
    )

    if hot_data:
        grade = hot_data.get(b"conviction_grade", b"").decode()
        score_raw = hot_data.get(b"conviction_score", b"0").decode()
        lifecycle = hot_data.get(b"lifecycle", b"").decode()
        check("Hot state has conviction_grade", grade in ("A+", "A", "B", "C", "D"), f"grade={grade}")
        check("Hot state conviction_score > 0", float(score_raw) > 0, f"score={score_raw}")
        check("Hot state lifecycle = active", lifecycle == "active", f"lifecycle={lifecycle}")

    # Verify active ZSET
    active_score = await r.zscore(KEY_SIGNAL_ACTIVE, "TEST_RELIANCE:vol_vwap_breakout")
    check(
        "Active ZSET entry exists",
        active_score is not None,
        f"score={active_score}",
    )

    # Verify cooldown set
    cooldown_key = f"{KEY_COOLDOWN_PREFIX}TEST_RELIANCE:vol_vwap_breakout"
    cooldown_exists = await r.exists(cooldown_key)
    check("Cooldown set after signal", bool(cooldown_exists), f"key={cooldown_key}")

    cooldown_ttl = await r.ttl(cooldown_key)
    check("Cooldown TTL reasonable", 0 < cooldown_ttl <= settings.cooldown_sec, f"ttl={cooldown_ttl}s")

    # ═══════════════════════════════════════════════
    # TEST 3: Duplicate suppression — same symbol+strategy
    # ═══════════════════════════════════════════════
    print("\n--- DUPLICATE SUPPRESSION ---")

    # Reset crossover state to trigger again
    state.prev_ltp = 2488.0
    state.prev_vwap = 2490.0

    trigger_features2 = _breakout_features("TEST_RELIANCE", now_us())
    await engine.process_feature(trigger_features2)

    # Should be suppressed (either duplicate or cooldown)
    sup_after = await r.xlen(STREAM_SCAN_SUPPRESSED)
    check(
        "Duplicate/cooldown suppressed",
        sup_after > sup_before,
        f"suppressed_before={sup_before}, after={sup_after}",
    )

    check(
        "Engine suppression counter > 0",
        engine._signals_suppressed > 0,
        f"suppressed={engine._signals_suppressed}",
    )

    # ═══════════════════════════════════════════════
    # TEST 4: No-trigger — conditions not met
    # ═══════════════════════════════════════════════
    print("\n--- NO TRIGGER ---")

    # Warmup INFY
    for i in range(settings.warmup_ticks + 1):
        features = _no_trigger_features("TEST_INFY", now_us())
        await engine.process_feature(features)

    # Set crossover state
    state_infy = engine.state_mgr.get_or_create("TEST_INFY")
    state_infy.prev_ltp = 1488.0
    state_infy.prev_vwap = 1490.0

    features_no_vol = _no_trigger_features("TEST_INFY", now_us())
    features_no_vol["ltp"] = 1500.0
    features_no_vol["vwap"] = 1490.0
    sig_before_notrigger = await r.xlen(STREAM_SCAN_SIGNALS)
    await engine.process_feature(features_no_vol)
    sig_after_notrigger = await r.xlen(STREAM_SCAN_SIGNALS)

    check(
        "Low volume → no signal",
        sig_after_notrigger == sig_before_notrigger,
        f"before={sig_before_notrigger}, after={sig_after_notrigger}",
    )

    # ═══════════════════════════════════════════════
    # TEST 5: Signal stream content validation
    # ═══════════════════════════════════════════════
    print("\n--- SIGNAL STREAM VALIDATION ---")

    # Read latest signal from stream
    msgs = await r.xrevrange(STREAM_SCAN_SIGNALS, count=5)
    found_test_signal = False
    for msg_id, fields in msgs:
        raw_data = fields.get(b"data")
        if raw_data:
            et, ver, ts, rx, payload = decode_event(raw_data)
            if payload.get("symbol") == "TEST_RELIANCE":
                found_test_signal = True
                check("Stream signal has signal_id", "signal_id" in payload)
                check("Stream signal has strategy_id", payload.get("strategy_id") == "vol_vwap_breakout")
                check("Stream signal has conviction_score", payload.get("conviction_score", 0) > 0)
                check("Stream signal has explanation", len(payload.get("explanation", [])) > 0,
                      f"explanations={len(payload.get('explanation', []))}")
                check("Stream signal has conditions_met", len(payload.get("conditions_met", {})) == 7,
                      f"conditions={len(payload.get('conditions_met', {}))}")
                check("Stream signal has entry_price", payload.get("entry_price", 0) > 0)
                check("Stream signal has invalidation_price", payload.get("invalidation_price", 0) > 0)
                check("Stream signal not suppressed", payload.get("suppressed") is False)
                check("Stream signal version = 2", ver == 2, f"version={ver}")
                break

    check("Test signal found in stream", found_test_signal)

    # ═══════════════════════════════════════════════
    # TEST 6: Suppressed stream content validation
    # ═══════════════════════════════════════════════
    print("\n--- SUPPRESSED STREAM VALIDATION ---")

    msgs_sup = await r.xrevrange(STREAM_SCAN_SUPPRESSED, count=5)
    found_suppressed = False
    for msg_id, fields in msgs_sup:
        raw_data = fields.get(b"data")
        if raw_data:
            et, ver, ts, rx, payload = decode_event(raw_data)
            if payload.get("symbol") == "TEST_RELIANCE":
                found_suppressed = True
                check("Suppressed signal has suppression_reason",
                      payload.get("suppression_reason") in ("duplicate_active", "cooldown_active"))
                check("Suppressed signal suppressed=True", payload.get("suppressed") is True)
                check("Suppressed signal has signal_id", "signal_id" in payload)
                break

    check("Suppressed signal found in stream", found_suppressed)

    # ═══════════════════════════════════════════════
    # TEST 7: Determinism — replay produces same score
    # ═══════════════════════════════════════════════
    print("\n--- DETERMINISM ---")

    from scanner.scoring import compute_conviction
    features_snap = _breakout_features("TEST_RELIANCE", now_us())
    score1, subs1 = compute_conviction(features_snap)
    score2, subs2 = compute_conviction(features_snap)
    check("Scoring determinism", score1 == score2, f"score1={score1}, score2={score2}")
    check("Sub-scores determinism", subs1 == subs2)

    # ═══════════════════════════════════════════════
    # TEST 8: Engine stats
    # ═══════════════════════════════════════════════
    print("\n--- ENGINE STATS ---")

    stats = engine.stats
    check("Evaluations > 0", stats["evaluations"] > 0, f"evaluations={stats['evaluations']}")
    check("Signals emitted >= 1", stats["signals_emitted"] >= 1, f"emitted={stats['signals_emitted']}")
    check("Signals suppressed >= 1", stats["signals_suppressed"] >= 1, f"suppressed={stats['signals_suppressed']}")
    check("Symbols tracked >= 1", stats["symbols_tracked"] >= 1, f"tracked={stats['symbols_tracked']}")

    # ═══════════════════════════════════════════════
    # Cleanup test keys
    # ═══════════════════════════════════════════════
    for key in await r.keys(b"infusion:signal:TEST_*"):
        await r.delete(key)
    for key in await r.keys(b"infusion:cooldown:TEST_*"):
        await r.delete(key)
    await r.zrem(KEY_SIGNAL_ACTIVE, "TEST_RELIANCE:vol_vwap_breakout")
    await r.zrem(KEY_SIGNAL_ACTIVE, "TEST_INFY:vol_vwap_breakout")

    await r.aclose()
    return passed, failed


def main():
    print("=" * 70)
    print("INFUSION PHASE 3B — SCANNER INTEGRATION TEST")
    print(f"Redis: {REDIS_URL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    p, f = asyncio.run(run_tests())

    print("\n" + "=" * 70)
    print(f"  Total: {p + f}  |  PASS: {p}  |  FAIL: {f}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'SCANNER VALIDATED' if f == 0 else 'SCANNER NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if f == 0 else 1)


if __name__ == "__main__":
    main()
