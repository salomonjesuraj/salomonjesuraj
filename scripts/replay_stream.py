"""Replay Redis stream messages — operational debugging tool.

Usage:
    python scripts/replay_stream.py infusion:stream:tick:raw --count 5
    python scripts/replay_stream.py infusion:stream:tick:normalized --count 10
    python scripts/replay_stream.py infusion:stream:feature:computed --count 3 --json
"""

import asyncio
import json
import sys
import time

import msgpack
from redis.asyncio import Redis


async def main():
    stream = sys.argv[1] if len(sys.argv) > 1 else "infusion:stream:tick:raw"
    count = 5
    output_json = False
    redis_url = "redis://localhost:6379/0"

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--count":
            count = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--json":
            output_json = True
            i += 1
        elif sys.argv[i] == "--redis-url":
            redis_url = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    redis = Redis.from_url(redis_url, decode_responses=False)
    await redis.ping()

    # Read latest N messages
    messages = await redis.xrevrange(stream, count=count)

    if not messages:
        print(f"No messages in {stream}")
        await redis.aclose()
        return

    print(f"Stream: {stream}")
    print(f"Messages: {len(messages)} (showing latest {count})")
    print("=" * 70)

    for msg_id, fields in reversed(messages):
        mid = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        raw = fields.get(b"data") or fields.get("data")

        if raw is None:
            print(f"[{mid}] (no data field)")
            continue

        try:
            envelope = msgpack.unpackb(raw, raw=False)
            event_type = envelope.get("t", "?")
            version = envelope.get("v", 0)
            ts = envelope.get("ts", 0)
            rx = envelope.get("rx", 0)
            payload = envelope.get("d", {})

            ts_human = time.strftime("%H:%M:%S", time.gmtime(ts / 1_000_000)) if ts else "?"

            if output_json:
                print(json.dumps({
                    "id": mid, "type": event_type, "version": version,
                    "ts": ts, "rx": rx, "payload": payload,
                }, indent=2, default=str))
            else:
                symbol = payload.get("symbol") or payload.get("instrument_key", "?")
                ltp = payload.get("ltp", "?")
                vol = payload.get("volume", "?")
                print(f"  [{mid}] {event_type} v{version} | {ts_human} UTC | {symbol} ltp={ltp} vol={vol}")

        except Exception as e:
            print(f"  [{mid}] DECODE ERROR: {e}")

    print("=" * 70)
    stream_len = await redis.xlen(stream)
    print(f"Total stream length: {stream_len}")

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
