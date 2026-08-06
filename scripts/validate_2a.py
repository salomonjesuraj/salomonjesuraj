"""Sub-Sprint 2A validation — verify all shared library imports and schemas."""

import sys
import os

# Add lib paths for direct import testing
base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for lib in ["infusion-models", "infusion-streams", "infusion-common"]:
    sys.path.insert(0, os.path.join(base, "libs", lib, "src"))

errors = []

def check(label, fn):
    try:
        fn()
        print(f"  ✓ {label}")
    except Exception as e:
        errors.append(f"{label}: {e}")
        print(f"  ✗ {label}: {e}")

print("=" * 60)
print("SUB-SPRINT 2A VALIDATION")
print("=" * 60)

# ── infusion-models ──
print("\n── infusion-models ──")

def check_tick_models():
    from infusion_models.tick import RawTickV1, NormalizedTickV1
    # Verify RawTickV1 has Phase 2 fields
    r = RawTickV1(
        broker="mock", instrument_key="NSE_EQ|INE002A01018",
        exchange="NSE", segment="EQ", ltp=2500.0, open=2490.0,
        high=2510.0, low=2485.0, close=2495.0, volume=100000,
        exchange_timestamp_ms=1700000000000, received_at_us=1700000000000000,
    )
    assert r.broker == "mock"
    assert r.instrument_key == "NSE_EQ|INE002A01018"
    assert r.best_bid == 0.0  # default

    # Verify NormalizedTickV1 has Phase 2 fields
    n = NormalizedTickV1(
        symbol="RELIANCE", sector_id="NIFTY_50", is_fno=True, tier=1,
        ltp=2500.0, open=2490.0, high=2510.0, low=2485.0, close=2495.0,
        volume=100000, exchange_timestamp_ms=1700000000000,
        received_at_us=1700000000000000, normalized_at_us=1700000000001000,
    )
    assert n.sector_id == "NIFTY_50"
    assert n.tier == 1
    assert isinstance(n.tier, int)

check("RawTickV1 + NormalizedTickV1 schemas", check_tick_models)

def check_feature_model():
    from infusion_models.feature import FeatureVectorV1
    f = FeatureVectorV1(symbol="RELIANCE", timestamp_us=1700000000000000, ltp=2500.0)
    assert f.rsi_14 == 50.0  # default
    assert f.stochastic_k == 50.0
    assert f.spread_bps == 0.0
    assert f.order_imbalance == 0.0
    assert hasattr(f, "ema_5")
    assert hasattr(f, "ema_50")
    assert hasattr(f, "cci_20")
    assert hasattr(f, "bb_upper")

check("FeatureVectorV1 schema", check_feature_model)

def check_events():
    from infusion_models.events import EventType
    assert EventType.RAW_TICK == "raw_tick"
    assert EventType.NORMALIZED_TICK == "normalized_tick"
    assert EventType.FEATURE_COMPUTED == "feature_computed"
    assert len(EventType) == 7

check("EventType enum", check_events)

def check_schema_registry():
    from infusion_models.schema_registry import CURRENT_VERSIONS, get_schema, SCHEMA_REGISTRY
    from infusion_models.events import EventType
    assert CURRENT_VERSIONS[EventType.RAW_TICK] == 1
    schema_cls = get_schema(EventType.RAW_TICK, 1)
    assert schema_cls.__name__ == "RawTickV1"

check("Schema registry", check_schema_registry)

# ── infusion-streams ──
print("\n── infusion-streams ──")

def check_constants():
    from infusion_streams.constants import (
        STREAM_TICK_RAW, STREAM_TICK_NORMALIZED, STREAM_FEATURE_COMPUTED,
        CG_NORMALIZER, CG_FEATURE, CG_DASHBOARD,
        DLQ_PREFIX, MAXLEN_TICK_RAW, MAXLEN_DLQ,
        KEY_TICK_PREFIX, KEY_FEATURE_PREFIX, KEY_HEALTH_PREFIX, KEY_SYMBOLS,
    )
    assert STREAM_TICK_RAW == "infusion:stream:tick:raw"
    assert STREAM_TICK_NORMALIZED == "infusion:stream:tick:normalized"
    assert CG_NORMALIZER == "normalizer-cg"
    assert DLQ_PREFIX == "infusion:dlq:"
    assert KEY_TICK_PREFIX == "infusion:tick:"
    assert MAXLEN_TICK_RAW == 50_000
    assert MAXLEN_DLQ == 1_000

check("Stream constants", check_constants)

def check_codec():
    from infusion_streams.codec import encode_event, decode_event
    from infusion_models.events import EventType
    payload = {"symbol": "RELIANCE", "ltp": 2500.0}
    encoded = encode_event(EventType.RAW_TICK, payload, 1700000000000000)
    assert isinstance(encoded, bytes)
    et, ver, ts, rx, data = decode_event(encoded)
    assert et == EventType.RAW_TICK
    assert ver == 1
    assert data["symbol"] == "RELIANCE"
    assert rx == 1700000000000000

check("Codec encode/decode", check_codec)

def check_producer_class():
    from infusion_streams.producer import StreamProducer
    # Just verify the class exists and has the right interface
    assert hasattr(StreamProducer, "publish")
    assert hasattr(StreamProducer, "publish_raw")
    assert hasattr(StreamProducer, "published_count")

check("StreamProducer interface", check_producer_class)

def check_consumer_class():
    from infusion_streams.consumer import StreamConsumer
    assert hasattr(StreamConsumer, "ensure_group")
    assert hasattr(StreamConsumer, "consume")
    assert hasattr(StreamConsumer, "handle_with_retry")
    assert hasattr(StreamConsumer, "stats")

check("StreamConsumer interface", check_consumer_class)

# ── infusion-common ──
print("\n── infusion-common ──")

def check_config():
    from infusion_common.config import InfusionSettings
    s = InfusionSettings(service_name="test")
    assert s.service_name == "test"
    assert s.environment == "development"
    assert s.health_interval_sec == 10
    assert s.health_ttl_sec == 30
    assert s.log_level == "INFO"

check("InfusionSettings", check_config)

def check_timing():
    from infusion_common.timing import now_us, measure_latency
    ts = now_us()
    assert isinstance(ts, int)
    assert ts > 1_000_000_000_000_000  # sanity: > year 2001 in microseconds

check("now_us() + measure_latency", check_timing)

def check_lifecycle():
    from infusion_common.lifecycle import ServiceLifecycle
    lc = ServiceLifecycle("test")
    assert lc.should_run is True
    assert hasattr(lc, "register_cleanup")
    assert hasattr(lc, "on_shutdown")  # backward compat alias
    assert hasattr(lc, "cleanup")

check("ServiceLifecycle", check_lifecycle)

def check_errors():
    from infusion_common.errors import classify_error, ErrorCategory, InfusionError
    err = classify_error(ValueError("bad data"))
    assert isinstance(err, InfusionError)
    assert err.category == ErrorCategory.MALFORMED_DATA
    d = err.to_log_dict()
    assert "error_category" in d

check("Error taxonomy", check_errors)

def check_health():
    from infusion_common.health import HealthReporter
    assert hasattr(HealthReporter, "set_details_fn")
    assert hasattr(HealthReporter, "start")
    assert hasattr(HealthReporter, "stop")

check("HealthReporter interface", check_health)

# ── Summary ──
print("\n" + "=" * 60)
if errors:
    print(f"FAILED: {len(errors)} errors")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED — Sub-Sprint 2A complete")
    sys.exit(0)
