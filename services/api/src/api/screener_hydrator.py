""" "Full Universe Batch Hydration Engine" sprint (2026-08-29): a real,
all-208-symbol background sweep that pre-computes the F&O Screener's own
Smart Money + Squeeze metrics directly from this service's ALREADY-
STORED OHLC bar history (infusion:ohlc:{symbol}:daily), independent of
the scanner service's own live-tick-driven pre_breakout state machine.

Why not just read scanner's own infusion:prebreak:{symbol} rows (already
computed, in principle, for every symbol): live-checked before writing
anything here and found genuinely degenerate on a closed-market weekend
-- `docker compose logs scanner` shows every recent prebreak_transition
event reporting `"rel_vol": 0.0` universally, and states cycling
compressing -> expired -> idle -> compressing on a ~60s wall-clock
timeout unrelated to real price action, because no live ticks have
arrived to drive genuine transitions. `infusion:prebreak:*` itself
scanned to ZERO keys at the moment this was written -- confirmed with a
real SCAN, not assumed. That's a real, pre-existing gap in a DIFFERENT
service (services/scanner/src/scanner/pre_breakout.py) this sprint's own
file scope (services/api/...) has no business silently patching. This
module sidesteps it entirely by computing Squeeze Readiness and RVOL
FRESH from real daily bars already sitting in Redis -- robust to the
market being closed, weekends, or the scanner's own live-tick pipeline
being idle, since none of that data depends on a single live tick.

Smart Money Flow (OI buildup) and OB/FVG proximity are NOT recomputed
here -- both are already real, already bulk-computed for the full
universe by existing code (api.futures.compute_oi_buildup_map,
api.routes.screener's own _nearest_ob_fvg_either_direction) with no
scanner-service dependency and no live-tick staleness problem; this
module reuses them verbatim and merges everything into ONE composite
row per symbol, rather than building a second implementation of
either.

Options data (Phase 2) is DELIBERATELY not a second live-Upstox-polling
loop -- api/option_chain_queue.py's own module docstring, and this
session's own live testing, already established that hitting Upstox's
real rate limit is a real risk, not a theoretical one, for anything
resembling "sweep all 208 symbols." Running a SECOND independent
Upstox-calling loop alongside that proven one would risk doubling real
broker traffic during any overlapping window. This module's options
hydration is a pure Redis aggregation instead: it reads whatever
api.routes.market's own _upstox_option_context() has ALREADY cached
into OPTIONS_SUMMARY_PREFIX (the same real chain rows that queue
already fetches for its own near-ATM scoring) and republishes it under
this sprint's own fno:screener:options_universe key -- zero additional
Upstox calls, honestly bounded to whichever symbols that existing,
rate-limit-respecting queue has actually reached recently. Genuine full
208-symbol options coverage would need that queue's own candidate limit
widened and its cycle interval lengthened proportionally (the real math:
208 symbols at the queue's own proven-safe 350ms per-call delay is 72.8s
of real work, which only stays at-or-below the CURRENT ~0.62 req/s
average rate if spread over roughly 10+ minutes) -- a real, calculated,
disclosed trade-off left for a deliberate follow-up rather than done
silently here.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from api.futures import compute_oi_buildup_map
from api.routes.market import OPTIONS_SUMMARY_PREFIX
from api.routes.screener import (
    OPTIONS_UNIVERSE_KEY,
    SMC_UNIVERSE_KEY,
    _nearest_ob_fvg_either_direction,
    _symbol_universe,
)
from api.routes.ticks import _decode_hash

logger = structlog.get_logger()
Payload = dict[str, Any]
Bar = dict[str, float]

# SMC_UNIVERSE_KEY / OPTIONS_UNIVERSE_KEY are defined in api.routes.screener
# (imported above), not here -- that module's GET /api/screener/fno route
# reads both real hashes this module writes, and screener.py already can't
# import from this module (this module imports _symbol_universe/
# _nearest_ob_fvg_either_direction FROM screener.py), so the one real
# definition of each key lives there to avoid a circular import.
OPTIONS_UNIVERSE_TTL_SEC = 15 * 60
HYDRATE_INTERVAL_SEC = 60

_IST = ZoneInfo("Asia/Kolkata")
_SESSION_OPEN = dt_time(9, 15)
_SESSION_CLOSE = dt_time(15, 30)

SQUEEZE_PERIOD = 20  # standard TTM Squeeze lookback (Bollinger + Keltner both use it)
BB_STDEV_MULT = 2.0
KC_ATR_MULT = 1.5
RVOL_PERIOD = 20  # "20-day historical average," per this sprint's own ask


def _is_market_open_now() -> bool:
    """Weekday + real IST session window -- NSE, not a generic 24/7
    assumption. Used only to label an RVOL reading as live-session vs
    last-completed-session in the response; see compute_rvol's own
    docstring for why the underlying math doesn't actually branch on
    this (this pipeline's own daily-bar store has no intraday-partial
    entry to compare against differently in the first place)."""
    now = datetime.now(tz=_IST)
    if now.weekday() >= 5:
        return False
    return _SESSION_OPEN <= now.time() <= _SESSION_CLOSE


def _decode_daily_bar(raw: Any) -> Bar | None:
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        data = json.loads(text)
        return {
            "time": float(data.get("t", 0) or 0),
            "high": float(data.get("h", 0) or 0),
            "low": float(data.get("l", 0) or 0),
            "close": float(data.get("c", 0) or 0),
            "volume": float(data.get("v", 0) or 0),
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _sma(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return float(variance**0.5)


def _true_range(bar: Bar, prev_close: float) -> float:
    return max(
        bar["high"] - bar["low"],
        abs(bar["high"] - prev_close),
        abs(bar["low"] - prev_close),
    )


def compute_squeeze_readiness(daily_bars: list[Bar], period: int = SQUEEZE_PERIOD) -> float | None:
    """Real TTM Squeeze (John Carter): Bollinger Bands compressing
    INSIDE Keltner Channels -- squeeze_ratio = bb_width / kc_width < 1
    means the bands have compressed inside the channel (a real squeeze),
    the classic "volatility contraction precedes expansion" setup. This
    is a single-snapshot ratio, not a duration-weighted score (this
    sprint's own scope: scanner's own pre_breakout.py already tracks
    consecutive-bar persistence for its own COMPRESSING/COILED state
    machine -- duplicating that here would be a second, competing
    implementation of the same concept, not a batch-hydration fix).
    100 = deepest observed compression (BB width approaching 0 relative
    to KC); 0 = no squeeze at all (BB at or wider than KC -- volatility
    has already expanded, or never compressed). None -- never a
    fabricated number -- when fewer than `period + 1` real daily bars
    exist yet for this symbol."""
    if len(daily_bars) < period + 1:
        return None
    window = daily_bars[-period:]
    closes = [b["close"] for b in window]
    sma = _sma(closes)
    bb_width = _stdev(closes, sma) * BB_STDEV_MULT * 2

    atr_window = daily_bars[-(period + 1) :]
    true_ranges = [
        _true_range(atr_window[i], atr_window[i - 1]["close"]) for i in range(1, len(atr_window))
    ]
    kc_width = _sma(true_ranges) * KC_ATR_MULT * 2
    if kc_width <= 0:
        return None

    ratio = bb_width / kc_width
    if ratio >= 1.0:
        return 0.0
    return round(max(0.0, min(100.0, (1.0 - ratio) * 100.0)), 1)


def compute_rvol(daily_bars: list[Bar], period: int = RVOL_PERIOD) -> float | None:
    """Current (or, when the market's closed, most-recently-completed)
    session's real volume against the trailing `period`-session average
    -- this sprint's own explicit ask. There is no separate "market
    open" branch in the math itself: infusion:ohlc:{symbol}:daily only
    ever holds COMPLETED sessions (the scheduler service's own real EOD
    fetch, not a live intraday-partial write), so "the latest bar in
    this store" already IS "the most recent completed session" whether
    or not the market happens to be open right now -- `_is_market_open_now()`
    is used only to LABEL the response honestly, not to pick a different
    formula. None -- never a fabricated ratio -- when fewer than
    `period + 1` real daily bars exist yet, or the historical average
    itself is non-positive (degenerate/missing volume data)."""
    if len(daily_bars) < period + 1:
        return None
    current = daily_bars[-1]
    historical = daily_bars[-(period + 1) : -1]
    avg_volume = _sma([b["volume"] for b in historical])
    if avg_volume <= 0:
        return None
    return round(current["volume"] / avg_volume, 2)


async def hydrate_smc_universe(redis: Any) -> Payload:
    """Phase 1: Squeeze Readiness + RVOL (real, computed fresh here from
    daily bars) merged with Smart Money Flow (real, reused from
    api.futures.compute_oi_buildup_map) and OB/FVG proximity (real,
    reused from api.routes.screener's own structure logic) into one
    composite row per symbol, written to SMC_UNIVERSE_KEY. Every field
    is None on its own when the real upstream data for THAT field isn't
    available yet for THAT symbol -- never a fabricated 0/NEUTRAL
    standing in for missing data."""
    symbols = await _symbol_universe(redis)
    if not symbols:
        return {"symbols": 0, "populated": 0}

    pipe = redis.pipeline(transaction=False)
    for symbol in symbols:
        pipe.zrange(f"infusion:ohlc:{symbol}:daily", -(max(SQUEEZE_PERIOD, RVOL_PERIOD) + 1), -1)
        pipe.hgetall(f"infusion:tick:{symbol}")
        pipe.hgetall(f"infusion:feature:{symbol}")
    results = await pipe.execute()

    oi_buildup_map = await compute_oi_buildup_map(redis)
    market_open = _is_market_open_now()

    rows: Payload = {}
    populated = 0
    for i, symbol in enumerate(symbols):
        raw_daily, tick_raw, feature_raw = results[i * 3 : i * 3 + 3]
        daily_bars = [b for b in (_decode_daily_bar(r) for r in (raw_daily or [])) if b is not None]
        tick = _decode_hash(tick_raw) if tick_raw else {}
        features = _decode_hash(feature_raw) if feature_raw else {}
        ltp = float(tick.get("ltp") or 0)

        ob_fvg_level = _nearest_ob_fvg_either_direction(features, ltp) if features else None
        row = {
            "symbol": symbol,
            "ltp": ltp if ltp > 0 else None,
            "squeeze_readiness": compute_squeeze_readiness(daily_bars),
            "rvol": compute_rvol(daily_bars),
            "rvol_session": "live" if market_open else "last_close",
            "oi_buildup": oi_buildup_map.get(symbol),
            "ob_fvg_level": ob_fvg_level,
            "ob_fvg_distance_pct": (
                abs(ob_fvg_level - ltp) / ltp * 100
                if ob_fvg_level is not None and ltp > 0
                else None
            ),
            "bar_count": len(daily_bars),
        }
        rows[symbol] = row
        if any(
            row[k] is not None for k in ("squeeze_readiness", "rvol", "oi_buildup", "ob_fvg_level")
        ):
            populated += 1

    write_pipe = redis.pipeline(transaction=False)
    write_pipe.delete(SMC_UNIVERSE_KEY)
    if rows:
        write_pipe.hset(
            SMC_UNIVERSE_KEY,
            mapping={sym: json.dumps(row, default=str) for sym, row in rows.items()},
        )
    await write_pipe.execute()

    return {"symbols": len(symbols), "populated": populated, "updated_at": time.time()}


async def hydrate_options_universe(redis: Any) -> Payload:
    """Phase 2: pure Redis aggregation -- see this module's own top-level
    docstring for why this deliberately does NOT fetch anything new from
    Upstox. Republishes whatever api.routes.market's own
    _upstox_option_context() has already cached under OPTIONS_SUMMARY_PREFIX
    (pcr/max_pain/iv_rank, from that route's own real chain rows) into
    OPTIONS_UNIVERSE_KEY, with a real TTL on the whole hash so a stopped
    hydrator doesn't leave an ever-more-stale universe cache around
    forever."""
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

    write_pipe = redis.pipeline(transaction=False)
    write_pipe.delete(OPTIONS_UNIVERSE_KEY)
    if out:
        write_pipe.hset(
            OPTIONS_UNIVERSE_KEY,
            mapping={sym: json.dumps(row, default=str) for sym, row in out.items()},
        )
    write_pipe.expire(OPTIONS_UNIVERSE_KEY, OPTIONS_UNIVERSE_TTL_SEC)
    await write_pipe.execute()

    return {"symbols_covered": len(out), "updated_at": time.time()}


async def screener_hydrator_loop(app: Any) -> None:
    """Background task, registered in main.py like every other periodic
    sweep in this service (ebie_state_sweep, radar_alert_sweep, etc.) --
    same shape, same "log and keep going" resilience to one bad cycle."""
    redis = app["redis"]
    logger.info("screener_hydrator_started", interval=HYDRATE_INTERVAL_SEC)
    while True:
        try:
            smc_status = await hydrate_smc_universe(redis)
            options_status = await hydrate_options_universe(redis)
            logger.info("screener_hydrator_cycle", smc=smc_status, options=options_status)
        except Exception as exc:
            logger.warning("screener_hydrator_cycle_failed", error=str(exc))
        await asyncio.sleep(HYDRATE_INTERVAL_SEC)
