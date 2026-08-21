# IMPLEMENTATION PHASE 2 — SIGNAL PIPELINE

> First end-to-end realtime vertical slice.
> Real ticks → normalized → features → visible in Redis → visible in API/dashboard.
> All decisions conform to [Global Architecture Constraints](./GLOBAL-ARCHITECTURE-CONSTRAINTS.md)
> and [Phase 1A Architecture Addendum](./IMPLEMENTATION-PHASE-1A-ARCHITECTURE-ADDENDUM.md).

---

## 1. Overview

### 1.1 What This Phase Delivers

A working pipeline where:
1. Broker WebSocket connects and receives live market ticks
2. Ticks are decoded, normalized, and enriched with symbol metadata
3. Feature engine computes 30+ technical features in realtime
4. All data is visible in Redis (hot state) and queryable via API
5. WS gateway pushes price updates to browser clients

### 1.2 Services Built in This Phase

| Service | Memory | Role |
|---|---|---|
| `ingestion` | 50MB | Broker WS → `tick:raw` stream |
| `normalizer` | 30MB | `tick:raw` → `tick:normalized` stream |
| `feature-engine` | 500MB–1GB | `tick:normalized` → `feature:computed` stream |
| `ws-gateway` | 50MB | Stream fan-out → browser WebSocket |
| `api` | 100MB | FastAPI REST endpoints |

### 1.3 Out of Scope

- NSE scraper (Phase 3)
- Scanner / conviction engine (Phase 3)
- AI worker / alerter / Telegram (Phase 4)
- Dashboard frontend (Phase 5)

### 1.4 Success Criteria

```
✓ docker compose up starts all Phase 2 services without error
✓ Ingestion connects to broker WS and receives ticks
✓ Normalized ticks appear in infusion:stream:tick:normalized within 2ms of raw
✓ Feature vectors appear in infusion:stream:feature:computed within 10ms of normalized tick
✓ HGET infusion:tick:RELIANCE returns current LTP within 5s of market data
✓ HGET infusion:feature:RELIANCE returns current feature vector
✓ GET /api/health returns 200 with all services healthy
✓ GET /api/features/RELIANCE returns current feature vector as JSON
✓ WS connection to ws://localhost:8080/ws receives price updates
✓ Pipeline survives broker WS disconnect + reconnect without data loss
✓ No memory growth > 10% over 1 hour of market operation
✓ All errors are classified per error taxonomy and logged with structured fields
✓ Malformed messages are DLQ'd, not dropped silently
```

---

## 2. Shared Library Implementation

These libraries are built FIRST. Every service depends on them.

### 2.1 infusion-models — Data Contracts

```
libs/infusion-models/
├── pyproject.toml
└── src/
    └── infusion_models/
        ├── __init__.py
        ├── events.py          ← EventType enum
        ├── schema_registry.py ← version registry
        ├── tick.py             ← RawTickV1, NormalizedTickV1
        ├── feature.py          ← FeatureVectorV1
        ├── signal.py           ← ScanSignalV1 (stub for Phase 3)
        ├── sector.py           ← SectorStateV1 (stub for Phase 3)
        └── health.py           ← HealthStatus model
```

#### pyproject.toml

```toml
[project]
name = "infusion-models"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "msgpack>=1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/infusion_models"]
```

#### tick.py — Tick Data Models

```python
# libs/infusion-models/src/infusion_models/tick.py

from pydantic import BaseModel, Field


class RawTickV1(BaseModel, frozen=True):
    """Broker-specific tick. Output of ingestion adapter."""

    broker: str  # "upstox" | "kite"
    instrument_key: str  # Broker-specific identifier
    exchange: str  # "NSE" | "BSE"
    segment: str  # "EQ" | "FO" | "INDEX"
    ltp: float
    open: float
    high: float
    low: float
    close: float  # Previous day close
    volume: int
    oi: int = 0  # 0 for non-F&O
    total_buy_qty: int = 0
    total_sell_qty: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_qty: int = 0
    best_ask_qty: int = 0
    exchange_timestamp_ms: int  # UTC epoch milliseconds (authoritative)
    received_at_us: int  # Local receipt epoch microseconds


class NormalizedTickV1(BaseModel, frozen=True):
    """Universal tick. Output of normalizer."""

    symbol: str  # "RELIANCE", "INFY"
    sector_id: str  # "NIFTY_BANK", "NIFTY_IT", "UNCATEGORIZED"
    is_fno: bool
    tier: int  # 1, 2, or 3
    ltp: float
    open: float
    high: float
    low: float
    close: float  # Previous day close
    volume: int
    oi: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    best_bid_qty: int = 0
    best_ask_qty: int = 0
    exchange_timestamp_ms: int
    received_at_us: int
    normalized_at_us: int
```

#### feature.py — Feature Vector Model

```python
# libs/infusion-models/src/infusion_models/feature.py

from pydantic import BaseModel


class FeatureVectorV1(BaseModel, frozen=True):
    """Computed features for a single symbol at a point in time."""

    symbol: str
    timestamp_us: int  # when features were computed

    # Price
    ltp: float
    vwap: float = 0.0
    gap_pct: float = 0.0  # (open - prev_close) / prev_close * 100
    day_high: float = 0.0
    day_low: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0  # (ltp - prev_close) / prev_close * 100

    # Moving averages
    ema_5: float = 0.0
    ema_9: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0

    # Volatility
    atr_14: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0

    # Momentum
    rsi_14: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    stochastic_k: float = 50.0
    stochastic_d: float = 50.0
    cci_20: float = 0.0

    # Volume
    rel_vol_20d: float = 1.0  # current volume / 20-day avg volume
    obv: float = 0.0
    volume_sma_20: float = 0.0

    # Microstructure
    spread_bps: float = 0.0  # (ask - bid) / mid * 10000
    order_imbalance: float = 0.0  # (buy_qty - sell_qty) / (buy_qty + sell_qty)
    delivery_pct: float = 0.0  # from NSE data (when available)

    # ML features (free-form, for future model iteration)
    ml_features: dict = {}
```

#### health.py — Health Model

```python
# libs/infusion-models/src/infusion_models/health.py

from pydantic import BaseModel
from enum import StrEnum


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ServiceHealth(BaseModel):
    service: str
    state: HealthState
    uptime_sec: float
    details: dict = {}
```

### 2.2 infusion-streams — Redis Stream Abstraction

```
libs/infusion-streams/
├── pyproject.toml
└── src/
    └── infusion_streams/
        ├── __init__.py
        ├── codec.py           ← encode/decode with versioned envelope
        ├── producer.py        ← StreamProducer (XADD wrapper)
        ├── consumer.py        ← StreamConsumer with DLQ support
        └── constants.py       ← stream names, consumer group names
```

#### pyproject.toml

```toml
[project]
name = "infusion-streams"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "redis[hiredis]>=5.0",
    "msgpack>=1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/infusion_streams"]
```

#### constants.py — Stream Names

```python
# libs/infusion-streams/src/infusion_streams/constants.py

# ═══════════════════════════════════════════════════
# Primary Streams
# ═══════════════════════════════════════════════════
STREAM_TICK_RAW = "infusion:stream:tick:raw"
STREAM_TICK_NORMALIZED = "infusion:stream:tick:normalized"
STREAM_FEATURE_COMPUTED = "infusion:stream:feature:computed"
STREAM_SCAN_SIGNALS = "infusion:stream:scan:signals"
STREAM_SECTOR_STATE = "infusion:stream:sector:state"
STREAM_CONVICTION_RANKED = "infusion:stream:conviction:ranked"

# ═══════════════════════════════════════════════════
# Consumer Groups
# ═══════════════════════════════════════════════════
CG_NORMALIZER = "normalizer-cg"
CG_FEATURE = "feature-cg"
CG_SCANNER = "scanner-cg"
CG_SECTOR = "sector-cg"
CG_CONVICTION = "conviction-cg"
CG_DASHBOARD = "dashboard-cg"

# ═══════════════════════════════════════════════════
# DLQ Streams
# ═══════════════════════════════════════════════════
DLQ_PREFIX = "infusion:dlq:"

# ═══════════════════════════════════════════════════
# MAXLEN Limits (approximate)
# ═══════════════════════════════════════════════════
MAXLEN_TICK_RAW = 50_000
MAXLEN_TICK_NORMALIZED = 100_000
MAXLEN_FEATURE_COMPUTED = 50_000
MAXLEN_SIGNALS = 10_000
MAXLEN_SECTOR_STATE = 10_000
MAXLEN_CONVICTION = 10_000
MAXLEN_DLQ = 1_000

# ═══════════════════════════════════════════════════
# Hot State Keys
# ═══════════════════════════════════════════════════
KEY_TICK_PREFIX = "infusion:tick:"  # + {symbol}  → HASH
KEY_FEATURE_PREFIX = "infusion:feature:"  # + {symbol}  → HASH
KEY_OHLC_PREFIX = "infusion:ohlc:"  # + {symbol}:{tf} → ZSET
KEY_HEALTH_PREFIX = "infusion:health:"  # + {service} → STRING
KEY_SYMBOLS = "infusion:symbols"  # HASH: instrument_key → symbol metadata
KEY_AUTH_UPSTOX = "infusion:auth:upstox"  # STRING: access_token
```

#### codec.py — Versioned Envelope Codec

```python
# libs/infusion-streams/src/infusion_streams/codec.py

import time
import msgpack
from infusion_models.events import EventType
from infusion_models.schema_registry import CURRENT_VERSIONS


def encode_event(
    event_type: EventType,
    payload: dict,
    received_at_us: int,
) -> bytes:
    """Encode a stream event with versioned envelope."""
    envelope = {
        "v": CURRENT_VERSIONS[event_type],
        "t": event_type.value,
        "ts": int(time.time() * 1_000_000),
        "rx": received_at_us,
        "d": payload,
    }
    return msgpack.packb(envelope, use_bin_type=True)


def decode_event(raw: bytes) -> tuple[EventType, int, int, int, dict]:
    """
    Decode stream event.
    Returns: (event_type, version, created_at_us, received_at_us, payload)
    """
    envelope = msgpack.unpackb(raw, raw=False)
    return (
        EventType(envelope["t"]),
        envelope["v"],
        envelope["ts"],
        envelope["rx"],
        envelope["d"],
    )
```

#### producer.py — Stream Producer

```python
# libs/infusion-streams/src/infusion_streams/producer.py

import structlog
from redis.asyncio import Redis
from infusion_streams.codec import encode_event
from infusion_models.events import EventType

logger = structlog.get_logger()


class StreamProducer:
    """Publishes events to a Redis stream with versioned envelope."""

    def __init__(self, redis: Redis, stream: str, maxlen: int):
        self.redis = redis
        self.stream = stream
        self.maxlen = maxlen
        self._count = 0

    async def publish(
        self,
        event_type: EventType,
        payload: dict,
        received_at_us: int,
    ) -> str:
        """Encode and XADD to stream. Returns message ID."""
        encoded = encode_event(event_type, payload, received_at_us)
        msg_id = await self.redis.xadd(
            self.stream,
            {"data": encoded},
            maxlen=self.maxlen,
            approximate=True,
        )
        self._count += 1
        return msg_id

    async def publish_raw(self, data: bytes) -> str:
        """XADD pre-encoded bytes (for hot-path optimization)."""
        msg_id = await self.redis.xadd(
            self.stream,
            {"data": data},
            maxlen=self.maxlen,
            approximate=True,
        )
        self._count += 1
        return msg_id

    @property
    def published_count(self) -> int:
        return self._count
```

#### consumer.py — Stream Consumer with DLQ

```python
# libs/infusion-streams/src/infusion_streams/consumer.py

import asyncio
import base64
import time
import traceback
from typing import Callable, Awaitable

import msgpack
import structlog
from redis.asyncio import Redis

from infusion_streams.codec import decode_event
from infusion_streams.constants import DLQ_PREFIX, MAXLEN_DLQ
from infusion_common.errors import classify_error, ErrorCategory

logger = structlog.get_logger()


class StreamConsumer:
    """
    Redis Stream consumer with consumer group semantics and DLQ support.

    Usage:
        consumer = StreamConsumer(redis, stream, group, name)
        await consumer.ensure_group()
        async for event_type, version, payload, ack in consumer.consume():
            process(payload)
            await ack()
    """

    def __init__(
        self,
        redis: Redis,
        stream: str,
        group: str,
        consumer_name: str,
        batch_size: int = 100,
        block_ms: int = 5,
        max_retries: int = 3,
    ):
        self.redis = redis
        self.stream = stream
        self.group = group
        self.consumer_name = consumer_name
        self.batch_size = batch_size
        self.block_ms = block_ms
        self.max_retries = max_retries
        self.dlq_stream = DLQ_PREFIX + stream.replace("infusion:stream:", "")
        self._retry_counts: dict[str, int] = {}
        self._processed = 0
        self._errors = 0

    async def ensure_group(self):
        """Create consumer group if it doesn't exist."""
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
            logger.info("consumer_group_created", stream=self.stream, group=self.group)
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass  # Group already exists
            else:
                raise

    async def consume(self):
        """
        Async generator yielding (event_type, version, rx_us, payload, ack_fn).

        Caller processes the payload, then calls ack_fn() on success.
        On failure, caller should NOT call ack_fn — the message stays in PEL.
        """
        while True:
            try:
                messages = await self.redis.xreadgroup(
                    groupname=self.group,
                    consumername=self.consumer_name,
                    streams={self.stream: ">"},
                    count=self.batch_size,
                    block=self.block_ms,
                )
            except Exception as e:
                err = classify_error(e)
                logger.error("stream_read_error", **err.to_log_dict())
                await asyncio.sleep(1)
                continue

            if not messages:
                continue

            for stream_name, entries in messages:
                for msg_id, fields in entries:
                    raw_data = fields.get(b"data") or fields.get("data")
                    if raw_data is None:
                        await self._send_to_dlq(
                            msg_id, b"", "MALFORMED_DATA", "Missing 'data' field"
                        )
                        await self.redis.xack(self.stream, self.group, msg_id)
                        continue

                    try:
                        event_type, version, ts, rx, payload = decode_event(raw_data)
                    except Exception as e:
                        await self._send_to_dlq(msg_id, raw_data, "MALFORMED_DATA", str(e))
                        await self.redis.xack(self.stream, self.group, msg_id)
                        self._errors += 1
                        continue

                    async def make_ack(mid=msg_id):
                        await self.redis.xack(self.stream, self.group, mid)
                        self._processed += 1

                    yield event_type, version, rx, payload, make_ack

    async def handle_with_retry(
        self,
        msg_id: str,
        raw_data: bytes,
        handler: Callable,
    ) -> bool:
        """Process message with retry + DLQ. Returns True on success."""
        try:
            await handler(raw_data)
            self._retry_counts.pop(msg_id, None)
            self._processed += 1
            return True
        except Exception as e:
            err = classify_error(e)
            if err.category == ErrorCategory.MALFORMED_DATA:
                await self._send_to_dlq(msg_id, raw_data, "MALFORMED_DATA", str(e))
                return False
            if err.category == ErrorCategory.FATAL:
                await self._send_to_dlq(msg_id, raw_data, "FATAL", str(e))
                raise
            # Retryable
            count = self._retry_counts.get(msg_id, 0) + 1
            self._retry_counts[msg_id] = count
            if count >= self.max_retries:
                await self._send_to_dlq(msg_id, raw_data, err.category.value, str(e))
                self._retry_counts.pop(msg_id, None)
                return False
            self._errors += 1
            return False

    async def _send_to_dlq(self, msg_id, raw_data: bytes, category: str, reason: str):
        """Move poison message to dead letter stream."""
        dlq_entry = {
            "original_stream": self.stream,
            "original_id": msg_id if isinstance(msg_id, str) else msg_id.decode(),
            "original_payload": base64.b64encode(raw_data).decode() if raw_data else "",
            "consumer_group": self.group,
            "consumer_name": self.consumer_name,
            "failure_category": category,
            "failure_reason": reason,
            "retry_count": self._retry_counts.get(msg_id, 0),
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stack_trace": traceback.format_exc(),
        }
        try:
            await self.redis.xadd(
                self.dlq_stream,
                {"data": msgpack.packb(dlq_entry)},
                maxlen=MAXLEN_DLQ,
                approximate=True,
            )
            logger.error(
                "message_dlq",
                message_id=msg_id,
                stream=self.stream,
                category=category,
                reason=reason,
            )
        except Exception as dlq_err:
            logger.critical(
                "dlq_write_failed",
                original_error=reason,
                dlq_error=str(dlq_err),
            )

    @property
    def stats(self) -> dict:
        return {
            "processed": self._processed,
            "errors": self._errors,
            "pending_retries": len(self._retry_counts),
        }
```

### 2.3 infusion-common — Shared Utilities

```
libs/infusion-common/
├── pyproject.toml
└── src/
    └── infusion_common/
        ├── __init__.py
        ├── config.py          ← InfusionSettings base class
        ├── logging.py         ← structlog setup
        ├── errors.py          ← Error taxonomy (from Phase 1A addendum)
        ├── timing.py          ← measure_latency decorator
        ├── health.py          ← Health reporter
        └── lifecycle.py       ← Graceful shutdown, signal handling
```

#### pyproject.toml

```toml
[project]
name = "infusion-common"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "pydantic-settings>=2.3",
    "structlog>=24.0",
    "redis[hiredis]>=5.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/infusion_common"]
```

#### config.py — Base Settings

```python
# libs/infusion-common/src/infusion_common/config.py

from pydantic_settings import BaseSettings


class InfusionSettings(BaseSettings):
    """Base configuration for all Infusion services."""

    # Service identity
    service_name: str = "unknown"
    environment: str = "development"  # development | staging | production

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL
    postgres_dsn: str = "postgresql://infusion:infusion@localhost:5432/infusion"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json | console

    # Health
    health_interval_sec: int = 10
    health_ttl_sec: int = 30

    model_config = {"env_prefix": "INFUSION_", "env_file": ".env"}
```

#### logging.py — Structured Logging Setup

```python
# libs/infusion-common/src/infusion_common/logging.py

import structlog
import logging
import sys


def setup_logging(service_name: str, level: str = "INFO", fmt: str = "json"):
    """Configure structlog for all Infusion services."""

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bind service name globally
    structlog.contextvars.bind_contextvars(service=service_name)
```

#### timing.py — Latency Measurement

```python
# libs/infusion-common/src/infusion_common/timing.py

import time
import functools
import structlog

logger = structlog.get_logger()


def now_us() -> int:
    """Current UTC epoch in microseconds."""
    return int(time.time() * 1_000_000)


def measure_latency(func):
    """Decorator that logs function execution time in microseconds."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter_ns()
        result = await func(*args, **kwargs)
        elapsed_us = (time.perf_counter_ns() - start) / 1_000
        logger.debug(
            "latency",
            function=func.__name__,
            elapsed_us=round(elapsed_us, 1),
        )
        return result

    return wrapper
```

#### lifecycle.py — Graceful Shutdown

```python
# libs/infusion-common/src/infusion_common/lifecycle.py

import asyncio
import signal
import structlog

logger = structlog.get_logger()


class ServiceLifecycle:
    """Manages graceful startup and shutdown for Infusion services."""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self._shutdown_event = asyncio.Event()
        self._cleanup_tasks: list = []

    @property
    def should_run(self) -> bool:
        return not self._shutdown_event.is_set()

    def register_cleanup(self, coro_fn):
        """Register an async cleanup function to call on shutdown."""
        self._cleanup_tasks.append(coro_fn)

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop):
        """Install SIGTERM/SIGINT handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal, sig)

    def _handle_signal(self, sig):
        logger.info("shutdown_signal", signal=sig.name, service=self.service_name)
        self._shutdown_event.set()

    async def wait_for_shutdown(self):
        """Block until shutdown is requested."""
        await self._shutdown_event.wait()

    async def cleanup(self):
        """Run all registered cleanup tasks."""
        logger.info("cleanup_start", service=self.service_name)
        for task in reversed(self._cleanup_tasks):
            try:
                await task()
            except Exception as e:
                logger.error("cleanup_error", error=str(e))
        logger.info("cleanup_complete", service=self.service_name)
```

#### health.py — Health Reporter

```python
# libs/infusion-common/src/infusion_common/health.py

import asyncio
import time
import structlog
from redis.asyncio import Redis
from infusion_streams.constants import KEY_HEALTH_PREFIX

logger = structlog.get_logger()


class HealthReporter:
    """Publishes service health heartbeats to Redis."""

    def __init__(
        self,
        redis: Redis,
        service_name: str,
        interval_sec: int = 10,
        ttl_sec: int = 30,
    ):
        self.redis = redis
        self.service_name = service_name
        self.interval_sec = interval_sec
        self.ttl_sec = ttl_sec
        self._start_time = time.time()
        self._details_fn = None
        self._task: asyncio.Task | None = None

    def set_details_fn(self, fn):
        """Set a callable that returns extra health details dict."""
        self._details_fn = fn

    async def start(self):
        """Start background health reporting loop."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while True:
            try:
                details = {}
                if self._details_fn:
                    details = self._details_fn()

                payload = {
                    "status": "healthy",
                    "uptime_sec": round(time.time() - self._start_time, 1),
                    **details,
                }

                import msgpack

                key = f"{KEY_HEALTH_PREFIX}{self.service_name}"
                await self.redis.set(
                    key,
                    msgpack.packb(payload),
                    ex=self.ttl_sec,
                )
            except Exception as e:
                logger.warning("health_report_failed", error=str(e))

            await asyncio.sleep(self.interval_sec)
```

---

## 3. Ingestion Service

### 3.1 Service Structure

```
services/ingestion/
├── pyproject.toml
├── Dockerfile
└── src/
    └── ingestion/
        ├── __init__.py
        ├── main.py              ← entry point
        ├── config.py            ← IngestionSettings
        ├── supervisor.py        ← connection state machine
        ├── adapter_factory.py   ← broker selection
        ├── adapters/
        │   ├── __init__.py
        │   ├── base.py          ← BrokerAdapter ABC
        │   ├── upstox.py        ← Upstox protobuf adapter
        │   └── mock.py          ← Mock adapter for testing
        └── publisher.py         ← tick → tick:raw stream
```

### 3.2 Config

```python
# services/ingestion/src/ingestion/config.py

from infusion_common.config import InfusionSettings


class IngestionSettings(InfusionSettings):
    service_name: str = "ingestion"

    # Broker
    broker_primary: str = "upstox"  # "upstox" | "kite" | "mock"
    broker_secondary: str = ""

    # Upstox
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "http://localhost:5000/callback"
    upstox_access_token: str = ""  # set via env or Redis

    # Mock adapter
    mock_symbols: int = 50  # number of symbols to simulate
    mock_tick_rate_hz: int = 100  # ticks per second

    # Connection
    ws_ping_interval_sec: int = 30
    ws_ping_timeout_sec: int = 10
    reconnect_base_sec: float = 1.0
    reconnect_max_sec: float = 30.0
    reconnect_jitter_pct: float = 0.20

    # Subscription
    subscribe_batch_size: int = 100
    subscribe_batch_delay_ms: int = 100
```

### 3.3 Broker Adapter ABC

```python
# services/ingestion/src/ingestion/adapters/base.py

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Callable, Awaitable
from infusion_models.tick import RawTickV1


class ConnectionState(StrEnum):
    INIT = "init"
    AUTHENTICATING = "authenticating"
    CONNECTING = "connecting"
    SUBSCRIBING = "subscribing"
    STREAMING = "streaming"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class BrokerAdapter(ABC):
    """Abstract base for broker-specific WebSocket adapters."""

    name: str
    state: ConnectionState = ConnectionState.INIT

    @abstractmethod
    async def authenticate(self) -> str:
        """Broker-specific auth. Returns access token."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish WebSocket connection."""
        ...

    @abstractmethod
    async def subscribe(self, instrument_keys: list[str]) -> None:
        """Subscribe to market data for instruments."""
        ...

    @abstractmethod
    async def start_streaming(self, on_tick: Callable[[RawTickV1], Awaitable[None]]) -> None:
        """Begin receiving ticks. Calls on_tick for each decoded tick."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Graceful close."""
        ...

    @abstractmethod
    def health(self) -> dict:
        """Return health status dict."""
        ...
```

### 3.4 Upstox Adapter

```python
# services/ingestion/src/ingestion/adapters/upstox.py
# Key implementation patterns (not complete code — exact signatures and flow)

import asyncio
import gzip
import time
import aiohttp
import structlog

from ingestion.adapters.base import BrokerAdapter, ConnectionState
from infusion_models.tick import RawTickV1
from infusion_common.timing import now_us

logger = structlog.get_logger()

# Import compiled protobuf module (from Upstox SDK proto definitions)
# from ingestion.proto import MarketDataFeed_pb2


class UpstoxAdapter(BrokerAdapter):
    name = "upstox"

    def __init__(self, config):
        self.config = config
        self.state = ConnectionState.INIT
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._access_token: str = ""
        self._tick_count = 0
        self._last_tick_time = 0.0
        self._reconnect_count = 0

    async def authenticate(self) -> str:
        """Get access token from config or Redis."""
        self.state = ConnectionState.AUTHENTICATING
        self._access_token = self.config.upstox_access_token
        if not self._access_token:
            raise RuntimeError("Upstox access token not configured")
        logger.info("upstox_authenticated")
        return self._access_token

    async def connect(self) -> None:
        """Get authorized WS URI and connect."""
        self.state = ConnectionState.CONNECTING

        self._session = aiohttp.ClientSession()

        # Step 1: Get authorized redirect URI
        auth_url = "https://api.upstox.com/v2/feed/market-data-feed/authorize"
        headers = {"Authorization": f"Bearer {self._access_token}"}

        async with self._session.get(auth_url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Upstox WS auth failed: {resp.status}")
            data = await resp.json()
            ws_uri = data["data"]["authorizedRedirectUri"]

        # Step 2: Connect to WebSocket
        self._ws = await self._session.ws_connect(
            ws_uri,
            heartbeat=self.config.ws_ping_interval_sec,
        )
        self.state = ConnectionState.CONNECTING
        logger.info("upstox_ws_connected", uri=ws_uri[:60])

    async def subscribe(self, instrument_keys: list[str]) -> None:
        """Subscribe in batches of 100."""
        self.state = ConnectionState.SUBSCRIBING
        batch_size = self.config.subscribe_batch_size

        for i in range(0, len(instrument_keys), batch_size):
            batch = instrument_keys[i : i + batch_size]
            msg = {
                "guid": f"sub-{i}",
                "method": "sub",
                "data": {
                    "mode": "full",
                    "instrumentKeys": batch,
                },
            }
            await self._ws.send_json(msg)
            logger.info(
                "upstox_subscribed_batch",
                batch=i // batch_size + 1,
                count=len(batch),
            )
            await asyncio.sleep(self.config.subscribe_batch_delay_ms / 1000)

        self.state = ConnectionState.STREAMING
        logger.info("upstox_subscribed_all", total=len(instrument_keys))

    async def start_streaming(self, on_tick) -> None:
        """Read WS frames, decode protobuf, invoke callback."""
        assert self._ws is not None
        self.state = ConnectionState.STREAMING

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                received_at = now_us()
                try:
                    # Upstox sends gzip-compressed protobuf
                    decompressed = gzip.decompress(msg.data)
                    ticks = self._decode_protobuf(decompressed, received_at)
                    for tick in ticks:
                        await on_tick(tick)
                        self._tick_count += 1
                        self._last_tick_time = time.time()
                except Exception as e:
                    logger.warning("tick_decode_error", error=str(e))

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("ws_error", error=str(self._ws.exception()))
                break

            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                logger.warning("ws_closed", close_code=msg.data)
                break

    def _decode_protobuf(self, data: bytes, received_at_us: int) -> list[RawTickV1]:
        """Decode Upstox protobuf into RawTickV1 list."""
        # Placeholder — actual implementation uses compiled proto module
        # feed = MarketDataFeed_pb2.FeedResponse()
        # feed.ParseFromString(data)
        #
        # ticks = []
        # for instrument_key, feed_data in feed.feeds.items():
        #     ff = feed_data.ff.marketFF
        #     tick = RawTickV1(
        #         broker="upstox",
        #         instrument_key=instrument_key,
        #         exchange=instrument_key.split("|")[0].split("_")[0],
        #         segment=instrument_key.split("|")[0].split("_")[1],
        #         ltp=ff.ltpc.ltp,
        #         open=ff.ohlcDay.open,
        #         high=ff.ohlcDay.high,
        #         low=ff.ohlcDay.low,
        #         close=ff.ltpc.cp,
        #         volume=int(ff.volumeTraded),
        #         oi=int(ff.oi),
        #         total_buy_qty=int(ff.totalBuyQty),
        #         total_sell_qty=int(ff.totalSellQty),
        #         best_bid=ff.depth.buy[0].price if ff.depth.buy else 0.0,
        #         best_ask=ff.depth.sell[0].price if ff.depth.sell else 0.0,
        #         best_bid_qty=int(ff.depth.buy[0].quantity) if ff.depth.buy else 0,
        #         best_ask_qty=int(ff.depth.sell[0].quantity) if ff.depth.sell else 0,
        #         exchange_timestamp_ms=ff.lastTradedTimestamp,
        #         received_at_us=received_at_us,
        #     )
        #     ticks.append(tick)
        # return ticks
        return []

    async def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("upstox_disconnected")

    def health(self) -> dict:
        return {
            "broker": self.name,
            "state": self.state.value,
            "tick_count": self._tick_count,
            "last_tick_age_ms": round((time.time() - self._last_tick_time) * 1000)
            if self._last_tick_time
            else -1,
            "reconnect_count": self._reconnect_count,
        }
```

### 3.5 Mock Adapter (for testing without broker credentials)

```python
# services/ingestion/src/ingestion/adapters/mock.py

import asyncio
import random
import time
from ingestion.adapters.base import BrokerAdapter, ConnectionState
from infusion_models.tick import RawTickV1
from infusion_common.timing import now_us


MOCK_SYMBOLS = [
    ("NSE_EQ|INE002A01018", "NSE", "EQ"),  # RELIANCE
    ("NSE_EQ|INE009A01021", "NSE", "EQ"),  # INFY
    ("NSE_EQ|INE040A01034", "NSE", "EQ"),  # HDFCBANK
    ("NSE_EQ|INE467B01029", "NSE", "EQ"),  # TCS
    ("NSE_INDEX|Nifty 50", "NSE", "INDEX"),
]


class MockAdapter(BrokerAdapter):
    """Generates fake tick data for development/testing."""

    name = "mock"

    def __init__(self, config):
        self.config = config
        self.state = ConnectionState.INIT
        self._prices = {}
        self._tick_count = 0

    async def authenticate(self) -> str:
        self.state = ConnectionState.AUTHENTICATING
        return "mock-token"

    async def connect(self) -> None:
        self.state = ConnectionState.CONNECTING
        # Initialize mock prices
        for key, _, _ in MOCK_SYMBOLS:
            self._prices[key] = round(random.uniform(100, 5000), 2)

    async def subscribe(self, instrument_keys: list[str]) -> None:
        self.state = ConnectionState.SUBSCRIBING
        self.state = ConnectionState.STREAMING

    async def start_streaming(self, on_tick) -> None:
        self.state = ConnectionState.STREAMING
        interval = 1.0 / max(self.config.mock_tick_rate_hz, 1)

        while self.state == ConnectionState.STREAMING:
            for key, exchange, segment in MOCK_SYMBOLS:
                price = self._prices[key]
                # Random walk: ±0.1% per tick
                change = price * random.uniform(-0.001, 0.001)
                price = round(price + change, 2)
                self._prices[key] = price

                tick = RawTickV1(
                    broker="mock",
                    instrument_key=key,
                    exchange=exchange,
                    segment=segment,
                    ltp=price,
                    open=price * 0.99,
                    high=price * 1.01,
                    low=price * 0.98,
                    close=price * 0.995,
                    volume=random.randint(10000, 1000000),
                    oi=0,
                    total_buy_qty=random.randint(5000, 50000),
                    total_sell_qty=random.randint(5000, 50000),
                    best_bid=round(price - 0.05, 2),
                    best_ask=round(price + 0.05, 2),
                    best_bid_qty=random.randint(100, 5000),
                    best_ask_qty=random.randint(100, 5000),
                    exchange_timestamp_ms=int(time.time() * 1000),
                    received_at_us=now_us(),
                )
                await on_tick(tick)
                self._tick_count += 1

            await asyncio.sleep(interval)

    async def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED

    def health(self) -> dict:
        return {
            "broker": "mock",
            "state": self.state.value,
            "tick_count": self._tick_count,
        }
```

### 3.6 Connection Supervisor

```python
# services/ingestion/src/ingestion/supervisor.py

import asyncio
import random
import structlog
from ingestion.adapters.base import BrokerAdapter, ConnectionState
from infusion_common.errors import classify_error, ErrorCategory

logger = structlog.get_logger()


class ConnectionSupervisor:
    """
    Manages adapter lifecycle with automatic reconnection.

    State machine:
      INIT → AUTHENTICATING → CONNECTING → SUBSCRIBING → STREAMING
                                                            │
                                                            ▼ (on disconnect/error)
                                                         RECONNECTING
                                                            │
                                                            ▼ (backoff)
                                                         CONNECTING → ...
    """

    def __init__(
        self,
        adapter: BrokerAdapter,
        instruments: list[str],
        on_tick,
        reconnect_base: float = 1.0,
        reconnect_max: float = 30.0,
        jitter_pct: float = 0.20,
    ):
        self.adapter = adapter
        self.instruments = instruments
        self.on_tick = on_tick
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.jitter_pct = jitter_pct
        self._consecutive_failures = 0
        self._running = True

    async def run(self):
        """Main supervisor loop. Runs until stopped."""
        while self._running:
            try:
                await self.adapter.authenticate()
                await self.adapter.connect()
                await self.adapter.subscribe(self.instruments)
                self._consecutive_failures = 0

                # This blocks until WS disconnects
                await self.adapter.start_streaming(self.on_tick)

                # If we reach here, WS closed normally
                logger.warning("ws_session_ended")

            except Exception as e:
                err = classify_error(e)
                logger.error("supervisor_error", **err.to_log_dict())

                if err.category == ErrorCategory.FATAL:
                    logger.critical("fatal_error_stopping", error=str(e))
                    raise

            finally:
                try:
                    await self.adapter.disconnect()
                except Exception:
                    pass

            if not self._running:
                break

            # Reconnect with exponential backoff
            self._consecutive_failures += 1
            delay = self._calculate_backoff()
            logger.info(
                "reconnecting",
                attempt=self._consecutive_failures,
                delay_sec=round(delay, 2),
            )
            await asyncio.sleep(delay)

    def _calculate_backoff(self) -> float:
        """Exponential backoff with jitter."""
        delay = min(
            self.reconnect_base * (2 ** (self._consecutive_failures - 1)),
            self.reconnect_max,
        )
        jitter = delay * self.jitter_pct * (2 * random.random() - 1)
        return max(0.1, delay + jitter)

    def stop(self):
        self._running = False
```

### 3.7 Publisher (Tick → Stream)

```python
# services/ingestion/src/ingestion/publisher.py

import structlog
from infusion_models.tick import RawTickV1
from infusion_models.events import EventType
from infusion_streams.producer import StreamProducer
from infusion_streams.constants import STREAM_TICK_RAW, MAXLEN_TICK_RAW, KEY_TICK_PREFIX
from redis.asyncio import Redis

logger = structlog.get_logger()


class TickPublisher:
    """Publishes RawTick to tick:raw stream and hot state."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.producer = StreamProducer(redis, STREAM_TICK_RAW, MAXLEN_TICK_RAW)

    async def publish(self, tick: RawTickV1):
        """Publish tick to stream."""
        await self.producer.publish(
            event_type=EventType.RAW_TICK,
            payload=tick.model_dump(),
            received_at_us=tick.received_at_us,
        )
```

### 3.8 Main Entry Point

```python
# services/ingestion/src/ingestion/main.py

import asyncio
import structlog
from redis.asyncio import Redis

from ingestion.config import IngestionSettings
from ingestion.supervisor import ConnectionSupervisor
from ingestion.publisher import TickPublisher
from ingestion.adapters.upstox import UpstoxAdapter
from ingestion.adapters.mock import MockAdapter
from infusion_common.logging import setup_logging
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.health import HealthReporter

logger = structlog.get_logger()


def create_adapter(config: IngestionSettings):
    match config.broker_primary:
        case "upstox":
            return UpstoxAdapter(config)
        case "mock":
            return MockAdapter(config)
        case other:
            raise ValueError(f"Unknown broker: {other}")


async def main():
    config = IngestionSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)

    logger.info("ingestion_starting", broker=config.broker_primary)

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    lifecycle = ServiceLifecycle(config.service_name)

    # Health reporter
    health = HealthReporter(redis, config.service_name)

    # Publisher
    publisher = TickPublisher(redis)

    # Adapter
    adapter = create_adapter(config)

    # Load instrument keys from Redis (or use mock defaults)
    # In production, these come from infusion:symbols populated by Phase 3
    instruments = []
    if config.broker_primary == "mock":
        instruments = [s[0] for s in MockAdapter.MOCK_SYMBOLS if True]  # mock handles internally
    else:
        # Load from Redis symbol master
        symbol_data = await redis.hgetall("infusion:symbols")
        instruments = [k.decode() for k in symbol_data.keys()] if symbol_data else []

    if not instruments and config.broker_primary != "mock":
        logger.warning("no_instruments_configured")

    # Supervisor
    supervisor = ConnectionSupervisor(
        adapter=adapter,
        instruments=instruments,
        on_tick=publisher.publish,
        reconnect_base=config.reconnect_base_sec,
        reconnect_max=config.reconnect_max_sec,
        jitter_pct=config.reconnect_jitter_pct,
    )

    # Set health details
    health.set_details_fn(
        lambda: {
            **adapter.health(),
            "published": publisher.producer.published_count,
        }
    )

    # Cleanup
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(supervisor.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Start
    await health.start()

    try:
        await supervisor.run()
    except KeyboardInterrupt:
        pass
    finally:
        await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
```

### 3.9 Dockerfile

```dockerfile
# services/ingestion/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install shared libs first (cached layer)
COPY libs/ /app/libs/
RUN pip install --no-cache-dir \
    /app/libs/infusion-models \
    /app/libs/infusion-streams \
    /app/libs/infusion-common

# Install service
COPY services/ingestion/ /app/services/ingestion/
RUN pip install --no-cache-dir /app/services/ingestion

CMD ["python", "-m", "ingestion.main"]
```

### 3.10 pyproject.toml

```toml
[project]
name = "ingestion"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "infusion-streams",
    "infusion-common",
    "aiohttp>=3.9",
    "protobuf>=4.25",
    # "curl_cffi>=0.6",  # Future: for NSE TLS fingerprinting
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ingestion"]
```

---

## 4. Normalizer Service

### 4.1 Service Structure

```
services/normalizer/
├── pyproject.toml
├── Dockerfile
└── src/
    └── normalizer/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── resolver.py         ← instrument_key → symbol metadata
        ├── throttler.py        ← tier-based throttling
        ├── dedup.py            ← duplicate tick detection
        └── transformer.py     ← RawTick → NormalizedTick
```

### 4.2 Config

```python
# services/normalizer/src/normalizer/config.py

from infusion_common.config import InfusionSettings


class NormalizerSettings(InfusionSettings):
    service_name: str = "normalizer"

    # Throttling
    tier2_min_interval_ms: int = 500
    tier3_min_interval_ms: int = 2000

    # Dedup
    dedup_ring_size: int = 20  # per-symbol ring buffer size

    # Consumer
    batch_size: int = 200
    block_ms: int = 5
```

### 4.3 Symbol Resolver

```python
# services/normalizer/src/normalizer/resolver.py

import msgpack
import structlog
from dataclasses import dataclass
from redis.asyncio import Redis

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    isin: str
    sector_id: str
    is_fno: bool
    lot_size: int
    tier: int  # 1, 2, or 3


class SymbolResolver:
    """
    Maps broker instrument_key → SymbolInfo.
    Loaded from Redis at startup, reloaded on config version change.
    """

    def __init__(self):
        self._map: dict[str, SymbolInfo] = {}
        self._config_version: str = ""

    async def load(self, redis: Redis):
        """Load symbol master from Redis."""
        raw = await redis.hgetall("infusion:symbols")
        new_map = {}
        for key, value in raw.items():
            k = key.decode() if isinstance(key, bytes) else key
            info = msgpack.unpackb(value, raw=False)
            new_map[k] = SymbolInfo(
                symbol=info["symbol"],
                isin=info.get("isin", ""),
                sector_id=info.get("sector_id", "UNCATEGORIZED"),
                is_fno=info.get("is_fno", False),
                lot_size=info.get("lot_size", 1),
                tier=info.get("tier", 3),
            )
        self._map = new_map
        logger.info("symbols_loaded", count=len(new_map))

    def resolve(self, instrument_key: str) -> SymbolInfo | None:
        """O(1) lookup. Returns None if unknown instrument."""
        return self._map.get(instrument_key)

    @property
    def count(self) -> int:
        return len(self._map)
```

### 4.4 Tier-Based Throttler

```python
# services/normalizer/src/normalizer/throttler.py

import time


class TierThrottler:
    """
    Drops ticks for Tier 2/3 symbols that arrive faster than threshold.
    Tier 1: no throttling (forward every tick)
    Tier 2: min 500ms between forwards
    Tier 3: min 2000ms between forwards
    """

    def __init__(self, tier2_ms: int = 500, tier3_ms: int = 2000):
        self._thresholds = {
            1: 0,  # no throttling
            2: tier2_ms / 1000.0,
            3: tier3_ms / 1000.0,
        }
        self._last_forwarded: dict[str, float] = {}
        self._dropped = 0

    def should_forward(self, symbol: str, tier: int) -> bool:
        """Returns True if this tick should be forwarded."""
        threshold = self._thresholds.get(tier, 0)
        if threshold == 0:
            return True

        now = time.monotonic()
        last = self._last_forwarded.get(symbol, 0)
        if now - last < threshold:
            self._dropped += 1
            return False

        self._last_forwarded[symbol] = now
        return True

    @property
    def dropped_count(self) -> int:
        return self._dropped
```

### 4.5 Deduplication

```python
# services/normalizer/src/normalizer/dedup.py

from collections import deque


class TickDedup:
    """
    Detects duplicate ticks using per-symbol ring buffer of exchange timestamps.
    Key: (symbol, exchange_timestamp_ms)
    """

    def __init__(self, ring_size: int = 20):
        self._ring_size = ring_size
        self._rings: dict[str, deque] = {}
        self._dupes = 0

    def is_duplicate(self, symbol: str, exchange_timestamp_ms: int) -> bool:
        """Returns True if this tick was already seen."""
        ring = self._rings.get(symbol)
        if ring is None:
            ring = deque(maxlen=self._ring_size)
            self._rings[symbol] = ring

        if exchange_timestamp_ms in ring:
            self._dupes += 1
            return True

        ring.append(exchange_timestamp_ms)
        return False

    @property
    def duplicate_count(self) -> int:
        return self._dupes
```

### 4.6 Transformer

```python
# services/normalizer/src/normalizer/transformer.py

from infusion_models.tick import NormalizedTickV1
from normalizer.resolver import SymbolInfo
from infusion_common.timing import now_us


def transform(raw_payload: dict, info: SymbolInfo) -> NormalizedTickV1:
    """Transform raw tick payload + symbol info → NormalizedTick."""
    return NormalizedTickV1(
        symbol=info.symbol,
        sector_id=info.sector_id,
        is_fno=info.is_fno,
        tier=info.tier,
        ltp=raw_payload["ltp"],
        open=raw_payload["open"],
        high=raw_payload["high"],
        low=raw_payload["low"],
        close=raw_payload["close"],
        volume=raw_payload["volume"],
        oi=raw_payload.get("oi", 0),
        best_bid=raw_payload.get("best_bid", 0.0),
        best_ask=raw_payload.get("best_ask", 0.0),
        best_bid_qty=raw_payload.get("best_bid_qty", 0),
        best_ask_qty=raw_payload.get("best_ask_qty", 0),
        exchange_timestamp_ms=raw_payload["exchange_timestamp_ms"],
        received_at_us=raw_payload["received_at_us"],
        normalized_at_us=now_us(),
    )
```

### 4.7 Main Entry Point

```python
# services/normalizer/src/normalizer/main.py

import asyncio
import structlog
from redis.asyncio import Redis

from normalizer.config import NormalizerSettings
from normalizer.resolver import SymbolResolver
from normalizer.throttler import TierThrottler
from normalizer.dedup import TickDedup
from normalizer.transformer import transform
from infusion_common.logging import setup_logging
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.health import HealthReporter
from infusion_models.events import EventType
from infusion_streams.consumer import StreamConsumer
from infusion_streams.producer import StreamProducer
from infusion_streams.constants import (
    STREAM_TICK_RAW,
    STREAM_TICK_NORMALIZED,
    CG_NORMALIZER,
    MAXLEN_TICK_NORMALIZED,
    KEY_TICK_PREFIX,
)

logger = structlog.get_logger()


async def main():
    config = NormalizerSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)
    logger.info("normalizer_starting")

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    lifecycle = ServiceLifecycle(config.service_name)

    # Components
    resolver = SymbolResolver()
    await resolver.load(redis)

    throttler = TierThrottler(
        tier2_ms=config.tier2_min_interval_ms,
        tier3_ms=config.tier3_min_interval_ms,
    )
    dedup = TickDedup(ring_size=config.dedup_ring_size)

    # Stream I/O
    consumer = StreamConsumer(
        redis,
        STREAM_TICK_RAW,
        CG_NORMALIZER,
        "normalizer-1",
        batch_size=config.batch_size,
        block_ms=config.block_ms,
    )
    await consumer.ensure_group()

    producer = StreamProducer(redis, STREAM_TICK_NORMALIZED, MAXLEN_TICK_NORMALIZED)

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(
        lambda: {
            "symbols_loaded": resolver.count,
            "throttled_dropped": throttler.dropped_count,
            "duplicates_dropped": dedup.duplicate_count,
            "consumed": consumer.stats,
            "published": producer.published_count,
        }
    )
    await health.start()
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Main processing loop
    logger.info("normalizer_consuming", stream=STREAM_TICK_RAW)
    resolve_misses = 0

    async for event_type, version, rx_us, payload, ack in consumer.consume():
        if not lifecycle.should_run:
            break

        # 1. Resolve symbol
        instrument_key = payload.get("instrument_key", "")
        info = resolver.resolve(instrument_key)
        if info is None:
            resolve_misses += 1
            if resolve_misses % 100 == 1:
                logger.warning("symbol_resolve_miss", key=instrument_key, total=resolve_misses)
            await ack()
            continue

        # 2. Dedup
        if dedup.is_duplicate(info.symbol, payload.get("exchange_timestamp_ms", 0)):
            await ack()
            continue

        # 3. Throttle
        if not throttler.should_forward(info.symbol, info.tier):
            await ack()
            continue

        # 4. Transform
        normalized = transform(payload, info)

        # 5. Publish to stream
        await producer.publish(
            event_type=EventType.NORMALIZED_TICK,
            payload=normalized.model_dump(),
            received_at_us=rx_us,
        )

        # 6. Update hot state (latest tick per symbol)
        import msgpack

        await redis.hset(
            f"{KEY_TICK_PREFIX}{info.symbol}",
            mapping={
                "ltp": str(normalized.ltp),
                "volume": str(normalized.volume),
                "change_pct": str(
                    round((normalized.ltp - normalized.close) / normalized.close * 100, 2)
                    if normalized.close > 0
                    else 0
                ),
                "exchange_ts": str(normalized.exchange_timestamp_ms),
                "updated_at": str(normalized.normalized_at_us),
            },
        )

        await ack()

    await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 5. Feature Engine Service

### 5.1 Service Structure

```
services/feature-engine/
├── pyproject.toml
├── Dockerfile
└── src/
    └── feature_engine/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── engine.py           ← orchestrator (micro-batch + dispatch)
        ├── bar_builder.py      ← tick → 1m/5m/15m bars
        ├── state.py            ← per-symbol state manager
        ├── features/
        │   ├── __init__.py
        │   ├── price.py        ← EMA, VWAP, gap
        │   ├── volume.py       ← relative volume, OBV
        │   ├── momentum.py     ← RSI, MACD, stochastic, CCI
        │   ├── volatility.py   ← ATR, Bollinger Bands
        │   └── microstructure.py ← spread, order imbalance
        └── publisher.py        ← feature → stream + hot state
```

### 5.2 Config

```python
# services/feature-engine/src/feature_engine/config.py

from infusion_common.config import InfusionSettings


class FeatureEngineSettings(InfusionSettings):
    service_name: str = "feature-engine"

    # Micro-batch
    batch_timer_ms: int = 5  # flush every 5ms
    batch_max_ticks: int = 200  # flush if buffer reaches 200

    # Consumer
    consumer_batch_size: int = 200
    consumer_block_ms: int = 2

    # Feature computation
    ema_periods: list[int] = [5, 9, 20, 50]
    rsi_period: int = 14
    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    cci_period: int = 20
    volume_sma_period: int = 20

    # OHLC bar retention in Redis sorted sets
    ohlc_1m_max: int = 390  # 1 day of 1-min bars
    ohlc_5m_max: int = 78  # 1 day of 5-min bars
    ohlc_15m_max: int = 26  # 1 day of 15-min bars
```

### 5.3 Per-Symbol State

```python
# services/feature-engine/src/feature_engine/state.py

import time
from dataclasses import dataclass, field
from collections import deque


@dataclass
class OHLCBar:
    """A single OHLC bar."""

    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    vwap_numerator: float = 0.0  # sum(price * volume)
    vwap_denominator: int = 0  # sum(volume)
    tick_count: int = 0
    bar_start_ms: int = 0  # exchange timestamp of bar start


@dataclass
class SymbolState:
    """Mutable state for a single symbol across the trading session."""

    symbol: str

    # Latest values
    ltp: float = 0.0
    prev_close: float = 0.0
    day_open: float = 0.0
    day_high: float = 0.0
    day_low: float = float("inf")
    volume: int = 0
    oi: int = 0
    best_bid: float = 0.0
    best_ask: float = 0.0
    total_buy_qty: int = 0
    total_sell_qty: int = 0

    # VWAP accumulators
    vwap_numerator: float = 0.0
    vwap_denominator: int = 0

    # EMA state (keyed by period)
    ema: dict[int, float] = field(default_factory=dict)
    ema_initialized: dict[int, bool] = field(default_factory=dict)

    # RSI state
    rsi_avg_gain: float = 0.0
    rsi_avg_loss: float = 0.0
    rsi_prev_close: float = 0.0
    rsi_initialized: bool = False
    rsi_warmup_count: int = 0
    rsi_gains: list[float] = field(default_factory=list)
    rsi_losses: list[float] = field(default_factory=list)

    # MACD state
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema_signal: float = 0.0
    macd_initialized: bool = False

    # ATR state
    atr_values: deque = field(default_factory=lambda: deque(maxlen=14))
    atr: float = 0.0
    atr_prev_close: float = 0.0

    # Bollinger
    bb_prices: deque = field(default_factory=lambda: deque(maxlen=20))

    # Stochastic
    stoch_highs: deque = field(default_factory=lambda: deque(maxlen=14))
    stoch_lows: deque = field(default_factory=lambda: deque(maxlen=14))
    stoch_k_values: deque = field(default_factory=lambda: deque(maxlen=3))

    # CCI
    cci_typical_prices: deque = field(default_factory=lambda: deque(maxlen=20))

    # OBV
    obv: float = 0.0
    obv_prev_close: float = 0.0

    # Volume tracking
    volume_history: deque = field(default_factory=lambda: deque(maxlen=20))

    # Bar builders
    bar_1m: OHLCBar = field(default_factory=OHLCBar)
    bar_5m: OHLCBar = field(default_factory=OHLCBar)
    bar_15m: OHLCBar = field(default_factory=OHLCBar)

    # Timing
    last_tick_exchange_ms: int = 0
    last_feature_compute_us: int = 0
```

### 5.4 Bar Builder

```python
# services/feature-engine/src/feature_engine/bar_builder.py

from feature_engine.state import SymbolState, OHLCBar


def update_bars(state: SymbolState, ltp: float, volume: int, exchange_ms: int):
    """
    Update 1m/5m/15m bar builders with new tick.
    Returns list of completed bars (timeframe, bar) when a bar closes.
    """
    completed = []

    for tf_minutes, bar_attr in [(1, "bar_1m"), (5, "bar_5m"), (15, "bar_15m")]:
        bar: OHLCBar = getattr(state, bar_attr)
        bar_duration_ms = tf_minutes * 60 * 1000

        # Determine bar boundary
        bar_start = (exchange_ms // bar_duration_ms) * bar_duration_ms

        if bar.bar_start_ms == 0:
            # First tick — initialize bar
            bar.bar_start_ms = bar_start

        if bar_start != bar.bar_start_ms:
            # New bar period — close previous bar and start new one
            if bar.tick_count > 0:
                completed.append((tf_minutes, bar))

            # Reset for new bar
            new_bar = OHLCBar(
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=volume,
                tick_count=1,
                bar_start_ms=bar_start,
            )
            setattr(state, bar_attr, new_bar)
        else:
            # Same bar — update
            if bar.tick_count == 0:
                bar.open = ltp
            bar.high = max(bar.high, ltp)
            bar.low = min(bar.low, ltp)
            bar.close = ltp
            bar.volume = volume  # cumulative from exchange
            bar.tick_count += 1

    return completed
```

### 5.5 Feature Modules

#### price.py

```python
# services/feature-engine/src/feature_engine/features/price.py

from feature_engine.state import SymbolState


def update_price_features(state: SymbolState, ltp: float, volume: int):
    """Update price-based features: VWAP, EMAs, gap%."""

    # Day high/low
    state.day_high = max(state.day_high, ltp)
    if state.day_low == float("inf"):
        state.day_low = ltp
    else:
        state.day_low = min(state.day_low, ltp)

    # VWAP (incremental)
    if volume > state.vwap_denominator:
        # Approximate: use LTP as representative price for new volume
        delta_vol = volume - state.vwap_denominator
        state.vwap_numerator += ltp * delta_vol
        state.vwap_denominator = volume

    # EMAs (incremental)
    for period in [5, 9, 20, 50]:
        if not state.ema_initialized.get(period, False):
            state.ema[period] = ltp
            state.ema_initialized[period] = True
        else:
            k = 2 / (period + 1)
            state.ema[period] = ltp * k + state.ema[period] * (1 - k)


def get_vwap(state: SymbolState) -> float:
    if state.vwap_denominator > 0:
        return state.vwap_numerator / state.vwap_denominator
    return state.ltp


def get_gap_pct(state: SymbolState) -> float:
    if state.prev_close > 0 and state.day_open > 0:
        return (state.day_open - state.prev_close) / state.prev_close * 100
    return 0.0


def get_change_pct(state: SymbolState) -> float:
    if state.prev_close > 0:
        return (state.ltp - state.prev_close) / state.prev_close * 100
    return 0.0
```

#### momentum.py

```python
# services/feature-engine/src/feature_engine/features/momentum.py

from feature_engine.state import SymbolState
import statistics


def update_rsi(state: SymbolState, ltp: float, period: int = 14):
    """Incremental RSI using Wilder's smoothing."""
    if state.rsi_prev_close == 0:
        state.rsi_prev_close = ltp
        return

    change = ltp - state.rsi_prev_close
    gain = max(change, 0)
    loss = abs(min(change, 0))
    state.rsi_prev_close = ltp

    if not state.rsi_initialized:
        state.rsi_gains.append(gain)
        state.rsi_losses.append(loss)
        state.rsi_warmup_count += 1

        if state.rsi_warmup_count >= period:
            state.rsi_avg_gain = sum(state.rsi_gains) / period
            state.rsi_avg_loss = sum(state.rsi_losses) / period
            state.rsi_initialized = True
            state.rsi_gains.clear()
            state.rsi_losses.clear()
    else:
        state.rsi_avg_gain = (state.rsi_avg_gain * (period - 1) + gain) / period
        state.rsi_avg_loss = (state.rsi_avg_loss * (period - 1) + loss) / period


def get_rsi(state: SymbolState) -> float:
    if not state.rsi_initialized:
        return 50.0
    if state.rsi_avg_loss == 0:
        return 100.0
    rs = state.rsi_avg_gain / state.rsi_avg_loss
    return 100 - (100 / (1 + rs))


def update_macd(state: SymbolState, ltp: float, fast: int = 12, slow: int = 26, sig: int = 9):
    """Incremental MACD."""
    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)
    k_sig = 2 / (sig + 1)

    if not state.macd_initialized:
        state.ema_fast = ltp
        state.ema_slow = ltp
        state.ema_signal = 0.0
        state.macd_initialized = True
    else:
        state.ema_fast = ltp * k_fast + state.ema_fast * (1 - k_fast)
        state.ema_slow = ltp * k_slow + state.ema_slow * (1 - k_slow)
        macd_line = state.ema_fast - state.ema_slow
        state.ema_signal = macd_line * k_sig + state.ema_signal * (1 - k_sig)


def get_macd(state: SymbolState) -> tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = state.ema_fast - state.ema_slow
    return macd_line, state.ema_signal, macd_line - state.ema_signal


def update_stochastic(state: SymbolState, high: float, low: float, close: float):
    """Stochastic %K and %D."""
    state.stoch_highs.append(high)
    state.stoch_lows.append(low)

    if len(state.stoch_highs) >= 14:
        highest = max(state.stoch_highs)
        lowest = min(state.stoch_lows)
        denom = highest - lowest
        k = ((close - lowest) / denom * 100) if denom > 0 else 50.0
        state.stoch_k_values.append(k)


def get_stochastic(state: SymbolState) -> tuple[float, float]:
    """Returns (%K, %D)."""
    if not state.stoch_k_values:
        return 50.0, 50.0
    k = state.stoch_k_values[-1]
    d = sum(state.stoch_k_values) / len(state.stoch_k_values) if state.stoch_k_values else 50.0
    return k, d


def update_cci(state: SymbolState, high: float, low: float, close: float, period: int = 20):
    """Commodity Channel Index."""
    typical = (high + low + close) / 3
    state.cci_typical_prices.append(typical)


def get_cci(state: SymbolState) -> float:
    if len(state.cci_typical_prices) < 2:
        return 0.0
    mean = sum(state.cci_typical_prices) / len(state.cci_typical_prices)
    mad = sum(abs(p - mean) for p in state.cci_typical_prices) / len(state.cci_typical_prices)
    if mad == 0:
        return 0.0
    typical = state.cci_typical_prices[-1]
    return (typical - mean) / (0.015 * mad)
```

#### volatility.py

```python
# services/feature-engine/src/feature_engine/features/volatility.py

from feature_engine.state import SymbolState
import math


def update_atr(state: SymbolState, high: float, low: float, close: float, period: int = 14):
    """Average True Range — incremental."""
    if state.atr_prev_close == 0:
        state.atr_prev_close = close
        return

    tr = max(
        high - low,
        abs(high - state.atr_prev_close),
        abs(low - state.atr_prev_close),
    )
    state.atr_prev_close = close

    state.atr_values.append(tr)
    if len(state.atr_values) >= period:
        if state.atr == 0:
            state.atr = sum(state.atr_values) / len(state.atr_values)
        else:
            state.atr = (state.atr * (period - 1) + tr) / period


def update_bollinger(state: SymbolState, close: float):
    """Bollinger Band prices buffer."""
    state.bb_prices.append(close)


def get_bollinger(
    state: SymbolState, period: int = 20, num_std: float = 2.0
) -> tuple[float, float, float]:
    """Returns (upper, lower, width)."""
    if len(state.bb_prices) < 2:
        return state.ltp * 1.02, state.ltp * 0.98, 0.04

    prices = list(state.bb_prices)
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    std = math.sqrt(variance) if variance > 0 else 0

    upper = mean + num_std * std
    lower = mean - num_std * std
    width = (upper - lower) / mean if mean > 0 else 0

    return upper, lower, width
```

#### volume.py

```python
# services/feature-engine/src/feature_engine/features/volume.py

from feature_engine.state import SymbolState


def update_obv(state: SymbolState, close: float, volume: int):
    """On-Balance Volume — incremental."""
    if state.obv_prev_close == 0:
        state.obv_prev_close = close
        return

    if close > state.obv_prev_close:
        state.obv += volume
    elif close < state.obv_prev_close:
        state.obv -= volume
    # close == prev: OBV unchanged

    state.obv_prev_close = close


def get_relative_volume(state: SymbolState) -> float:
    """Current volume / 20-day average volume."""
    if not state.volume_history or state.volume == 0:
        return 1.0
    avg = sum(state.volume_history) / len(state.volume_history)
    if avg == 0:
        return 1.0
    return state.volume / avg


def get_volume_sma(state: SymbolState) -> float:
    if not state.volume_history:
        return 0.0
    return sum(state.volume_history) / len(state.volume_history)
```

#### microstructure.py

```python
# services/feature-engine/src/feature_engine/features/microstructure.py

from feature_engine.state import SymbolState


def get_spread_bps(state: SymbolState) -> float:
    """Bid-ask spread in basis points."""
    if state.best_bid <= 0 or state.best_ask <= 0:
        return 0.0
    mid = (state.best_bid + state.best_ask) / 2
    if mid == 0:
        return 0.0
    return (state.best_ask - state.best_bid) / mid * 10_000


def get_order_imbalance(state: SymbolState) -> float:
    """(buy_qty - sell_qty) / (buy_qty + sell_qty). Range: -1 to +1."""
    total = state.total_buy_qty + state.total_sell_qty
    if total == 0:
        return 0.0
    return (state.total_buy_qty - state.total_sell_qty) / total
```

### 5.6 Engine Orchestrator

```python
# services/feature-engine/src/feature_engine/engine.py

import asyncio
import time
import structlog
from collections import defaultdict

from feature_engine.state import SymbolState
from feature_engine.bar_builder import update_bars
from feature_engine.features.price import (
    update_price_features,
    get_vwap,
    get_gap_pct,
    get_change_pct,
)
from feature_engine.features.momentum import (
    update_rsi,
    get_rsi,
    update_macd,
    get_macd,
    update_stochastic,
    get_stochastic,
    update_cci,
    get_cci,
)
from feature_engine.features.volatility import update_atr, update_bollinger, get_bollinger
from feature_engine.features.volume import update_obv, get_relative_volume, get_volume_sma
from feature_engine.features.microstructure import get_spread_bps, get_order_imbalance
from infusion_models.feature import FeatureVectorV1

logger = structlog.get_logger()


class FeatureEngine:
    """
    Orchestrates feature computation with micro-batching.

    Flow:
      1. Ticks arrive and are buffered
      2. Every 5ms OR when buffer hits 200: flush
      3. Group ticks by symbol (only latest tick per symbol in batch matters)
      4. Compute features for each symbol
      5. Emit FeatureVector
    """

    def __init__(self, config):
        self.config = config
        self._states: dict[str, SymbolState] = {}
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._on_feature = None  # callback
        self._processed = 0

    def set_callback(self, fn):
        """Set callback for emitting feature vectors."""
        self._on_feature = fn

    async def ingest(self, payload: dict):
        """Buffer a normalized tick for batch processing."""
        self._buffer.append(payload)
        if len(self._buffer) >= self.config.batch_max_ticks:
            await self._flush()

    async def flush_timer(self):
        """Background timer that flushes buffer every batch_timer_ms."""
        interval = self.config.batch_timer_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            if self._buffer:
                await self._flush()

    async def _flush(self):
        """Process buffered ticks."""
        async with self._lock:
            if not self._buffer:
                return

            batch = self._buffer
            self._buffer = []

        # Deduplicate: keep only latest tick per symbol
        latest_by_symbol: dict[str, dict] = {}
        for tick in batch:
            symbol = tick.get("symbol", "")
            latest_by_symbol[symbol] = tick

        # Compute features for each symbol
        for symbol, tick in latest_by_symbol.items():
            state = self._get_or_create_state(symbol)
            feature = self._compute(state, tick)
            if feature and self._on_feature:
                await self._on_feature(feature)
                self._processed += 1

    def _get_or_create_state(self, symbol: str) -> SymbolState:
        if symbol not in self._states:
            self._states[symbol] = SymbolState(symbol=symbol)
        return self._states[symbol]

    def _compute(self, state: SymbolState, tick: dict) -> FeatureVectorV1 | None:
        """Run all feature computations for a symbol."""
        ltp = tick.get("ltp", 0.0)
        if ltp <= 0:
            return None

        volume = tick.get("volume", 0)
        high = tick.get("high", ltp)
        low = tick.get("low", ltp)
        close = tick.get("close", ltp)  # prev day close

        # Update state from tick
        state.ltp = ltp
        state.volume = volume
        state.oi = tick.get("oi", 0)
        state.best_bid = tick.get("best_bid", 0.0)
        state.best_ask = tick.get("best_ask", 0.0)
        state.total_buy_qty = tick.get("best_bid_qty", 0)
        state.total_sell_qty = tick.get("best_ask_qty", 0)
        state.last_tick_exchange_ms = tick.get("exchange_timestamp_ms", 0)

        if state.prev_close == 0 and close > 0:
            state.prev_close = close
        if state.day_open == 0:
            state.day_open = tick.get("open", ltp)

        # Update all features
        update_price_features(state, ltp, volume)
        update_rsi(state, ltp, self.config.rsi_period)
        update_macd(
            state, ltp, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal
        )
        update_atr(state, high, low, ltp, self.config.atr_period)
        update_bollinger(state, ltp)
        update_stochastic(state, high, low, ltp)
        update_cci(state, high, low, ltp, self.config.cci_period)
        update_obv(state, ltp, volume)
        update_bars(state, ltp, volume, state.last_tick_exchange_ms)

        # Collect computed features
        macd_line, macd_sig, macd_hist = get_macd(state)
        bb_upper, bb_lower, bb_width = get_bollinger(state)
        stoch_k, stoch_d = get_stochastic(state)

        from infusion_common.timing import now_us

        return FeatureVectorV1(
            symbol=state.symbol,
            timestamp_us=now_us(),
            ltp=ltp,
            vwap=get_vwap(state),
            gap_pct=get_gap_pct(state),
            day_high=state.day_high,
            day_low=state.day_low if state.day_low != float("inf") else ltp,
            prev_close=state.prev_close,
            change_pct=get_change_pct(state),
            ema_5=state.ema.get(5, ltp),
            ema_9=state.ema.get(9, ltp),
            ema_20=state.ema.get(20, ltp),
            ema_50=state.ema.get(50, ltp),
            atr_14=state.atr,
            bb_upper=bb_upper,
            bb_lower=bb_lower,
            bb_width=bb_width,
            rsi_14=get_rsi(state),
            macd=macd_line,
            macd_signal=macd_sig,
            macd_hist=macd_hist,
            stochastic_k=stoch_k,
            stochastic_d=stoch_d,
            cci_20=get_cci(state),
            rel_vol_20d=get_relative_volume(state),
            obv=state.obv,
            volume_sma_20=get_volume_sma(state),
            spread_bps=get_spread_bps(state),
            order_imbalance=get_order_imbalance(state),
        )

    @property
    def stats(self) -> dict:
        return {
            "symbols_tracked": len(self._states),
            "features_computed": self._processed,
        }
```

### 5.7 Main Entry Point

```python
# services/feature-engine/src/feature_engine/main.py

import asyncio
import msgpack
import structlog
from redis.asyncio import Redis

from feature_engine.config import FeatureEngineSettings
from feature_engine.engine import FeatureEngine
from infusion_common.logging import setup_logging
from infusion_common.lifecycle import ServiceLifecycle
from infusion_common.health import HealthReporter
from infusion_models.events import EventType
from infusion_streams.consumer import StreamConsumer
from infusion_streams.producer import StreamProducer
from infusion_streams.constants import (
    STREAM_TICK_NORMALIZED,
    STREAM_FEATURE_COMPUTED,
    CG_FEATURE,
    MAXLEN_FEATURE_COMPUTED,
    KEY_FEATURE_PREFIX,
)

logger = structlog.get_logger()


async def main():
    config = FeatureEngineSettings()
    setup_logging(config.service_name, config.log_level, config.log_format)
    logger.info("feature_engine_starting")

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    lifecycle = ServiceLifecycle(config.service_name)

    # Stream I/O
    consumer = StreamConsumer(
        redis,
        STREAM_TICK_NORMALIZED,
        CG_FEATURE,
        "feature-engine-1",
        batch_size=config.consumer_batch_size,
        block_ms=config.consumer_block_ms,
    )
    await consumer.ensure_group()

    producer = StreamProducer(redis, STREAM_FEATURE_COMPUTED, MAXLEN_FEATURE_COMPUTED)

    # Feature engine
    engine = FeatureEngine(config)

    async def on_feature(fv):
        """Callback: publish feature vector to stream + hot state."""
        payload = fv.model_dump()
        await producer.publish(
            event_type=EventType.FEATURE_COMPUTED,
            payload=payload,
            received_at_us=fv.timestamp_us,
        )
        # Update hot state
        await redis.hset(
            f"{KEY_FEATURE_PREFIX}{fv.symbol}",
            mapping={k: str(v) for k, v in payload.items() if k != "ml_features"},
        )

    engine.set_callback(on_feature)

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(
        lambda: {
            **engine.stats,
            "consumed": consumer.stats,
            "published": producer.published_count,
        }
    )
    await health.start()
    lifecycle.register_cleanup(health.stop)
    lifecycle.register_cleanup(redis.aclose)

    # Start flush timer
    timer_task = asyncio.create_task(engine.flush_timer())

    # Main loop
    logger.info("feature_engine_consuming", stream=STREAM_TICK_NORMALIZED)

    async for event_type, version, rx_us, payload, ack in consumer.consume():
        if not lifecycle.should_run:
            break
        await engine.ingest(payload)
        await ack()

    timer_task.cancel()
    await lifecycle.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. WS Gateway Service

### 6.1 Service Structure

```
services/ws-gateway/
├── pyproject.toml
├── Dockerfile
└── src/
    └── ws_gateway/
        ├── __init__.py
        ├── main.py
        ├── config.py
        ├── server.py           ← aiohttp WS server
        ├── fan_out.py          ← stream reader → client dispatch
        └── client_manager.py   ← connected client registry
```

### 6.2 Config

```python
# services/ws-gateway/src/ws_gateway/config.py

from infusion_common.config import InfusionSettings


class WSGatewaySettings(InfusionSettings):
    service_name: str = "ws-gateway"

    # Server
    ws_host: str = "0.0.0.0"
    ws_port: int = 8080

    # Batching
    price_batch_ms: int = 100  # batch price updates every 100ms
    signal_immediate: bool = True  # push signals immediately

    # Consumer
    consumer_batch_size: int = 100
    consumer_block_ms: int = 5
```

### 6.3 Client Manager

```python
# services/ws-gateway/src/ws_gateway/client_manager.py

import asyncio
import json
import time
import structlog
from aiohttp import web

logger = structlog.get_logger()


class ClientManager:
    """Manages connected WebSocket clients and their subscriptions."""

    def __init__(self):
        self._clients: dict[str, web.WebSocketResponse] = {}  # id → ws
        self._subscriptions: dict[str, set[str]] = {}  # id → set of symbols
        self._batch_buffer: dict[str, dict] = {}  # symbol → latest tick data
        self._batch_lock = asyncio.Lock()

    async def add(self, client_id: str, ws: web.WebSocketResponse):
        self._clients[client_id] = ws
        self._subscriptions[client_id] = set()  # subscribe to all by default
        logger.info("client_connected", client_id=client_id, total=len(self._clients))

    async def remove(self, client_id: str):
        self._clients.pop(client_id, None)
        self._subscriptions.pop(client_id, None)
        logger.info("client_disconnected", client_id=client_id, total=len(self._clients))

    async def handle_subscribe(self, client_id: str, symbols: list[str]):
        """Client subscribes to specific symbols."""
        if client_id in self._subscriptions:
            self._subscriptions[client_id] = set(symbols)

    async def buffer_tick(self, symbol: str, data: dict):
        """Buffer a tick update for batched delivery."""
        async with self._batch_lock:
            self._batch_buffer[symbol] = data

    async def flush_batch(self):
        """Send batched tick updates to all connected clients."""
        async with self._batch_lock:
            if not self._batch_buffer:
                return
            buffer = self._batch_buffer
            self._batch_buffer = {}

        if not self._clients:
            return

        # Build message
        message = json.dumps(
            {
                "type": "tick_batch",
                "data": buffer,
                "ts": int(time.time() * 1000),
            }
        )

        # Fan out to all clients
        dead_clients = []
        for client_id, ws in self._clients.items():
            try:
                if not ws.closed:
                    await ws.send_str(message)
            except Exception:
                dead_clients.append(client_id)

        for cid in dead_clients:
            await self.remove(cid)

    async def send_signal(self, signal_data: dict):
        """Push signal immediately to all clients."""
        if not self._clients:
            return

        message = json.dumps(
            {
                "type": "signal",
                "data": signal_data,
                "ts": int(time.time() * 1000),
            }
        )

        dead_clients = []
        for client_id, ws in self._clients.items():
            try:
                if not ws.closed:
                    await ws.send_str(message)
            except Exception:
                dead_clients.append(client_id)

        for cid in dead_clients:
            await self.remove(cid)

    @property
    def client_count(self) -> int:
        return len(self._clients)
```

### 6.4 Main Entry Point

```python
# services/ws-gateway/src/ws_gateway/main.py

import asyncio
import uuid
import structlog
from aiohttp import web
from redis.asyncio import Redis

from ws_gateway.config import WSGatewaySettings
from ws_gateway.client_manager import ClientManager
from infusion_common.logging import setup_logging
from infusion_common.health import HealthReporter
from infusion_streams.consumer import StreamConsumer
from infusion_streams.constants import (
    STREAM_TICK_NORMALIZED,
    STREAM_FEATURE_COMPUTED,
    CG_DASHBOARD,
)

logger = structlog.get_logger()


async def main():
    config = WSGatewaySettings()
    setup_logging(config.service_name, config.log_level, config.log_format)

    redis = Redis.from_url(config.redis_url, decode_responses=False)
    clients = ClientManager()

    # Health
    health = HealthReporter(redis, config.service_name)
    health.set_details_fn(lambda: {"clients": clients.client_count})
    await health.start()

    # Stream consumers for tick and feature data
    tick_consumer = StreamConsumer(
        redis,
        STREAM_TICK_NORMALIZED,
        CG_DASHBOARD,
        "ws-gateway-tick",
        batch_size=config.consumer_batch_size,
        block_ms=config.consumer_block_ms,
    )
    await tick_consumer.ensure_group()

    # Background: read ticks and buffer for batch delivery
    async def tick_reader():
        async for event_type, version, rx_us, payload, ack in tick_consumer.consume():
            symbol = payload.get("symbol", "")
            await clients.buffer_tick(
                symbol,
                {
                    "ltp": payload.get("ltp"),
                    "volume": payload.get("volume"),
                    "high": payload.get("high"),
                    "low": payload.get("low"),
                },
            )
            await ack()

    # Background: flush batched ticks every 100ms
    async def batch_flusher():
        interval = config.price_batch_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            await clients.flush_batch()

    # WebSocket handler
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client_id = str(uuid.uuid4())[:8]
        await clients.add(client_id, ws)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    import json

                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "subscribe":
                            await clients.handle_subscribe(client_id, data.get("symbols", []))
                    except Exception:
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            await clients.remove(client_id)

        return ws

    # Health endpoint
    async def health_handler(request):
        return web.json_response({"status": "healthy", "clients": clients.client_count})

    # Setup aiohttp app
    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.ws_host, config.ws_port)
    await site.start()

    logger.info("ws_gateway_started", host=config.ws_host, port=config.ws_port)

    # Run background tasks
    await asyncio.gather(
        tick_reader(),
        batch_flusher(),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 7. API Service

### 7.1 Service Structure

```
services/api/
├── pyproject.toml
├── Dockerfile
└── src/
    └── api/
        ├── __init__.py
        ├── main.py
        ├── config.py
        └── routes/
            ├── __init__.py
            ├── health.py
            ├── symbols.py
            ├── ticks.py
            └── features.py
```

### 7.2 Config

```python
# services/api/src/api/config.py

from infusion_common.config import InfusionSettings


class APISettings(InfusionSettings):
    service_name: str = "api"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
```

### 7.3 Main Entry Point

```python
# services/api/src/api/main.py

import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from redis.asyncio import Redis

from api.config import APISettings
from api.routes import health, symbols, ticks, features
from infusion_common.logging import setup_logging
from infusion_common.health import HealthReporter


config = APISettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(config.service_name, config.log_level, config.log_format)
    app.state.redis = Redis.from_url(config.redis_url, decode_responses=True)
    app.state.health = HealthReporter(app.state.redis, config.service_name)
    await app.state.health.start()
    yield
    await app.state.health.stop()
    await app.state.redis.aclose()


app = FastAPI(
    title="Infusion API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api")
app.include_router(symbols.router, prefix="/api")
app.include_router(ticks.router, prefix="/api")
app.include_router(features.router, prefix="/api")


if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=config.api_host,
        port=config.api_port,
        log_level="info",
    )
```

### 7.4 Routes

#### health.py

```python
# services/api/src/api/routes/health.py

import msgpack
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Aggregated health check across all services."""
    redis = request.app.state.redis

    services = ["ingestion", "normalizer", "feature-engine", "ws-gateway", "api"]
    result = {}

    for svc in services:
        raw = await redis.get(f"infusion:health:{svc}")
        if raw:
            result[svc] = (
                msgpack.unpackb(raw, raw=False) if isinstance(raw, bytes) else {"status": "healthy"}
            )
        else:
            result[svc] = {"status": "unhealthy", "reason": "no heartbeat"}

    all_healthy = all(s.get("status") == "healthy" for s in result.values())

    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": result,
    }
```

#### symbols.py

```python
# services/api/src/api/routes/symbols.py

import msgpack
from fastapi import APIRouter, Request

router = APIRouter(tags=["symbols"])


@router.get("/symbols")
async def list_symbols(request: Request):
    """List all tracked symbols with metadata."""
    redis = request.app.state.redis
    raw = await redis.hgetall("infusion:symbols")

    symbols = []
    for key, value in raw.items():
        k = key if isinstance(key, str) else key.decode()
        info = msgpack.unpackb(value if isinstance(value, bytes) else value.encode(), raw=False)
        symbols.append(
            {
                "instrument_key": k,
                **info,
            }
        )

    return {"count": len(symbols), "symbols": symbols}
```

#### ticks.py

```python
# services/api/src/api/routes/ticks.py

from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["ticks"])


@router.get("/ticks/{symbol}")
async def get_tick(symbol: str, request: Request):
    """Get latest tick data for a symbol."""
    redis = request.app.state.redis
    data = await redis.hgetall(f"infusion:tick:{symbol.upper()}")

    if not data:
        raise HTTPException(status_code=404, detail=f"No tick data for {symbol}")

    return {
        "symbol": symbol.upper(),
        **{k: v for k, v in data.items()},
    }
```

#### features.py

```python
# services/api/src/api/routes/features.py

from fastapi import APIRouter, Request, HTTPException

router = APIRouter(tags=["features"])


@router.get("/features/{symbol}")
async def get_features(symbol: str, request: Request):
    """Get latest computed features for a symbol."""
    redis = request.app.state.redis
    data = await redis.hgetall(f"infusion:feature:{symbol.upper()}")

    if not data:
        raise HTTPException(status_code=404, detail=f"No feature data for {symbol}")

    # Convert string values back to appropriate types
    result = {"symbol": symbol.upper()}
    for k, v in data.items():
        try:
            result[k] = float(v)
        except (ValueError, TypeError):
            result[k] = v

    return result
```

### 7.5 pyproject.toml

```toml
[project]
name = "api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "infusion-models",
    "infusion-streams",
    "infusion-common",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/api"]
```

---

## 8. Docker Compose — Phase 2 Services

Add these services to the existing `docker-compose.yml` from Phase 1:

```yaml
# docker-compose.phase2.yaml (extends base docker-compose.yml)

services:
  ingestion:
    build:
      context: .
      dockerfile: services/ingestion/Dockerfile
    environment:
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_BROKER_PRIMARY=mock
      - INFUSION_LOG_LEVEL=INFO
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 50M
    restart: unless-stopped

  normalizer:
    build:
      context: .
      dockerfile: services/normalizer/Dockerfile
    environment:
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=INFO
    depends_on:
      redis:
        condition: service_healthy
      ingestion:
        condition: service_started
    deploy:
      resources:
        limits:
          memory: 30M
    restart: unless-stopped

  feature-engine:
    build:
      context: .
      dockerfile: services/feature-engine/Dockerfile
    environment:
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=INFO
    depends_on:
      redis:
        condition: service_healthy
      normalizer:
        condition: service_started
    deploy:
      resources:
        limits:
          memory: 1G
    restart: unless-stopped

  ws-gateway:
    build:
      context: .
      dockerfile: services/ws-gateway/Dockerfile
    ports:
      - "8080:8080"
    environment:
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=INFO
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 50M
    restart: unless-stopped

  api:
    build:
      context: .
      dockerfile: services/api/Dockerfile
    ports:
      - "8000:8000"
    environment:
      - INFUSION_REDIS_URL=redis://redis:6379/0
      - INFUSION_LOG_LEVEL=INFO
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 100M
    restart: unless-stopped
```

---

## 9. Integration Test Plan

### 9.1 End-to-End Pipeline Test

```
TEST: full_pipeline_e2e
═══════════════════════

Setup:
  1. Start Redis, PostgreSQL
  2. Bootstrap streams and consumer groups
  3. Populate infusion:symbols with mock symbol data
  4. Start ingestion (mock adapter), normalizer, feature-engine

Verify:
  1. XLEN infusion:stream:tick:raw > 0 within 5 seconds
  2. XLEN infusion:stream:tick:normalized > 0 within 10 seconds
  3. XLEN infusion:stream:feature:computed > 0 within 15 seconds
  4. HGET infusion:tick:RELIANCE returns non-empty hash
  5. HGET infusion:feature:RELIANCE returns non-empty hash with rsi_14 field
  6. All consumer groups have zero pending (lag = 0)
```

### 9.2 Reconnect Resilience Test

```
TEST: ingestion_reconnect
═════════════════════════

Setup:
  1. Start full pipeline with mock adapter
  2. Wait for ticks to flow (10 seconds)

Action:
  1. Kill ingestion container
  2. Wait 5 seconds
  3. Restart ingestion container

Verify:
  1. Ingestion reconnects (health becomes "streaming" again)
  2. Ticks resume in tick:raw within 10 seconds of restart
  3. Feature engine continues computing (no crash)
  4. No messages in DLQ streams
```

### 9.3 Malformed Data Test

```
TEST: dlq_malformed_data
═══════════════════════

Action:
  1. XADD infusion:stream:tick:raw * data "GARBAGE_BYTES"

Verify:
  1. Normalizer logs error with error_category=malformed_data
  2. XLEN infusion:dlq:tick:raw == 1
  3. Normalizer continues processing valid messages
  4. DLQ entry contains original_payload, failure_reason
```

### 9.4 Schema Version Test

```
TEST: schema_version_handling
══════════════════════════════

Action:
  1. Manually XADD a message with schema version 99

Verify:
  1. Consumer logs SchemaVersionError
  2. Message goes to DLQ
  3. Processing continues for valid messages
```

---

## 10. Smoke Test Procedure

### 10.1 Manual Checklist

```
PHASE 2 SMOKE TEST
═══════════════════

Pre-requisites:
  [ ] Docker running
  [ ] docker compose up -d (all services green)

Infrastructure checks:
  [ ] redis-cli PING → PONG
  [ ] redis-cli XINFO GROUPS infusion:stream:tick:raw → normalizer-cg exists
  [ ] redis-cli XINFO GROUPS infusion:stream:tick:normalized → feature-cg exists

Pipeline checks:
  [ ] redis-cli XLEN infusion:stream:tick:raw → growing
  [ ] redis-cli XLEN infusion:stream:tick:normalized → growing
  [ ] redis-cli XLEN infusion:stream:feature:computed → growing
  [ ] redis-cli HGETALL infusion:tick:RELIANCE → has ltp field
  [ ] redis-cli HGETALL infusion:feature:RELIANCE → has rsi_14 field

API checks:
  [ ] curl http://localhost:8000/api/health → 200 with all services
  [ ] curl http://localhost:8000/api/ticks/RELIANCE → 200 with ltp
  [ ] curl http://localhost:8000/api/features/RELIANCE → 200 with features

WebSocket check:
  [ ] wscat -c ws://localhost:8080/ws → receives tick_batch messages

DLQ check:
  [ ] redis-cli XLEN infusion:dlq:tick:raw → 0
  [ ] redis-cli XLEN infusion:dlq:tick:normalized → 0
  [ ] redis-cli XLEN infusion:dlq:feature:computed → 0

Health check:
  [ ] redis-cli GET infusion:health:ingestion → has status=healthy
  [ ] redis-cli GET infusion:health:normalizer → has status=healthy
  [ ] redis-cli GET infusion:health:feature-engine → has status=healthy

Memory check:
  [ ] docker stats → all services within memory limits
  [ ] After 10 min: no significant memory growth
```

### 10.2 Automated Smoke Script

```bash
#!/bin/bash
# scripts/smoke_phase2.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

echo "=== Phase 2 Smoke Test ==="

# Wait for data to flow
echo "Waiting 15s for pipeline warmup..."
sleep 15

# Stream checks
RAW_LEN=$(redis-cli XLEN infusion:stream:tick:raw)
[[ $RAW_LEN -gt 0 ]] && pass "tick:raw has $RAW_LEN messages" || fail "tick:raw empty"

NORM_LEN=$(redis-cli XLEN infusion:stream:tick:normalized)
[[ $NORM_LEN -gt 0 ]] && pass "tick:normalized has $NORM_LEN messages" || fail "tick:normalized empty"

FEAT_LEN=$(redis-cli XLEN infusion:stream:feature:computed)
[[ $FEAT_LEN -gt 0 ]] && pass "feature:computed has $FEAT_LEN messages" || fail "feature:computed empty"

# Hot state checks
LTP=$(redis-cli HGET infusion:tick:RELIANCE ltp 2>/dev/null || echo "")
[[ -n "$LTP" ]] && pass "RELIANCE tick hot state: ltp=$LTP" || fail "No tick hot state for RELIANCE"

RSI=$(redis-cli HGET infusion:feature:RELIANCE rsi_14 2>/dev/null || echo "")
[[ -n "$RSI" ]] && pass "RELIANCE feature hot state: rsi=$RSI" || fail "No feature hot state for RELIANCE"

# API checks
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health)
[[ $HEALTH == "200" ]] && pass "API /health returns 200" || fail "API /health returns $HEALTH"

TICK_API=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/ticks/RELIANCE)
[[ $TICK_API == "200" ]] && pass "API /ticks/RELIANCE returns 200" || fail "API /ticks/RELIANCE returns $TICK_API"

FEAT_API=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/features/RELIANCE)
[[ $FEAT_API == "200" ]] && pass "API /features/RELIANCE returns 200" || fail "API /features/RELIANCE returns $FEAT_API"

# DLQ checks
DLQ_RAW=$(redis-cli XLEN infusion:dlq:tick:raw 2>/dev/null || echo "0")
[[ $DLQ_RAW == "0" ]] && pass "DLQ tick:raw is empty" || echo -e "${RED}⚠ DLQ tick:raw has $DLQ_RAW entries${NC}"

DLQ_NORM=$(redis-cli XLEN infusion:dlq:tick:normalized 2>/dev/null || echo "0")
[[ $DLQ_NORM == "0" ]] && pass "DLQ tick:normalized is empty" || echo -e "${RED}⚠ DLQ tick:normalized has $DLQ_NORM entries${NC}"

echo ""
echo "=== Phase 2 Smoke Test Complete ==="
```

---

## 11. Phase 2 Boundary

### What Exists After This Phase

```
✓ 3 shared libraries: infusion-models, infusion-streams, infusion-common
✓ Schema versioning with envelope codec
✓ DLQ infrastructure with retry policies
✓ Error taxonomy with classifier
✓ Clock synchronization policy enforced
✓ Ingestion service with mock + Upstox adapter
✓ Normalizer service with symbol resolution, throttling, dedup
✓ Feature engine with 30+ incremental features
✓ WS gateway with batched price delivery
✓ REST API with health, ticks, features endpoints
✓ Docker compose for all Phase 2 services
✓ End-to-end pipeline: tick → normalized → features → Redis → API → browser
```

### What Does NOT Exist Yet

```
✗ NSE scraper (Phase 3)
✗ Scanner strategies (Phase 3)
✗ Conviction engine (Phase 3)
✗ Sector intelligence / breadth engine (Phase 3)
✗ AI worker / Gemini integration (Phase 4)
✗ Alerter / Telegram bot (Phase 4)
✗ Dashboard frontend (Phase 5)
✗ PostgreSQL persistence of features/bars (deferred to Phase 3)
✗ Historical backfill scripts (deferred to Phase 3)
✗ Symbol master population (manual or script for now)
✗ Upstox OAuth callback server (manual token for now)
```

### Bridge to Phase 3

Phase 3 (Scanner & Conviction Engine) depends on:
1. `feature:computed` stream being populated ← **this phase delivers it**
2. Symbol master in `infusion:symbols` ← **manual seed or populate script needed**
3. Sector classification in `infusion:sectors:*` ← **Phase 3 builds this**
4. NSE scraper data (OI, delivery%, FII/DII) ← **Phase 3 builds this**

Phase 2 is **fully operational** without Phase 3. The pipeline runs, features compute,
data is visible in API and WebSocket. Phase 3 adds intelligence on top of this data flow.
