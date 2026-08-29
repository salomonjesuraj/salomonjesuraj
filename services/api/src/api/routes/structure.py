"""Structure & Breakout Suite routes -- Phase 1/2 (2026-08-29).

Both routes are computed on request, mirroring GET /api/screener/
structure's own pattern -- no new background hydration loop for this
phase (see api.structure_signal's own module docstring for what's
reused and what Phase 3 still owes: a historical replay backtester and
auto-optimizer, deliberately not built here).
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from api.structure_signal import (
    TIMEFRAME_MINUTES,
    StructureSignalConfig,
    compute_structure_signal,
    compute_structure_universe,
)

routes = web.RouteTableDef()
Payload = dict[str, Any]

DEFAULT_TIMEFRAME = "15m"


def _config_from_query(request: web.Request) -> StructureSignalConfig:
    """Every StructureSignalConfig field is overridable via query string
    -- lets a caller (or, in Phase 3, the optimizer) probe a specific
    parameter combination without a second endpoint. Invalid/missing
    values fall back to the real defaults rather than erroring, matching
    this codebase's own established query-param tolerance (e.g.
    api/routes/backtest.py's own days/target parsing)."""
    q = request.query

    def _int(name: str, default: int) -> int:
        try:
            return int(q.get(name, default))
        except (TypeError, ValueError):
            return default

    def _float(name: str, default: float) -> float:
        try:
            return float(q.get(name, default))
        except (TypeError, ValueError):
            return default

    defaults = StructureSignalConfig()
    return StructureSignalConfig(
        min_setup_quality=_int("min_setup_quality", defaults.min_setup_quality),
        min_bias_edge=_int("min_bias_edge", defaults.min_bias_edge),
        fast_trigger_lookback=_int("fast_trigger_lookback", defaults.fast_trigger_lookback),
        atr_breakout_buffer=_float("atr_breakout_buffer", defaults.atr_breakout_buffer),
        strict_stop_max_atr=_float("strict_stop_max_atr", defaults.strict_stop_max_atr),
        tp1_r=_float("tp1_r", defaults.tp1_r),
        tp2_r=_float("tp2_r", defaults.tp2_r),
        tp3_r=_float("tp3_r", defaults.tp3_r),
        trade_mode=(q.get("trade_mode") or defaults.trade_mode).upper(),
        vwap_enabled=(q.get("vwap_enabled", "1") not in ("0", "false", "False")),
    )


@routes.get("/api/structure/signal")
async def structure_signal(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    symbol = request.query.get("symbol", "").upper().strip()
    if not symbol:
        return web.json_response({"ready": False, "reason": "Missing required ?symbol="})
    timeframe = request.query.get("timeframe", DEFAULT_TIMEFRAME).lower().strip()
    config = _config_from_query(request)
    result = await compute_structure_signal(redis, symbol, timeframe, config)
    return web.json_response(result)


@routes.get("/api/structure/universe")
async def structure_universe(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    timeframe = request.query.get("timeframe", DEFAULT_TIMEFRAME).lower().strip()
    if timeframe not in TIMEFRAME_MINUTES:
        return web.json_response(
            {
                "count": 0,
                "rows": {},
                "reason": f"Unsupported timeframe: {timeframe}. Use one of {sorted(TIMEFRAME_MINUTES)}.",
            }
        )
    config = _config_from_query(request)
    result = await compute_structure_universe(redis, timeframe, config)
    return web.json_response(result)
