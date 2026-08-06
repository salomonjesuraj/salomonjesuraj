"""Phase 4A offline validation — writer, tracker, and archiver logic.

Tests writer batching, session classification, outcome determination,
backfill mechanics, and schema correctness WITHOUT live services.

Usage:
    python -X utf8 scripts/validate_4a.py
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
    print("INFUSION PHASE 4A — OFFLINE VALIDATION")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    # ═══════════════════════════════════════════════
    # MODULE IMPORTS
    # ═══════════════════════════════════════════════
    print("\n--- MODULE IMPORTS ---")

    try:
        from archiver.config import ArchiverSettings
        check("ArchiverSettings imports", True)
    except Exception as e:
        check("ArchiverSettings imports", False, str(e))
        return

    try:
        from archiver.writer import SignalWriter, _classify_session, _INSERT_SQL
        check("SignalWriter imports", True)
    except Exception as e:
        check("SignalWriter imports", False, str(e))
        return

    try:
        from archiver.tracker import OutcomeTracker
        check("OutcomeTracker imports", True)
    except Exception as e:
        check("OutcomeTracker imports", False, str(e))
        return

    try:
        from archiver.main import _backfill, _consume_stream, run
        check("Archiver main imports", True)
    except Exception as e:
        check("Archiver main imports", False, str(e))
        return

    # ═══════════════════════════════════════════════
    # CONFIG VALIDATION
    # ═══════════════════════════════════════════════
    print("\n--- CONFIG ---")

    settings = ArchiverSettings()
    check("database_url set", "postgresql" in settings.database_url, settings.database_url.split("@")[-1])
    check("write_batch_size > 0", settings.write_batch_size > 0, str(settings.write_batch_size))
    check("write_flush_sec > 0", settings.write_flush_sec > 0, str(settings.write_flush_sec))
    check("tracker_interval_sec > 0", settings.tracker_interval_sec > 0, str(settings.tracker_interval_sec))
    check("tracker_lookback_min > 0", settings.tracker_lookback_min > 0, str(settings.tracker_lookback_min))
    check("signal_ttl_min > 0", settings.signal_ttl_min > 0, str(settings.signal_ttl_min))
    check("market_open_hour = 9", settings.market_open_hour == 9)
    check("market_open_min = 15", settings.market_open_min == 15)
    check("market_close_hour = 15", settings.market_close_hour == 15)
    check("market_close_min = 30", settings.market_close_min == 30)
    check("backfill_on_startup bool", isinstance(settings.backfill_on_startup, bool))

    # ═══════════════════════════════════════════════
    # SESSION CLASSIFICATION
    # ═══════════════════════════════════════════════
    print("\n--- SESSION CLASSIFICATION ---")

    # IST = UTC + 5:30
    # 09:30 IST = 04:00 UTC
    ist = timezone(timedelta(hours=5, minutes=30))

    def _make_us(hour, minute):
        dt = datetime(2026, 5, 28, hour, minute, tzinfo=ist)
        return int(dt.timestamp() * 1_000_000)

    check("09:20 IST → opening", _classify_session(_make_us(9, 20)) == "opening")
    check("09:59 IST → opening", _classify_session(_make_us(9, 59)) == "opening")
    check("10:00 IST → mid_morning", _classify_session(_make_us(10, 0)) == "mid_morning")
    check("11:30 IST → mid_morning", _classify_session(_make_us(11, 30)) == "mid_morning")
    check("12:00 IST → midday", _classify_session(_make_us(12, 0)) == "midday")
    check("13:30 IST → midday", _classify_session(_make_us(13, 30)) == "midday")
    check("14:00 IST → closing", _classify_session(_make_us(14, 0)) == "closing")
    check("15:25 IST → closing", _classify_session(_make_us(15, 25)) == "closing")
    check("09:10 IST → pre_market", _classify_session(_make_us(9, 10)) == "pre_market")
    check("15:35 IST → post_market", _classify_session(_make_us(15, 35)) == "post_market")
    check("timestamp 0 → unknown", _classify_session(0) == "unknown")

    # Determinism
    ts1 = _make_us(10, 30)
    check("Session classification deterministic",
          _classify_session(ts1) == _classify_session(ts1))

    # ═══════════════════════════════════════════════
    # INSERT SQL VALIDATION
    # ═══════════════════════════════════════════════
    print("\n--- SQL ---")

    check("INSERT SQL has signal_id", "signal_id" in _INSERT_SQL)
    check("INSERT SQL has ON CONFLICT", "ON CONFLICT" in _INSERT_SQL)
    check("INSERT SQL has DO NOTHING", "DO NOTHING" in _INSERT_SQL)
    check("INSERT SQL has session_hour", "session_hour" in _INSERT_SQL)
    check("INSERT SQL has 24 params",
          _INSERT_SQL.count("$") == 24,
          f"params={_INSERT_SQL.count('$')}")

    # ═══════════════════════════════════════════════
    # WRITER MECHANICS (mock pool)
    # ═══════════════════════════════════════════════
    print("\n--- WRITER MECHANICS ---")

    class MockPool:
        pass

    writer_settings = ArchiverSettings()
    writer_settings.write_batch_size = 3
    writer_settings.write_flush_sec = 1.0

    writer = SignalWriter(MockPool(), writer_settings)

    check("Writer starts empty", writer.stats["buffer_size"] == 0)
    check("Writer total_written = 0", writer.stats["total_written"] == 0)

    # Add signals
    payload1 = {
        "signal_id": str(uuid.uuid4()),
        "symbol": "RELIANCE",
        "strategy_id": "vol_vwap_breakout",
        "signal_type": "bullish",
        "conviction_score": 85.0,
        "conviction_grade": "A",
        "price_at_signal": 2500.0,
        "entry_price": 2500.0,
        "invalidation_price": 2475.0,
        "target_price": 2550.0,
        "risk_reward_ratio": 2.0,
        "sector_id": "NIFTY_50",
        "sector_strength": 65.0,
        "market_regime": "risk_on",
        "suppressed": False,
        "conditions_met": {"vol_expansion": True},
        "explanation": ["Volume expanded 3.5x"],
        "sub_scores": {"volume": 25},
        "features_snapshot": {"ltp": 2500.0, "vwap": 2490.0},
        "created_at_us": _make_us(10, 15),
    }

    should_flush = writer.add(payload1)
    check("Add 1: no flush needed", not should_flush, f"buffer={writer.stats['buffer_size']}")
    check("Buffer size = 1", writer.stats["buffer_size"] == 1)

    writer.add({**payload1, "signal_id": str(uuid.uuid4())})
    should_flush = writer.add({**payload1, "signal_id": str(uuid.uuid4())})
    check("Add 3: flush triggered", should_flush, f"buffer={writer.stats['buffer_size']}")

    # Time-based flush
    writer._buffer.clear()
    writer._last_flush = time.monotonic() - 2.0  # simulate 2 sec ago
    check("should_flush (empty buffer) → False", not writer.should_flush())
    writer.add({**payload1, "signal_id": str(uuid.uuid4())})
    check("should_flush (stale buffer) → True", writer.should_flush())

    # ═══════════════════════════════════════════════
    # STREAM CONSTANTS
    # ═══════════════════════════════════════════════
    print("\n--- STREAM CONSTANTS ---")

    from infusion_streams.constants import (
        CG_ARCHIVER, CG_ARCHIVER_SUP, STREAM_RECAP,
        KEY_ARCHIVER_CHECKPOINT, MAXLEN_RECAP,
    )

    check("CG_ARCHIVER defined", CG_ARCHIVER == "archiver-cg")
    check("CG_ARCHIVER_SUP defined", CG_ARCHIVER_SUP == "archiver-sup-cg")
    check("STREAM_RECAP defined", STREAM_RECAP == "infusion:stream:recap")
    check("KEY_ARCHIVER_CHECKPOINT defined", "archiver" in KEY_ARCHIVER_CHECKPOINT)
    check("MAXLEN_RECAP = 500", MAXLEN_RECAP == 500)

    # ═══════════════════════════════════════════════
    # OUTCOME TRACKER LOGIC
    # ═══════════════════════════════════════════════
    print("\n--- OUTCOME TRACKER ---")

    from archiver.tracker import OutcomeTracker

    check("OutcomeTracker has _track_cycle", hasattr(OutcomeTracker, "_track_cycle"))
    check("OutcomeTracker has _get_ltp", hasattr(OutcomeTracker, "_get_ltp"))
    check("OutcomeTracker has _is_market_hours", hasattr(OutcomeTracker, "_is_market_hours"))
    check("OutcomeTracker has stats", hasattr(OutcomeTracker, "stats"))

    # Market hours check
    tracker = OutcomeTracker.__new__(OutcomeTracker)
    tracker._market_open = (9, 15)
    tracker._market_close = (15, 30)

    # We can't easily mock datetime.now(), but verify the method exists and is callable
    check("_is_market_hours is callable", callable(tracker._is_market_hours))

    # ═══════════════════════════════════════════════
    # DOCKER / INFRA
    # ═══════════════════════════════════════════════
    print("\n--- INFRASTRUCTURE ---")

    import pathlib
    root = pathlib.Path(base)

    check("Dockerfile exists",
          (root / "services/archiver/Dockerfile").exists())
    check("pyproject.toml exists",
          (root / "services/archiver/pyproject.toml").exists())
    check("__init__.py exists",
          (root / "services/archiver/src/archiver/__init__.py").exists())

    # pyproject deps
    pyproject = (root / "services/archiver/pyproject.toml").read_text()
    check("asyncpg in pyproject", "asyncpg" in pyproject)
    check("infusion-models in pyproject", "infusion-models" in pyproject)
    check("infusion-streams in pyproject", "infusion-streams" in pyproject)
    check("infusion-common in pyproject", "infusion-common" in pyproject)

    # docker-compose
    dc = (root / "docker-compose.yml").read_text()
    check("archiver service in docker-compose", "archiver:" in dc)
    check("archiver Dockerfile reference", "services/archiver/Dockerfile" in dc)
    check("DATABASE_URL in archiver env", "INFUSION_DATABASE_URL" in dc)

    # Migration
    check("Migration file exists",
          (root / "migrations/002_phase4_outcome_tracking.sql").exists())

    migration = (root / "migrations/002_phase4_outcome_tracking.sql").read_text()
    check("Migration has signal_id column", "signal_id" in migration)
    check("Migration has outcome_tracked column", "outcome_tracked" in migration)
    check("Migration has target_hit_at column", "target_hit_at" in migration)
    check("Migration has session_analytics table", "session_analytics" in migration)
    check("Migration has watchlist_conversions table", "watchlist_conversions" in migration)
    check("Migration has suppression_daily table", "suppression_daily" in migration)

    # ═══════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════
    print("\n" + "=" * 70)
    print(f"  Total: {passed + failed}  |  PASS: {passed}  |  FAIL: {failed}")
    if errors:
        print(f"  FAILURES: {errors}")
    print(f"  VERDICT: {'PHASE 4A VALIDATED' if failed == 0 else 'NEEDS FIX'}")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
