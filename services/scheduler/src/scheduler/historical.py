"""Upstox historical-data bootstrap for chart candles and relative volume."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote

import aiohttp
import msgpack
import structlog

logger = structlog.get_logger()
UPSTOX_API_BASE = "https://api.upstox.com/v3"

# EB-15 Phase 1 item 1 -- relative-strength benchmark indices.
#
# EB-3's multi-timeframe RS (api/relative_strength.py) and VCP's own RS
# component (api/vcp.py) both hardcode NIFTY50 as the sole benchmark,
# reading infusion:ohlc:NIFTY50:daily directly (see mtf.py's
# NIFTY50_DAILY_KEY). That key is populated by this same bootstrap loop,
# via _universe()'s instrument list below -- but _universe() only ever saw
# whatever nse-scraper had already written to infusion:symbols, and
# nse-scraper's own loader.py deliberately EXCLUDES indices.json whenever
# INFUSION_SYMBOL_UNIVERSE=fno ("fno is stocks-only for scanner purity...
# Top market ticker fetches NIFTY/BANKNIFTY separately" -- see loader.py's
# own docstring/comment). Confirmed live: this deployment's real .env sets
# SYMBOL_UNIVERSE=fno, so infusion:symbols has held zero NSE_INDEX| keys
# since the day EBIE's RS/VCP features shipped -- NIFTY50 was never
# bootstrapped and every RS/VCP consumer has been silently reporting
# "unavailable" (correctly, honestly -- but never actually working).
#
# Fixed HERE, not in nse-scraper: this loop's job is fetching benchmark
# history for RS math, not defining the tradeable/scanned universe, so it
# independently guarantees these regardless of which tier nse-scraper is
# configured for. nse-scraper's fno-tier exclusion stays exactly as
# designed -- indices still never enter the scanner's own symbol universe.
BENCHMARK_INDICES: dict[str, str] = {
    "NIFTY50": "NSE_INDEX|Nifty 50",
}

# Below this many cached daily bars, an RS/VCP 60D lookback window can't be
# trusted even though the Redis key technically exists (e.g. moments after a
# fresh bootstrap). Real precedent already observed live during EB-3/VCP:
# Upstox's historical-candle endpoint caps index lookback tighter than
# equity lookback (NIFTY50 ~120-250 trading days vs a stock's ~250-370).
_BENCHMARK_READY_MIN_BARS = 200
_BENCHMARK_DEGRADED_MIN_BARS = 1


def _bar(raw: list[Any]) -> tuple[int, dict[str, int | float]] | None:
    if len(raw) < 6:
        return None
    try:
        dt = datetime.fromisoformat(str(raw[0]))
        ts = int(dt.timestamp())
        return ts, {
            "t": ts,
            "o": float(raw[1]),
            "h": float(raw[2]),
            "l": float(raw[3]),
            "c": float(raw[4]),
            "v": int(float(raw[5])),
        }
    except (TypeError, ValueError):
        return None


async def _universe(redis: Any) -> dict[str, str]:
    symbols_raw = await redis.hgetall("infusion:symbols")
    result: dict[str, str] = {}
    for key, value in symbols_raw.items():
        instrument_key = key.decode() if isinstance(key, bytes) else key
        try:
            payload = msgpack.unpackb(value, raw=False) if isinstance(value, bytes) else value
            symbol = payload.get("symbol")
            if symbol and instrument_key.startswith(("NSE_EQ|", "NSE_INDEX|")):
                result[symbol] = instrument_key
        except Exception:
            continue
    # Always ensure RS-benchmark indices are present, regardless of what
    # infusion:symbols contains for the currently-configured tier (see the
    # BENCHMARK_INDICES comment above). setdefault -- a tier that already
    # includes indices (nifty50/100/200/500) is left untouched, this only
    # fills the gap fno-tier deployments otherwise leave permanently empty.
    for symbol, instrument_key in BENCHMARK_INDICES.items():
        result.setdefault(symbol, instrument_key)
    return result


async def _benchmark_status(redis: Any) -> dict[str, str]:
    """READY / DEGRADED(n_bars) / UNAVAILABLE per benchmark index, from a
    real ZCARD count -- never inferred, never assumed present. Folded into
    bootstrap_historical()'s own return dict so the scheduler's existing
    per-cycle log line (main.py: logger.info("historical_bootstrap",
    **result)) surfaces this on every run including the very first one at
    startup -- a missing/thin RS benchmark is now visible in ops logs
    directly, not just buried as a per-symbol "unavailable" reason deep
    inside an individual RS/VCP feature response."""
    status: dict[str, str] = {}
    for symbol in BENCHMARK_INDICES:
        try:
            count = await redis.zcard(f"infusion:ohlc:{symbol}:daily")
        except Exception:
            count = 0
        if count >= _BENCHMARK_READY_MIN_BARS:
            status[symbol] = "READY"
        elif count >= _BENCHMARK_DEGRADED_MIN_BARS:
            status[symbol] = f"DEGRADED({count}_bars)"
        else:
            status[symbol] = "UNAVAILABLE"
    return status


async def _fetch_historical(
    session: aiohttp.ClientSession,
    instrument_key: str,
    unit: str,
    interval: int,
    start: date,
    end: date,
) -> list[list[Any]]:
    encoded_key = quote(instrument_key, safe="")
    url = (
        f"{UPSTOX_API_BASE}/historical-candle/{encoded_key}/"
        f"{unit}/{interval}/{end.isoformat()}/{start.isoformat()}"
    )
    async with session.get(
        url, headers={"Accept": "application/json"}, timeout=aiohttp.ClientTimeout(total=30)
    ) as response:
        payload = cast(dict[str, Any], await response.json())
        if response.status == 429:
            raise RuntimeError("upstox_rate_limited")
        if response.status != 200 or payload.get("status") != "success":
            raise RuntimeError(payload.get("message", f"upstox_http_{response.status}"))
        return cast(list[list[Any]], payload.get("data", {}).get("candles", []))


async def _fetch_intraday(
    session: aiohttp.ClientSession,
    instrument_key: str,
    unit: str = "minutes",
    interval: int = 1,
) -> list[list[Any]]:
    encoded_key = quote(instrument_key, safe="")
    url = f"{UPSTOX_API_BASE}/historical-candle/intraday/{encoded_key}/{unit}/{interval}"
    async with session.get(
        url, headers={"Accept": "application/json"}, timeout=aiohttp.ClientTimeout(total=30)
    ) as response:
        payload = cast(dict[str, Any], await response.json())
        if response.status == 429:
            raise RuntimeError("upstox_rate_limited")
        if response.status != 200 or payload.get("status") != "success":
            raise RuntimeError(payload.get("message", f"upstox_http_{response.status}"))
        return cast(list[list[Any]], payload.get("data", {}).get("candles", []))


async def _store_zset(redis: Any, key: str, rows: list[list[Any]], ttl: int) -> None:
    mapping: dict[str, int] = {}
    for raw in rows:
        parsed = _bar(raw)
        if parsed:
            ts, bar = parsed
            mapping[json.dumps(bar, separators=(",", ":"))] = ts
    if mapping:
        pipe = redis.pipeline(transaction=False)
        pipe.delete(key)
        pipe.zadd(key, mapping)
        pipe.expire(key, ttl)
        await pipe.execute()


async def _store_volume_profile(redis: Any, symbol: str, rows: list[list[Any]]) -> None:
    sessions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for raw in rows:
        try:
            dt = datetime.fromisoformat(str(raw[0]))
            minute = dt.hour * 60 + dt.minute - 555
            if 0 <= minute <= 375:
                sessions[dt.date().isoformat()].append((minute, int(float(raw[5]))))
        except (IndexError, TypeError, ValueError):
            continue
    totals: dict[int, list[int]] = defaultdict(list)
    for _, bars in list(sorted(sessions.items()))[-20:]:
        cumulative = 0
        for minute, volume in sorted(bars):
            cumulative += max(volume, 0)
            totals[minute].append(cumulative)
    profile = {str(k): str(sum(v) / len(v)) for k, v in totals.items() if v}
    if profile:
        key = f"infusion:volume-profile:{symbol}"
        pipe = redis.pipeline(transaction=False)
        pipe.delete(key)
        pipe.hset(key, mapping=profile)
        pipe.expire(key, 14 * 86400)
        await pipe.execute()


async def bootstrap_historical(redis: Any) -> dict[str, Any]:
    instruments = await _universe(redis)
    if not instruments:
        return {"status": "waiting_for_symbols", "symbols": 0}
    # Note: instruments is never empty from here on even before nse-scraper
    # has written anything, since _universe() now always seeds the
    # BENCHMARK_INDICES entries -- this loop will make its first NIFTY50
    # history call slightly earlier (before real symbols exist) than
    # before, which is harmless and gets the RS benchmark flowing sooner.

    today = date.today()
    completed = 0
    async with aiohttp.ClientSession() as session:
        for symbol, instrument_key in instruments.items():
            try:
                daily = await _fetch_historical(
                    session,
                    instrument_key,
                    "days",
                    1,
                    # 370 calendar days (not 180) -- a real 52-week-high/low
                    # read (api/routes/mtf.py's _week52_stats) needs a full
                    # year of daily bars plus buffer for weekends/holidays;
                    # 180 days silently truncated every "52-week" claim to
                    # ~6 months. mtf.py's _load_bars already caps its
                    # zrange read at the most recent 260 bars, so this is
                    # the only place the window needs widening.
                    today - timedelta(days=370),
                    today,
                )
                await _store_zset(redis, f"infusion:ohlc:{symbol}:daily", daily, 14 * 86400)
                await asyncio.sleep(0.20)

                minute = await _fetch_historical(
                    session,
                    instrument_key,
                    "minutes",
                    1,
                    today - timedelta(days=30),
                    today,
                )
                intraday = await _fetch_intraday(session, instrument_key)
                combined_minute = list(reversed(intraday)) + minute if intraday else minute
                await _store_zset(
                    redis, f"infusion:ohlc:{symbol}:history:1m", combined_minute, 3 * 86400
                )
                await _store_volume_profile(redis, symbol, combined_minute)
                completed += 1
                await asyncio.sleep(0.20)
            except Exception as exc:
                logger.warning("historical_symbol_failed", symbol=symbol, error=str(exc))
                if "rate_limited" in str(exc):
                    await asyncio.sleep(2)

    benchmark_status = await _benchmark_status(redis)
    return {
        "status": "complete",
        "symbols": completed,
        "requested": len(instruments),
        "benchmark_status": benchmark_status,
    }
