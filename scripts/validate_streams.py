"""Idempotent Redis stream + consumer group bootstrap.
Run on every deployment. Safe to run multiple times.
"""

import asyncio
import os

import redis.asyncio as aioredis

STREAM_CONFIG = {
    "infusion:stream:tick:raw": {
        "maxlen": 50000,
        "groups": ["normalizer-cg"],
    },
    "infusion:stream:tick:normalized": {
        "maxlen": 100000,
        "groups": ["feature-cg", "dashboard-cg"],
    },
    "infusion:stream:feature:computed": {
        "maxlen": 50000,
        "groups": ["scanner-cg", "sector-cg", "conviction-cg", "dashboard-cg"],
    },
    "infusion:stream:scan:signals": {
        "maxlen": 10000,
        "groups": ["alert-cg", "dashboard-cg"],
    },
    "infusion:stream:scan:suppressed": {
        "maxlen": 5000,
        "groups": ["dashboard-cg"],
    },
    "infusion:stream:sector:state": {
        "maxlen": 20000,
        "groups": ["conviction-cg", "dashboard-cg"],
    },
    "infusion:stream:conviction:ranked": {
        "maxlen": 10000,
        "groups": ["alert-cg", "dashboard-cg"],
    },
}

# DLQ streams — no consumer groups, read manually by operators
DLQ_STREAMS = [
    "infusion:dlq:tick:raw",
    "infusion:dlq:tick:normalized",
    "infusion:dlq:feature:computed",
    "infusion:dlq:scan:signals",
    "infusion:dlq:scan:suppressed",
    "infusion:dlq:sector:state",
    "infusion:dlq:conviction:ranked",
]


async def bootstrap_streams(redis_url: str) -> None:
    """Create all streams and consumer groups idempotently."""
    r = aioredis.from_url(redis_url, decode_responses=True)

    try:
        # Test connectivity
        pong = await r.ping()
        print(f"✓ Redis connected (PING={pong})")

        # Create primary streams + consumer groups
        for stream, config in STREAM_CONFIG.items():
            for group in config["groups"]:
                try:
                    await r.xgroup_create(stream, group, id="0", mkstream=True)
                    print(f"  ✓ Created group '{group}' on '{stream}'")
                except aioredis.ResponseError as e:
                    if "BUSYGROUP" in str(e):
                        print(f"  · Group '{group}' on '{stream}' already exists")
                    else:
                        raise

        # Create DLQ streams (just ensure they exist with a dummy entry + trim)
        for dlq in DLQ_STREAMS:
            exists = await r.exists(dlq)
            if not exists:
                # Add and immediately delete a bootstrap entry
                msg_id = await r.xadd(dlq, {"_bootstrap": "1"}, maxlen=1000)
                await r.xdel(dlq, msg_id)
                print(f"  ✓ Created DLQ stream '{dlq}'")
            else:
                print(f"  · DLQ stream '{dlq}' already exists")

        # Summary
        all_streams = await r.keys("infusion:stream:*")
        all_dlqs = await r.keys("infusion:dlq:*")
        print(f"\n═══ Bootstrap complete: {len(all_streams)} streams, {len(all_dlqs)} DLQs ═══")

    finally:
        await r.aclose()


def main() -> None:
    redis_url = os.environ.get("INFUSION_REDIS_URL", "redis://localhost:6379/0")
    print("═══ INFUSION STREAM BOOTSTRAP ═══")
    print(f"Redis: {redis_url}")
    asyncio.run(bootstrap_streams(redis_url))


if __name__ == "__main__":
    main()
