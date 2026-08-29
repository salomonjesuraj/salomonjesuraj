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

  - GET /api/screener/fno: "Full Universe Batch Hydration Engine"
    sprint (2026-08-29), Phase 3. A single, cheap composite read of the
    two Redis hashes api.screener_hydrator's own background loop
    already writes every HYDRATE_INTERVAL_SEC: SMC_UNIVERSE_KEY (real
    Squeeze Readiness + RVOL computed fresh from daily bars, merged
    with Smart Money Flow/OB-FVG proximity reused from this same
    module) and OPTIONS_UNIVERSE_KEY (a pure republish of whatever
    /api/screener/options-summary's own OPTIONS_SUMMARY_PREFIX cache
    already holds). Two HGETALLs and a per-symbol merge -- no per-
    request computation, no live Upstox calls. "Zero nulls for symbols
    with existing OHLC history" (this sprint's own explicit ask)
    applies to the SMC-side fields (squeeze_readiness/rvol/oi_buildup/
    ob_fvg_level) -- those are real once bar_count is sufficient. The
    options-side fields (pcr/max_pain/iv_rank) stay honestly null for
    any symbol option_chain_queue.py's own rotating ~28-candidate sweep
    hasn't reached recently -- see screener_hydrator.py's own module
    docstring for the disclosed, calculated reason true 208-symbol
    options coverage isn't attempted here.

    SMC_UNIVERSE_KEY/OPTIONS_UNIVERSE_KEY are defined HERE (not
    imported from api.screener_hydrator) because that module already
    imports _symbol_universe/_nearest_ob_fvg_either_direction FROM this
    one -- importing back would be a circular import. screener_hydrator
    imports these two constants from here instead, so there is exactly
    one real definition of each key, not a duplicated literal.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import msgpack
from aiohttp import web
from infusion_models.smc import nearest_ob_or_fvg_level

from api.routes.market import OPTIONS_SUMMARY_PREFIX
from api.routes.ticks import _decode_hash

routes = web.RouteTableDef()
Payload = dict[str, Any]

# Real Redis hash keys api.screener_hydrator.py's own background loop
# writes into every HYDRATE_INTERVAL_SEC -- defined here, not there,
# to avoid a circular import; see this module's own docstring above.
SMC_UNIVERSE_KEY = "fno:screener:smc_universe"
OPTIONS_UNIVERSE_KEY = "fno:screener:options_universe"


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


def _decode_universe_hash(raw: Payload) -> Payload:
    """HGETALL on a universe key returns {symbol_bytes: json_bytes} --
    unlike ticks.py's own _decode_hash (a flat field->float hash), each
    value here is a whole JSON-encoded composite row, so this decodes
    the outer hash AND parses each inner JSON payload. A row that fails
    to parse (should not happen for anything the hydrator itself wrote)
    is skipped rather than surfaced as a fabricated empty row."""
    out: Payload = {}
    for key, value in raw.items():
        try:
            symbol = key.decode() if isinstance(key, bytes) else key
            text = value.decode() if isinstance(value, bytes) else value
            out[symbol] = json.loads(text)
        except Exception:
            continue
    return out


@routes.get("/api/screener/fno")
async def screener_fno(request: web.Request) -> web.Response:
    """Phase 3: the unified composite payload -- SMC_UNIVERSE_KEY merged
    with OPTIONS_UNIVERSE_KEY, both already fully computed by
    api.screener_hydrator's background loop. Pure Redis reads, no
    per-request computation. See this module's own docstring for the
    "zero nulls" scope (SMC fields only) and why options fields stay
    honestly null outside the existing rate-limited candidate subset."""
    redis = request.app["redis"]
    smc_raw, options_raw = await asyncio.gather(
        redis.hgetall(SMC_UNIVERSE_KEY), redis.hgetall(OPTIONS_UNIVERSE_KEY)
    )
    smc_rows = _decode_universe_hash(smc_raw)
    options_rows = _decode_universe_hash(options_raw)

    merged: Payload = {}
    for symbol, row in smc_rows.items():
        opt = options_rows.get(symbol)
        merged[symbol] = {
            **row,
            "pcr": opt.get("pcr") if opt else None,
            "max_pain": opt.get("max_pain") if opt else None,
            "oi_support_resistance": opt.get("oi_support_resistance") if opt else None,
            "iv_rank": opt.get("iv_rank") if opt else None,
            "iv_rank_history_count": opt.get("iv_rank_history_count", 0) if opt else 0,
            "options_updated_at": opt.get("updated_at") if opt else None,
        }

    return web.json_response(
        {
            "count": len(merged),
            "options_recent_count": sum(1 for s in merged if s in options_rows),
            "rows": merged,
        }
    )
