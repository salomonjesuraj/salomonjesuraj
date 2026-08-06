"""Reconnect and recovery testing.

Tests system resilience to infrastructure failures:
- Redis restart recovery
- Individual service restart
- Partial pipeline restart

Usage:
    python -X utf8 scripts/test_reconnect.py [--redis-url redis://localhost:6379/0]

WARNING: This script will restart Docker containers. Run against dev only.
"""

import asyncio
import subprocess
import sys
import time

import msgpack
from redis.asyncio import Redis


RESULTS = {"pass": 0, "fail": 0, "skip": 0}


def log_result(status, name, detail=""):
    tag = f"[{status.upper()}]"
    msg = f"  {tag:<8} {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    RESULTS[status] = RESULTS.get(status, 0) + 1


def docker_compose(*args):
    """Run docker compose command."""
    cmd = ["docker", "compose"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return result.returncode == 0, result.stdout, result.stderr


async def wait_for_redis(redis_url, timeout=30):
    """Wait until Redis is reachable."""
    redis = Redis.from_url(redis_url, decode_responses=False)
    start = time.time()
    while time.time() - start < timeout:
        try:
            await redis.ping()
            await redis.aclose()
            return True
        except Exception:
            await asyncio.sleep(0.5)
    try:
        await redis.aclose()
    except Exception:
        pass
    return False


async def wait_for_stream_growth(redis_url, stream, timeout=30):
    """Wait until stream has new messages (compares last entry ID, not XLEN)."""
    redis = Redis.from_url(redis_url, decode_responses=False)
    try:
        # Get the current last entry ID
        entries = await redis.xrevrange(stream, count=1)
        start_id = entries[0][0] if entries else b"0-0"

        start = time.time()
        while time.time() - start < timeout:
            await asyncio.sleep(2)
            entries = await redis.xrevrange(stream, count=1)
            current_id = entries[0][0] if entries else b"0-0"
            if current_id != start_id:
                # Count approximate new messages via XRANGE between IDs
                new_entries = await redis.xrange(stream, min=start_id, count=100)
                delta = max(1, len(new_entries) - 1)  # -1 to exclude start_id itself
                await redis.aclose()
                return True, delta
        await redis.aclose()
        return False, 0
    except Exception as e:
        try:
            await redis.aclose()
        except Exception:
            pass
        return False, 0


async def wait_for_health(redis_url, service, timeout=30):
    """Wait until service health heartbeat appears."""
    redis = Redis.from_url(redis_url, decode_responses=False)
    start = time.time()
    while time.time() - start < timeout:
        try:
            raw = await redis.get(f"infusion:health:{service}")
            if raw:
                info = msgpack.unpackb(raw, raw=False)
                if info.get("status") == "healthy":
                    await redis.aclose()
                    return True
        except Exception:
            pass
        await asyncio.sleep(1)
    try:
        await redis.aclose()
    except Exception:
        pass
    return False


async def test_service_restart(redis_url, service_name, verify_stream):
    """Test that a single service recovers after restart."""
    print(f"\n--- SERVICE RESTART: {service_name} ---")

    # 1. Verify running
    recovered = await wait_for_health(redis_url, service_name, timeout=5)
    if not recovered:
        log_result("skip", f"{service_name} restart", "service not running")
        return

    # 2. Restart
    print(f"  Restarting {service_name}...")
    ok, _, err = docker_compose("restart", service_name)
    if not ok:
        log_result("fail", f"{service_name} restart", f"docker compose restart failed: {err[:100]}")
        return

    # 3. Wait for health
    print(f"  Waiting for {service_name} health...")
    recovered = await wait_for_health(redis_url, service_name, timeout=30)
    if recovered:
        log_result("pass", f"{service_name} restart", "health restored")
    else:
        log_result("fail", f"{service_name} restart", "health not restored within 30s")
        return

    # 4. Wait for stream activity
    print(f"  Waiting for stream activity on {verify_stream}...")
    growing, delta = await wait_for_stream_growth(redis_url, verify_stream, timeout=30)
    if growing:
        log_result("pass", f"{service_name} data flow", f"+{delta} messages after restart")
    else:
        log_result("fail", f"{service_name} data flow", "no new messages after restart")


async def test_redis_restart(redis_url):
    """Test that all services recover after Redis restart."""
    print("\n--- REDIS RESTART RECOVERY ---")
    print("  WARNING: This will briefly interrupt all services")

    # 1. Verify baseline
    redis = Redis.from_url(redis_url, decode_responses=False)
    try:
        await redis.ping()
        baseline_len = await redis.xlen("infusion:stream:tick:raw")
        await redis.aclose()
    except Exception as e:
        log_result("skip", "Redis restart", f"Redis not reachable: {e}")
        return

    # 2. Restart Redis
    print("  Restarting Redis...")
    ok, _, err = docker_compose("restart", "redis")
    if not ok:
        log_result("fail", "Redis restart", f"command failed: {err[:100]}")
        return

    # 3. Wait for Redis
    print("  Waiting for Redis...")
    recovered = await wait_for_redis(redis_url, timeout=30)
    if not recovered:
        log_result("fail", "Redis recovery", "Redis not reachable after 30s")
        return
    log_result("pass", "Redis recovery", "Redis reachable")

    # 4. Re-seed symbols (lost on Redis restart with appendonly no)
    print("  Re-seeding symbols...")
    result = subprocess.run(
        [sys.executable, "scripts/seed_symbols.py"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        log_result("warn", "Symbol re-seed", f"failed: {result.stderr[:100]}")

    # 5. Wait for services to recover
    print("  Waiting for services to recover (45s)...")
    await asyncio.sleep(15)  # Give services time to reconnect

    for svc in ["ingestion", "normalizer", "feature-engine"]:
        recovered = await wait_for_health(redis_url, svc, timeout=30)
        if recovered:
            log_result("pass", f"{svc} recovered after Redis restart")
        else:
            log_result("fail", f"{svc} recovered after Redis restart", "no health after 30s")

    # 6. Verify data flow resumed
    growing, delta = await wait_for_stream_growth(redis_url, "infusion:stream:tick:raw", timeout=30)
    if growing:
        log_result("pass", "Pipeline resumed", f"+{delta} ticks after Redis restart")
    else:
        log_result("fail", "Pipeline resumed", "no new ticks after Redis restart")


async def test_partial_pipeline_restart(redis_url):
    """Test restarting normalizer while ingestion continues."""
    print("\n--- PARTIAL PIPELINE RESTART (normalizer) ---")

    # 1. Check ingestion is producing
    growing, _ = await wait_for_stream_growth(redis_url, "infusion:stream:tick:raw", timeout=10)
    if not growing:
        log_result("skip", "Partial restart", "ingestion not producing ticks")
        return

    # 2. Restart normalizer
    print("  Restarting normalizer...")
    docker_compose("restart", "normalizer")

    # 3. Verify ingestion kept running
    await asyncio.sleep(5)
    growing, delta = await wait_for_stream_growth(redis_url, "infusion:stream:tick:raw", timeout=10)
    if growing:
        log_result("pass", "Ingestion survived normalizer restart", f"+{delta} ticks")
    else:
        log_result("fail", "Ingestion survived normalizer restart")

    # 4. Verify normalizer recovered
    recovered = await wait_for_health(redis_url, "normalizer", timeout=30)
    if recovered:
        log_result("pass", "Normalizer recovered")
    else:
        log_result("fail", "Normalizer recovery", "no health after 30s")

    # 5. Verify normalization resumed
    growing, delta = await wait_for_stream_growth(redis_url, "infusion:stream:tick:normalized", timeout=30)
    if growing:
        log_result("pass", "Normalization resumed", f"+{delta} messages")
    else:
        log_result("fail", "Normalization resumed")

    # 6. Check for message loss (lag should drain)
    redis = Redis.from_url(redis_url, decode_responses=False)
    try:
        groups = await redis.xinfo_groups("infusion:stream:tick:raw")
        for g in groups:
            name = g.get("name") or g.get(b"name", b"")
            if isinstance(name, bytes):
                name = name.decode()
            if name == "normalizer-cg":
                pending = g.get("pending") or g.get(b"pending", 0)
                if pending < 500:
                    log_result("pass", "Normalizer lag drained", f"pending={pending}")
                else:
                    log_result("warn", "Normalizer lag", f"pending={pending} (draining)")
        await redis.aclose()
    except Exception:
        pass


async def main():
    redis_url = "redis://localhost:6379/0"
    skip_redis_restart = False

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--redis-url":
            redis_url = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--skip-redis":
            skip_redis_restart = True
            i += 1
        else:
            i += 1

    print("=" * 70)
    print("INFUSION RECONNECT/RECOVERY TEST")
    print(f"Redis: {redis_url}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)
    print("\nWARNING: This test will restart Docker containers!")
    print("Press Ctrl+C within 5 seconds to abort...")
    await asyncio.sleep(5)

    # Test individual service restarts
    await test_service_restart(redis_url, "ingestion", "infusion:stream:tick:raw")
    await test_service_restart(redis_url, "normalizer", "infusion:stream:tick:normalized")
    await test_service_restart(redis_url, "feature-engine", "infusion:stream:feature:computed")

    # Test partial pipeline restart
    await test_partial_pipeline_restart(redis_url)

    # Test Redis restart (most destructive)
    if not skip_redis_restart:
        await test_redis_restart(redis_url)
    else:
        print("\n--- REDIS RESTART SKIPPED (--skip-redis) ---")

    # Summary
    print("\n" + "=" * 70)
    print("RECONNECT TEST SUMMARY")
    print(f"  PASS: {RESULTS['pass']}")
    print(f"  FAIL: {RESULTS['fail']}")
    print(f"  SKIP: {RESULTS['skip']}")
    verdict = "RESILIENT" if RESULTS["fail"] == 0 else "FRAGILE"
    print(f"\n  VERDICT: {verdict}")
    print("=" * 70)

    sys.exit(1 if RESULTS["fail"] > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
