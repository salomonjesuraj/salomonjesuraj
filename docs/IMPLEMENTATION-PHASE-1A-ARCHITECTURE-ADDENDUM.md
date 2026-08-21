# IMPLEMENTATION PHASE 1A — ARCHITECTURE ADDENDUM

> Four cross-cutting policies that must be established before any service logic
> is implemented. These are system-wide invariants, not per-service concerns.

---

## 1. Centralized Versioned Event Schemas

### 1.1 The Problem

Every stream message is a contract between producer and consumer. As the system
evolves, message schemas will change: new features get added to `FeatureVector`,
new fields appear in `NormalizedTick`, conviction scoring adds new factors.
Without explicit versioning, schema drift causes silent data corruption —
consumers reading fields that shifted position, or missing fields that were
removed.

### 1.2 Schema Version Contract

Every message on every Redis stream carries a mandatory `schema_version` field.

```
STREAM MESSAGE ENVELOPE
═══════════════════════

Every msgpack-encoded message on every Infusion stream includes:

  {
    "v": 1,                          ← schema version (integer, monotonic)
    "t": "normalized_tick",          ← event type (string, machine-readable)
    "ts": 1716789012345678,          ← created_at_us (epoch microseconds)
    "rx": 1716789012340000,          ← received_at_us (origin timestamp, propagated)
    "d": { ... }                     ← payload (event-type-specific fields)
  }

Field semantics:
  v   → schema version of the payload structure. Integer ≥ 1.
  t   → event type tag. Consumers use this for routing and deserialization.
  ts  → when THIS message was created (by its producer).
  rx  → when the ORIGINAL tick was received from the broker WS.
        Propagated unchanged through the entire pipeline.
        Used for end-to-end latency measurement.
  d   → the actual data payload. Schema depends on (t, v) tuple.
```

### 1.3 Event Type Registry

All event types are registered in a single source-of-truth module:

```python
# libs/infusion-models/src/infusion_models/events.py

from enum import StrEnum


class EventType(StrEnum):
    """All event types that flow through Infusion streams."""

    RAW_TICK = "raw_tick"
    NORMALIZED_TICK = "normalized_tick"
    FEATURE_COMPUTED = "feature_computed"
    SCAN_SIGNAL = "scan_signal"
    SECTOR_STATE = "sector_state"
    CONVICTION_RANKED = "conviction_ranked"
    ALERT_OUTBOUND = "alert_outbound"
```

### 1.4 Schema Version Registry

```python
# libs/infusion-models/src/infusion_models/schema_registry.py

"""
Central registry mapping (event_type, version) → schema class.

Rules:
  1. Every schema class is a frozen Pydantic model.
  2. Versions are monotonically increasing integers.
  3. A new version is created when fields are added or semantics change.
  4. The CURRENT_VERSIONS dict is the source of truth for what producers emit.
  5. Consumers MUST handle current version AND current-1 (one version back).
"""

from infusion_models.tick import RawTickV1, NormalizedTickV1
from infusion_models.feature import FeatureVectorV1
from infusion_models.signal import ScanSignalV1
from infusion_models.sector import SectorStateV1
from infusion_models.events import EventType

# What version producers currently emit
CURRENT_VERSIONS: dict[EventType, int] = {
    EventType.RAW_TICK: 1,
    EventType.NORMALIZED_TICK: 1,
    EventType.FEATURE_COMPUTED: 1,
    EventType.SCAN_SIGNAL: 1,
    EventType.SECTOR_STATE: 1,
    EventType.CONVICTION_RANKED: 1,
    EventType.ALERT_OUTBOUND: 1,
}

# Schema class lookup: (event_type, version) → Pydantic model class
SCHEMA_REGISTRY: dict[tuple[EventType, int], type] = {
    (EventType.RAW_TICK, 1): RawTickV1,
    (EventType.NORMALIZED_TICK, 1): NormalizedTickV1,
    (EventType.FEATURE_COMPUTED, 1): FeatureVectorV1,
    (EventType.SCAN_SIGNAL, 1): ScanSignalV1,
    (EventType.SECTOR_STATE, 1): SectorStateV1,
    # When v2 is introduced:
    # (EventType.NORMALIZED_TICK, 2): NormalizedTickV2,
}


def get_schema(event_type: EventType, version: int) -> type:
    """Resolve schema class for a given event type and version."""
    key = (event_type, version)
    if key not in SCHEMA_REGISTRY:
        raise SchemaVersionError(
            f"Unknown schema: {event_type} v{version}. "
            f"Known versions: {[v for (e, v) in SCHEMA_REGISTRY if e == event_type]}"
        )
    return SCHEMA_REGISTRY[key]


class SchemaVersionError(Exception):
    """Raised when a message has an unrecognized schema version."""

    pass
```

### 1.5 Schema Evolution Rules

```
ADDING FIELDS (safe — backward compatible)
══════════════════════════════════════════

  Rule: New fields MUST have default values.

  Example — adding spread_bps to FeatureVector:
    1. Add field to FeatureVectorV1 with default:
       spread_bps: float = 0.0
    2. Producer starts populating spread_bps.
    3. Consumers that don't know about spread_bps ignore it.
    4. Consumers that want spread_bps read it (with fallback to 0.0).
    5. NO version bump needed for additive changes with defaults.

  When to bump version:
    If the new field's absence changes the semantic meaning of the message,
    bump the version. Consumer must know "this message has spread_bps" vs
    "this message predates spread_bps."

    Example: adding conviction_ai_score to ScanSignal.
    Old consumers should NOT treat absence as "AI score = 0" (that's wrong).
    Bump to v2. v2 schema requires the field. v1 consumers ignore it.


REMOVING FIELDS (requires version bump)
═══════════════════════════════════════

  Rule: Never remove a field from a schema version.
        Instead, add a new version without the field.

  Procedure:
    1. Create FeatureVectorV2 without the removed field.
    2. Add to SCHEMA_REGISTRY: (FEATURE_COMPUTED, 2): FeatureVectorV2
    3. Update CURRENT_VERSIONS: FEATURE_COMPUTED: 2
    4. Producers start emitting v2.
    5. Consumers must handle BOTH v1 and v2 during transition.
    6. After all consumers are updated: v1 support can be dropped.
    7. Drop v1 from SCHEMA_REGISTRY (optional, for cleanliness).


RENAMING FIELDS (requires version bump)
═══════════════════════════════════════

  Rule: Treat as remove old + add new.
  Bump version. Both old and new fields present in transition period.


CHANGING FIELD TYPES (requires version bump)
════════════════════════════════════════════

  Rule: Never change a field's type in an existing version.
  Create new version with new type.


STREAM REPLAY COMPATIBILITY
═══════════════════════════

  Redis streams retain messages (up to MAXLEN).
  After a schema bump, the stream contains BOTH old and new versions.

  Consumer handling:
    1. Read message envelope: extract v (version)
    2. If v == current_version: deserialize with current schema
    3. If v == current_version - 1: deserialize with previous schema
    4. If v < current_version - 1: DLQ the message (too old to process)
    5. If v > current_version: log.error, DLQ (consumer is outdated)

  This means:
    - At most 2 schema versions coexist at any time.
    - During rolling deploys, producers update before consumers.
    - Stream replay after restart always works within the 2-version window.


ML FEATURE ADDITIONS (special case)
═══════════════════════════════════

  ML features are added to FeatureVector frequently during model iteration.

  Policy:
    - New ML features are always optional fields with defaults.
    - They are grouped under a nested dict: features.ml_features: dict
    - This dict is schema-free (JSONB-style). Any key-value pair is valid.
    - The structured fields (rsi_14, macd, etc.) remain typed and versioned.
    - This gives ML iteration freedom without polluting the core schema.

  Example FeatureVector:
    {
      "v": 1,
      "t": "feature_computed",
      "d": {
        "symbol": "RELIANCE",
        "rsi_14": 62.3,         ← typed, versioned
        "macd": 1.25,           ← typed, versioned
        ...
        "ml_features": {        ← free-form, not versioned
          "lgbm_breakout_prob": 0.73,
          "embedding_cluster": 4
        }
      }
    }
```

### 1.6 Codec Integration

The stream codec (in `infusion-streams`) wraps/unwraps the envelope:

```python
# libs/infusion-streams/src/infusion_streams/codec.py

import msgpack
import time
from infusion_models.events import EventType
from infusion_models.schema_registry import CURRENT_VERSIONS, get_schema


def encode_event(
    event_type: EventType,
    payload: dict,
    received_at_us: int,
) -> bytes:
    """Encode a stream event with versioned envelope."""
    envelope = {
        "v": CURRENT_VERSIONS[event_type],
        "t": event_type.value,
        "ts": int(time.time() * 1_000_000),  # created_at_us
        "rx": received_at_us,
        "d": payload,
    }
    return msgpack.packb(envelope, use_bin_type=True)


def decode_event(raw: bytes) -> tuple[EventType, int, int, int, dict]:
    """Decode a stream event. Returns (event_type, version, created_at_us, received_at_us, payload)."""
    envelope = msgpack.unpackb(raw, raw=False)
    return (
        EventType(envelope["t"]),
        envelope["v"],
        envelope["ts"],
        envelope["rx"],
        envelope["d"],
    )
```

---

## 2. Dead Letter Streams (DLQ)

### 2.1 The Problem

Stream consumers will encounter messages they cannot process:
- Malformed msgpack (deserialization failure)
- Unknown schema version (producer updated, consumer didn't)
- Data validation failure (negative price, NaN volume)
- Processing exception (unexpected None in computation)
- Infrastructure transient (Redis/PG timeout during message handling)

Without a DLQ, these messages either:
1. Block the consumer forever (infinite retry)
2. Get silently ACK'd and lost
3. Get logged but never investigated

All three are unacceptable for a trading intelligence system.

### 2.2 DLQ Stream Topology

```
For each primary stream, a corresponding DLQ stream exists:

  infusion:stream:tick:raw         → infusion:dlq:tick:raw
  infusion:stream:tick:normalized  → infusion:dlq:tick:normalized
  infusion:stream:feature:computed → infusion:dlq:feature:computed
  infusion:stream:scan:signals     → infusion:dlq:scan:signals
  infusion:stream:sector:state     → infusion:dlq:sector:state
  infusion:stream:conviction:ranked → infusion:dlq:conviction:ranked

DLQ streams use MAXLEN ~ 1000 (bounded — these should be rare events).
```

### 2.3 DLQ Message Schema

```
DLQ messages carry full context for inspection and replay:

  {
    "original_stream": "infusion:stream:tick:normalized",
    "original_id": "1716789012345-0",
    "original_payload": <base64-encoded raw bytes>,
    "consumer_group": "feature-cg",
    "consumer_name": "feature-engine-1",
    "failure_reason": "SchemaVersionError: Unknown schema: normalized_tick v3",
    "failure_category": "MALFORMED_DATA",
    "retry_count": 3,
    "first_failed_at": "2026-05-27T09:15:30.123456Z",
    "last_failed_at": "2026-05-27T09:15:32.456789Z",
    "service_name": "feature-engine",
    "stack_trace": "Traceback (most recent call last): ..."
  }
```

### 2.4 Retry Policy

```
RETRY DECISION TREE
═══════════════════

Message processing fails:
  │
  ├── Is exception TRANSIENT? (RedisConnectionError, asyncpg.ConnectionError)
  │   └── YES → retry immediately (within same batch), up to 3 times
  │       └── Still failing → increment retry_count, NACK, sleep 1s
  │           └── retry_count < MAX_RETRIES (3) → message stays in PEL
  │           └── retry_count >= MAX_RETRIES → DLQ the message
  │
  ├── Is exception MALFORMED_DATA? (SchemaVersionError, msgpack.UnpackError, ValidationError)
  │   └── YES → DLQ immediately (no retry — data won't fix itself)
  │
  ├── Is exception FATAL? (out of memory, assertion error)
  │   └── YES → DLQ the message, log.critical, service health → UNHEALTHY
  │
  └── Is exception unknown?
      └── YES → treat as RETRYABLE, follow retry path above


MAX_RETRIES = 3 (configurable per service)
RETRY_BACKOFF = [0s, 1s, 5s]  (immediate, then exponential)
```

### 2.5 Consumer DLQ Integration

```python
# libs/infusion-streams/src/infusion_streams/consumer.py (DLQ-aware)


class StreamConsumer:
    """Redis Stream consumer with DLQ support."""

    def __init__(
        self,
        redis,
        stream: str,
        group: str,
        consumer_name: str,
        max_retries: int = 3,
    ):
        self.redis = redis
        self.stream = stream
        self.group = group
        self.consumer_name = consumer_name
        self.max_retries = max_retries
        self.dlq_stream = stream.replace("infusion:stream:", "infusion:dlq:")
        self._retry_counts: dict[str, int] = {}  # message_id → retry count

    async def process_with_dlq(
        self,
        message_id: str,
        raw_data: bytes,
        handler,  # async callable
    ) -> bool:
        """Process a message with retry + DLQ semantics. Returns True on success."""
        try:
            await handler(raw_data)
            # Success — clear retry state
            self._retry_counts.pop(message_id, None)
            return True
        except (SchemaVersionError, msgpack.UnpackError, ValidationError) as e:
            # Malformed data — DLQ immediately, no retry
            await self._send_to_dlq(message_id, raw_data, e, "MALFORMED_DATA")
            return False
        except (RedisConnectionError, asyncpg.ConnectionDoesNotExistError) as e:
            # Transient — retry
            return await self._handle_retry(message_id, raw_data, e, "TRANSIENT")
        except Exception as e:
            # Unknown — treat as retryable
            return await self._handle_retry(message_id, raw_data, e, "UNKNOWN")

    async def _handle_retry(self, msg_id, raw_data, error, category):
        count = self._retry_counts.get(msg_id, 0) + 1
        self._retry_counts[msg_id] = count

        if count >= self.max_retries:
            await self._send_to_dlq(msg_id, raw_data, error, category)
            self._retry_counts.pop(msg_id, None)
            return False

        logger.warning(
            "message_retry",
            message_id=msg_id,
            retry_count=count,
            max_retries=self.max_retries,
            error=str(error),
            category=category,
        )
        # Don't ACK — message stays in PEL for re-delivery
        return False

    async def _send_to_dlq(self, msg_id, raw_data, error, category):
        """Move a poison message to the dead letter stream."""
        import base64
        import traceback

        dlq_entry = {
            "original_stream": self.stream,
            "original_id": msg_id,
            "original_payload": base64.b64encode(raw_data).decode(),
            "consumer_group": self.group,
            "consumer_name": self.consumer_name,
            "failure_reason": str(error),
            "failure_category": category,
            "retry_count": self._retry_counts.get(msg_id, 0),
            "failed_at": datetime.utcnow().isoformat() + "Z",
            "service_name": self.consumer_name.split("-")[0],
            "stack_trace": traceback.format_exc(),
        }

        try:
            await self.redis.xadd(
                self.dlq_stream,
                {"data": msgpack.packb(dlq_entry)},
                maxlen=1000,
                approximate=True,
            )
            logger.error(
                "message_dlq",
                message_id=msg_id,
                stream=self.stream,
                category=category,
                reason=str(error),
            )
        except Exception as dlq_err:
            # If DLQ write fails, log everything — last resort
            logger.critical(
                "dlq_write_failed",
                message_id=msg_id,
                original_error=str(error),
                dlq_error=str(dlq_err),
            )
```

### 2.6 DLQ Inspection and Replay

```
INSPECTION
══════════

# List DLQ entries for a stream
redis-cli XRANGE infusion:dlq:tick:normalized - + COUNT 10

# Count DLQ entries
redis-cli XLEN infusion:dlq:tick:normalized

# Read specific DLQ entry details (after msgpack decode)
python scripts/inspect_dlq.py --stream tick:normalized --count 5


REPLAY
══════

# Replay a single message from DLQ back to its original stream
python scripts/replay_dlq.py --stream tick:normalized --id 1716789012345-0

# Replay all DLQ messages (with rate limiting)
python scripts/replay_dlq.py --stream tick:normalized --all --rate 10/sec

Replay script logic:
  1. XRANGE infusion:dlq:tick:normalized to get entries
  2. For each entry:
     a. Decode original_payload (base64 → bytes)
     b. XADD to original_stream (infusion:stream:tick:normalized)
     c. XDEL from DLQ stream
  3. Rate-limit to avoid flooding the consumer

INFINITE LOOP PREVENTION:
  DLQ replay adds a special field to the replayed message:
    __replay: true
    __replay_at: epoch_us

  Consumers check for __replay flag:
    If message fails again AND __replay is true:
      → DLQ with failure_category = "PERMANENT"
      → Do NOT replay again (operator must investigate)
```

### 2.7 DLQ Monitoring

```
HEALTH INTEGRATION
══════════════════

The health reporter (from Phase 1) is extended to include DLQ metrics:

  Every 30 seconds, each service checks:
    dlq_length = XLEN infusion:dlq:<my-input-stream>
    SET infusion:health:dlq:<service_name> <dlq_length> EX 60

  Alerting thresholds:
    dlq_length > 0  → log.warning("dlq_non_empty", count=N)
    dlq_length > 10 → log.error("dlq_growing", count=N)
    dlq_length > 50 → log.critical("dlq_overflow", count=N)
                       (something is systematically wrong)
```

### 2.8 Bootstrap

Add DLQ streams to `scripts/validate_streams.py`:

```python
DLQ_STREAMS = [
    "infusion:dlq:tick:raw",
    "infusion:dlq:tick:normalized",
    "infusion:dlq:feature:computed",
    "infusion:dlq:scan:signals",
    "infusion:dlq:sector:state",
    "infusion:dlq:conviction:ranked",
]

# DLQ streams have no consumer groups — they are read manually by operators.
# Created with XADD + MAXLEN ~ 1000.
```

---

## 3. Clock Synchronization Policy

### 3.1 The Problem

A market intelligence system deals with multiple time sources:
- **Exchange clock**: when the trade actually happened (IST, precise to milliseconds)
- **Broker clock**: when the broker's server relayed the trade (may differ by 1-50ms)
- **System clock**: when our service received/processed the data (depends on NTP sync)
- **Browser clock**: when the dashboard renders the data (may be minutes off)

Without an explicit policy, timestamps become untrustworthy. A signal appears to
have fired "before" its triggering tick. Latency measurements are negative. EOD
boundaries are misclassified.

### 3.2 Authoritative Timestamp Policy

```
THE SINGLE RULE
═══════════════

exchange_timestamp_ms is the ONLY authoritative timestamp for market data ordering.

All other timestamps are operational metadata (for debugging, latency measurement,
and audit trail), NOT for business logic ordering.


TIMESTAMP FIELDS AND THEIR ROLES
═════════════════════════════════

Field                    Source           Format                Use For
────────────────────── ──────────────── ──────────────────── ──────────────────
exchange_timestamp_ms   Exchange         Epoch ms (IST→UTC)    Data ordering
                        via broker WS                          Deduplication key
                                                               OHLC bar boundaries
                                                               "When did the trade happen?"

received_at_us          ingestion svc    Epoch µs (UTC)         Latency measurement start
                        time.perf_counter_ns()                  End-to-end pipeline timing
                        at WS frame arrival                     "When did WE see it?"

normalized_at_us        normalizer svc   Epoch µs (UTC)         Per-stage latency
created_at_us           any producer     Epoch µs (UTC)         Per-stage latency
                                                                "When was this message created?"

log timestamps          structlog        ISO 8601 UTC           Debugging
                                         "2026-05-27T09:15:30.123456Z"
```

### 3.3 UTC Normalization

```
ALL RULE
════════

Every timestamp stored, transmitted, or logged by Infusion is in UTC.

No exceptions. No local timezone strings. No IST epoch values.


CONVERSION AT BOUNDARIES
═════════════════════════

1. Exchange timestamps (IST) → UTC at ingestion:
     utc_ms = exchange_ms   # NSE epoch is already UTC-based in broker feeds
     
     CAVEAT: Verify per-broker. Some brokers report IST epoch, some UTC epoch.
     The Upstox protobuf lastTradedTimestamp is UTC epoch milliseconds.
     The Kite binary packet timestamp is exchange epoch (IST = UTC+5:30).
     
     ADAPTER RESPONSIBILITY:
       Each broker adapter MUST normalize timestamps to UTC epoch milliseconds
       before emitting RawTick. This is part of the adapter contract.

2. Dashboard rendering (UTC → IST):
     Frontend converts UTC to IST for display only.
     All API responses return UTC. Client-side formatting handles timezone.

3. Scheduled jobs (IST-aware):
     APScheduler jobs use Asia/Kolkata timezone for cron expressions.
     "Run at 09:10 IST" = cron with timezone=Asia/Kolkata.
     Internal timestamps in scheduler are still UTC.

4. PostgreSQL storage:
     All TIMESTAMPTZ columns store UTC.
     DATE columns (trade_date) are IST dates (market date).
     These differ after midnight UTC but before midnight IST:
       A trade at 15:00 IST on May 27 = 09:30 UTC May 27.
       trade_date = 2026-05-27 (IST date, correct).
```

### 3.4 System Clock Requirements

```
NTP SYNCHRONIZATION
═══════════════════

All hosts running Infusion services MUST have NTP-synchronized clocks.

Docker containers inherit the host's clock.
The host MUST run an NTP client (ntpd, chronyd, or systemd-timesyncd).

Acceptable drift: ≤ 50ms from NTP reference.
Warning threshold: > 100ms drift.
Error threshold: > 500ms drift.


DRIFT DETECTION
═══════════════

At service startup, each service:
  1. Queries Redis: TIME command (returns server epoch seconds + microseconds)
  2. Compares against local time.time()
  3. If abs(redis_time - local_time) > 0.5 seconds:
       log.error("clock_drift_detected",
                  redis_epoch=redis_time,
                  local_epoch=local_time,
                  drift_ms=abs(redis_time - local_time) * 1000)

  This catches gross clock misconfigurations (container with wrong timezone,
  host without NTP, etc.).

  This is a startup check only — not continuous monitoring.
  Continuous NTP monitoring is an OS-level concern, not application-level.


LATENCY MEASUREMENT SEMANTICS
══════════════════════════════

Pipeline latency is measured using MONOTONIC clocks, not wall clocks.

  For within-service measurements:
    start = time.perf_counter_ns()
    ... do work ...
    elapsed_us = (time.perf_counter_ns() - start) / 1000
    
    perf_counter_ns is monotonic — not affected by NTP adjustments.

  For cross-service measurements:
    Use received_at_us (set once in ingestion, propagated through pipeline).
    At each stage: e2e_latency_us = time.time() * 1_000_000 - received_at_us
    
    This uses wall clock (time.time) which IS affected by NTP adjustments.
    Acceptable because NTP adjustments are < 1ms (slew mode), and our
    latency budget is 25ms. NTP jitter is noise, not signal.


EXCHANGE TIME VS SYSTEM TIME DIVERGENCE
═══════════════════════════════════════

Broker WS may deliver ticks with exchange_timestamp_ms that is:
  - In the past (stale tick, queued on broker side)
  - In the future (exchange clock ahead of our clock)

Policy:
  If exchange_timestamp_ms is > 5 seconds in the future:
    → log.warning("exchange_time_ahead", drift_ms=<value>)
    → process normally (exchange is authoritative)
  
  If exchange_timestamp_ms is > 30 seconds in the past:
    → log.warning("stale_tick", age_ms=<value>)
    → process normally BUT flag as stale in NormalizedTick
    → feature engine can choose to skip stale ticks
    
  This handles broker reconnects that deliver a backlog of old ticks.
```

### 3.5 OHLC Bar Time Boundaries

```
Bar boundaries use exchange_timestamp_ms, NOT system time.

  1-minute bar for 09:15 covers:
    exchange_timestamp_ms >= 09:15:00.000 IST (in UTC epoch)
    exchange_timestamp_ms <  09:16:00.000 IST (in UTC epoch)

  Why exchange time, not system time:
    If our system is 200ms slow, ticks for 09:15:59.900 arrive at 09:16:00.100.
    Using system time would incorrectly place these in the 09:16 bar.
    Exchange time puts them correctly in 09:15.
```

---

## 4. Structured Error Taxonomy

### 4.1 The Problem

Without a standardized error classification, every service invents its own
error handling. Retry logic is inconsistent. Log messages use different fields.
Debugging requires reading code to understand what each error means.
Alert rules can't be written generically across services.

### 4.2 Error Category Hierarchy

```
INFUSION ERROR TAXONOMY
═══════════════════════

ErrorCategory (top level — determines retry behavior)
├── TRANSIENT
│   Retry: immediately, up to 3 times
│   Examples: Redis momentary timeout, PG connection pool exhausted
│   Recovery: usually self-resolves within seconds
│
├── RETRYABLE
│   Retry: with exponential backoff (1s, 5s, 30s)
│   Examples: Broker WS disconnect, NSE 429, PG replication lag
│   Recovery: may take seconds to minutes
│
├── FATAL
│   Retry: NO — service should log, alert, and crash/restart
│   Examples: Out of memory, assertion failure, corrupted state
│   Recovery: requires restart or operator intervention
│
├── MALFORMED_DATA
│   Retry: NO — data won't fix itself, send to DLQ
│   Examples: msgpack decode failure, schema version unknown,
│             negative price, NaN in feature vector
│   Recovery: operator inspects DLQ, fixes producer
│
├── INFRASTRUCTURE
│   Retry: with backoff, alert operator
│   Examples: Redis down, PG down, Docker network partition
│   Recovery: infrastructure team fixes
│
├── BROKER
│   Retry: with backoff, may require re-auth
│   Examples: WS disconnect, auth expired, API rate limit,
│             broker server error
│   Recovery: reconnect, re-authenticate, or wait
│
└── DOWNSTREAM_OVERLOAD
    Retry: backpressure — slow down, don't retry failed message
    Examples: Consumer lag > threshold, XADD backpressure,
              Telegram API 429
    Recovery: reduce input rate, scale consumer, wait
```

### 4.3 Error Envelope

Every error logged by any Infusion service uses this structured format:

```python
# libs/infusion-common/src/infusion_common/errors.py

from enum import StrEnum
from dataclasses import dataclass


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    RETRYABLE = "retryable"
    FATAL = "fatal"
    MALFORMED_DATA = "malformed_data"
    INFRASTRUCTURE = "infrastructure"
    BROKER = "broker"
    DOWNSTREAM_OVERLOAD = "downstream_overload"


class ErrorSource(StrEnum):
    REDIS = "redis"
    POSTGRES = "postgres"
    BROKER_WS = "broker_ws"
    BROKER_API = "broker_api"
    NSE = "nse"
    TELEGRAM = "telegram"
    GEMINI = "gemini"
    INTERNAL = "internal"
    CODEC = "codec"
    VALIDATION = "validation"


@dataclass(frozen=True, slots=True)
class InfusionError:
    """Structured error for consistent logging and handling."""

    category: ErrorCategory
    source: ErrorSource
    message: str
    original_exception: Exception | None = None
    context: dict | None = None  # arbitrary key-value context

    def to_log_dict(self) -> dict:
        """Fields to include in structured log output."""
        d = {
            "error_category": self.category.value,
            "error_source": self.source.value,
            "error_message": self.message,
        }
        if self.context:
            d["error_context"] = self.context
        if self.original_exception:
            d["error_type"] = type(self.original_exception).__name__
        return d
```

### 4.4 Error Classification Rules

```
CLASSIFICATION MATRIX
═════════════════════

Exception Type                           → Category            → Source
────────────────────────────────────────   ──────────────────   ──────────
redis.ConnectionError                    → INFRASTRUCTURE      → REDIS
redis.TimeoutError                       → TRANSIENT           → REDIS
redis.ResponseError("OOM")              → FATAL               → REDIS
redis.ResponseError("BUSYGROUP")        → TRANSIENT           → REDIS

asyncpg.ConnectionDoesNotExistError      → INFRASTRUCTURE      → POSTGRES
asyncpg.TooManyConnectionsError          → TRANSIENT           → POSTGRES
asyncpg.DataError                        → MALFORMED_DATA      → POSTGRES
asyncpg.InterfaceError                   → RETRYABLE           → POSTGRES

aiohttp.WSServerHandshakeError           → BROKER              → BROKER_WS
aiohttp.ClientConnectionError            → BROKER              → BROKER_WS
aiohttp.ClientResponseError(status=401)  → BROKER              → BROKER_API
aiohttp.ClientResponseError(status=429)  → DOWNSTREAM_OVERLOAD → BROKER_API

msgpack.UnpackError                      → MALFORMED_DATA      → CODEC
msgpack.PackOverflowError                → MALFORMED_DATA      → CODEC

pydantic.ValidationError                 → MALFORMED_DATA      → VALIDATION
SchemaVersionError                       → MALFORMED_DATA      → CODEC

ValueError (in feature computation)      → MALFORMED_DATA      → INTERNAL
ZeroDivisionError                        → MALFORMED_DATA      → INTERNAL
MemoryError                              → FATAL               → INTERNAL
AssertionError                           → FATAL               → INTERNAL
KeyboardInterrupt                        → FATAL               → INTERNAL

requests.HTTPError(403) from NSE         → RETRYABLE           → NSE
requests.HTTPError(429) from NSE         → DOWNSTREAM_OVERLOAD → NSE
requests.ConnectionError from NSE        → RETRYABLE           → NSE

httpx.HTTPStatusError(429) from Telegram → DOWNSTREAM_OVERLOAD → TELEGRAM
httpx.TimeoutException from Gemini       → RETRYABLE           → GEMINI

CATCH-ALL: Unknown exception             → RETRYABLE           → INTERNAL
```

### 4.5 Error Classifier Function

```python
# libs/infusion-common/src/infusion_common/errors.py (continued)


def classify_error(exception: Exception) -> InfusionError:
    """Classify an exception into the Infusion error taxonomy."""

    exc_type = type(exception).__name__
    msg = str(exception)

    # Redis errors
    if "redis" in exc_type.lower() or "Redis" in exc_type:
        if "timeout" in msg.lower():
            return InfusionError(ErrorCategory.TRANSIENT, ErrorSource.REDIS, msg, exception)
        if "OOM" in msg:
            return InfusionError(ErrorCategory.FATAL, ErrorSource.REDIS, msg, exception)
        if "BUSYGROUP" in msg:
            return InfusionError(ErrorCategory.TRANSIENT, ErrorSource.REDIS, msg, exception)
        return InfusionError(ErrorCategory.INFRASTRUCTURE, ErrorSource.REDIS, msg, exception)

    # Postgres errors
    if "asyncpg" in exc_type.lower() or "Postgres" in exc_type:
        if "too many" in msg.lower():
            return InfusionError(ErrorCategory.TRANSIENT, ErrorSource.POSTGRES, msg, exception)
        if "data" in exc_type.lower():
            return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.POSTGRES, msg, exception)
        return InfusionError(ErrorCategory.INFRASTRUCTURE, ErrorSource.POSTGRES, msg, exception)

    # Codec errors
    if isinstance(exception, (msgpack.UnpackError, SchemaVersionError)):
        return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.CODEC, msg, exception)

    # Validation errors
    if "ValidationError" in exc_type:
        return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.VALIDATION, msg, exception)

    # Fatal errors
    if isinstance(exception, (MemoryError, AssertionError)):
        return InfusionError(ErrorCategory.FATAL, ErrorSource.INTERNAL, msg, exception)

    # Data errors
    if isinstance(exception, (ValueError, ZeroDivisionError)):
        return InfusionError(ErrorCategory.MALFORMED_DATA, ErrorSource.INTERNAL, msg, exception)

    # Default: retryable
    return InfusionError(ErrorCategory.RETRYABLE, ErrorSource.INTERNAL, msg, exception)
```

### 4.6 Structured Error Logging

Every caught error is logged with the full error envelope:

```python
import structlog
from infusion_common.errors import classify_error

logger = structlog.get_logger()

try:
    await process_message(data)
except Exception as e:
    err = classify_error(e)
    logger.log(
        "error" if err.category != ErrorCategory.FATAL else "critical",
        "processing_failed",
        **err.to_log_dict(),
        message_id=msg_id,
        symbol=symbol,
    )
```

Output:

```json
{
  "timestamp": "2026-05-27T09:15:30.123Z",
  "level": "error",
  "service": "feature-engine",
  "event": "processing_failed",
  "error_category": "malformed_data",
  "error_source": "codec",
  "error_message": "Unknown schema: normalized_tick v3",
  "error_type": "SchemaVersionError",
  "message_id": "1716789012345-0",
  "symbol": "RELIANCE"
}
```

### 4.7 Retry Behavior Summary

| Category | Retry | Backoff | Max Attempts | On Exhaust |
|---|---|---|---|---|
| `TRANSIENT` | Yes | None (immediate) | 3 | DLQ |
| `RETRYABLE` | Yes | 1s → 5s → 30s | 3 | DLQ + alert |
| `FATAL` | No | — | — | Crash + restart + alert |
| `MALFORMED_DATA` | No | — | — | DLQ (immediate) |
| `INFRASTRUCTURE` | Yes | 1s → 5s → 30s | 5 | Log critical + health=unhealthy |
| `BROKER` | Yes | ExponentialBackoff (1-30s) | Unlimited (it's a connection) | Reconnect loop |
| `DOWNSTREAM_OVERLOAD` | Wait | Backpressure (slow down) | N/A | Reduce batch size |

---

## Addendum Application

These four policies are **system-wide invariants**. They apply to every service,
every stream message, every error path, and every timestamp in the system.

All subsequent implementation phases (Phase 2 Signal Pipeline onward) must
conform to these policies. They are referenced by, but not duplicated in,
per-phase implementation documents.

| Policy | Primary Implementation Location |
|---|---|
| Schema Versioning | `libs/infusion-models/src/infusion_models/schema_registry.py` |
| Event Type Registry | `libs/infusion-models/src/infusion_models/events.py` |
| DLQ Consumer | `libs/infusion-streams/src/infusion_streams/consumer.py` |
| DLQ Scripts | `scripts/inspect_dlq.py`, `scripts/replay_dlq.py` |
| Clock Policy | Enforced per-adapter in ingestion, per-consumer everywhere else |
| Error Taxonomy | `libs/infusion-common/src/infusion_common/errors.py` |
| Error Classifier | `libs/infusion-common/src/infusion_common/errors.py` |

**Next:** `IMPLEMENTATION-PHASE-2-SIGNAL-PIPELINE.md` — First vertical slice
with real data flowing through the pipeline.
