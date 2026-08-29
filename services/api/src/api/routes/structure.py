"""Structure & Breakout Suite routes -- Phase 1/2 (2026-08-29) live
signal routes, Phase 3 (2026-08-29) historical replay backtest routes.

GET /api/structure/signal and GET /api/structure/universe are computed
on request, mirroring GET /api/screener/structure's own pattern -- no
background hydration loop.

POST /api/structure/backtest/run and GET /api/structure/backtest/
{run_id} front api.structure_backtest's own real replay engine -- see
that module's own docstring for the exact reuse map and disclosed
scope limits (real ~1yr daily / ~1mo intraday data depth, single-leg
exit simulation, R-multiple-primary P&L). The run itself executes as a
background asyncio task (same shape as every other long-running sweep
in this service) so the POST returns immediately with a run_id rather
than blocking on what can be minutes of replay work.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any, cast

from aiohttp import web

from api.structure_backtest import (
    CostAssumptions,
    Side,
    _date_to_ts,
    create_backtest_run,
    get_backtest_run,
    run_structure_backtest,
)
from api.structure_optimize import (
    DEFAULT_MAX_COMBINATIONS,
    get_cached_optimize_result,
    get_optimize_progress,
    optimize_structure_backtest,
    persist_optimized_profiles,
)
from api.structure_signal import (
    TIMEFRAME_MINUTES,
    StructureSignalConfig,
    compute_structure_signal,
    compute_structure_universe,
)

routes = web.RouteTableDef()
Payload = dict[str, Any]

DEFAULT_TIMEFRAME = "15m"
VALID_SIDES = ("LONG_ONLY", "SHORT_ONLY", "BOTH")


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


def _config_from_body(body: Payload) -> StructureSignalConfig:
    defaults = StructureSignalConfig()
    return StructureSignalConfig(
        min_setup_quality=int(body.get("min_setup_quality", defaults.min_setup_quality)),
        min_bias_edge=int(body.get("min_bias_edge", defaults.min_bias_edge)),
        fast_trigger_lookback=int(
            body.get("fast_trigger_lookback", defaults.fast_trigger_lookback)
        ),
        atr_breakout_buffer=float(body.get("atr_breakout_buffer", defaults.atr_breakout_buffer)),
        strict_stop_max_atr=float(body.get("strict_stop_max_atr", defaults.strict_stop_max_atr)),
        tp1_r=float(body.get("tp1_r", defaults.tp1_r)),
        tp2_r=float(body.get("tp2_r", defaults.tp2_r)),
        tp3_r=float(body.get("tp3_r", defaults.tp3_r)),
        trade_mode=str(body.get("trade_mode") or defaults.trade_mode).upper(),
        vwap_enabled=bool(body.get("vwap_enabled", defaults.vwap_enabled)),
    )


@routes.post("/api/structure/backtest/run")
async def structure_backtest_run(request: web.Request) -> web.Response:
    """Kicks off a real historical replay -- see api.structure_backtest's
    own module docstring for exactly what "real" means here (actual
    Redis-stored OHLC, actual bar-by-bar signal recomputation, actual
    simulated fills) and its disclosed scope limits. Returns a run_id
    immediately; poll GET /api/structure/backtest/{run_id} for status
    and, once DONE, the real computed metrics."""
    pg_pool = request.app.get("pg_pool")
    if not pg_pool:
        return web.json_response(
            {"available": False, "reason": "Postgres analytics pool is not available."},
            status=200,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"available": False, "reason": "Request body must be JSON."})

    symbols = [str(s).upper().strip() for s in (body.get("symbols") or []) if str(s).strip()]
    timeframes = [str(t).lower().strip() for t in (body.get("timeframes") or []) if str(t).strip()]
    if not symbols:
        return web.json_response({"available": False, "reason": "At least one symbol is required."})
    if not timeframes:
        return web.json_response(
            {"available": False, "reason": "At least one timeframe is required."}
        )
    bad_timeframes = [t for t in timeframes if t not in TIMEFRAME_MINUTES]
    if bad_timeframes:
        return web.json_response(
            {
                "available": False,
                "reason": f"Unsupported timeframe(s) {bad_timeframes}. Use one of {sorted(TIMEFRAME_MINUTES)}.",
            }
        )

    side_raw = str(body.get("side") or "BOTH").upper()
    if side_raw not in VALID_SIDES:
        return web.json_response(
            {"available": False, "reason": f"side must be one of {VALID_SIDES}, got {side_raw!r}."}
        )
    side = cast(Side, side_raw)

    try:
        start_date = dt.date.fromisoformat(str(body.get("start_date")))
        end_date = dt.date.fromisoformat(str(body.get("end_date")))
    except (TypeError, ValueError):
        return web.json_response(
            {"available": False, "reason": "start_date/end_date must be YYYY-MM-DD strings."}
        )
    if end_date < start_date:
        return web.json_response({"available": False, "reason": "end_date must be >= start_date."})

    config = _config_from_body(body)
    cost_body = body.get("cost_assumptions") or {}
    costs = CostAssumptions(
        slippage_bps=float(cost_body.get("slippage_bps", CostAssumptions().slippage_bps)),
        brokerage_per_trade=float(
            cost_body.get("brokerage_per_trade", CostAssumptions().brokerage_per_trade)
        ),
    )

    run_id = await create_backtest_run(
        pg_pool,
        symbols=symbols,
        timeframes=timeframes,
        start_date=start_date,
        end_date=end_date,
        side=side,
        config=config,
        costs=costs,
    )

    redis = request.app["redis"]
    # asyncio's own docs warn that a fire-and-forget task with no
    # surviving reference can be garbage-collected mid-run -- every
    # OTHER background task in this service is created once in main()'s
    # own long-lived scope, but a replay kicked off from inside a
    # request handler has no such natural home. Pin it on request.app
    # (which outlives any single request) with a done-callback to
    # discard it once finished, so the set doesn't grow unbounded.
    background_tasks: set[asyncio.Task[None]] = request.app.setdefault(
        "structure_backtest_tasks", set()
    )
    task = asyncio.create_task(
        run_structure_backtest(
            pg_pool,
            redis,
            run_id=run_id,
            symbols=symbols,
            timeframes=timeframes,
            start_date=start_date,
            end_date=end_date,
            side=side,
            config=config,
            costs=costs,
        )
    )
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return web.json_response({"available": True, "run_id": run_id, "status": "RUNNING"})


@routes.get("/api/structure/backtest/{run_id}")
async def structure_backtest_get(request: web.Request) -> web.Response:
    pg_pool = request.app.get("pg_pool")
    if not pg_pool:
        return web.json_response(
            {"available": False, "reason": "Postgres analytics pool is not available."}
        )
    run_id = request.match_info["run_id"]
    run = await get_backtest_run(pg_pool, run_id)
    if run is None:
        return web.json_response({"available": False, "reason": f"No run found for {run_id!r}."})
    return web.json_response({"available": True, **run})


@routes.get("/api/structure/optimize/{run_id}")
async def structure_optimize_get(request: web.Request) -> web.Response:
    """Runs (or, by default, reads back a previously-persisted) Phase 4
    optimizer search over the given backtest run's own symbols/
    timeframes/side/date-range/costs -- see api.structure_optimize's
    own module docstring for the real, disclosed compute constraint
    that makes a sampled search (not the full 18,000-combination grid)
    the honest choice here. ?refresh=1 forces a fresh search even when
    a cached result already exists; ?max_combinations= overrides the
    real default cap."""
    pg_pool = request.app.get("pg_pool")
    if not pg_pool:
        return web.json_response(
            {"available": False, "reason": "Postgres analytics pool is not available."}
        )
    run_id = request.match_info["run_id"]
    run = await get_backtest_run(pg_pool, run_id)
    if run is None:
        return web.json_response({"available": False, "reason": f"No run found for {run_id!r}."})
    if run["status"] != "DONE":
        return web.json_response(
            {
                "available": False,
                "reason": f"Run {run_id!r} is not DONE yet (status={run['status']!r}) -- optimize needs a completed backtest to know its real symbol/timeframe/date scope.",
            }
        )

    refresh = request.query.get("refresh") == "1"
    if not refresh:
        cached = await get_cached_optimize_result(pg_pool, run_id)
        if cached is not None:
            return web.json_response(
                {"available": True, "cached": True, "run_id": run_id, **cached}
            )

    try:
        max_combinations = int(request.query.get("max_combinations", DEFAULT_MAX_COMBINATIONS))
    except (TypeError, ValueError):
        max_combinations = DEFAULT_MAX_COMBINATIONS
    max_combinations = max(1, min(max_combinations, 2000))

    redis = request.app["redis"]
    start_date = dt.date.fromisoformat(run["start_date"])
    end_date = dt.date.fromisoformat(run["end_date"])
    cost_body = run.get("cost_assumptions") or {}
    costs = CostAssumptions(
        slippage_bps=float(cost_body.get("slippage_bps", CostAssumptions().slippage_bps)),
        brokerage_per_trade=float(
            cost_body.get("brokerage_per_trade", CostAssumptions().brokerage_per_trade)
        ),
    )

    result = await optimize_structure_backtest(
        redis,
        symbols=run["symbols"],
        timeframes=run["timeframes"],
        side=cast(Side, run["side"]),
        start_ts=_date_to_ts(start_date),
        end_ts=_date_to_ts(end_date, end_of_day=True),
        costs=costs,
        max_combinations=max_combinations,
        run_id=run_id,
    )
    await persist_optimized_profiles(pg_pool, run_id, result)
    return web.json_response({"available": True, "cached": False, "run_id": run_id, **result})


@routes.get("/api/structure/optimize/{run_id}/progress")
async def structure_optimize_progress(request: web.Request) -> web.Response:
    """Task 3's own "return progress information so the UI does not feel
    stuck" ask -- pollable from a SEPARATE request while GET /api/
    structure/optimize/{run_id} is still blocked on a fresh search, since
    the replay/evaluate loop's own periodic `asyncio.sleep(0)` yields
    (Phase 4's fix) keep this process' event loop free to answer it.
    Returns an honest "no progress recorded yet" rather than a fabricated
    0% when this run_id has never started a fresh search in this
    process."""
    run_id = request.match_info["run_id"]
    progress = get_optimize_progress(run_id)
    if progress is None:
        return web.json_response(
            {
                "available": False,
                "reason": f"No in-flight or completed optimizer progress recorded for {run_id!r} "
                "in this process yet -- either it hasn't started, or it finished before this "
                "process last restarted.",
            }
        )
    return web.json_response({"available": True, "run_id": run_id, **progress})
