"""F&O Screener support routes -- "Unified Omni-Screener & Deep-Dive
Interactivity" sprint (2026-08-28). Two bulk, cheap endpoints backing
the /screener page's merge of Smart Money geometry and Options data:

  - GET /api/screener/structure: real Order Block/FVG proximity for
    every tracked symbol, one Redis pipeline round-trip over
    feature-engine's own already-computed 1-minute OB/FVG state
    (infusion_models.smc's nearest_ob_or_fvg_level(), the exact same
    function api/broker_sync.py's own Position Intelligence Engine
    already uses for open positions -- not a second implementation).

  - GET /api/screener/options-summary: real PCR/Max Pain/OI-based
    support-resistance, but ONLY for whichever symbols
    api/option_chain_queue.py's own background loop has actually
    refreshed recently (api/routes/market.py's own widened
    _upstox_option_context() caches it there, from the exact same real
    chain rows it already fetches for its own near-ATM scoring -- zero
    additional Upstox calls).

    Deliberately NOT a live per-request fetch for the whole 200+ symbol
    universe: that would mean 200+ real Upstox /option/chain calls on
    every Screener page load, which this codebase's own architecture
    already avoids everywhere else (option_chain_queue.py itself only
    ever refreshes a small rotating ~28-symbol candidate subset,
    throttled with a real delay between calls, precisely to avoid
    hammering the real broker API -- and hitting Upstox's real rate
    limit while live-testing this exact sprint is what confirmed the
    constraint is real, not theoretical). Symbols outside that rotating
    subset simply aren't in this route's response; the Screener itself
    shows those honestly as unavailable rather than fabricating a
    number or stalling the page waiting on 200 live fetches.
"""

from __future__ import annotations

import json
from typing import Any

import msgpack
from aiohttp import web
from infusion_models.smc import nearest_ob_or_fvg_level

from api.routes.market import OPTIONS_SUMMARY_PREFIX
from api.routes.ticks import _decode_hash

routes = web.RouteTableDef()
Payload = dict[str, Any]


async def _symbol_universe(redis: Any) -> list[str]:
    """Same real infusion:symbols source GET /api/symbols itself reads
    -- duplicated here rather than imported, since list_symbols there
    is a route handler bound to a web.Request, not a plain callable."""
    raw = await redis.hgetall("infusion:symbols")
    out: list[str] = []
    for meta_raw in raw.values():
        try:
            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            sym = str(meta.get("symbol") or "").strip()
            if sym:
                out.append(sym)
        except Exception:
            continue
    return sorted(out)


def _nearest_ob_fvg_either_direction(features: Payload, ltp: float) -> float | None:
    """A Screener row has no single real "direction" the way an open
    position does -- checks both the bullish and bearish Order Block/
    FVG proximity (the real function, called twice) and reports
    whichever real zone is actually closer to the current LTP, or the
    only one that's currently validated. None when neither is."""
    bullish_level = nearest_ob_or_fvg_level(features, bearish=False)
    bearish_level = nearest_ob_or_fvg_level(features, bearish=True)
    if bullish_level is None:
        return bearish_level
    if bearish_level is None:
        return bullish_level
    if ltp <= 0:
        return bullish_level
    return bullish_level if abs(bullish_level - ltp) <= abs(bearish_level - ltp) else bearish_level


@routes.get("/api/screener/structure")
async def screener_structure(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    symbols = await _symbol_universe(redis)
    if not symbols:
        return web.json_response({"count": 0, "structure": {}})

    pipe = redis.pipeline(transaction=False)
    for symbol in symbols:
        pipe.hgetall(f"infusion:tick:{symbol}")
        pipe.hgetall(f"infusion:feature:{symbol}")
    results = await pipe.execute()

    out: Payload = {}
    for i, symbol in enumerate(symbols):
        tick_raw = results[i * 2]
        feature_raw = results[i * 2 + 1]
        if not feature_raw:
            continue
        tick = _decode_hash(tick_raw) if tick_raw else {}
        features = _decode_hash(feature_raw)
        ltp = float(tick.get("ltp") or 0)
        level = _nearest_ob_fvg_either_direction(features, ltp)
        if level is None:
            continue
        out[symbol] = {
            "ob_fvg_level": level,
            "distance_pct": abs(level - ltp) / ltp * 100 if ltp > 0 else None,
        }

    return web.json_response({"count": len(out), "structure": out})


@routes.get("/api/screener/options-summary")
async def screener_options_summary(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    out: Payload = {}
    cursor = 0
    while True:
        cursor, keys = await redis.scan(
            cursor=cursor, match=f"{OPTIONS_SUMMARY_PREFIX}*", count=200
        )
        if keys:
            pipe = redis.pipeline(transaction=False)
            for key in keys:
                pipe.get(key)
            values = await pipe.execute()
            for key, raw in zip(keys, values, strict=False):
                if not raw:
                    continue
                try:
                    key_text = key.decode() if isinstance(key, bytes) else key
                    symbol = key_text.replace(OPTIONS_SUMMARY_PREFIX, "")
                    text = raw.decode() if isinstance(raw, bytes) else raw
                    out[symbol] = json.loads(text)
                except Exception:
                    continue
        if cursor == 0:
            break

    return web.json_response({"count": len(out), "summary": out})
