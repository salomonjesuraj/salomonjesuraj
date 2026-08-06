"""Phase 3A validation — models, enums, schema registry, and stream constants.

Validates all scanner engine foundation types before service implementation.

Usage:
    python -X utf8 scripts/validate_3a.py
"""

import os
import sys
import uuid

# Add lib paths
base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ("infusion-models", "infusion-streams", "infusion-common"):
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))

errors = []


def check(label, fn):
    try:
        fn()
        print(f"  ✓ {label}")
    except Exception as e:
        errors.append(f"{label}: {e}")
        print(f"  ✗ {label}: {e}")


# ═══════════════════════════════════════════════════
# 1. Enum validation
# ═══════════════════════════════════════════════════
print("\n--- ENUMS ---")


def test_signal_lifecycle():
    from infusion_models.enums import SignalLifecycle
    assert SignalLifecycle.CANDIDATE == "candidate"
    assert SignalLifecycle.CONFIRMED == "confirmed"
    assert SignalLifecycle.ACTIVE == "active"
    assert SignalLifecycle.EXPIRED == "expired"
    assert SignalLifecycle.INVALIDATED == "invalidated"
    assert SignalLifecycle.SUPPRESSED == "suppressed"
    assert len(SignalLifecycle) == 6


def test_market_regime():
    from infusion_models.enums import MarketRegime
    assert MarketRegime.TRENDING_UP == "trending_up"
    assert MarketRegime.TRENDING_DOWN == "trending_down"
    assert MarketRegime.RANGING == "ranging"
    assert MarketRegime.VOLATILE == "volatile"
    assert len(MarketRegime) == 4


def test_pre_breakout_state():
    from infusion_models.enums import PreBreakoutState
    assert PreBreakoutState.IDLE == "idle"
    assert PreBreakoutState.COMPRESSING == "compressing"
    assert PreBreakoutState.ACCUMULATING == "accumulating"
    assert PreBreakoutState.COILED == "coiled"
    assert PreBreakoutState.TRIGGERED == "triggered"
    assert len(PreBreakoutState) == 5


def test_existing_enums_preserved():
    from infusion_models.enums import (
        Exchange, Segment, Series, SignalType, ConvictionGrade, InstrumentTier
    )
    assert Exchange.NSE == "NSE"
    assert SignalType.BULLISH == "bullish"
    assert ConvictionGrade.A_PLUS == "A+"


check("SignalLifecycle enum", test_signal_lifecycle)
check("MarketRegime enum", test_market_regime)
check("PreBreakoutState enum", test_pre_breakout_state)
check("Existing enums preserved", test_existing_enums_preserved)


# ═══════════════════════════════════════════════════
# 2. Signal model validation
# ═══════════════════════════════════════════════════
print("\n--- SIGNAL MODELS ---")


def test_scan_signal_v1_compat():
    from infusion_models.signal import ScanSignalV1
    s = ScanSignalV1(
        symbol="RELIANCE", strategy="test", signal_type="bullish",
        exchange_timestamp_ms=1700000000000,
    )
    assert s.symbol == "RELIANCE"
    assert s.strength == 0.0


def test_scan_signal_v2():
    from infusion_models.signal import ScanSignalV2
    sig_id = str(uuid.uuid4())
    s = ScanSignalV2(
        signal_id=sig_id,
        symbol="RELIANCE",
        strategy_id="vol_vwap_breakout",
        signal_type="bullish",
        lifecycle="confirmed",
        created_at_us=1700000000000000,
        confirmed_at_us=1700000001000000,
        conviction_score=85.0,
        conviction_grade="A+",
        sub_scores={"volume": 25, "vwap": 25, "rsi": 15, "ema": 10, "flow": 10},
        price_at_signal=2485.60,
        entry_price=2485.60,
        invalidation_price=2462.30,
        target_price=2531.80,
        risk_reward_ratio=2.0,
        features_snapshot={"ltp": 2485.60, "vwap": 2481.0, "rel_vol_20d": 3.2},
        sector_id="NIFTY_50",
        sector_strength=78.0,
        market_regime="trending_up",
        pre_breakout_state="coiled",
        tier=1,
        suppressed=False,
        suppression_reason="",
        explanation=[
            "Volume 3.2x vs 20-day avg",
            "VWAP reclaimed at ₹2,481",
            "RSI 58.4 — bullish momentum",
        ],
        conditions_met={
            "vol_expansion": True,
            "vwap_reclaim": True,
            "above_ema9": True,
            "rsi_range": True,
            "bb_context": True,
            "order_flow": True,
            "spread_filter": True,
        },
    )
    assert s.signal_id == sig_id
    assert s.conviction_score == 85.0
    assert s.conviction_grade == "A+"
    assert len(s.explanation) == 3
    assert all(s.conditions_met.values())
    assert s.risk_reward_ratio == 2.0
    assert s.suppressed is False
    assert s.ttl_sec == 300  # default


def test_scan_signal_v2_defaults():
    from infusion_models.signal import ScanSignalV2
    s = ScanSignalV2(
        signal_id="test-id",
        symbol="INFY",
        strategy_id="vol_vwap_breakout",
        signal_type="bullish",
    )
    assert s.lifecycle == "candidate"
    assert s.conviction_score == 0.0
    assert s.conviction_grade == ""
    assert s.sub_scores == {}
    assert s.suppressed is False
    assert s.explanation == []
    assert s.conditions_met == {}
    assert s.ttl_sec == 300
    assert s.tier == 1


def test_scan_signal_v2_frozen():
    from infusion_models.signal import ScanSignalV2
    s = ScanSignalV2(
        signal_id="test-id",
        symbol="TCS",
        strategy_id="vol_vwap_breakout",
        signal_type="bullish",
    )
    try:
        s.symbol = "CHANGED"
        assert False, "Should be frozen"
    except Exception:
        pass  # Expected


check("ScanSignalV1 backward compat", test_scan_signal_v1_compat)
check("ScanSignalV2 full construction", test_scan_signal_v2)
check("ScanSignalV2 defaults", test_scan_signal_v2_defaults)
check("ScanSignalV2 frozen", test_scan_signal_v2_frozen)


# ═══════════════════════════════════════════════════
# 3. Event types
# ═══════════════════════════════════════════════════
print("\n--- EVENT TYPES ---")


def test_event_types():
    from infusion_models.events import EventType
    assert EventType.SCAN_SIGNAL == "scan_signal"
    assert EventType.SCAN_SUPPRESSED == "scan_suppressed"
    assert EventType.RAW_TICK == "raw_tick"
    assert EventType.NORMALIZED_TICK == "normalized_tick"
    assert EventType.FEATURE_COMPUTED == "feature_computed"
    assert EventType.SECTOR_STATE == "sector_state"
    assert EventType.CONVICTION_RANKED == "conviction_ranked"
    assert EventType.ALERT_OUTBOUND == "alert_outbound"
    assert len(EventType) == 8


check("EventType completeness", test_event_types)


# ═══════════════════════════════════════════════════
# 4. Schema registry
# ═══════════════════════════════════════════════════
print("\n--- SCHEMA REGISTRY ---")


def test_current_versions():
    from infusion_models.schema_registry import CURRENT_VERSIONS
    from infusion_models.events import EventType
    assert CURRENT_VERSIONS[EventType.SCAN_SIGNAL] == 2
    assert CURRENT_VERSIONS[EventType.SCAN_SUPPRESSED] == 2
    assert CURRENT_VERSIONS[EventType.RAW_TICK] == 1
    assert CURRENT_VERSIONS[EventType.FEATURE_COMPUTED] == 1


def test_schema_lookup():
    from infusion_models.schema_registry import get_schema, SCHEMA_REGISTRY
    from infusion_models.events import EventType
    from infusion_models.signal import ScanSignalV1, ScanSignalV2

    # V1 still resolvable
    assert get_schema(EventType.SCAN_SIGNAL, 1) is ScanSignalV1
    # V2 is current
    assert get_schema(EventType.SCAN_SIGNAL, 2) is ScanSignalV2
    # Suppressed uses same schema
    assert get_schema(EventType.SCAN_SUPPRESSED, 2) is ScanSignalV2


def test_schema_version_error():
    from infusion_models.schema_registry import get_schema, SchemaVersionError
    from infusion_models.events import EventType
    try:
        get_schema(EventType.SCAN_SIGNAL, 99)
        assert False, "Should raise SchemaVersionError"
    except SchemaVersionError:
        pass


check("CURRENT_VERSIONS updated", test_current_versions)
check("Schema lookup V1/V2", test_schema_lookup)
check("Schema version error", test_schema_version_error)


# ═══════════════════════════════════════════════════
# 5. Stream constants
# ═══════════════════════════════════════════════════
print("\n--- STREAM CONSTANTS ---")


def test_new_streams():
    from infusion_streams.constants import (
        STREAM_SCAN_SUPPRESSED, MAXLEN_SCAN_SUPPRESSED,
        CG_ALERT,
    )
    assert STREAM_SCAN_SUPPRESSED == "infusion:stream:scan:suppressed"
    assert MAXLEN_SCAN_SUPPRESSED == 5_000
    assert CG_ALERT == "alert-cg"


def test_scanner_keys():
    from infusion_streams.constants import (
        KEY_SIGNAL_PREFIX, KEY_SIGNAL_ACTIVE, KEY_COOLDOWN_PREFIX,
        KEY_PRE_BREAKOUT_PREFIX, KEY_SECTOR_PREFIX,
    )
    assert KEY_SIGNAL_PREFIX == "infusion:signal:"
    assert KEY_SIGNAL_ACTIVE == "infusion:signals:active"
    assert KEY_COOLDOWN_PREFIX == "infusion:cooldown:"
    assert KEY_PRE_BREAKOUT_PREFIX == "infusion:prebreak:"
    assert KEY_SECTOR_PREFIX == "infusion:sector:"


def test_existing_constants_preserved():
    from infusion_streams.constants import (
        STREAM_TICK_RAW, STREAM_TICK_NORMALIZED, STREAM_FEATURE_COMPUTED,
        STREAM_SCAN_SIGNALS, CG_NORMALIZER, CG_FEATURE, CG_SCANNER,
        KEY_TICK_PREFIX, KEY_FEATURE_PREFIX, KEY_SYMBOLS,
        MAXLEN_TICK_RAW, MAXLEN_SIGNALS, MAXLEN_DLQ,
    )
    assert STREAM_TICK_RAW == "infusion:stream:tick:raw"
    assert STREAM_SCAN_SIGNALS == "infusion:stream:scan:signals"
    assert CG_SCANNER == "scanner-cg"
    assert MAXLEN_TICK_RAW == 50_000
    assert MAXLEN_SIGNALS == 10_000
    assert MAXLEN_DLQ == 1_000


check("New stream constants", test_new_streams)
check("Scanner hot state keys", test_scanner_keys)
check("Existing constants preserved", test_existing_constants_preserved)


# ═══════════════════════════════════════════════════
# 6. Codec round-trip with V2
# ═══════════════════════════════════════════════════
print("\n--- CODEC ROUND-TRIP ---")


def test_codec_signal_v2():
    from infusion_streams.codec import encode_event, decode_event
    from infusion_models.events import EventType

    payload = {
        "signal_id": "test-123",
        "symbol": "RELIANCE",
        "strategy_id": "vol_vwap_breakout",
        "signal_type": "bullish",
        "conviction_score": 85.0,
    }
    encoded = encode_event(EventType.SCAN_SIGNAL, payload, 1700000000000000)
    assert isinstance(encoded, bytes)

    et, ver, ts, rx, data = decode_event(encoded)
    assert et == EventType.SCAN_SIGNAL
    assert ver == 2  # current version
    assert rx == 1700000000000000
    assert data["signal_id"] == "test-123"
    assert data["conviction_score"] == 85.0


def test_codec_suppressed_v2():
    from infusion_streams.codec import encode_event, decode_event
    from infusion_models.events import EventType

    payload = {
        "signal_id": "test-456",
        "symbol": "INFY",
        "strategy_id": "vol_vwap_breakout",
        "signal_type": "bullish",
        "suppressed": True,
        "suppression_reason": "cooldown_active",
    }
    encoded = encode_event(EventType.SCAN_SUPPRESSED, payload, 1700000000000000)
    et, ver, ts, rx, data = decode_event(encoded)
    assert et == EventType.SCAN_SUPPRESSED
    assert ver == 2
    assert data["suppressed"] is True
    assert data["suppression_reason"] == "cooldown_active"


check("Codec round-trip: SCAN_SIGNAL V2", test_codec_signal_v2)
check("Codec round-trip: SCAN_SUPPRESSED V2", test_codec_suppressed_v2)


# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
print("\n" + "=" * 60)
total = len(errors)
if total == 0:
    print("ALL CHECKS PASSED — Phase 3A foundation validated")
else:
    print(f"FAILURES: {total}")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
