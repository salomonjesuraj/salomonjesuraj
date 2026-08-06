"""Sprint 2 Integration Test — validates the full realtime pipeline.

Connects to a running system (Redis + services) and verifies:
- Stream topology and data flow
- Schema correctness
- Hot state consistency
- Service health heartbeats
- Consumer group setup
- DLQ emptiness
- Throughput measurement
- Latency measurement
- API endpoint responses
- WS gateway health

Usage:
    python -X utf8 scripts/integration_test.py [--redis-url redis://localhost:6379/0]
"""

import asyncio
import sys
import time

import msgpack
from redis.asyncio import Redis

# Optional HTTP checks
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


RESULTS = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}


def log_result(status, name, detail=""):
    tag = f"[{status.upper()}]"
    msg = f"  {tag:<8} {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    RESULTS[status] = RESULTS.get(status, 0) + 1


async def check_redis_connectivity(redis):
    try:
        await redis.ping()
        info = await redis.info("server")
        version = info.get("redis_version", "?")
        log_result("pass", "Redis connectivity", f"version={version}")
    except Exception as e:
        log_result("fail", "Redis connectivity", str(e))
        return False
    return True


async def check_symbols_seeded(redis):
    count = await redis.hlen("infusion:symbols")
    if count >= 5:
        log_result("pass", "Symbols seeded", f"count={count}")
    elif count > 0:
        log_result("warn", "Symbols seeded", f"count={count}, expected 5")
    else:
        log_result("fail", "Symbols seeded", "infusion:symbols is empty. Run: python scripts/seed_symbols.py")
    return count


async def check_stream_exists(redis, stream, min_messages=1):
    try:
        length = await redis.xlen(stream)
        if length >= min_messages:
            log_result("pass", f"Stream {stream}", f"len={length}")
        else:
            log_result("fail", f"Stream {stream}", f"len={length}, need >={min_messages}")
        return length
    except Exception as e:
        log_result("fail", f"Stream {stream}", str(e))
        return 0


async def check_stream_schema(redis, stream, required_fields, label):
    """Read 3 messages and verify payload contains required fields."""
    messages = await redis.xrevrange(stream, count=3)
    if not messages:
        log_result("fail", f"Schema: {label}", "no messages in stream")
        return False

    all_ok = True
    for msg_id, fields in messages:
        raw = fields.get(b"data") or fields.get("data")
        if not raw:
            log_result("fail", f"Schema: {label}", f"msg {msg_id} has no 'data' field")
            all_ok = False
            continue
        try:
            envelope = msgpack.unpackb(raw, raw=False)
            payload = envelope.get("d", {})
            missing = [f for f in required_fields if f not in payload]
            if missing:
                log_result("fail", f"Schema: {label}", f"missing fields: {missing}")
                all_ok = False
        except Exception as e:
            log_result("fail", f"Schema: {label}", f"decode error: {e}")
            all_ok = False

    if all_ok:
        log_result("pass", f"Schema: {label}", f"verified {len(messages)} messages")
    return all_ok


async def check_hot_state(redis, prefix, expected_symbols, label):
    """Check hot state keys exist for expected symbols."""
    found = []
    for sym in expected_symbols:
        key = f"{prefix}{sym}"
        exists = await redis.exists(key)
        if exists:
            found.append(sym)

    if len(found) >= 3:
        log_result("pass", f"Hot state: {label}", f"found {len(found)}/5: {found}")
    elif found:
        log_result("warn", f"Hot state: {label}", f"found {len(found)}/5: {found}")
    else:
        log_result("fail", f"Hot state: {label}", "no hot state keys found")
    return found


async def check_service_health(redis, services):
    """Check health heartbeats for each service."""
    for svc in services:
        raw = await redis.get(f"infusion:health:{svc}")
        if raw:
            try:
                info = msgpack.unpackb(raw, raw=False)
                status = info.get("status", "?")
                uptime = info.get("uptime_sec", "?")
                if status == "healthy":
                    log_result("pass", f"Health: {svc}", f"uptime={uptime}s")
                else:
                    log_result("warn", f"Health: {svc}", f"status={status}")
            except Exception as e:
                log_result("warn", f"Health: {svc}", f"decode error: {e}")
        else:
            log_result("fail", f"Health: {svc}", "no heartbeat")


async def check_consumer_groups(redis):
    """Verify consumer groups exist on expected streams."""
    checks = [
        ("infusion:stream:tick:raw", "normalizer-cg"),
        ("infusion:stream:tick:normalized", "feature-cg"),
        ("infusion:stream:tick:normalized", "dashboard-cg"),
    ]
    for stream, group in checks:
        try:
            groups = await redis.xinfo_groups(stream)
            group_names = []
            for g in groups:
                name = g.get("name") or g.get(b"name", b"")
                if isinstance(name, bytes):
                    name = name.decode()
                group_names.append(name)

            if group in group_names:
                log_result("pass", f"Consumer group: {group} on {stream.split(':')[-1]}")
            else:
                log_result("fail", f"Consumer group: {group}", f"not found, got: {group_names}")
        except Exception as e:
            log_result("fail", f"Consumer group: {group}", str(e))


async def check_dlq_empty(redis):
    """Verify DLQ streams are empty."""
    dlqs = [
        "infusion:dlq:tick:raw",
        "infusion:dlq:tick:normalized",
        "infusion:dlq:feature:computed",
    ]
    for dlq in dlqs:
        try:
            length = await redis.xlen(dlq)
            if length == 0:
                log_result("pass", f"DLQ empty: {dlq}")
            else:
                log_result("warn", f"DLQ: {dlq}", f"has {length} poison messages")
        except Exception:
            log_result("pass", f"DLQ empty: {dlq}", "stream does not exist (ok)")


async def measure_throughput(redis):
    """Measure tick throughput over 5 seconds."""
    stream = "infusion:stream:tick:raw"
    try:
        start_len = await redis.xlen(stream)
        await asyncio.sleep(5)
        end_len = await redis.xlen(stream)
        delta = end_len - start_len
        rate = delta / 5.0

        if rate > 0:
            log_result("pass", f"Throughput: tick:raw", f"{rate:.1f} msgs/sec ({delta} in 5s)")
        else:
            log_result("warn", f"Throughput: tick:raw", f"0 msgs/sec (pipeline may be stopped)")
    except Exception as e:
        log_result("fail", "Throughput measurement", str(e))


async def measure_latency(redis):
    """Measure normalization and feature latency from recent messages."""
    # Normalization latency
    msgs = await redis.xrevrange("infusion:stream:tick:normalized", count=20)
    norm_latencies = []
    for _, fields in msgs:
        raw = fields.get(b"data") or fields.get("data")
        if not raw:
            continue
        try:
            env = msgpack.unpackb(raw, raw=False)
            p = env.get("d", {})
            rx = p.get("received_at_us", 0)
            nx = p.get("normalized_at_us", 0)
            if rx > 0 and nx > 0:
                norm_latencies.append(nx - rx)
        except Exception:
            pass

    if norm_latencies:
        norm_latencies.sort()
        p50 = norm_latencies[len(norm_latencies) // 2]
        p99 = norm_latencies[int(len(norm_latencies) * 0.99)]
        log_result("pass", "Latency: normalization",
                   f"P50={p50}us ({p50/1000:.1f}ms), P99={p99}us ({p99/1000:.1f}ms), n={len(norm_latencies)}")
    else:
        log_result("skip", "Latency: normalization", "no data")


async def check_api(base_url="http://localhost:8000"):
    """Test API endpoints."""
    if not HAS_AIOHTTP:
        log_result("skip", "API endpoints", "aiohttp not installed")
        return

    endpoints = [
        ("/health", 200),
        ("/api/health", 200),
        ("/api/ticks", 200),
        ("/api/ticks/RELIANCE", [200, 404]),
        ("/api/features/RELIANCE", [200, 404]),
    ]

    try:
        async with aiohttp.ClientSession() as session:
            for path, expected in endpoints:
                try:
                    async with session.get(f"{base_url}{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        ok_codes = expected if isinstance(expected, list) else [expected]
                        if resp.status in ok_codes:
                            log_result("pass", f"API: GET {path}", f"status={resp.status}")
                        else:
                            body = await resp.text()
                            log_result("fail", f"API: GET {path}", f"status={resp.status}: {body[:100]}")
                except asyncio.TimeoutError:
                    log_result("fail", f"API: GET {path}", "timeout")
                except aiohttp.ClientConnectorError:
                    log_result("warn", f"API: GET {path}", "connection refused (API not running?)")
                    return
    except Exception as e:
        log_result("warn", "API endpoints", f"error: {e}")


async def check_ws_gateway(base_url="http://localhost:8001"):
    """Test WS gateway health."""
    if not HAS_AIOHTTP:
        log_result("skip", "WS gateway health", "aiohttp not installed")
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log_result("pass", "WS gateway health", f"clients={data.get('clients', '?')}")
                else:
                    log_result("fail", "WS gateway health", f"status={resp.status}")
    except aiohttp.ClientConnectorError:
        log_result("warn", "WS gateway health", "connection refused (gateway not running?)")
    except Exception as e:
        log_result("warn", "WS gateway health", str(e))


async def main():
    redis_url = "redis://localhost:6379/0"
    if "--redis-url" in sys.argv:
        idx = sys.argv.index("--redis-url")
        redis_url = sys.argv[idx + 1]

    print("=" * 70)
    print("INFUSION SPRINT 2 INTEGRATION TEST")
    print(f"Redis: {redis_url}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    redis = Redis.from_url(redis_url, decode_responses=False)

    # 1. Infrastructure
    print("\n--- INFRASTRUCTURE ---")
    connected = await check_redis_connectivity(redis)
    if not connected:
        print("\n[FATAL] Cannot connect to Redis. Aborting.")
        return

    # 2. Data seeding
    print("\n--- DATA SEEDING ---")
    await check_symbols_seeded(redis)

    # 3. Stream topology
    print("\n--- STREAM TOPOLOGY ---")
    await check_stream_exists(redis, "infusion:stream:tick:raw")
    await check_stream_exists(redis, "infusion:stream:tick:normalized")
    await check_stream_exists(redis, "infusion:stream:feature:computed")

    # 4. Schema validation
    print("\n--- SCHEMA VALIDATION ---")
    await check_stream_schema(
        redis, "infusion:stream:tick:raw",
        ["broker", "instrument_key", "ltp", "exchange_timestamp_ms", "received_at_us"],
        "tick:raw (RawTickV1)",
    )
    await check_stream_schema(
        redis, "infusion:stream:tick:normalized",
        ["symbol", "sector_id", "tier", "ltp", "normalized_at_us"],
        "tick:normalized (NormalizedTickV1)",
    )
    await check_stream_schema(
        redis, "infusion:stream:feature:computed",
        ["symbol", "timestamp_us", "ltp", "rsi_14", "macd", "ema_5", "vwap", "spread_bps"],
        "feature:computed (FeatureVectorV1)",
    )

    # 5. Hot state
    print("\n--- HOT STATE ---")
    symbols = ["RELIANCE", "INFY", "HDFCBANK", "TCS", "NIFTY50"]
    await check_hot_state(redis, "infusion:tick:", symbols, "tick")
    await check_hot_state(redis, "infusion:feature:", symbols, "feature")

    # 6. Service health
    print("\n--- SERVICE HEALTH ---")
    await check_service_health(redis, ["ingestion", "normalizer", "feature-engine", "ws-gateway", "api"])

    # 7. Consumer groups
    print("\n--- CONSUMER GROUPS ---")
    await check_consumer_groups(redis)

    # 8. DLQ
    print("\n--- DEAD LETTER QUEUES ---")
    await check_dlq_empty(redis)

    # 9. Throughput
    print("\n--- THROUGHPUT (5s sample) ---")
    await measure_throughput(redis)

    # 10. Latency
    print("\n--- LATENCY ---")
    await measure_latency(redis)

    # 11. API endpoints
    print("\n--- API ENDPOINTS ---")
    await check_api()

    # 12. WS gateway
    print("\n--- WS GATEWAY ---")
    await check_ws_gateway()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    total = sum(RESULTS.values())
    print(f"  Total checks: {total}")
    print(f"  PASS:  {RESULTS['pass']}")
    print(f"  FAIL:  {RESULTS['fail']}")
    print(f"  WARN:  {RESULTS['warn']}")
    print(f"  SKIP:  {RESULTS['skip']}")

    if RESULTS["fail"] == 0:
        print("\n  VERDICT: PIPELINE OPERATIONAL")
    elif RESULTS["fail"] <= 3:
        print(f"\n  VERDICT: PARTIALLY OPERATIONAL ({RESULTS['fail']} failures)")
    else:
        print(f"\n  VERDICT: NOT OPERATIONAL ({RESULTS['fail']} failures)")

    print("=" * 70)
    await redis.aclose()

    sys.exit(1 if RESULTS["fail"] > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
