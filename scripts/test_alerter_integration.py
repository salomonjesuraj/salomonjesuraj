"""Phase 3E integration test — alerter delivery gate + engine with Redis.

Tests: delivery gate logic, cooldowns, rate limiting, burst protection,
mute controls, duplicate suppression, delivery logging, replay determinism.

Requires: Redis running on localhost:6379

Usage:
    python -X utf8 scripts/test_alerter_integration.py
"""

import asyncio
import json
import os
import sys
import time

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "alerter", "src"))

import redis.asyncio as aioredis
from alerter.config import AlerterSettings
from alerter.engine import AlerterEngine
from alerter.formatter import format_signal
from alerter.gate import DeliveryGate
from alerter.telegram import TelegramClient
from infusion_streams.constants import (
    KEY_ALERT_BURST,
    KEY_ALERT_COOLDOWN_PREFIX,
    KEY_ALERT_DELIVERED,
    KEY_ALERT_LOG,
    KEY_ALERT_MUTE_STRATEGIES,
    KEY_ALERT_MUTE_SYMBOLS,
    KEY_ALERT_RATE,
)

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


def _signal(symbol="RELIANCE", grade="A", score=85.0, signal_id=None):
    return {
        "signal_id": signal_id or f"sig-{symbol}-{time.time_ns()}",
        "symbol": symbol,
        "strategy_id": "vol_vwap_breakout",
        "signal_type": "bullish",
        "conviction_score": score,
        "conviction_grade": grade,
        "risk_reward_ratio": 2.2,
        "entry_price": 2500.0,
        "invalidation_price": 2477.50,
        "target_price": 2550.0,
        "sector_id": "NIFTY_50",
        "sector_strength": 72.0,
        "market_regime": "risk_on",
        "pre_breakout_state": "coiled",
        "created_at_us": int(time.time() * 1_000_000),
        "explanation": [
            "VWAP reclaim crossover",
            "Volume 3.5x above average",
        ],
        "conditions_met": {"vol_expansion": True, "vwap_crossover": True},
    }


async def cleanup(r):
    """Clean all test keys."""
    for key in (
        KEY_ALERT_RATE,
        KEY_ALERT_BURST,
        KEY_ALERT_LOG,
        KEY_ALERT_DELIVERED,
        KEY_ALERT_MUTE_SYMBOLS,
        KEY_ALERT_MUTE_STRATEGIES,
    ):
        await r.delete(key)
    # Clean specific cooldowns
    for sym in ("RELIANCE", "INFY", "TCS", "HDFCBANK", "TEST_MUTED"):
        await r.delete(f"{KEY_ALERT_COOLDOWN_PREFIX}{sym}")


async def run_tests():
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    await r.ping()
    print("✓ Redis connected\n")

    settings = AlerterSettings()
    await cleanup(r)

    gate = DeliveryGate(r, settings)
    telegram = TelegramClient("", settings)  # dry-run mode
    engine = AlerterEngine(
        redis=r,
        settings=settings,
        gate=gate,
        formatter_fn=format_signal,
        telegram_client=telegram,
    )

    # ═══════════════════════════════════════════════
    # 1. Delivery gate — pass case
    # ═══════════════════════════════════════════════
    print("--- DELIVERY GATE: PASS ---")
    sig = _signal(grade="A")
    result = await gate.evaluate(
        signal_id=sig["signal_id"],
        symbol=sig["symbol"],
        grade=sig["conviction_grade"],
        strategy_id=sig["strategy_id"],
    )
    check("A grade passes gate", result.passed)
    check("A grade tier = HIGH", result.priority_tier == "HIGH")

    # ═══════════════════════════════════════════════
    # 2. Delivery gate — priority check
    # ═══════════════════════════════════════════════
    print("\n--- DELIVERY GATE: PRIORITY ---")
    # B grade should be blocked (below B+)
    result_b = await gate.evaluate(
        signal_id="sig-b",
        symbol="TEST",
        grade="B",
        strategy_id="test",
    )
    check("B grade blocked", not result_b.passed)
    check("B grade reason", result_b.reason == "below_min_grade", f"reason={result_b.reason}")

    # A+ should pass
    result_ap = await gate.evaluate(
        signal_id="sig-ap",
        symbol="TEST",
        grade="A+",
        strategy_id="test",
    )
    check("A+ grade passes", result_ap.passed)
    check(
        "A+ tier = CRITICAL",
        result_ap.priority_tier == "CRITICAL",
        f"tier={result_ap.priority_tier}",
    )

    # ═══════════════════════════════════════════════
    # 3. Full delivery pipeline (dry-run)
    # ═══════════════════════════════════════════════
    print("\n--- FULL DELIVERY PIPELINE ---")
    sig1 = _signal(symbol="RELIANCE", grade="A", score=85.0)
    await engine.process_signal(sig1)
    check("Alert sent (dry-run)", engine._alerts_sent == 1, f"sent={engine._alerts_sent}")

    # Check delivery log
    log_raw = await r.lrange(KEY_ALERT_LOG, 0, -1)
    check("Delivery log has entry", len(log_raw) >= 1, f"entries={len(log_raw)}")
    if log_raw:
        entry = json.loads(log_raw[0])
        check(
            "Log entry has outcome",
            entry.get("outcome") == "delivered",
            f"outcome={entry.get('outcome')}",
        )
        check("Log entry has symbol", entry.get("symbol") == "RELIANCE")

    # ═══════════════════════════════════════════════
    # 4. Delivery cooldown
    # ═══════════════════════════════════════════════
    print("\n--- DELIVERY COOLDOWN ---")
    # Same symbol should be blocked by cooldown
    sig2 = _signal(symbol="RELIANCE", grade="A", score=90.0)
    await engine.process_signal(sig2)
    check(
        "Cooldown blocks second alert",
        engine._alerts_blocked >= 1,
        f"blocked={engine._alerts_blocked}",
    )
    check("Still only 1 sent", engine._alerts_sent == 1)

    # Verify cooldown key exists
    cooldown_exists = await r.exists(f"{KEY_ALERT_COOLDOWN_PREFIX}RELIANCE")
    check("Cooldown key in Redis", bool(cooldown_exists))
    cooldown_ttl = await r.ttl(f"{KEY_ALERT_COOLDOWN_PREFIX}RELIANCE")
    check("Cooldown TTL reasonable", 0 < cooldown_ttl <= 1800, f"ttl={cooldown_ttl}")

    # ═══════════════════════════════════════════════
    # 5. Different symbol passes cooldown
    # ═══════════════════════════════════════════════
    print("\n--- DIFFERENT SYMBOL ---")
    sig3 = _signal(symbol="INFY", grade="A+", score=95.0)
    await engine.process_signal(sig3)
    check("Different symbol delivered", engine._alerts_sent == 2, f"sent={engine._alerts_sent}")

    # ═══════════════════════════════════════════════
    # 6. Duplicate check
    # ═══════════════════════════════════════════════
    print("\n--- DUPLICATE CHECK ---")
    # Clean INFY cooldown to test duplicate separately
    await r.delete(f"{KEY_ALERT_COOLDOWN_PREFIX}INFY")
    sig3_dup = dict(sig3)  # same signal_id
    await engine.process_signal(sig3_dup)
    check("Duplicate signal_id blocked", engine._alerts_sent == 2, f"sent={engine._alerts_sent}")

    # ═══════════════════════════════════════════════
    # 7. Rate limiting
    # ═══════════════════════════════════════════════
    print("\n--- RATE LIMITING ---")
    rate_raw = await r.get(KEY_ALERT_RATE)
    check("Rate counter incremented", rate_raw is not None)
    if rate_raw:
        rate_count = int(rate_raw)
        check("Rate count = 2", rate_count == 2, f"count={rate_count}")

    burst_raw = await r.get(KEY_ALERT_BURST)
    check("Burst counter incremented", burst_raw is not None)

    # ═══════════════════════════════════════════════
    # 8. Burst limit enforcement
    # ═══════════════════════════════════════════════
    print("\n--- BURST LIMIT ---")
    # Send one more to hit burst limit (3 = limit)
    sig4 = _signal(symbol="TCS", grade="A", score=82.0)
    await engine.process_signal(sig4)
    check("Third alert delivered", engine._alerts_sent == 3, f"sent={engine._alerts_sent}")

    # Fourth should be burst-blocked (for non-CRITICAL)
    sig5 = _signal(symbol="HDFCBANK", grade="B+", score=72.0)
    await engine.process_signal(sig5)
    check("Fourth alert burst-blocked", engine._alerts_sent == 3, f"sent={engine._alerts_sent}")

    # But A+ (CRITICAL) bypasses burst
    sig6 = _signal(symbol="HDFCBANK", grade="A+", score=96.0)
    await r.delete(f"{KEY_ALERT_COOLDOWN_PREFIX}HDFCBANK")  # clear cooldown
    await engine.process_signal(sig6)
    check("A+ bypasses burst", engine._alerts_sent == 4, f"sent={engine._alerts_sent}")

    # ═══════════════════════════════════════════════
    # 9. Mute controls
    # ═══════════════════════════════════════════════
    print("\n--- MUTE CONTROLS ---")
    await r.sadd(KEY_ALERT_MUTE_SYMBOLS, "TEST_MUTED")
    sig_muted = _signal(symbol="TEST_MUTED", grade="A+", score=99.0)
    await engine.process_signal(sig_muted)
    check("Muted symbol blocked", engine._alerts_sent == 4, f"sent={engine._alerts_sent}")

    # Unmute
    await r.srem(KEY_ALERT_MUTE_SYMBOLS, "TEST_MUTED")
    is_muted = await gate.is_muted("TEST_MUTED")
    check("Symbol unmuted", not is_muted)

    # ═══════════════════════════════════════════════
    # 10. Strategy mute
    # ═══════════════════════════════════════════════
    print("\n--- STRATEGY MUTE ---")
    await r.sadd(KEY_ALERT_MUTE_STRATEGIES, "vol_vwap_breakout")
    sig_strat = _signal(symbol="TEST_STRAT", grade="A+", score=99.0)
    await engine.process_signal(sig_strat)
    check("Muted strategy blocked", engine._alerts_sent == 4)
    await r.srem(KEY_ALERT_MUTE_STRATEGIES, "vol_vwap_breakout")

    # ═══════════════════════════════════════════════
    # 11. Engine stats
    # ═══════════════════════════════════════════════
    print("\n--- ENGINE STATS ---")
    stats = engine.stats
    check("Stats: processed > 0", stats["processed"] > 0, f"processed={stats['processed']}")
    check("Stats: alerts_sent = 4", stats["alerts_sent"] == 4, f"sent={stats['alerts_sent']}")
    check(
        "Stats: alerts_blocked > 0",
        stats["alerts_blocked"] > 0,
        f"blocked={stats['alerts_blocked']}",
    )
    check(
        "Stats: by_reason has data",
        len(stats["by_reason"]) > 0,
        f"reasons={list(stats['by_reason'].keys())}",
    )

    # ═══════════════════════════════════════════════
    # 12. Delivery log capping
    # ═══════════════════════════════════════════════
    print("\n--- DELIVERY LOG ---")
    log_entries = await r.lrange(KEY_ALERT_LOG, 0, -1)
    check("Log has entries", len(log_entries) > 0, f"count={len(log_entries)}")
    check(
        "Log capped",
        len(log_entries) <= settings.delivery_log_max,
        f"max={settings.delivery_log_max}",
    )

    # ═══════════════════════════════════════════════
    # 13. Determinism
    # ═══════════════════════════════════════════════
    print("\n--- DETERMINISM ---")
    msg1 = format_signal(sig1)
    msg2 = format_signal(sig1)
    check("Format determinism", msg1 == msg2)

    # Cleanup
    await cleanup(r)
    await telegram.close()
    await r.aclose()
    return passed, failed


def main():
    print("=" * 70)
    print("INFUSION PHASE 3E — ALERTER INTEGRATION TEST")
    print(f"Redis: {REDIS_URL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    p, f = asyncio.run(run_tests())

    print("\n" + "=" * 70)
    print(f"  Total: {p + f}  |  PASS: {p}  |  FAIL: {f}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'ALERTER VALIDATED' if f == 0 else 'ALERTER NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if f == 0 else 1)


if __name__ == "__main__":
    main()
