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

from api.routes.market import _default_symbol
from api.routes.mtf import _load_bars
from api.smc_geometry import compute_smc_geometry

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


SMC_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}


@routes.get("/api/chart/smc")
async def get_smc_geometry(request: web.Request) -> web.Response:
    """ "Institutional Chart Overlay" sprint (2026-08-28), made timeframe-
    aware by "TradingView Parity" (2026-08-29): real BOS/CHOCH, liquidity
    sweep, Order Block, Fibonacci target-zone, and trendline geometry for
    the chart overlay -- see api/smc_geometry.py's own module docstring
    for exactly what's computed and why it's a batch replay of
    feature-engine's real rules rather than a live Redis read of the
    hot-state hash (which only ever keeps the CURRENT trend/swing/OB
    state and the single most recent break event, not the full history
    a chart's markers need).

    `?interval=` (1m/5m/15m/1h/4h, default 1m) selects which candle
    width the geometry is computed ON, aggregating the SAME real 1-minute
    bars via charts.py's own `_aggregate()` -- the identical real
    aggregation the candlestick series itself renders, not a second
    approximation. This is a genuine behavior change from this route's
    first version, which always computed on 1-minute bars regardless of
    the caller's own displayed timeframe: at `1m` (the default), this
    overlay's trend/swing story still matches the rest of the app's own
    1-minute trend_text/last_event_label reporting for the same symbol
    exactly as before; at any other interval, it's now a REAL higher-
    timeframe read, intentionally different from the 1-minute one --
    that divergence is what multi-timeframe analysis is supposed to show,
    not a bug. Honest `ready: false` at the wider timeframes (1h/4h) is
    expected, not a defect: `_load_bars`' own real lookback is 10 days of
    1-minute history (mtf.py's own real constant), which aggregates down
    to too few 4H bars to confirm even one fractal pivot -- widening that
    lookback is a real, separate piece of work this route doesn't take on.

    Accepts any real F&O underlying via `_default_symbol()`'s own dynamic
    lookup (most recent active signal, else best pre-breakout candidate)
    when no `?symbol=` is given -- no hardcoded default.
    """
    redis = request.app["redis"]
    symbol = request.query.get("symbol", "").upper().strip()
    if not symbol:
        symbol = await _default_symbol(redis)
    if not symbol:
        return web.json_response(
            {"ready": False, "reason": "No symbol provided and no default symbol available."}
        )

    interval = request.query.get("interval", "1m").lower()
    interval_minutes = SMC_INTERVAL_MINUTES.get(interval)
    if interval_minutes is None:
        return web.json_response({"ready": False, "reason": f"Unsupported interval: {interval}"})

    intraday, _daily, _nifty = await _load_bars(redis, symbol)
    bars = _aggregate(intraday, interval_minutes)
    geometry = compute_smc_geometry(bars)
    return web.json_response({"symbol": symbol, "interval": interval, **geometry})
