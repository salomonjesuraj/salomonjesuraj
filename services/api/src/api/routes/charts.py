"""Chart routes — OHLC data for TradingView Lightweight Charts.

Endpoints:
  GET /api/chart/{symbol}/intraday  → 1-min OHLC bars from live tick aggregation
  GET /api/chart/{symbol}/daily     → daily OHLC from cached historical data

Data sources:
  - Intraday: infusion:ohlc:{symbol}:1m ZSET (built by feature-engine bar_builder)
  - Daily: infusion:ohlc:{symbol}:daily ZSET (fetched from broker API, cached in Redis)
"""

import asyncio
import json
import time
from typing import Any, cast

from aiohttp import web

routes = web.RouteTableDef()
Payload = dict[str, Any]
Bar = dict[str, Any]


def _decode_ohlc(members: list[object]) -> list[Bar]:
    """Decode ZSET members into OHLC bar dicts for TradingView."""
    bars: list[Bar] = []
    for member in members:
        if isinstance(member, bytes):
            val = member.decode()
        elif isinstance(member, str):
            val = member
        else:
            continue
        try:
            bar = cast(Payload, json.loads(val))
            bars.append(
                {
                    "time": int(bar.get("t", 0)),  # Unix timestamp
                    "open": float(bar.get("o", 0)),
                    "high": float(bar.get("h", 0)),
                    "low": float(bar.get("l", 0)),
                    "close": float(bar.get("c", 0)),
                    "volume": int(bar.get("v", 0)),
                }
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return bars


def _merge_bars(*groups: list[Bar]) -> list[Bar]:
    by_time: dict[int, Bar] = {}
    for group in groups:
        for bar in group:
            if bar.get("time"):
                by_time[int(bar["time"])] = bar
    return [by_time[key] for key in sorted(by_time)]


def _aggregate(bars: list[Bar], minutes: int) -> list[Bar]:
    if minutes <= 1:
        return bars
    buckets: dict[int, Bar] = {}
    width = minutes * 60
    for bar in bars:
        bucket = int(bar["time"]) // width * width
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = {**bar, "time": bucket}
        else:
            current["high"] = max(current["high"], bar["high"])
            current["low"] = min(current["low"], bar["low"])
            current["close"] = bar["close"]
            current["volume"] += bar["volume"]
    return [buckets[key] for key in sorted(buckets)]


@routes.get("/api/chart/{symbol}/intraday")
async def get_intraday_chart(request: web.Request) -> web.Response:
    """Get intraday 1-min OHLC bars.

    Built by the feature-engine bar_builder from live tick aggregation.
    Available only for the current trading session.

    Query params:
      ?from={unix_ts}  — start time (default: market open 9:15 AM IST)
      ?to={unix_ts}    — end time (default: now)
    """
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]

    # Default: today's session
    from_ts = request.query.get("from", "0")
    to_ts = request.query.get("to", str(int(time.time())))

    try:
        from_score = float(from_ts)
        to_score = float(to_ts)
    except ValueError:
        return web.json_response({"error": "Invalid timestamp"}, status=400)

    interval = request.query.get("interval", "1m").lower()
    interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}.get(interval)
    if interval_minutes is None:
        return web.json_response({"error": "Unsupported interval"}, status=400)

    # ZRANGEBYSCORE to get bars in time range
    history, live = await asyncio.gather(
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:history:1m", from_score, to_score),
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:1m", from_score, to_score),
    )
    bars = _aggregate(_merge_bars(_decode_ohlc(history), _decode_ohlc(live)), interval_minutes)

    return web.json_response(
        {
            "symbol": symbol,
            "interval": interval,
            "count": len(bars),
            "bars": bars,
        }
    )


@routes.get("/api/chart/{symbol}/daily")
async def get_daily_chart(request: web.Request) -> web.Response:
    """Get daily OHLC bars.

    Data source priority:
      1. Redis cache: infusion:ohlc:{symbol}:daily
      2. If empty, return error (data fetched by scheduler service)

    Query params:
      ?days={n}  — number of days (default: 90, max: 365)
    """
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]

    days = min(int(request.query.get("days", "90")), 365)
    from_ts = time.time() - (days * 86400)

    key = f"infusion:ohlc:{symbol}:daily"

    members = await redis.zrangebyscore(key, from_ts, "+inf")
    bars = _decode_ohlc(members)

    if not bars:
        return web.json_response(
            {
                "symbol": symbol,
                "interval": "1D",
                "count": 0,
                "bars": [],
                "hint": "Daily data not yet cached. Will be populated by scheduler.",
            }
        )

    return web.json_response(
        {
            "symbol": symbol,
            "interval": "1D",
            "count": len(bars),
            "bars": bars,
        }
    )
