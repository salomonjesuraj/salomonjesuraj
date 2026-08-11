"""Upstox historical-data bootstrap for chart candles and relative volume."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote

import aiohttp
import msgpack
import structlog

logger = structlog.get_logger()
UPSTOX_API_BASE = "https://api.upstox.com/v3"


def _bar(raw: list) -> tuple[int, dict] | None:
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


async def _universe(redis) -> dict[str, str]:
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
    return result


async def _fetch_historical(
    session: aiohttp.ClientSession,
    instrument_key: str,
    unit: str,
    interval: int,
    start: date,
    end: date,
) -> list:
    encoded_key = quote(instrument_key, safe="")
    url = (
        f"{UPSTOX_API_BASE}/historical-candle/{encoded_key}/"
        f"{unit}/{interval}/{end.isoformat()}/{start.isoformat()}"
    )
    async with session.get(url, headers={"Accept": "application/json"}, timeout=30) as response:
        payload = await response.json()
        if response.status == 429:
            raise RuntimeError("upstox_rate_limited")
        if response.status != 200 or payload.get("status") != "success":
            raise RuntimeError(payload.get("message", f"upstox_http_{response.status}"))
        return payload.get("data", {}).get("candles", [])


async def _fetch_intraday(
    session: aiohttp.ClientSession,
    instrument_key: str,
    unit: str = "minutes",
    interval: int = 1,
) -> list:
    encoded_key = quote(instrument_key, safe="")
    url = f"{UPSTOX_API_BASE}/historical-candle/intraday/{encoded_key}/{unit}/{interval}"
    async with session.get(url, headers={"Accept": "application/json"}, timeout=30) as response:
        payload = await response.json()
        if response.status == 429:
            raise RuntimeError("upstox_rate_limited")
        if response.status != 200 or payload.get("status") != "success":
            raise RuntimeError(payload.get("message", f"upstox_http_{response.status}"))
        return payload.get("data", {}).get("candles", [])


async def _store_zset(redis, key: str, rows: list, ttl: int) -> None:
    mapping = {}
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


async def _store_volume_profile(redis, symbol: str, rows: list) -> None:
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


async def bootstrap_historical(redis) -> dict:
    instruments = await _universe(redis)
    if not instruments:
        return {"status": "waiting_for_symbols", "symbols": 0}

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
                await _store_zset(redis, f"infusion:ohlc:{symbol}:history:1m", combined_minute, 3 * 86400)
                await _store_volume_profile(redis, symbol, combined_minute)
                completed += 1
                await asyncio.sleep(0.20)
            except Exception as exc:
                logger.warning("historical_symbol_failed", symbol=symbol, error=str(exc))
                if "rate_limited" in str(exc):
                    await asyncio.sleep(2)

    return {"status": "complete", "symbols": completed, "requested": len(instruments)}
