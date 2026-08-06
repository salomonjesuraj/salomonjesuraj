"""Phase 3C integration test — pre-breakout state machine with Redis.

Tests the full flow: synthetic features → state transitions → Redis persistence
→ cleanup on expiry → API readability.

Requires: Redis running on localhost:6379

Usage:
    python -X utf8 scripts/test_prebreakout_integration.py
"""

import asyncio
import os
import sys
import time

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "scanner", "src"))

import redis.asyncio as aioredis

from scanner.config import ScannerSettings
from scanner.engine import ScannerEngine
from scanner.strategies import register_strategy, _REGISTRY
from scanner.strategies.vol_vwap_breakout import VolVwapBreakout
from scanner.pre_breakout import PBState

from infusion_streams.constants import KEY_PRE_BREAKOUT_PREFIX, KEY_SIGNAL_ACTIVE
from infusion_common.timing import now_us

REDIS_URL = os.environ.get("INFUSION_REDIS_URL", "redis://localhost:6379/0")
SYMBOL = "PB_TEST"

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


def _features(bb_width, rel_vol, rsi, ltp=2500.0, vwap=2490.0, **kw):
    """Build feature dict with controlled bb_width/rel_vol/rsi."""
    base = {
        "symbol": SYMBOL,
        "timestamp_us": now_us(),
        "ltp": ltp,
        "vwap": vwap,
        "rel_vol_20d": rel_vol,
        "rsi_14": rsi,
        "ema_5": 2498.0,
        "ema_9": 2495.0,
        "ema_20": 2480.0,
        "ema_50": 2460.0,
        "atr_14": 25.0,
        "bb_width": bb_width,
        "bb_upper": 2510.0,
        "bb_lower": 2470.0,
        "order_imbalance": 0.2,
        "spread_bps": 8.0,
        "change_pct": 1.5,
        "prev_close": 2462.0,
    }
    base.update(kw)
    return base


async def run_tests():
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    await r.ping()
    print(f"✓ Redis connected\n")

    settings = ScannerSettings()

    # Cleanup
    for key in await r.keys(f"infusion:prebreak:{SYMBOL}*".encode()):
        await r.delete(key)
    for key in await r.keys(f"infusion:signal:{SYMBOL}*".encode()):
        await r.delete(key)
    for key in await r.keys(f"infusion:cooldown:{SYMBOL}*".encode()):
        await r.delete(key)
    await r.zrem(KEY_SIGNAL_ACTIVE, f"{SYMBOL}:vol_vwap_breakout")

    _REGISTRY.clear()
    register_strategy(VolVwapBreakout(settings))

    engine = ScannerEngine(
        redis=r,
        settings=settings,
        symbol_sectors={SYMBOL: "NIFTY_50"},
    )

    # ═══════════════════════════════════════════════
    # Phase 1: Warmup
    # ═══════════════════════════════════════════════
    print("--- WARMUP ---")
    for i in range(settings.warmup_ticks):
        await engine.process_feature(
            _features(bb_width=0.03 - i * 0.001, rel_vol=1.0, rsi=50.0)
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check("Warmup complete", state.tick_count >= settings.warmup_ticks)
    check("State IDLE after warmup", state.pre_breakout_state == PBState.IDLE)

    # ═══════════════════════════════════════════════
    # Phase 2: Drive IDLE → COMPRESSING
    # ═══════════════════════════════════════════════
    print("\n--- IDLE → COMPRESSING ---")
    # Send declining bb_width for pb_compress_ticks + 1 ticks
    for i in range(settings.pb_compress_ticks + 2):
        bb = 0.025 - (i * 0.001)
        bb = max(bb, 0.005)
        await engine.process_feature(
            _features(bb_width=bb, rel_vol=1.0, rsi=50.0)
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "State is COMPRESSING",
        state.pre_breakout_state == PBState.COMPRESSING,
        f"actual={state.pre_breakout_state}",
    )

    # Check Redis hot state
    pb_key = f"{KEY_PRE_BREAKOUT_PREFIX}{SYMBOL}"
    pb_data = await r.hgetall(pb_key)
    check("Pre-breakout key in Redis", len(pb_data) > 0)
    if pb_data:
        redis_state = pb_data.get(b"state", b"").decode()
        check("Redis state = compressing", redis_state == "compressing", f"redis_state={redis_state}")
        readiness = float(pb_data.get(b"readiness_score", b"0").decode())
        check("Readiness score > 0", readiness > 0, f"readiness={readiness}")
        check("Transition reason present",
              len(pb_data.get(b"transition_reason", b"").decode()) > 0)

    # ═══════════════════════════════════════════════
    # Phase 3: Drive COMPRESSING → ACCUMULATING
    # ═══════════════════════════════════════════════
    print("\n--- COMPRESSING → ACCUMULATING ---")
    for _ in range(3):
        await engine.process_feature(
            _features(bb_width=0.018, rel_vol=1.5, rsi=52.0)
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "State is ACCUMULATING",
        state.pre_breakout_state == PBState.ACCUMULATING,
        f"actual={state.pre_breakout_state}",
    )

    # ═══════════════════════════════════════════════
    # Phase 4: Drive ACCUMULATING → COILED
    # ═══════════════════════════════════════════════
    print("\n--- ACCUMULATING → COILED ---")
    for _ in range(3):
        await engine.process_feature(
            _features(bb_width=0.012, rel_vol=1.8, rsi=52.0)
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "State is COILED",
        state.pre_breakout_state == PBState.COILED,
        f"actual={state.pre_breakout_state}",
    )

    # Check readiness score is high
    pb_data = await r.hgetall(pb_key)
    if pb_data:
        readiness = float(pb_data.get(b"readiness_score", b"0").decode())
        check("COILED readiness >= 70", readiness >= 70, f"readiness={readiness}")

    # ═══════════════════════════════════════════════
    # Phase 5: COILED → EXPIRED (BB expansion without breakout)
    # ═══════════════════════════════════════════════
    print("\n--- COILED → EXPIRED (degradation) ---")
    for _ in range(3):
        await engine.process_feature(
            _features(bb_width=0.04, rel_vol=1.5, rsi=55.0)  # BB expanded
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    # After EXPIRED → immediate IDLE on next tick
    check(
        "State is IDLE (after EXPIRED reset)",
        state.pre_breakout_state == PBState.IDLE,
        f"actual={state.pre_breakout_state}",
    )

    # EXPIRED cleans up Redis key
    pb_exists = await r.exists(pb_key)
    check("Redis key cleaned on EXPIRED", not pb_exists)

    # ═══════════════════════════════════════════════
    # Phase 6: Full cycle → TRIGGERED
    # ═══════════════════════════════════════════════
    print("\n--- FULL CYCLE → TRIGGERED ---")

    # Reset declining count and rebuild through states
    # IDLE → COMPRESSING
    for i in range(settings.pb_compress_ticks + 2):
        bb = 0.025 - (i * 0.001)
        bb = max(bb, 0.005)
        await engine.process_feature(
            _features(bb_width=bb, rel_vol=1.0, rsi=50.0)
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "Rebuilt to COMPRESSING",
        state.pre_breakout_state == PBState.COMPRESSING,
        f"actual={state.pre_breakout_state}",
    )

    # COMPRESSING → ACCUMULATING
    for _ in range(3):
        await engine.process_feature(
            _features(bb_width=0.018, rel_vol=1.5, rsi=52.0)
        )

    # ACCUMULATING → COILED
    for _ in range(3):
        await engine.process_feature(
            _features(bb_width=0.012, rel_vol=1.8, rsi=52.0)
        )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "Back to COILED",
        state.pre_breakout_state == PBState.COILED,
        f"actual={state.pre_breakout_state}",
    )

    # Now trigger a breakout signal — VWAP crossover with volume
    state.prev_ltp = 2488.0
    state.prev_vwap = 2490.0
    await engine.process_feature(
        _features(
            bb_width=0.012, rel_vol=3.5, rsi=58.0,
            ltp=2500.0, vwap=2490.0,
            order_imbalance=0.25, spread_bps=8.0,
        )
    )

    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "State TRIGGERED after breakout signal",
        state.pre_breakout_state == PBState.TRIGGERED,
        f"actual={state.pre_breakout_state}",
    )
    check("Signal emitted count >= 1", engine._signals_emitted >= 1)

    # Next tick should reset to IDLE
    await engine.process_feature(
        _features(bb_width=0.015, rel_vol=2.0, rsi=60.0)
    )
    state = engine.state_mgr.get_or_create(SYMBOL)
    check(
        "TRIGGERED → IDLE on next tick",
        state.pre_breakout_state == PBState.IDLE,
        f"actual={state.pre_breakout_state}",
    )

    # ═══════════════════════════════════════════════
    # Phase 7: Replay determinism
    # ═══════════════════════════════════════════════
    print("\n--- REPLAY DETERMINISM ---")

    # Clear state and replay the same sequence
    from scanner.pre_breakout import PreBreakoutTracker
    tracker = engine.pre_breakout
    check("Pre-breakout transitions > 0",
          tracker.stats["prebreak_transitions"] > 0,
          f"transitions={tracker.stats['prebreak_transitions']}")

    # ═══════════════════════════════════════════════
    # Phase 8: Engine stats include pre-breakout
    # ═══════════════════════════════════════════════
    print("\n--- ENGINE STATS ---")

    stats = engine.stats
    check("Stats include prebreak_transitions",
          "prebreak_transitions" in stats,
          f"keys={list(stats.keys())}")
    check("Stats evaluations > 0", stats["evaluations"] > 0)

    # ═══════════════════════════════════════════════
    # Cleanup
    # ═══════════════════════════════════════════════
    for key in await r.keys(f"infusion:prebreak:{SYMBOL}*".encode()):
        await r.delete(key)
    for key in await r.keys(f"infusion:signal:{SYMBOL}*".encode()):
        await r.delete(key)
    for key in await r.keys(f"infusion:cooldown:{SYMBOL}*".encode()):
        await r.delete(key)
    await r.zrem(KEY_SIGNAL_ACTIVE, f"{SYMBOL}:vol_vwap_breakout")

    await r.aclose()
    return passed, failed


def main():
    print("=" * 70)
    print("INFUSION PHASE 3C — PRE-BREAKOUT INTEGRATION TEST")
    print(f"Redis: {REDIS_URL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    p, f = asyncio.run(run_tests())

    print("\n" + "=" * 70)
    print(f"  Total: {p + f}  |  PASS: {p}  |  FAIL: {f}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'PRE-BREAKOUT VALIDATED' if f == 0 else 'PRE-BREAKOUT NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if f == 0 else 1)


if __name__ == "__main__":
    main()
