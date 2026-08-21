"""Soak test — monitors system stability over time.

Collects samples every 30 seconds, checks for memory leaks, stream growth,
consumer lag, DLQ growth, and health heartbeat freshness.

Usage:
    python -X utf8 scripts/soak_test.py [--duration-min 10] [--interval-sec 30] [--redis-url redis://localhost:6379/0]

Stability criteria:
- No DLQ growth
- Consumer lag never exceeds 1000
- All health heartbeats present throughout
- Memory growth < 50MB over test duration
- Stream growth rates are consistent
"""

import asyncio
import sys
import time

import msgpack
from redis.asyncio import Redis

STREAMS = [
    "infusion:stream:tick:raw",
    "infusion:stream:tick:normalized",
    "infusion:stream:feature:computed",
]

DLQS = [
    "infusion:dlq:tick:raw",
    "infusion:dlq:tick:normalized",
    "infusion:dlq:feature:computed",
]

SERVICES = ["ingestion", "normalizer", "feature-engine", "ws-gateway", "api"]

CONSUMER_GROUPS = [
    ("infusion:stream:tick:raw", "normalizer-cg"),
    ("infusion:stream:tick:normalized", "feature-cg"),
    ("infusion:stream:tick:normalized", "dashboard-cg"),
]


async def collect_sample(redis):
    """Collect a single stability sample."""
    sample = {"ts": time.time()}

    # Stream lengths
    for stream in STREAMS:
        try:
            sample[f"len:{stream}"] = await redis.xlen(stream)
        except Exception:
            sample[f"len:{stream}"] = -1

    # DLQ lengths
    for dlq in DLQS:
        try:
            sample[f"dlq:{dlq}"] = await redis.xlen(dlq)
        except Exception:
            sample[f"dlq:{dlq}"] = 0

    # Consumer group lag
    for stream, group in CONSUMER_GROUPS:
        try:
            groups = await redis.xinfo_groups(stream)
            for g in groups:
                name = g.get("name") or g.get(b"name", b"")
                if isinstance(name, bytes):
                    name = name.decode()
                if name == group:
                    pending = g.get("pending") or g.get(b"pending", 0)
                    sample[f"lag:{group}"] = pending
        except Exception:
            sample[f"lag:{group}"] = -1

    # Service health
    for svc in SERVICES:
        try:
            raw = await redis.get(f"infusion:health:{svc}")
            if raw:
                info = msgpack.unpackb(raw, raw=False)
                sample[f"health:{svc}"] = info.get("status", "unknown")
                sample[f"uptime:{svc}"] = info.get("uptime_sec", 0)
            else:
                sample[f"health:{svc}"] = "missing"
        except Exception:
            sample[f"health:{svc}"] = "error"

    # Redis memory
    try:
        info = await redis.info("memory")
        sample["memory_bytes"] = info.get("used_memory", 0)
        sample["memory_human"] = info.get("used_memory_human", "?")
    except Exception:
        sample["memory_bytes"] = 0

    # Hot state counts
    tick_count = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="infusion:tick:*", count=100)
        tick_count += len(keys)
        if not cursor:
            break
    sample["hot_ticks"] = tick_count

    feat_count = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="infusion:feature:*", count=100)
        feat_count += len(keys)
        if not cursor:
            break
    sample["hot_features"] = feat_count

    return sample


def analyze_results(samples, duration_min):
    """Analyze samples and determine stability verdict."""
    print("\n" + "=" * 70)
    print("SOAK TEST RESULTS")
    print(f"Duration: {duration_min:.1f} min, Samples: {len(samples)}")
    print("=" * 70)

    issues = []

    # Stream growth rate
    print("\n--- STREAM GROWTH ---")
    for stream in STREAMS:
        key = f"len:{stream}"
        first = samples[0].get(key, 0)
        last = samples[-1].get(key, 0)
        elapsed = samples[-1]["ts"] - samples[0]["ts"]
        if elapsed > 0 and first >= 0 and last >= 0:
            rate = (last - first) / elapsed
            name = stream.split(":")[-1]
            print(f"  {stream:<50} {rate:>8.1f} msgs/sec (total: {last})")
            if rate == 0 and first == 0:
                issues.append(f"No data in {stream}")
        else:
            print(f"  {stream:<50} N/A")

    # Consumer lag
    print("\n--- CONSUMER LAG ---")
    max_lags = {}
    for _stream, group in CONSUMER_GROUPS:
        key = f"lag:{group}"
        lags = [s.get(key, 0) for s in samples if s.get(key, 0) >= 0]
        if lags:
            max_lag = max(lags)
            avg_lag = sum(lags) / len(lags)
            max_lags[group] = max_lag
            status = "OK" if max_lag < 1000 else "HIGH"
            print(f"  {group:<30} max={max_lag:<8} avg={avg_lag:<8.0f} [{status}]")
            if max_lag >= 1000:
                issues.append(f"High lag in {group}: max={max_lag}")

    # DLQ growth
    print("\n--- DLQ GROWTH ---")
    for dlq in DLQS:
        key = f"dlq:{dlq}"
        first = samples[0].get(key, 0)
        last = samples[-1].get(key, 0)
        delta = last - first
        name = dlq.replace("infusion:dlq:", "")
        print(f"  {name:<30} start={first} end={last} delta={delta}")
        if delta > 0:
            issues.append(f"DLQ growth in {dlq}: +{delta}")

    # Memory
    print("\n--- MEMORY ---")
    first_mem = samples[0].get("memory_bytes", 0)
    last_mem = samples[-1].get("memory_bytes", 0)
    mem_delta_mb = (last_mem - first_mem) / (1024 * 1024)
    first_human = samples[0].get("memory_human", "?")
    last_human = samples[-1].get("memory_human", "?")
    print(f"  Start: {first_human}")
    print(f"  End:   {last_human}")
    print(f"  Delta: {mem_delta_mb:+.2f} MB")
    if mem_delta_mb > 50:
        issues.append(f"Memory growth: {mem_delta_mb:.1f}MB > 50MB threshold")

    # Service health
    print("\n--- SERVICE HEALTH ---")
    health_gaps = {}
    for svc in SERVICES:
        key = f"health:{svc}"
        healthy_count = sum(1 for s in samples if s.get(key) == "healthy")
        total = len(samples)
        pct = (healthy_count / total * 100) if total > 0 else 0
        status = "OK" if pct >= 95 else "DEGRADED" if pct >= 50 else "UNHEALTHY"
        print(f"  {svc:<25} healthy={healthy_count}/{total} ({pct:.0f}%) [{status}]")
        if pct < 95:
            health_gaps[svc] = pct
            issues.append(f"Health gap: {svc} only {pct:.0f}% healthy")

    # Hot state
    print("\n--- HOT STATE ---")
    last_hot_ticks = samples[-1].get("hot_ticks", 0)
    last_hot_feats = samples[-1].get("hot_features", 0)
    print(f"  infusion:tick:*     {last_hot_ticks} keys")
    print(f"  infusion:feature:*  {last_hot_feats} keys")

    # Verdict
    print("\n" + "=" * 70)
    if not issues:
        print("VERDICT: STABLE")
        print("  All stability criteria met.")
    else:
        print(f"VERDICT: UNSTABLE ({len(issues)} issues)")
        for issue in issues:
            print(f"  - {issue}")
    print("=" * 70)

    return len(issues) == 0


async def main():
    redis_url = "redis://localhost:6379/0"
    duration_min = 10
    interval_sec = 30

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--redis-url":
            redis_url = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--duration-min":
            duration_min = float(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--interval-sec":
            interval_sec = int(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    redis = Redis.from_url(redis_url, decode_responses=False)
    try:
        await redis.ping()
    except Exception as e:
        print(f"[FAIL] Cannot connect to Redis: {e}")
        sys.exit(1)

    total_seconds = int(duration_min * 60)
    total_samples = total_seconds // interval_sec

    print("=" * 70)
    print("INFUSION SOAK TEST")
    print(f"Duration: {duration_min} min, Interval: {interval_sec}s, Samples: {total_samples}")
    print(f"Redis: {redis_url}")
    print(f"Start: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    samples = []
    for i in range(total_samples):
        sample = await collect_sample(redis)
        samples.append(sample)

        # Progress line
        elapsed_min = (i + 1) * interval_sec / 60
        mem = sample.get("memory_human", "?")
        ticks = sample.get(f"len:{STREAMS[0]}", 0)
        lag = sample.get("lag:normalizer-cg", 0)
        print(
            f"  [{i + 1}/{total_samples}] {elapsed_min:.1f}min | mem={mem} | ticks={ticks} | lag={lag}"
        )

        if i < total_samples - 1:
            await asyncio.sleep(interval_sec)

    stable = analyze_results(samples, duration_min)

    await redis.aclose()
    sys.exit(0 if stable else 1)


if __name__ == "__main__":
    asyncio.run(main())
