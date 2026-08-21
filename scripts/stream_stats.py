"""Stream stats — operational health overview of all Redis streams.

Usage:
    python scripts/stream_stats.py [--redis-url redis://localhost:6379/0]
"""

import asyncio
import sys
import time

from redis.asyncio import Redis

STREAMS = [
    "infusion:stream:tick:raw",
    "infusion:stream:tick:normalized",
    "infusion:stream:feature:computed",
    "infusion:stream:scan:signals",
    "infusion:stream:scan:suppressed",
    "infusion:stream:sector:state",
    "infusion:stream:conviction:ranked",
]

DLQ_STREAMS = [
    "infusion:dlq:tick:raw",
    "infusion:dlq:tick:normalized",
    "infusion:dlq:feature:computed",
    "infusion:dlq:scan:signals",
    "infusion:dlq:scan:suppressed",
]


async def main():
    redis_url = "redis://localhost:6379/0"
    if "--redis-url" in sys.argv:
        idx = sys.argv.index("--redis-url")
        redis_url = sys.argv[idx + 1]

    redis = Redis.from_url(redis_url, decode_responses=False)
    await redis.ping()

    print("=" * 70)
    print("INFUSION STREAM STATS")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    # Stream lengths
    print("\n--- Primary Streams ---")
    for stream in STREAMS:
        try:
            length = await redis.xlen(stream)
            print(f"  {stream:<50} len={length}")

            # Consumer groups
            try:
                groups = await redis.xinfo_groups(stream)
                for g in groups:
                    name = g.get("name") or g.get(b"name", b"").decode()
                    pending = g.get("pending") or g.get(b"pending", 0)
                    consumers = g.get("consumers") or g.get(b"consumers", 0)
                    lag_str = "  LAGGING!" if pending > 100 else ""
                    print(f"    group={name} pending={pending} consumers={consumers}{lag_str}")
            except Exception:
                pass
        except Exception:
            print(f"  {stream:<50} (not created)")

    # DLQ
    print("\n--- Dead Letter Queues ---")
    for stream in DLQ_STREAMS:
        try:
            length = await redis.xlen(stream)
            status = "  WARNING!" if length > 0 else ""
            print(f"  {stream:<50} len={length}{status}")
        except Exception:
            print(f"  {stream:<50} (empty)")

    # Hot state counts
    print("\n--- Hot State ---")
    tick_keys = []
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="infusion:tick:*", count=100)
        tick_keys.extend(keys)
        if not cursor:
            break
    print(f"  infusion:tick:*          count={len(tick_keys)}")

    feature_keys = []
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="infusion:feature:*", count=100)
        feature_keys.extend(keys)
        if not cursor:
            break
    print(f"  infusion:feature:*       count={len(feature_keys)}")

    # Health
    print("\n--- Service Health ---")
    services = ["ingestion", "normalizer", "feature-engine", "ws-gateway", "api"]
    import msgpack

    for svc in services:
        raw = await redis.get(f"infusion:health:{svc}")
        if raw:
            try:
                info = msgpack.unpackb(raw, raw=False)
                status = info.get("status", "?")
                uptime = info.get("uptime_sec", "?")
                print(f"  {svc:<25} status={status} uptime={uptime}s")
            except Exception:
                print(f"  {svc:<25} status=healthy (raw)")
        else:
            print(f"  {svc:<25} status=NO_HEARTBEAT")

    print("\n" + "=" * 70)
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
