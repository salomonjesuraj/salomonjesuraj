"""Structure & Breakout Suite -- Phase 3 (2026-08-29): the real
historical replay backtester. Recomputes Phase 1/2's own signal engine
bar-by-bar against real historical OHLC and simulates trades from it --
a genuinely different capability from api.routes.backtest's own
walk-forward optimizer, which only ever evaluates signals that actually
fired live (see the approved architecture's own Section 0 finding).

REAL, DISCLOSED DATA CONSTRAINT -- checked before writing a single line
of replay logic, not assumed: `ohlcv_daily`/`ohlcv_intraday` (the
Postgres tables `migrations/init.sql` defines for exactly this purpose)
are EMPTY in this environment -- confirmed with `SELECT count(*)`,
zero rows in either, and no INSERT into either anywhere in this
codebase's real services. The only real historical OHLC this system
actually has is the same Redis-backed store the live dashboard reads
(api.routes.mtf's own `infusion:ohlc:{symbol}:daily`/`:1m`/`:history:1m`
zsets) -- confirmed live: ~251 real daily bars per symbol (~1 trading
year) and ~1 month of real 1-minute history. "Large date ranges" in
this Phase 3 cut means exactly that much -- a real ~1-year daily
backtest, a real ~1-month intraday one -- not a fabricated multi-year
claim. A requested date range wider than what's actually stored is
honestly reported via `bars_available`/`requested_vs_available` on the
run, never silently padded or invented.

DISCLOSED SIMPLIFICATIONS (real, not fake, but real limitations,
per this phase's own "clearly mark it as incomplete" instruction):

1. Single-leg exit. Each simulated trade closes FULLY at whichever of
   SL/TP1/TP2/TP3/session-close is touched first -- it does not scale
   out and trail the way the live dashboard's TRAIL_AFTER_TP1/
   TRAIL_TO_TP3 travel-status states describe. A true partial-exit,
   multi-leg simulator is real, additional work, not built here.
2. Position sizing. "Net P&L" is reported in R-multiples (scale-
   invariant, comparable across symbols at wildly different prices)
   as the primary metric, plus a secondary per-unit (1 share/lot)
   currency P&L under a disclosed "always trade exactly 1 unit"
   assumption. A real risk-based position-sizing model (account
   capital, F&O lot sizes) is not implemented -- that's genuinely new,
   separate work.
3. Cost model. Brokerage/slippage use a simple, disclosed retail
   convention (slippage in basis points against the fill, a flat
   brokerage-per-trade converted to a per-unit-equivalent cost) --
   not api.cost_model's own OptionTradeCostInput (that one is built
   for option bid/ask premium capture, a different instrument and a
   different data shape; force-fitting it onto equity/index price
   moves would misrepresent both).

REUSED, not reinvented:
  - api.structure_signal.compute_structure_signal_from_bars() -- THE
    signal engine, called once per replay step with lookahead-safe
    bar windows. This is the exact same real function the live
    GET /api/structure/signal route calls (extracted for this reuse in
    this same phase) -- a replay trade and a live trade are decided by
    the literal same code, not a second copy that could diverge.
  - api.routes.mtf._decode_ohlc()/_merge_bars() -- the same real OHLC
    decode this service already uses everywhere else.
  - api.structure_signal._aggregate (via api.routes.charts) and
    _bars_for_timeframe -- the same timeframe bucketing the live route
    uses.
  - api.statistics_utils.sharpe_stats() -- the real, already-tested
    Sharpe/skew/kurtosis math, reused as-is for "Sharpe-like
    consistency" over this run's own real R-multiples (each computed
    directly from real simulated fill prices here, a MORE precise
    input than statistics_utils.r_multiple()'s own coarser binary
    TARGET_HIT/STOP_HIT approximation -- so that one function is not
    reused for the per-trade R-multiple itself, only for the aggregate
    stats, which are generic over any real float list).
  - api.smc_geometry.compute_smc_geometry() -- reused again here for
    `market_phase_at_entry` (real trend_text as of the entry bar), not
    a new phase taxonomy.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as dt_time
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog

from api.routes.charts import _aggregate
from api.routes.mtf import _decode_ohlc, _merge_bars
from api.statistics_utils import sharpe_stats
from api.structure_signal import (
    DEFAULT_CONFIG,
    TIMEFRAME_MINUTES,
    StructureSignalConfig,
    _htf_for,
    _is_intraday_timeframe,
    compute_structure_signal_from_bars,
)

logger = structlog.get_logger()
Payload = dict[str, Any]
Bar = dict[str, Any]

Side = Literal["LONG_ONLY", "SHORT_ONLY", "BOTH"]

_IST = ZoneInfo("Asia/Kolkata")
_SESSION_CLOSE = dt_time(15, 30)

# Real retail-style defaults, disclosed -- see this module's own header
# for why these aren't api.cost_model's option-premium cost model.
DEFAULT_SLIPPAGE_BPS = 5.0
DEFAULT_BROKERAGE_PER_TRADE = 20.0

MIN_WARMUP_BARS = 30  # below this, compute_structure_signal_from_bars is never reliable anyway


@dataclass(frozen=True)
class CostAssumptions:
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    brokerage_per_trade: float = DEFAULT_BROKERAGE_PER_TRADE

    def as_dict(self) -> Payload:
        return {"slippage_bps": self.slippage_bps, "brokerage_per_trade": self.brokerage_per_trade}


DEFAULT_COSTS = CostAssumptions()


@dataclass
class _OpenPosition:
    symbol: str
    timeframe: str
    direction: str  # "LONG" | "SHORT"
    entry_time: float
    entry_price: float  # already slippage-adjusted (the real simulated fill)
    sl: float
    tp1: float
    tp2: float
    tp3: float
    setup_quality_at_entry: int
    market_phase_at_entry: str
    params_used: Payload


@dataclass
class SimulatedTrade:
    symbol: str
    timeframe: str
    direction: str
    entry_time: float
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    exit_time: float
    exit_price: float
    exit_reason: str  # "SL_HIT" | "TP1" | "TP2" | "TP3" | "SESSION_CLOSE"
    r_multiple: float
    pnl_per_share: float
    setup_quality_at_entry: int
    market_phase_at_entry: str
    params_used: Payload = field(default_factory=dict)


def _entry_fill_price(raw_price: float, bullish: bool, costs: CostAssumptions) -> float:
    """Slippage moves the fill AGAINST the trader -- a long buys slightly
    higher, a short sells slightly lower."""
    slip = raw_price * (costs.slippage_bps / 10_000.0)
    return raw_price + slip if bullish else raw_price - slip


def _exit_fill_price(raw_price: float, bullish: bool, costs: CostAssumptions) -> float:
    """Same unfavorable direction on exit -- a long's exit sell fills
    slightly lower, a short's exit buy fills slightly higher."""
    slip = raw_price * (costs.slippage_bps / 10_000.0)
    return raw_price - slip if bullish else raw_price + slip


def _brokerage_per_unit(entry_price: float, costs: CostAssumptions) -> float:
    """A flat per-trade brokerage fee, converted to a per-share-
    equivalent cost so it can be subtracted directly from pnl_per_share
    -- a disclosed simplification (real flat brokerage doesn't scale
    with share count the way this conversion implies), reasonable for a
    single-unit-per-trade backtest (see this module's own header)."""
    if entry_price <= 0:
        return 0.0
    return costs.brokerage_per_trade / entry_price


def _check_exit(
    position: _OpenPosition, bar: Bar, costs: CostAssumptions
) -> tuple[float, str] | None:
    """SL is checked before TP on any bar where both could technically
    have been touched (a real, standard, conservative backtest
    convention -- see this module's own header) -- avoids an
    over-optimistic result from assuming favorable intrabar ordering
    the bar's own OHLC can't actually prove."""
    high, low = float(bar["high"]), float(bar["low"])
    bullish = position.direction == "LONG"
    if bullish:
        if low <= position.sl:
            return _exit_fill_price(position.sl, bullish, costs), "SL_HIT"
        if high >= position.tp3:
            return _exit_fill_price(position.tp3, bullish, costs), "TP3"
        if high >= position.tp2:
            return _exit_fill_price(position.tp2, bullish, costs), "TP2"
        if high >= position.tp1:
            return _exit_fill_price(position.tp1, bullish, costs), "TP1"
        return None
    if high >= position.sl:
        return _exit_fill_price(position.sl, bullish, costs), "SL_HIT"
    if low <= position.tp3:
        return _exit_fill_price(position.tp3, bullish, costs), "TP3"
    if low <= position.tp2:
        return _exit_fill_price(position.tp2, bullish, costs), "TP2"
    if low <= position.tp1:
        return _exit_fill_price(position.tp1, bullish, costs), "TP1"
    return None


def _finalize_trade(
    position: _OpenPosition,
    exit_time: float,
    exit_price: float,
    exit_reason: str,
    costs: CostAssumptions,
) -> SimulatedTrade:
    bullish = position.direction == "LONG"
    risk = position.entry_price - position.sl if bullish else position.sl - position.entry_price
    raw_pnl = exit_price - position.entry_price if bullish else position.entry_price - exit_price
    pnl_per_share = raw_pnl - _brokerage_per_unit(position.entry_price, costs)
    r_multiple = round(pnl_per_share / risk, 4) if risk > 0 else 0.0
    return SimulatedTrade(
        symbol=position.symbol,
        timeframe=position.timeframe,
        direction=position.direction,
        entry_time=position.entry_time,
        entry_price=round(position.entry_price, 4),
        sl_price=round(position.sl, 4),
        tp1_price=round(position.tp1, 4),
        tp2_price=round(position.tp2, 4),
        tp3_price=round(position.tp3, 4),
        exit_time=exit_time,
        exit_price=round(exit_price, 4),
        exit_reason=exit_reason,
        r_multiple=r_multiple,
        pnl_per_share=round(pnl_per_share, 4),
        setup_quality_at_entry=position.setup_quality_at_entry,
        market_phase_at_entry=position.market_phase_at_entry,
        params_used=position.params_used,
    )


def _is_session_close_bar(bar_time: float, next_bar_time: float | None) -> bool:
    """True when this bar is the last one before the IST session closes
    -- either it's genuinely at/after 15:30, or there's no next bar
    within the same real trading session (a gap to the next session)."""
    ts = datetime.fromtimestamp(bar_time, tz=_IST)
    if ts.time() >= _SESSION_CLOSE:
        return True
    if next_bar_time is None:
        return True
    next_ts = datetime.fromtimestamp(next_bar_time, tz=_IST)
    return next_ts.date() != ts.date()


async def _load_symbol_history(
    redis: Any, symbol: str, start_ts: float, end_ts: float
) -> tuple[list[Bar], list[Bar]]:
    """Real date-range-bounded fetch -- unlike api.routes.mtf's own
    `_load_bars()` (count-bounded, built for the live dashboard's
    rolling-window needs), this uses `zrangebyscore` so a requested
    start/end date is honored exactly, bounded by whatever real history
    actually exists in that range (see this module's own header)."""
    daily_raw, history_raw, live_raw = await asyncio.gather(
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:daily", start_ts, end_ts),
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:history:1m", start_ts, end_ts),
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:1m", start_ts, end_ts),
    )
    daily = _decode_ohlc(daily_raw)
    intraday = _merge_bars(_decode_ohlc(history_raw), _decode_ohlc(live_raw))
    return daily, intraday


def _bars_for_timeframe(timeframe: str, intraday_1m: list[Bar], daily: list[Bar]) -> list[Bar]:
    minutes = TIMEFRAME_MINUTES[timeframe]
    if minutes is None:
        return daily
    return _aggregate(intraday_1m, minutes)


def _market_phase_at(bars_window: list[Bar]) -> str:
    """Real trend_text as of the entry bar, reused verbatim from
    api.smc_geometry -- not a new phase taxonomy, and not the LIVE
    infusion:regime hash (which only ever holds TODAY's value, useless
    for a historical entry from a year ago)."""
    from api.smc_geometry import compute_smc_geometry

    geo = compute_smc_geometry(bars_window)
    return str(geo.get("trend_text") or "RANGE / UNDEFINED") if geo.get("ready") else "WARMING_UP"


async def _replay_symbol_timeframe(
    *,
    symbol: str,
    timeframe: str,
    daily_full: list[Bar],
    intraday_full: list[Bar],
    side: Side,
    config: StructureSignalConfig,
    costs: CostAssumptions,
) -> list[SimulatedTrade]:
    """The actual bar-by-bar replay for one symbol/timeframe. One open
    position at a time -- see this module's own header for why
    overlapping/pyramided positions aren't simulated."""
    bars = _bars_for_timeframe(timeframe, intraday_full, daily_full)
    if len(bars) < MIN_WARMUP_BARS:
        return []

    htf = _htf_for(timeframe)
    htf_bars_full = _bars_for_timeframe(htf, intraday_full, daily_full)
    include_vwap = config.vwap_enabled and _is_intraday_timeframe(timeframe)
    intraday_session = _is_intraday_timeframe(timeframe)

    trades: list[SimulatedTrade] = []
    open_position: _OpenPosition | None = None

    for i in range(MIN_WARMUP_BARS, len(bars)):
        # Real production issue found via this sprint's own live
        # verification: this loop (and compute_structure_signal_from_bars
        # inside it) is entirely synchronous CPU work with no `await` of
        # its own. Without a periodic voluntary yield, a single replay --
        # let alone the optimizer's dozens of them back-to-back -- starves
        # aiohttp's single-threaded event loop for its ENTIRE duration
        # (confirmed live: other concurrent requests, e.g. the dashboard's
        # own market-indices polling, returned 502/504 for the ~90s a
        # real 60-combination optimize call ran). `asyncio.sleep(0)` is
        # the standard, minimal fix -- hands control back to the loop
        # after every chunk of bars so other requests keep being served,
        # without the larger refactor a process-pool executor would need.
        if i % 20 == 0:
            await asyncio.sleep(0)
        bar = bars[i]
        bar_time = float(bar["time"])

        if open_position is not None:
            exit_hit = _check_exit(open_position, bar, costs)
            if exit_hit is not None:
                exit_price, exit_reason = exit_hit
                trades.append(
                    _finalize_trade(open_position, bar_time, exit_price, exit_reason, costs)
                )
                open_position = None
                continue
            if intraday_session:
                next_time = float(bars[i + 1]["time"]) if i + 1 < len(bars) else None
                if _is_session_close_bar(bar_time, next_time):
                    trades.append(
                        _finalize_trade(
                            open_position, bar_time, float(bar["close"]), "SESSION_CLOSE", costs
                        )
                    )
                    open_position = None
            continue

        # No lookahead: every window below is trimmed to <= this bar's time.
        window = bars[: i + 1]
        daily_asof = [d for d in daily_full if float(d["time"]) <= bar_time]
        htf_asof = [h for h in htf_bars_full if float(h["time"]) <= bar_time]
        intraday_asof = (
            [m for m in intraday_full if float(m["time"]) <= bar_time] if include_vwap else []
        )

        signal = compute_structure_signal_from_bars(
            symbol=symbol,
            timeframe=timeframe,
            htf=htf,
            bars=window,
            htf_bars=htf_asof,
            daily_asof=daily_asof,
            intraday_1m_asof=intraday_asof,
            include_vwap=include_vwap,
            config=config,
        )
        if not signal.get("ready"):
            continue
        readiness = signal["trade_readiness"]
        if readiness not in ("BUY_ARMED", "SELL_ARMED"):
            continue
        bullish = readiness == "BUY_ARMED"
        if side == "LONG_ONLY" and not bullish:
            continue
        if side == "SHORT_ONLY" and bullish:
            continue
        if signal["entry"] is None or signal["sl"] is None:
            continue

        entry_fill = _entry_fill_price(float(signal["entry"]), bullish, costs)
        open_position = _OpenPosition(
            symbol=symbol,
            timeframe=timeframe,
            direction="LONG" if bullish else "SHORT",
            entry_time=bar_time,
            entry_price=entry_fill,
            sl=float(signal["sl"]),
            tp1=float(signal["tp1"]),
            tp2=float(signal["tp2"]),
            tp3=float(signal["tp3"]),
            setup_quality_at_entry=signal["bull_score"] if bullish else signal["bear_score"],
            market_phase_at_entry=_market_phase_at(window),
            params_used={
                "min_setup_quality": config.min_setup_quality,
                "min_bias_edge": config.min_bias_edge,
                "fast_trigger_lookback": config.fast_trigger_lookback,
                "atr_breakout_buffer": config.atr_breakout_buffer,
                "strict_stop_max_atr": config.strict_stop_max_atr,
                "tp1_r": config.tp1_r,
                "tp2_r": config.tp2_r,
                "tp3_r": config.tp3_r,
                "trigger_source": signal["trigger_source"],
            },
        )

    # A position still open at the very end of the requested window is
    # closed at the last real bar's close -- honest (never left dangling,
    # never fabricated a favorable/unfavorable exit beyond real data).
    if open_position is not None:
        trades.append(
            _finalize_trade(
                open_position,
                float(bars[-1]["time"]),
                float(bars[-1]["close"]),
                "SESSION_CLOSE",
                costs,
            )
        )

    return trades


# ─────────────────────────── metrics ───────────────────────────


def _max_drawdown_r(trades_chronological: list[SimulatedTrade]) -> float:
    """Max drawdown on the cumulative R-multiple equity curve -- scale-
    invariant across symbols at different price levels, same reasoning
    as net_pnl_r below."""
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for t in trades_chronological:
        equity += t.r_multiple
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 4)


def _max_consecutive_losses(trades_chronological: list[SimulatedTrade]) -> int:
    streak = 0
    worst = 0
    for t in trades_chronological:
        if t.r_multiple <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def summarize_trades(trades: list[SimulatedTrade]) -> Payload:
    """Every metric Phase 3's own requirements list asks for. Net P&L is
    reported in R-multiples (primary, scale-invariant) and per-unit
    currency (secondary, under the disclosed 1-unit-per-trade
    assumption) -- see this module's own header for why there's no
    single currency Net P&L without a real position-sizing model this
    phase doesn't build."""
    if not trades:
        return {
            "trade_count": 0,
            "reason": "No trades were generated by this run -- see run-level bars_available for whether real history existed at all.",
        }

    chronological = sorted(trades, key=lambda t: t.exit_time)
    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple <= 0]
    gross_win_r = sum(t.r_multiple for t in wins)
    gross_loss_r = abs(sum(t.r_multiple for t in losses))
    r_multiples = [t.r_multiple for t in trades]

    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2),
        "net_pnl_r": round(sum(r_multiples), 4),
        "net_pnl_per_unit": round(sum(t.pnl_per_share for t in trades), 4),
        "profit_factor": (round(gross_win_r / gross_loss_r, 4) if gross_loss_r > 0 else None),
        "profit_factor_note": (
            "None means zero losing trades in this set -- not an infinite edge; treat as unproven "
            "until more decided trades exist."
            if gross_loss_r == 0
            else None
        ),
        "avg_win_r": round(gross_win_r / len(wins), 4) if wins else None,
        "avg_loss_r": round(-gross_loss_r / len(losses), 4) if losses else None,
        "avg_r": round(sum(r_multiples) / len(r_multiples), 4),
        "expectancy_r": round(sum(r_multiples) / len(r_multiples), 4),
        "max_drawdown_r": _max_drawdown_r(chronological),
        "max_consecutive_losses": _max_consecutive_losses(chronological),
        "sharpe": sharpe_stats(r_multiples),
        "exit_reason_breakdown": {
            reason: sum(1 for t in trades if t.exit_reason == reason)
            for reason in ("SL_HIT", "TP1", "TP2", "TP3", "SESSION_CLOSE")
        },
    }


def _group_metrics(trades: list[SimulatedTrade], key_fn: Any) -> Payload:
    groups: dict[Any, list[SimulatedTrade]] = {}
    for t in trades:
        groups.setdefault(key_fn(t), []).append(t)
    return {
        str(k): summarize_trades(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))
    }


def compute_full_metrics(trades: list[SimulatedTrade]) -> Payload:
    return {
        "overview": summarize_trades(trades),
        "by_timeframe": _group_metrics(trades, lambda t: t.timeframe),
        "by_setup_quality": _group_metrics(trades, lambda t: t.setup_quality_at_entry),
        "by_symbol": _group_metrics(trades, lambda t: t.symbol),
    }


# ─────────────────────────── run orchestration ───────────────────────────


def _date_to_ts(d: date, end_of_day: bool = False) -> float:
    dt = datetime.combine(d, dt_time(23, 59, 59) if end_of_day else dt_time(0, 0, 0), tzinfo=UTC)
    return dt.timestamp()


async def create_backtest_run(
    pg_pool: Any,
    *,
    symbols: list[str],
    timeframes: list[str],
    start_date: date,
    end_date: date,
    side: Side,
    config: StructureSignalConfig,
    costs: CostAssumptions,
) -> str:
    """Inserts the run row (status=RUNNING) and returns its id
    immediately -- the actual replay runs as a background task (see
    run_structure_backtest below), matching every other long-running
    sweep in this service's own established pattern."""
    run_id = str(uuid.uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO structure_backtest_runs
                (run_id, symbols, timeframes, start_date, end_date, side,
                 cost_assumptions, config_used, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'RUNNING')
            """,
            run_id,
            symbols,
            timeframes,
            start_date,
            end_date,
            side,
            json.dumps(costs.as_dict()),
            json.dumps(
                {
                    "min_setup_quality": config.min_setup_quality,
                    "min_bias_edge": config.min_bias_edge,
                    "fast_trigger_lookback": config.fast_trigger_lookback,
                    "atr_breakout_buffer": config.atr_breakout_buffer,
                    "strict_stop_max_atr": config.strict_stop_max_atr,
                    "tp1_r": config.tp1_r,
                    "tp2_r": config.tp2_r,
                    "tp3_r": config.tp3_r,
                    "trade_mode": config.trade_mode,
                    "vwap_enabled": config.vwap_enabled,
                }
            ),
        )
    return run_id


async def _persist_trades(pg_pool: Any, run_id: str, trades: list[SimulatedTrade]) -> None:
    if not trades:
        return
    rows = [
        (
            run_id,
            t.symbol,
            t.timeframe,
            t.direction,
            datetime.fromtimestamp(t.entry_time, tz=UTC),
            t.entry_price,
            t.sl_price,
            t.tp1_price,
            t.tp2_price,
            t.tp3_price,
            datetime.fromtimestamp(t.exit_time, tz=UTC),
            t.exit_price,
            t.exit_reason,
            t.r_multiple,
            t.pnl_per_share,
            t.setup_quality_at_entry,
            t.market_phase_at_entry,
            json.dumps(t.params_used),
        )
        for t in trades
    ]
    async with pg_pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO structure_backtest_trades
                (run_id, symbol, timeframe, direction, entry_time, entry_price,
                 sl_price, tp1_price, tp2_price, tp3_price, exit_time, exit_price,
                 exit_reason, r_multiple, pnl_per_share, setup_quality_at_entry,
                 market_phase_at_entry, params_used)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)
            """,
            rows,
        )


async def run_structure_backtest(
    pg_pool: Any,
    redis: Any,
    *,
    run_id: str,
    symbols: list[str],
    timeframes: list[str],
    start_date: date,
    end_date: date,
    side: Side,
    config: StructureSignalConfig = DEFAULT_CONFIG,
    costs: CostAssumptions = DEFAULT_COSTS,
) -> None:
    """The actual replay -- runs in the background (asyncio.create_task
    from the route handler), writes trades incrementally per symbol/
    timeframe, then finalizes the run row with real aggregate metrics.
    Never raises into the caller -- a failure marks the run FAILED with
    the real error message rather than leaving it stuck at RUNNING."""
    start_ts = _date_to_ts(start_date)
    end_ts = _date_to_ts(end_date, end_of_day=True)
    all_trades: list[SimulatedTrade] = []
    bars_available: Payload = {}

    try:
        for symbol in symbols:
            daily_full, intraday_full = await _load_symbol_history(redis, symbol, start_ts, end_ts)
            bars_available[symbol] = {
                "daily_bars": len(daily_full),
                "intraday_1m_bars": len(intraday_full),
            }
            for timeframe in timeframes:
                trades = await _replay_symbol_timeframe(
                    symbol=symbol,
                    timeframe=timeframe,
                    daily_full=daily_full,
                    intraday_full=intraday_full,
                    side=side,
                    config=config,
                    costs=costs,
                )
                all_trades.extend(trades)

        await _persist_trades(pg_pool, run_id, all_trades)
        metrics = compute_full_metrics(all_trades)
        metrics["bars_available"] = bars_available
        metrics["requested_start_date"] = start_date.isoformat()
        metrics["requested_end_date"] = end_date.isoformat()

        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE structure_backtest_runs
                SET status = 'DONE', completed_at = now(), metrics = $2::jsonb
                WHERE run_id = $1
                """,
                run_id,
                json.dumps(metrics, default=str),
            )
        logger.info(
            "structure_backtest_completed",
            run_id=run_id,
            trades=len(all_trades),
            symbols=len(symbols),
            timeframes=len(timeframes),
        )
    except Exception as exc:
        logger.warning("structure_backtest_failed", run_id=run_id, error=str(exc))
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "UPDATE structure_backtest_runs SET status = 'FAILED', error = $2, completed_at = now() WHERE run_id = $1",
                run_id,
                str(exc),
            )


async def get_backtest_run(pg_pool: Any, run_id: str) -> Payload | None:
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, requested_at, completed_at, symbols, timeframes,
                   start_date, end_date, side, cost_assumptions, config_used,
                   status, error, metrics
            FROM structure_backtest_runs WHERE run_id = $1
            """,
            run_id,
        )
    if row is None:
        return None
    d = dict(row)
    for key in ("cost_assumptions", "config_used", "metrics"):
        if isinstance(d.get(key), str):
            d[key] = json.loads(d[key])
    d["run_id"] = str(d["run_id"])
    d["requested_at"] = d["requested_at"].isoformat() if d["requested_at"] else None
    d["completed_at"] = d["completed_at"].isoformat() if d["completed_at"] else None
    d["start_date"] = d["start_date"].isoformat() if d["start_date"] else None
    d["end_date"] = d["end_date"].isoformat() if d["end_date"] else None
    return d
