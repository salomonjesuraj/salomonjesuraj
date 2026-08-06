"""Pipeline throughput and latency benchmark.

Measures real performance of the ingestion -> normalization -> feature pipeline.

Usage:
    python -X utf8 scripts/benchmark_pipeline.py [--duration-sec 30] [--redis-url redis://localhost:6379/0]
"""

import asyncio
import sys
import time
import statistics

import msgpack
from redis.asyncio import Redis


STREAMS = {
    "tick:raw": "infusion:stream:tick:raw",
    "tick:normalized": "infusion:stream:tick:normalized",
    "feature:computed": "infusion:stream:feature:computed",
}


async def measure_throughput(redis: Redis, duration_sec: int):
    """Measure messages/sec for each stream over the duration."""
    print(f"\n--- THROUGHPUT BENCHMARK ({duration_sec}s) ---\n")

    # Initial counts
    start_counts = {}
    for name, stream in STREAMS.items():
        try:
            start_counts[name] = await redis.xlen(stream)
        except Exception:
            start_counts[name] = 0

    start_time = time.time()
    samples = {name: [] for name in STREAMS}

    # Sample every second
    for i in range(duration_sec):
        await asyncio.sleep(1.0)
        elapsed = time.time() - start_time
        for name, stream in STREAMS.items():
            try:
                current = await redis.xlen(stream)
                rate = (current - start_counts[name]) / elapsed
                samples[name].append(rate)
            except Exception:
                pass

        # Progress
        if (i + 1) % 5 == 0:
            print(f"  ... {i + 1}/{duration_sec}s sampled")

    # Results
    print(f"\n  Stream Throughput (msgs/sec):")
    print(f"  {'Stream':<25} {'Avg':>10} {'Min':>10} {'Max':>10} {'StdDev':>10}")
    print(f"  {'-'*65}")

    for name in STREAMS:
        if samples[name]:
            avg = statistics.mean(samples[name])
            mn = min(samples[name])
            mx = max(samples[name])
            sd = statistics.stdev(samples[name]) if len(samples[name]) > 1 else 0
            print(f"  {name:<25} {avg:>10.1f} {mn:>10.1f} {mx:>10.1f} {sd:>10.1f}")
        else:
            print(f"  {name:<25} {'N/A':>10}")

    final_counts = {}
    for name, stream in STREAMS.items():
        try:
            final_counts[name] = await redis.xlen(stream)
        except Exception:
            final_counts[name] = 0

    elapsed = time.time() - start_time
    print(f"\n  Total messages produced in {elapsed:.1f}s:")
    for name in STREAMS:
        delta = final_counts.get(name, 0) - start_counts.get(name, 0)
        print(f"    {name}: {delta}")

    return samples


async def measure_latency(redis: Redis, sample_count: int = 50):
    """Measure pipeline latency by reading recent messages."""
    print(f"\n--- LATENCY BENCHMARK ({sample_count} samples) ---\n")

    latencies = {
        "normalization_us": [],
        "feature_us": [],
        "e2e_us": [],
    }

    # Read recent normalized ticks
    norm_msgs = await redis.xrevrange(
        "infusion:stream:tick:normalized", count=sample_count
    )

    for msg_id, fields in norm_msgs:
        raw = fields.get(b"data") or fields.get("data")
        if not raw:
            continue
        try:
            envelope = msgpack.unpackb(raw, raw=False)
            payload = envelope.get("d", {})
            received_at = payload.get("received_at_us", 0)
            normalized_at = payload.get("normalized_at_us", 0)
            if received_at > 0 and normalized_at > 0:
                latencies["normalization_us"].append(normalized_at - received_at)
        except Exception:
            pass

    # Read recent feature vectors
    feat_msgs = await redis.xrevrange(
        "infusion:stream:feature:computed", count=sample_count
    )

    for msg_id, fields in feat_msgs:
        raw = fields.get(b"data") or fields.get("data")
        if not raw:
            continue
        try:
            envelope = msgpack.unpackb(raw, raw=False)
            payload = envelope.get("d", {})
            timestamp_us = payload.get("timestamp_us", 0)
            rx_us = envelope.get("rx", 0)
            if timestamp_us > 0 and rx_us > 0:
                latencies["feature_us"].append(timestamp_us - rx_us)
        except Exception:
            pass

    # E2E: compare tick:raw received_at_us to feature:computed timestamp_us
    raw_msgs = await redis.xrevrange(
        "infusion:stream:tick:raw", count=sample_count
    )
    raw_times = []
    for msg_id, fields in raw_msgs:
        raw_data = fields.get(b"data") or fields.get("data")
        if not raw_data:
            continue
        try:
            envelope = msgpack.unpackb(raw_data, raw=False)
            payload = envelope.get("d", {})
            raw_times.append(payload.get("received_at_us", 0))
        except Exception:
            pass

    feat_times = []
    for msg_id, fields in feat_msgs:
        raw_data = fields.get(b"data") or fields.get("data")
        if not raw_data:
            continue
        try:
            envelope = msgpack.unpackb(raw_data, raw=False)
            payload = envelope.get("d", {})
            feat_times.append(payload.get("timestamp_us", 0))
        except Exception:
            pass

    if raw_times and feat_times:
        # Approximate E2E by pairing latest raw with latest feature
        for i in range(min(len(raw_times), len(feat_times))):
            e2e = feat_times[i] - raw_times[i]
            if e2e > 0:
                latencies["e2e_us"].append(e2e)

    # Print results
    print(f"  {'Metric':<25} {'P50':>10} {'P95':>10} {'P99':>10} {'Max':>10} {'Samples':>10}")
    print(f"  {'-'*75}")

    for name, values in latencies.items():
        if not values:
            print(f"  {name:<25} {'N/A':>10}")
            continue
        values.sort()
        n = len(values)
        p50 = values[n // 2]
        p95 = values[int(n * 0.95)]
        p99 = values[int(n * 0.99)]
        mx = values[-1]
        print(f"  {name:<25} {p50:>10} {p95:>10} {p99:>10} {mx:>10} {n:>10}")

    # Thresholds
    print(f"\n  Performance Targets:")
    if latencies["normalization_us"]:
        p99_norm = sorted(latencies["normalization_us"])[int(len(latencies["normalization_us"]) * 0.99)]
        status = "PASS" if p99_norm < 1_000_000 else "FAIL"  # < 1 second
        print(f"  [{status}] Normalization P99 < 1s: {p99_norm}us ({p99_norm/1000:.1f}ms)")

    if latencies["feature_us"]:
        p99_feat = sorted(latencies["feature_us"])[int(len(latencies["feature_us"]) * 0.99)]
        status = "PASS" if p99_feat < 5_000_000 else "FAIL"  # < 5 seconds
        print(f"  [{status}] Feature P99 < 5s: {p99_feat}us ({p99_feat/1000:.1f}ms)")

    return latencies


async def measure_redis_latency(redis: Redis, iterations: int = 100):
    """Measure raw Redis XADD + XLEN latency."""
    print(f"\n--- REDIS OPERATION LATENCY ({iterations} ops) ---\n")

    # PING latency
    ping_times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await redis.ping()
        elapsed = (time.perf_counter_ns() - start) / 1000
        ping_times.append(elapsed)

    # XLEN latency
    xlen_times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await redis.xlen("infusion:stream:tick:raw")
        elapsed = (time.perf_counter_ns() - start) / 1000
        xlen_times.append(elapsed)

    # HGETALL latency (hot state read)
    hget_times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await redis.hgetall("infusion:tick:RELIANCE")
        elapsed = (time.perf_counter_ns() - start) / 1000
        hget_times.append(elapsed)

    print(f"  {'Operation':<25} {'P50':>10} {'P95':>10} {'P99':>10} {'Max':>10}")
    print(f"  {'-'*65}")

    for name, values in [("PING", ping_times), ("XLEN", xlen_times), ("HGETALL", hget_times)]:
        values.sort()
        n = len(values)
        p50 = values[n // 2]
        p95 = values[int(n * 0.95)]
        p99 = values[int(n * 0.99)]
        mx = values[-1]
        print(f"  {name:<25} {p50:>8.0f}us {p95:>8.0f}us {p99:>8.0f}us {mx:>8.0f}us")


async def measure_memory(redis: Redis):
    """Check Redis memory usage."""
    print(f"\n--- REDIS MEMORY ---\n")

    info = await redis.info("memory")
    used = info.get("used_memory_human", "?")
    peak = info.get("used_memory_peak_human", "?")
    frag = info.get("mem_fragmentation_ratio", "?")

    print(f"  Used:         {used}")
    print(f"  Peak:         {peak}")
    print(f"  Fragmentation: {frag}")

    # Stream-specific memory
    for name, stream in STREAMS.items():
        try:
            info = await redis.xinfo_stream(stream)
            length = info.get("length") or info.get(b"length", 0)
            print(f"  {name}: {length} messages")
        except Exception:
            print(f"  {name}: not found")


async def main():
    redis_url = "redis://localhost:6379/0"
    duration_sec = 30
    sample_count = 50

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--redis-url":
            redis_url = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--duration-sec":
            duration_sec = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--samples":
            sample_count = int(sys.argv[i + 1])
            i += 2
        else:
            i += 1

    redis = Redis.from_url(redis_url, decode_responses=False)
    try:
        await redis.ping()
    except Exception as e:
        print(f"[FAIL] Cannot connect to Redis: {e}")
        return

    print("=" * 75)
    print("INFUSION PIPELINE BENCHMARK")
    print(f"Redis: {redis_url}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 75)

    await measure_memory(redis)
    await measure_redis_latency(redis)
    await measure_throughput(redis, duration_sec)
    await measure_latency(redis, sample_count)

    print("\n" + "=" * 75)
    print("BENCHMARK COMPLETE")
    print("=" * 75)

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
