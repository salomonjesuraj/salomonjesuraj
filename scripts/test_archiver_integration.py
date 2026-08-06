"""Phase 4A integration test — end-to-end signal archival + outcome tracking.

Tests:
  1. Publish synthetic signal to Redis stream
  2. Archiver backfill picks it up and writes to Postgres
  3. Outcome tracker updates outcomes based on Redis LTP
  4. Verify Postgres data matches expectations

Requires: Redis + Postgres running on localhost

Usage:
    python -X utf8 scripts/test_archiver_integration.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))
sys.path.insert(0, os.path.join(base, "services", "archiver", "src"))

import asyncpg
import redis.asyncio as aioredis

from archiver.config import ArchiverSettings
from archiver.writer import SignalWriter, _classify_session
from archiver.tracker import OutcomeTracker

from infusion_streams.constants import (
    STREAM_SCAN_SIGNALS,
    STREAM_SCAN_SUPPRESSED,
    KEY_TICK_PREFIX,
    KEY_ARCHIVER_CHECKPOINT,
)
from infusion_streams.codec import encode_event
from infusion_models.events import EventType
from infusion_common.timing import now_us

REDIS_URL = os.environ.get("INFUSION_REDIS_URL", "redis://localhost:6379/0")
DB_URL = os.environ.get(
    "INFUSION_DATABASE_URL",
    "postgresql://infusion:changeme@localhost:5432/infusion",
)

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


def _make_signal(symbol, grade="A", score=85.0, suppressed=False):
    """Create a synthetic ScanSignalV2 payload."""
    return {
        "signal_id": str(uuid.uuid4()),
        "symbol": symbol,
        "strategy_id": "vol_vwap_breakout",
        "signal_type": "bullish",
        "lifecycle": "suppressed" if suppressed else "active",
        "created_at_us": now_us(),
        "confirmed_at_us": now_us(),
        "ttl_sec": 300,
        "conviction_score": score,
        "conviction_grade": grade,
        "sub_scores": {"volume": 25, "vwap": 20, "rsi": 15, "structure": 12},
        "price_at_signal": 2500.0,
        "entry_price": 2500.0,
        "invalidation_price": 2475.0,
        "target_price": 2550.0,
        "risk_reward_ratio": 2.0,
        "features_snapshot": {"ltp": 2500.0, "vwap": 2490.0, "rel_vol_20d": 3.5},
        "sector_id": "NIFTY_50",
        "sector_strength": 65.0,
        "market_regime": "risk_on",
        "pre_breakout_state": "coiled",
        "tier": 1,
        "suppressed": suppressed,
        "suppression_reason": "cooldown_active" if suppressed else "",
        "explanation": ["Volume expanded 3.5x", "VWAP reclaimed"],
        "conditions_met": {"vol_expansion": True, "vwap_reclaim": True},
    }


async def run_tests():
    r = aioredis.from_url(REDIS_URL, decode_responses=False)
    await r.ping()
    print("✓ Redis connected")

    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=5)
    print("✓ Postgres connected\n")

    settings = ArchiverSettings()
    settings.write_batch_size = 5
    settings.write_flush_sec = 1.0

    # ═══════════════════════════════════════════════
    # CLEANUP prior test data
    # ═══════════════════════════════════════════════
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM signals WHERE symbol LIKE 'ARCHI_TEST_%'")

    # Clear backfill checkpoint for test
    await r.hdel(KEY_ARCHIVER_CHECKPOINT, STREAM_SCAN_SIGNALS)
    await r.hdel(KEY_ARCHIVER_CHECKPOINT, STREAM_SCAN_SUPPRESSED)

    # ═══════════════════════════════════════════════
    # TEST 1: Writer batch insert
    # ═══════════════════════════════════════════════
    print("--- WRITER BATCH INSERT ---")

    writer = SignalWriter(pool, settings)

    signal1 = _make_signal("ARCHI_TEST_1", "A+", 95.0)
    signal2 = _make_signal("ARCHI_TEST_2", "A", 82.0)
    signal3 = _make_signal("ARCHI_TEST_3", "B+", 70.0, suppressed=True)

    writer.add(signal1)
    writer.add(signal2)
    writer.add(signal3)
    written = await writer.flush()

    check("Writer flushed 3 signals", written == 3, f"written={written}")
    check("Writer total_written = 3", writer.stats["total_written"] == 3)
    check("Writer buffer empty after flush", writer.stats["buffer_size"] == 0)

    # Verify in Postgres
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT symbol, conviction_grade, suppressed, session_hour "
            "FROM signals WHERE symbol LIKE 'ARCHI_TEST_%' ORDER BY symbol"
        )

    check("3 signals in Postgres", len(rows) == 3, f"rows={len(rows)}")
    if len(rows) >= 3:
        check("Signal 1 grade = A+", rows[0]["conviction_grade"] == "A+")
        check("Signal 2 grade = A", rows[1]["conviction_grade"] == "A")
        check("Signal 3 suppressed", rows[2]["suppressed"] is True)
        check("Session hour classified", rows[0]["session_hour"] is not None,
              f"session={rows[0]['session_hour']}")

    # ═══════════════════════════════════════════════
    # TEST 2: Idempotent UPSERT
    # ═══════════════════════════════════════════════
    print("\n--- IDEMPOTENT UPSERT ---")

    # Re-insert same signal — should not duplicate
    writer.add(signal1)
    written2 = await writer.flush()
    check("Replay insert succeeds", written2 == 1, f"written={written2}")

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE signal_id = $1::uuid",
            signal1["signal_id"],
        )
    check("No duplicate — still 1 row", count == 1, f"count={count}")

    # ═══════════════════════════════════════════════
    # TEST 3: Backfill from stream
    # ═══════════════════════════════════════════════
    print("\n--- BACKFILL FROM STREAM ---")

    # Publish a signal to the stream
    bf_signal = _make_signal("ARCHI_TEST_BF", "A", 88.0)
    encoded = encode_event(
        EventType.SCAN_SIGNAL, bf_signal, bf_signal["created_at_us"]
    )
    await r.xadd(STREAM_SCAN_SIGNALS, {"data": encoded})

    # Backfill
    from archiver.main import _backfill
    bf_writer = SignalWriter(pool, settings)
    bf_count = await _backfill(r, bf_writer, STREAM_SCAN_SIGNALS, settings)

    check("Backfill processed messages", bf_count > 0, f"count={bf_count}")

    # Verify checkpoint saved
    cp = await r.hget(KEY_ARCHIVER_CHECKPOINT, STREAM_SCAN_SIGNALS)
    check("Backfill checkpoint saved", cp is not None)

    # Verify in Postgres
    async with pool.acquire() as conn:
        bf_row = await conn.fetchrow(
            "SELECT symbol, conviction_grade FROM signals "
            "WHERE signal_id = $1::uuid",
            bf_signal["signal_id"],
        )
    check("Backfilled signal in Postgres",
          bf_row is not None and bf_row["symbol"] == "ARCHI_TEST_BF")

    # ═══════════════════════════════════════════════
    # TEST 4: Outcome tracker — TARGET_HIT
    # ═══════════════════════════════════════════════
    print("\n--- OUTCOME TRACKER: TARGET_HIT ---")

    # Insert a trackable signal
    target_signal = _make_signal("ARCHI_TEST_TGT", "A+", 95.0)
    target_signal["entry_price"] = 2500.0
    target_signal["invalidation_price"] = 2475.0
    target_signal["target_price"] = 2550.0
    target_signal["created_at_us"] = now_us()

    writer.add(target_signal)
    await writer.flush()

    # Set Redis LTP above target
    await r.hset(
        f"{KEY_TICK_PREFIX}ARCHI_TEST_TGT",
        mapping={"ltp": b"2560.0", "symbol": b"ARCHI_TEST_TGT"},
    )
    await r.expire(f"{KEY_TICK_PREFIX}ARCHI_TEST_TGT", 60)

    # Run tracker cycle
    tracker = OutcomeTracker(pool, r, settings)
    tracker._market_open = (0, 0)   # Override to always be market hours
    tracker._market_close = (23, 59)
    await tracker._track_cycle()

    async with pool.acquire() as conn:
        tgt_row = await conn.fetchrow(
            "SELECT outcome_label, outcome_tracked, target_hit_at, "
            "high_after_signal, max_favorable_pct "
            "FROM signals WHERE signal_id = $1::uuid",
            target_signal["signal_id"],
        )

    check("Outcome = TARGET_HIT",
          tgt_row and tgt_row["outcome_label"] == "TARGET_HIT")
    check("outcome_tracked = True",
          tgt_row and tgt_row["outcome_tracked"] is True)
    check("target_hit_at set",
          tgt_row and tgt_row["target_hit_at"] is not None)
    check("high_after_signal tracked",
          tgt_row and float(tgt_row["high_after_signal"]) >= 2550.0,
          f"high={tgt_row['high_after_signal'] if tgt_row else 'N/A'}")
    check("max_favorable_pct > 0",
          tgt_row and float(tgt_row["max_favorable_pct"]) > 0)

    # ═══════════════════════════════════════════════
    # TEST 5: Outcome tracker — STOP_HIT
    # ═══════════════════════════════════════════════
    print("\n--- OUTCOME TRACKER: STOP_HIT ---")

    stop_signal = _make_signal("ARCHI_TEST_STP", "A", 80.0)
    stop_signal["entry_price"] = 2500.0
    stop_signal["invalidation_price"] = 2475.0
    stop_signal["target_price"] = 2550.0
    stop_signal["created_at_us"] = now_us()

    writer.add(stop_signal)
    await writer.flush()

    # Set Redis LTP below stop
    await r.hset(
        f"{KEY_TICK_PREFIX}ARCHI_TEST_STP",
        mapping={"ltp": b"2470.0", "symbol": b"ARCHI_TEST_STP"},
    )
    await r.expire(f"{KEY_TICK_PREFIX}ARCHI_TEST_STP", 60)

    await tracker._track_cycle()

    async with pool.acquire() as conn:
        stp_row = await conn.fetchrow(
            "SELECT outcome_label, outcome_tracked, stop_hit_at, "
            "low_after_signal, max_adverse_pct "
            "FROM signals WHERE signal_id = $1::uuid",
            stop_signal["signal_id"],
        )

    check("Outcome = STOP_HIT",
          stp_row and stp_row["outcome_label"] == "STOP_HIT")
    check("stop_hit_at set",
          stp_row and stp_row["stop_hit_at"] is not None)
    check("low_after_signal tracked",
          stp_row and float(stp_row["low_after_signal"]) <= 2475.0,
          f"low={stp_row['low_after_signal'] if stp_row else 'N/A'}")
    check("max_adverse_pct > 0",
          stp_row and float(stp_row["max_adverse_pct"]) > 0)

    # ═══════════════════════════════════════════════
    # TEST 6: Tracker stats
    # ═══════════════════════════════════════════════
    print("\n--- TRACKER STATS ---")

    stats = tracker.stats
    check("tracker_cycles counter exists", "tracker_cycles" in stats,
          f"cycles={stats['tracker_cycles']}")
    check("target_hits >= 1", stats["target_hits"] >= 1,
          f"hits={stats['target_hits']}")
    check("stop_hits >= 1", stats["stop_hits"] >= 1,
          f"hits={stats['stop_hits']}")
    check("tracked_total >= 2", stats["tracked_total"] >= 2,
          f"total={stats['tracked_total']}")

    # ═══════════════════════════════════════════════
    # TEST 7: Suppressed signals archived
    # ═══════════════════════════════════════════════
    print("\n--- SUPPRESSED SIGNALS ---")

    async with pool.acquire() as conn:
        sup_count = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE symbol LIKE 'ARCHI_TEST_%' "
            "AND suppressed = true"
        )
    check("Suppressed signals archived", sup_count >= 1, f"count={sup_count}")

    # ═══════════════════════════════════════════════
    # TEST 8: Schema validation
    # ═══════════════════════════════════════════════
    print("\n--- SCHEMA VALIDATION ---")

    async with pool.acquire() as conn:
        # Check new tables exist
        for table in ("signal_outcomes_daily", "session_analytics",
                      "watchlist_conversions", "suppression_daily"):
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_name = $1)", table
            )
            check(f"Table {table} exists", exists)

        # Check new columns on signals
        for col in ("signal_id", "entry_price", "invalidation_price",
                     "outcome_tracked", "target_hit_at", "stop_hit_at",
                     "high_after_signal", "max_favorable_pct", "session_hour"):
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'signals' AND column_name = $1)", col
            )
            check(f"Column signals.{col} exists", exists)

    # ═══════════════════════════════════════════════
    # CLEANUP
    # ═══════════════════════════════════════════════
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM signals WHERE symbol LIKE 'ARCHI_TEST_%'")

    for sym in ("ARCHI_TEST_TGT", "ARCHI_TEST_STP"):
        await r.delete(f"{KEY_TICK_PREFIX}{sym}")

    await pool.close()
    await r.aclose()
    return passed, failed


def main():
    print("=" * 70)
    print("INFUSION PHASE 4A — ARCHIVER INTEGRATION TEST")
    print(f"Redis: {REDIS_URL}")
    print(f"Postgres: {DB_URL.split('@')[-1]}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    p, f = asyncio.run(run_tests())

    print("\n" + "=" * 70)
    print(f"  Total: {p + f}  |  PASS: {p}  |  FAIL: {f}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'ARCHIVER VALIDATED' if f == 0 else 'ARCHIVER NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if f == 0 else 1)


if __name__ == "__main__":
    main()
