"""EBIE EB-1 -- canonical early-breakout state machine, run in SHADOW mode.

Same in-process asyncio sweep-loop architecture as radar_alert_queue.py
(Phase R9) -- reads _build_ticks() in-process (no HTTP round-trip, no
second copy of that pipeline), compares each symbol/direction's current
mapped EBIE state against what it was last sweep via a small TTL-bound
Redis cache, and logs every genuine transition to Postgres. Deliberately
never influences live scanner/alert behavior -- see docs/EBIE-
IMPLEMENTATION-ANSWERS.md's Section 38 "Shadow Mode": a new model/feature
enters shadow before it can alter user-facing verdicts, and stays there
until EB-13's shadow-validation gate is actually passed.

State mapping (v1) is deliberately built ONLY from evidence that already
exists today -- stock_breakout_tier/breakout_type (api/routes/ticks.py,
Phases R1/R2/R8), PreBreakoutTracker's raw state (services/scanner/src/
scanner/pre_breakout.py), and whether a real signal is currently active
(infusion:signal:{symbol}). EB-1's job is the STATE MACHINE STRUCTURE and
the shared EpisodeManager (see episode_manager.py on the scanner side) --
genuinely new evidence families (accumulation, microstructure, futures/
options positioning, sentiment) are EB-2 through EB-7's job, not this
one's. This mapping is disclosed as a starting point, not a finished
model -- it will visibly improve as those phases land.

Canonical states (docs/EBIE-BLUEPRINT.md Section 5):
    IDLE -> DEVELOPING -> PRE_BREAKOUT/PRE_BREAKDOWN -> READY -> ARMED
    -> TRIGGERED -> CONFIRMED, or FAILED at any point along the way
    (also covers TRAP for this v1 -- EB-9 builds a dedicated trap-
    probability model later; a nuanced TRAP sub-classification isn't
    real evidence yet, just a terminal bucket a setup can fall into).

Per Q3.1's authorized migration plan, this does NOT replace
PreBreakoutTracker/stock_breakout_tier/radar_alerts -- they keep running
exactly as before, feeding this as inputs, persisted alongside it for
comparison (legacy_tier/legacy_pb_state columns on every transition row).
Promotion to canonical authority, and deprecating the old outputs, is a
separate, later, explicitly-gated step (EB-13/EB-14) -- not this phase.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import msgpack
import structlog
from datetime import datetime, timezone

from api.futures import FUTURES_STATE_PREFIX
from api.market_breadth import compute_market_breadth
from api.routes.ticks import _build_ticks
from infusion_streams.constants import (
    KEY_EBIE_STATE_PREFIX, KEY_EBIE_VERDICT_LITE_PREFIX, KEY_MARKET_CONTEXT_PREFIX,
    KEY_SIGNAL_PREFIX,
)

logger = structlog.get_logger()

EBIE_STATE_TTL_SEC = 24 * 3600  # same TTL-bound principle as R2/R8/R9's own transitional caches
EBIE_VERDICT_LITE_TTL_SEC = 24 * 3600
MARKET_CONTEXT_TTL_SEC = 24 * 3600
SWEEP_INTERVAL_SEC = 60
STATUS_KEY = "infusion:ebie-state-queue:status"

CANONICAL_STATES = (
    "IDLE", "DEVELOPING", "PRE_BREAKOUT", "PRE_BREAKDOWN",
    "READY", "ARMED", "TRIGGERED", "CONFIRMED", "FAILED",
)

# EBIE EB-15 Phase 3 (P2 item 4, "Generate Lightweight Verdicts for
# Developing Universe Symbols") -- the directive's own required label
# set, verbatim. This is deliberately a SEPARATE, cheaper verdict from
# EB-8's compute_verdict() (scanner/verdict_engine.py): that one needs
# futures/options-dynamics/sentiment caches and only ever runs for a
# promoted SignalCandidate; this one runs for EVERY symbol EVERY sweep
# (208 symbols/60s) using only what _build_ticks() already computes --
# the directive's own "two-stage verdict model" (lightweight for the
# whole universe, full after candidate promotion).
LIGHTWEIGHT_VERDICT_LABELS = (
    "NO_TRADE", "WATCH_LONG", "WATCH_SHORT", "LONG_DEVELOPING", "SHORT_DEVELOPING",
    "LONG_READY", "SHORT_READY", "BREAKOUT_ARMED", "BREAKDOWN_ARMED",
    "AVOID_TRAP_RISK", "DATA_UNRELIABLE",
)

# Same literal P6 policy as api/routes/ebie_candidates.py's _dq_status()
# and scanner/verdict_engine.py's DQ_HARD_FAIL/DQ_DEGRADED -- kept as a
# separate copy (not imported across the scanner/api service boundary)
# since these are two independent services; the THRESHOLDS must still
# match exactly, so keep any change to one in sync with the other two.
DQ_HARD_FAIL = 80
DQ_DEGRADED = 90

# Pre-calibration confidence bands (item 10's required display before a
# real calibrated probability exists -- "LOW / MEDIUM / HIGH / VERY_HIGH").
# Thresholds are against stock_breakout_score, which is a real 0-100
# scale as of Phase R8 (opening-range/sector/RS components completed the
# score to its full 100 points) -- this session's own calibration, not
# from the directive (which specifies the labels but not thresholds).
CONFIDENCE_BANDS = (
    (80, "VERY_HIGH"),
    (60, "HIGH"),
    (40, "MEDIUM"),
    (0, "LOW"),
)


def _confidence_band(score) -> str:
    if not isinstance(score, (int, float)):
        return "LOW"
    for threshold, label in CONFIDENCE_BANDS:
        if score >= threshold:
            return label
    return "LOW"


def compute_lightweight_verdict(
    entry: dict, state: str, direction: str,
    market_context: dict | None = None, futures: dict | None = None,
) -> dict:
    """Phase 3 -- one lightweight verdict per (symbol, direction) per
    sweep. Pure function, no I/O, same testability shape as
    map_ebie_state() above -- takes the already-mapped canonical `state`
    (so the two functions can never independently disagree about what
    state a symbol is in) and derives the directive's required verdict
    label, a pre-calibration confidence band, a short reason summary, a
    qualitative invalidation reason, and the real data-quality status
    (never fabricated -- UNKNOWN when the score itself is unavailable).

    EBIE-KNOWN-GAPS.md §6.5's own scoped middle ground: `market_context`/
    `futures` are OPTIONAL, purely INFORMATIONAL enrichment -- both are
    genuinely full-universe caches with no rolling-subset/rate-limit cost
    (see §1.7's own coverage table: market-context 208/208, futures
    209/208), unlike mtf/options-dynamics/option-chain which stay
    deliberately narrow. Deliberately does NOT feed into `verdict`/
    `confidence_band` -- that would be recalibrating this function's own
    decision logic, a materially bigger, unvalidated change nobody asked
    for. This only makes two already-fully-covered evidence sources
    VISIBLE on every symbol's lightweight verdict, same "surface real
    data without changing behavior" shape as every other EBIE-KNOWN-GAPS
    fix this session (§1.7, §7.1, §7.2).
    """
    dq_score = entry.get("data_quality_score")
    if isinstance(dq_score, (int, float)) and dq_score < DQ_HARD_FAIL:
        dq_status = "DATA_UNRELIABLE"
    elif isinstance(dq_score, (int, float)) and dq_score < DQ_DEGRADED:
        dq_status = "DEGRADED"
    elif isinstance(dq_score, (int, float)):
        dq_status = "READY"
    else:
        dq_status = "UNKNOWN"

    bullish = direction == "BULLISH"
    anti_chase_reasons = list(entry.get("anti_chase_reasons") or [])
    confidence_band = _confidence_band(entry.get("stock_breakout_score"))

    reasons: list[str] = []
    if entry.get("breakout_type"):
        reasons.append(f"breakout_type={entry['breakout_type']}")
    if entry.get("stock_breakout_tier"):
        reasons.append(f"tier={entry['stock_breakout_tier']}")
    if entry.get("vwap_state") in {"ABOVE", "BELOW"}:
        reasons.append(f"vwap={entry['vwap_state']}")
    rel_vol = entry.get("rel_vol")
    if isinstance(rel_vol, (int, float)) and rel_vol > 0:
        reasons.append(f"rvol={rel_vol:.2f}x")
    nifty_change = (market_context or {}).get("nifty_change_pct")
    if isinstance(nifty_change, (int, float)):
        reasons.append(f"nifty={nifty_change:+.2f}%")
    sector_change = (market_context or {}).get("sector_avg_change_pct")
    if isinstance(sector_change, (int, float)):
        reasons.append(f"sector={sector_change:+.2f}%")
    basis_pct = (futures or {}).get("basis_pct")
    if isinstance(basis_pct, (int, float)):
        reasons.append(f"futures_basis={basis_pct:+.2f}%")
    oi_change_pct = (futures or {}).get("oi_change_pct")
    if isinstance(oi_change_pct, (int, float)):
        reasons.append(f"futures_oi={oi_change_pct:+.2f}%")

    day_high = entry.get("day_high")
    day_low = entry.get("day_low")
    if bullish and isinstance(day_low, (int, float)) and day_low > 0:
        invalidation_reason = f"Would invalidate on a sustained break below day low (~₹{day_low:.2f})"
    elif not bullish and isinstance(day_high, (int, float)) and day_high > 0:
        invalidation_reason = f"Would invalidate on a sustained break above day high (~₹{day_high:.2f})"
    else:
        invalidation_reason = "No structural invalidation level available yet"

    # Data quality is checked FIRST and overrides everything else -- per
    # item 12's own policy (DQ<80 hard-fails), a verdict must never claim
    # directional conviction on data that's already known unreliable.
    if dq_status == "DATA_UNRELIABLE":
        verdict = "DATA_UNRELIABLE"
    elif anti_chase_reasons and state in {"READY", "ARMED", "TRIGGERED", "CONFIRMED"}:
        # Structurally close to actionable, but the same anti-chase
        # evidence this codebase already computes (R1/R8's own
        # anti_chase_reasons -- VWAP stretch, large candle, chase RSI)
        # flags it as a real chase-trap risk -- the trap reasons are WHY
        # this fired, so they lead the reason summary.
        verdict = "AVOID_TRAP_RISK"
        reasons = anti_chase_reasons[:3] + reasons
    elif state in {"IDLE", "FAILED"}:
        verdict = "NO_TRADE"
    elif state == "DEVELOPING":
        verdict = "LONG_DEVELOPING" if bullish else "SHORT_DEVELOPING"
    elif state == "PRE_BREAKOUT":
        verdict = "WATCH_LONG"
    elif state == "PRE_BREAKDOWN":
        verdict = "WATCH_SHORT"
    elif state == "READY":
        verdict = "LONG_READY" if bullish else "SHORT_READY"
    elif state in {"ARMED", "TRIGGERED", "CONFIRMED"}:
        verdict = "BREAKOUT_ARMED" if bullish else "BREAKDOWN_ARMED"
    else:
        verdict = "NO_TRADE"

    return {
        "verdict": verdict,
        "direction": direction,
        "confidence_band": confidence_band,
        "reasons": reasons[:5],
        "invalidation_reason": invalidation_reason,
        "data_quality_status": dq_status,
        "data_quality_score": dq_score,
        # EBIE-KNOWN-GAPS.md §6.5 middle ground -- informational only,
        # see this function's own docstring. None (not a guessed value)
        # when the sweep genuinely has nothing cached for this symbol.
        "market_context": (
            {
                "nifty_change_pct": (market_context or {}).get("nifty_change_pct"),
                "sector_avg_change_pct": (market_context or {}).get("sector_avg_change_pct"),
                "market_health_score": (market_context or {}).get("market_health_score"),
            }
            if market_context else None
        ),
        "futures_context": (
            {
                "basis_pct": (futures or {}).get("basis_pct"),
                "oi_change_pct": (futures or {}).get("oi_change_pct"),
            }
            if futures else None
        ),
    }


def map_ebie_state(entry: dict, has_active_signal: bool) -> tuple[str, str, str]:
    """Derive (state, direction, reason) for one /api/ticks row.

    Pure function, no I/O -- easy to unit-test and to keep in sync with
    what's actually being read from `entry` below. `entry` is one row
    from _build_ticks(); `has_active_signal` is a separate, cheap check
    of whether infusion:signal:{symbol} currently exists (a real,
    scanner-fired signal, not this shadow state machine's own read).
    """
    tier = str(entry.get("stock_breakout_tier") or "NO_CHASE").upper()
    pb_state = str(entry.get("setup_state") or "IDLE").upper()
    chase_quality = str(entry.get("chase_quality") or "").upper()
    direction = "BEARISH" if entry.get("trend_bias") == "SELL" else "BULLISH"

    if has_active_signal:
        return "CONFIRMED", direction, "a real scanner-fired signal is currently active"

    if pb_state == "TRIGGERED":
        return "TRIGGERED", direction, "pre-breakout tracker just triggered"

    if tier == "OPTION_READY" or (
        tier == "BREAKOUT_NOW" and chase_quality in {"HIGHLY_CHASEABLE", "CLEAN"}
    ):
        return "ARMED", direction, f"tier={tier} chase_quality={chase_quality or 'n/a'}"

    if tier in {"RETEST_ENTRY", "BREAKOUT_NOW"}:
        return "READY", direction, f"tier={tier}"

    if tier == "EARLY_WATCH" or pb_state == "COILED":
        state = "PRE_BREAKDOWN" if direction == "BEARISH" else "PRE_BREAKOUT"
        return state, direction, f"tier={tier} pb_state={pb_state}"

    if pb_state in {"COMPRESSING", "ACCUMULATING"}:
        return "DEVELOPING", direction, f"pb_state={pb_state}"

    if pb_state == "EXPIRED":
        return "FAILED", direction, "pre-breakout setup expired without ever breaking out"

    return "IDLE", direction, "no active evidence"


async def _read_prev_states(redis, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    values = await redis.mget([f"{KEY_EBIE_STATE_PREFIX}{k}" for k in keys])
    result: dict[str, str] = {}
    for key, raw in zip(keys, values):
        if raw:
            result[key] = raw.decode() if isinstance(raw, bytes) else raw
    return result


async def _write_states(redis, updates: dict[str, str]) -> None:
    if not updates:
        return
    pipe = redis.pipeline(transaction=False)
    for key, state in updates.items():
        pipe.set(f"{KEY_EBIE_STATE_PREFIX}{key}", state, ex=EBIE_STATE_TTL_SEC)
    await pipe.execute()


async def sweep_once(app) -> dict:
    """One shadow-state-machine pass. Called every SWEEP_INTERVAL_SEC by
    ebie_state_loop(); also directly callable (e.g. from a verification
    script) since it only needs `app`'s redis/pg_pool.
    """
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        return {"available": False, "reason": "Redis or Postgres pool not available."}

    ticks = await _build_ticks(redis)
    symbols = [t["symbol"] for t in ticks if t.get("symbol")]

    # Bulk-check which symbols currently have a real, scanner-fired
    # active signal (infusion:signal:{symbol} -- see engine.py's
    # legacy_signal_key). Existence alone is enough; the key is TTL'd by
    # the signal's own ttl_sec, so "exists" already means "currently
    # active," no extra freshness check needed.
    active_pipe = redis.pipeline(transaction=False)
    for symbol in symbols:
        active_pipe.exists(f"{KEY_SIGNAL_PREFIX}{symbol}")
    active_results = await active_pipe.execute() if symbols else []
    has_signal = {s: bool(r) for s, r in zip(symbols, active_results)}

    mapped: dict[str, tuple[str, str, str]] = {}
    for entry in ticks:
        symbol = entry.get("symbol")
        if not symbol:
            continue
        state, direction, reason = map_ebie_state(entry, has_signal.get(symbol, False))
        mapped[symbol] = (state, direction, reason)

    cache_keys = [f"{s}:{d}" for s, (_, d, _) in mapped.items()]
    prev_states = await _read_prev_states(redis, cache_keys)

    transitions = 0
    state_updates: dict[str, str] = {}
    by_entry = {e.get("symbol"): e for e in ticks if e.get("symbol")}

    async with pool.acquire() as conn:
        for symbol, (state, direction, reason) in mapped.items():
            cache_key = f"{symbol}:{direction}"
            prev_state = prev_states.get(cache_key)
            state_updates[cache_key] = state

            if prev_state == state:
                continue  # no transition -- exactly the point of a cache, not a snapshot

            entry = by_entry.get(symbol, {})
            await conn.execute(
                """
                INSERT INTO ebie_state_transitions
                    (symbol, direction, sector_id, state, prev_state, reason,
                     legacy_tier, legacy_pb_state, score, ltp)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                symbol, direction, entry.get("sector_id"), state, prev_state, reason,
                entry.get("stock_breakout_tier"), entry.get("setup_state"),
                entry.get("stock_breakout_score"), entry.get("ltp"),
            )
            transitions += 1

    await _write_states(redis, state_updates)

    # EBIE EB-15 Phase 4 item 5: raw market/sector-context inputs per
    # symbol -- reuses `ticks` (already fetched above for this same
    # sweep) for a cheap sector-average pass, matching ticks.py's own
    # _build_ticks() computation exactly (same grouping logic, so the
    # two never quietly diverge), plus one whole-universe breadth read
    # (already the established "~0.1s, pure Redis, once per request"
    # cost per market_breadth.py's own header). See constants.py's own
    # comment on KEY_MARKET_CONTEXT_PREFIX for why RAW inputs are cached
    # here rather than a pre-biased score. Computed BEFORE the
    # lightweight-verdict loop below (moved up from its original
    # position after that loop) so §6.5's middle-ground enrichment can
    # reuse these in-process values directly -- zero extra Redis round
    # trip for data already being computed this same sweep anyway.
    sector_changes: dict[str, list[float]] = {}
    for entry in ticks:
        sid = entry.get("sector_id") or ""
        if sid:
            sector_changes.setdefault(sid, []).append(float(entry.get("change_pct") or 0))
    sector_avg_change = {sid: sum(vals) / len(vals) for sid, vals in sector_changes.items() if vals}
    try:
        market_health_score = (await compute_market_breadth(redis)).get("health_score")
    except Exception:
        market_health_score = None

    # EBIE-KNOWN-GAPS.md §6.5 middle ground -- one batched hgetall over
    # the already-fully-covered futures cache (FUTURES_STATE_PREFIX,
    # 209/208 symbols per §1.7's own table -- no rolling-subset/rate-
    # limit concern here, unlike mtf/options-dynamics/option-chain).
    # Best-effort: a decode failure on one symbol's hash never drops the
    # others, and a total Redis failure just means every symbol's
    # lightweight verdict gets futures_context=None this sweep, same
    # "never a silent fabricated number" convention as everywhere else.
    futures_map: dict[str, dict] = {}
    try:
        futures_pipe = redis.pipeline(transaction=False)
        for symbol in symbols:
            futures_pipe.hgetall(f"{FUTURES_STATE_PREFIX}{symbol}")
        futures_results = await futures_pipe.execute()
        for symbol, raw in zip(symbols, futures_results):
            if not raw:
                continue
            decoded: dict = {}
            for k, v in raw.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                if key in ("basis_pct", "oi_change_pct") and val != "":
                    try:
                        decoded[key] = float(val)
                    except (TypeError, ValueError):
                        pass
            if decoded:
                futures_map[symbol] = decoded
    except Exception:
        futures_map = {}

    # EBIE EB-15 Phase 3: compute + cache one lightweight verdict per
    # symbol, every sweep -- reuses `mapped`/`by_entry` already built
    # above, zero extra I/O beyond the pipeline write itself.
    verdict_pipe = redis.pipeline(transaction=False)
    verdicts_written = 0
    for symbol, (state, direction, _reason) in mapped.items():
        entry = by_entry.get(symbol, {})
        sid = entry.get("sector_id") or ""
        symbol_market_context = {
            "nifty_change_pct": entry.get("nifty_change_pct"),
            "sector_avg_change_pct": sector_avg_change.get(sid),
            "market_health_score": market_health_score,
        }
        verdict = compute_lightweight_verdict(
            entry, state, direction,
            market_context=symbol_market_context, futures=futures_map.get(symbol),
        )
        verdict["symbol"] = symbol
        verdict["ebie_state"] = state
        verdict["sector_id"] = entry.get("sector_id")
        verdict["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        verdict_pipe.set(
            f"{KEY_EBIE_VERDICT_LITE_PREFIX}{symbol}",
            msgpack.packb(verdict, use_bin_type=True),
            ex=EBIE_VERDICT_LITE_TTL_SEC,
        )
        verdicts_written += 1
    if verdicts_written:
        await verdict_pipe.execute()

    market_context_pipe = redis.pipeline(transaction=False)
    market_context_written = 0
    for entry in ticks:
        symbol = entry.get("symbol")
        if not symbol:
            continue
        sid = entry.get("sector_id") or ""
        payload = {
            "nifty_change_pct": entry.get("nifty_change_pct"),
            "sector_avg_change_pct": sector_avg_change.get(sid),
            "market_health_score": market_health_score,
        }
        # JSON, not msgpack -- this cache is read scanner-side, which
        # already reads every other per-symbol cache (mtf/sentiment/
        # futures/options-dynamics) as JSON via json.loads(), not msgpack
        # (that dependency isn't even in scanner's own pyproject.toml).
        # KEY_EBIE_VERDICT_LITE_PREFIX above stays msgpack correctly --
        # that one's consumed by api's own routes, a different reader.
        market_context_pipe.set(
            f"{KEY_MARKET_CONTEXT_PREFIX}{symbol}",
            json.dumps(payload, separators=(",", ":")),
            ex=MARKET_CONTEXT_TTL_SEC,
        )
        market_context_written += 1
    if market_context_written:
        await market_context_pipe.execute()

    status = {
        "available": True,
        "swept": len(ticks),
        "transitions": transitions,
        "verdicts_written": verdicts_written,
        "market_context_written": market_context_written,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    await redis.set(STATUS_KEY, json.dumps(status, separators=(",", ":")), ex=600)
    return status


async def ebie_state_loop(app) -> None:
    redis = app.get("redis")
    pool = app.get("pg_pool")
    if not redis or not pool:
        logger.info("ebie_state_queue_skipped", reason="redis_or_pg_pool_unavailable")
        return
    logger.info("ebie_state_queue_started", interval=SWEEP_INTERVAL_SEC)
    while True:
        with contextlib.suppress(Exception):
            status = await sweep_once(app)
            logger.info("ebie_state_sweep", **{k: v for k, v in status.items() if k != "checked_at"})
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
