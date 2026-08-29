"""Structure & Breakout Suite -- Phase 4 (2026-08-29): the optimizer.

Searches the literal parameter grid the approved spec names for a
"statistically stronger setup candidate" -- never "perfect" or
"guaranteed" (see DISCLAIMER below, returned verbatim on every
response, same convention as api.structure_signal's own DISCLAIMER).

REUSED, not reinvented, from api.routes.backtest's own real walk-
forward machinery (see that module's own compute_walkforward() /
_purge_and_embargo() / _compute_dsr()):
  - The purge/embargo chronological split concept -- adapted here to
    SimulatedTrade's own entry_time/exit_time fields (backtest.py's
    version operates on archived-signal rows with a `created_at`
    datetime column, an incompatible shape; the ALGORITHM is the same
    real logic, not reinvented, just re-expressed for this module's own
    trade dataclass).
  - api.statistics_utils.expected_max_sharpe()/probabilistic_sharpe_ratio()
    -- the exact same Deflated Sharpe Ratio correction for the
    multiple-testing bias a large grid search creates, reused as-is.
  - api.structure_backtest.summarize_trades() -- the exact same win
    rate/profit factor/max drawdown/expectancy math already built and
    tested in Phase 3, reused for both the train and test metrics here.

REAL, DISCLOSED COMPUTE CONSTRAINT (checked by reasoning about the
actual cost, not assumed): unlike api.routes.backtest's own optimizer
(which only ever re-filters already-computed archived signal rows --
cheap), most of this module's own parameters
(min_setup_quality/min_bias_edge/fast_trigger_lookback/
atr_breakout_buffer/strict_stop_max_atr/tp_ratios/trade_mode) change
what the SIGNAL ENGINE ITSELF decides -- a different lookback produces
different trigger prices and therefore entirely different trades, not
a filterable subset of one baseline replay's trades. A true evaluation
of one parameter combination requires a full bar-by-bar replay (Phase
3's own `_replay_symbol_timeframe`), and the spec's full named grid is
4x3x5x5x5x2x2x3 = 18,000 combinations -- literally exhaustive-replaying
all 18,000 against even a few symbols of real daily history would take
on the order of hours in pure Python, not something a request (even a
backgrounded one meant to finish before a human loses interest) should
attempt. This module therefore uses SEEDED RANDOM SEARCH over the full
grid (Bergstra & Bengio 2012 -- a well-established, real alternative to
exhaustive grid search when the full grid is computationally
prohibitive, not an invented shortcut), capped at `max_combinations`
(default below), with the full grid size and the actual sampled count
both reported honestly in the response -- never silently presented as
if the whole grid were searched. `trigger_source` is the one dimension
that IS a cheap post-hoc filter (it doesn't change signal generation,
only which already-generated trades count) -- combinations that only
differ by trigger_source share one real replay, refiltered, instead of
re-running it 3 times.
"""

from __future__ import annotations

import itertools
import json
import random
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from api.statistics_utils import expected_max_sharpe, probabilistic_sharpe_ratio
from api.structure_backtest import (
    DEFAULT_COSTS,
    CostAssumptions,
    Side,
    SimulatedTrade,
    _load_symbol_history,
    _replay_symbol_timeframe,
    summarize_trades,
)
from api.structure_signal import StructureSignalConfig

logger = structlog.get_logger()
Payload = dict[str, Any]

DISCLAIMER = (
    "Statistically stronger setup candidate, evaluated out-of-sample -- "
    "never a perfect or guaranteed setup."
)

# The spec's own literal grid.
MIN_SETUP_QUALITY_GRID = [4, 5, 6, 7]
MIN_BIAS_EDGE_GRID = [0, 1, 2]
FAST_TRIGGER_LOOKBACK_GRID = [8, 10, 12, 15, 20]
ATR_BREAKOUT_BUFFER_GRID = [0.10, 0.15, 0.20, 0.25, 0.30]
STRICT_STOP_MAX_ATR_GRID = [0.8, 1.0, 1.15, 1.25, 1.5]
TP_RATIO_GRID = [
    (1.2, 2.0, 3.0),
    (1.5, 2.5, 3.5),
]  # paired (TP1,TP2,TP3), per the spec's own pairing
TRADE_MODE_GRID = ["BALANCED", "STRICT"]
TRIGGER_SOURCE_GRID = ["fast_range", "swing_zone", "trendline"]

FULL_GRID_SIZE = (
    len(MIN_SETUP_QUALITY_GRID)
    * len(MIN_BIAS_EDGE_GRID)
    * len(FAST_TRIGGER_LOOKBACK_GRID)
    * len(ATR_BREAKOUT_BUFFER_GRID)
    * len(STRICT_STOP_MAX_ATR_GRID)
    * len(TP_RATIO_GRID)
    * len(TRADE_MODE_GRID)
    * len(TRIGGER_SOURCE_GRID)
)
assert FULL_GRID_SIZE == 18_000, f"grid size drifted from the spec's own 18,000: {FULL_GRID_SIZE}"

DEFAULT_MAX_COMBINATIONS = 60  # see this module's own header for why not the full 18,000
DEFAULT_TRAIN_PCT = 0.70
DEFAULT_EMBARGO_SEC = 86_400.0  # 1 real trading day -- generous relative to daily-bar entries
MIN_TEST_TRADES = 10
MAX_OVERFIT_GAP_R = 0.5


@dataclass(frozen=True)
class ParamCombo:
    min_setup_quality: int
    min_bias_edge: int
    fast_trigger_lookback: int
    atr_breakout_buffer: float
    strict_stop_max_atr: float
    tp1_r: float
    tp2_r: float
    tp3_r: float
    trade_mode: str
    trigger_source: str

    def to_config(self) -> StructureSignalConfig:
        return StructureSignalConfig(
            min_setup_quality=self.min_setup_quality,
            min_bias_edge=self.min_bias_edge,
            fast_trigger_lookback=self.fast_trigger_lookback,
            atr_breakout_buffer=self.atr_breakout_buffer,
            strict_stop_max_atr=self.strict_stop_max_atr,
            tp1_r=self.tp1_r,
            tp2_r=self.tp2_r,
            tp3_r=self.tp3_r,
            trade_mode=self.trade_mode,
        )

    def replay_key(self) -> tuple[Any, ...]:
        """Everything EXCEPT trigger_source -- combos differing only by
        trigger_source share one real replay, refiltered afterward (see
        this module's own header)."""
        return (
            self.min_setup_quality,
            self.min_bias_edge,
            self.fast_trigger_lookback,
            self.atr_breakout_buffer,
            self.strict_stop_max_atr,
            self.tp1_r,
            self.tp2_r,
            self.tp3_r,
            self.trade_mode,
        )

    def as_dict(self) -> Payload:
        return {
            "min_setup_quality": self.min_setup_quality,
            "min_bias_edge": self.min_bias_edge,
            "fast_trigger_lookback": self.fast_trigger_lookback,
            "atr_breakout_buffer": self.atr_breakout_buffer,
            "strict_stop_max_atr": self.strict_stop_max_atr,
            "tp1_r": self.tp1_r,
            "tp2_r": self.tp2_r,
            "tp3_r": self.tp3_r,
            "trade_mode": self.trade_mode,
            "trigger_source": self.trigger_source,
        }


def _all_combos() -> list[ParamCombo]:
    combos = []
    for q, e, lb, buf, stop, (t1, t2, t3), mode, src in itertools.product(
        MIN_SETUP_QUALITY_GRID,
        MIN_BIAS_EDGE_GRID,
        FAST_TRIGGER_LOOKBACK_GRID,
        ATR_BREAKOUT_BUFFER_GRID,
        STRICT_STOP_MAX_ATR_GRID,
        TP_RATIO_GRID,
        TRADE_MODE_GRID,
        TRIGGER_SOURCE_GRID,
    ):
        combos.append(ParamCombo(q, e, lb, buf, stop, t1, t2, t3, mode, src))
    return combos


def sample_param_grid(
    max_combinations: int = DEFAULT_MAX_COMBINATIONS, seed: int = 42
) -> tuple[list[ParamCombo], int]:
    """Seeded random sample of the full 18,000-combination grid -- see
    this module's own header for why exhaustive search isn't attempted.
    Returns (sampled combos, full grid size) so callers can report both
    honestly. Deterministic (fixed seed) so re-running the same optimize
    call is reproducible, not a new random draw every time."""
    combos = _all_combos()
    if len(combos) <= max_combinations:
        return combos, len(combos)
    rng = random.Random(seed)
    return rng.sample(combos, max_combinations), len(combos)


def _purge_and_embargo_trades(
    trades: list[SimulatedTrade], train_pct: float, embargo_sec: float
) -> tuple[list[SimulatedTrade], list[SimulatedTrade], int, int]:
    """Same real purge/embargo reasoning as api.routes.backtest's own
    _purge_and_embargo() -- a train trade whose OUTCOME resolves at or
    after the split boundary leaks test-period information into the
    profile selection (purged); test trades starting within embargo_sec
    of the boundary can still be riding conditions that were live when
    training ended (embargoed) -- re-expressed here for SimulatedTrade's
    own entry_time/exit_time fields rather than backtest.py's row shape."""
    if len(trades) < 2:
        return list(trades), [], 0, 0
    ordered = sorted(trades, key=lambda t: t.entry_time)
    split_idx = max(1, min(len(ordered) - 1, round(len(ordered) * train_pct)))
    split_time = ordered[split_idx].entry_time

    train_candidates = ordered[:split_idx]
    train = [t for t in train_candidates if t.exit_time < split_time]
    purged_count = len(train_candidates) - len(train)

    embargo_end = split_time + embargo_sec
    test_candidates = ordered[split_idx:]
    test = [t for t in test_candidates if t.entry_time >= embargo_end]
    embargoed_count = len(test_candidates) - len(test)

    return train, test, purged_count, embargoed_count


def _consistency_fraction(trades: list[SimulatedTrade], key_fn: Any) -> float:
    """Fraction of the real distinct groups (symbols, or timeframes) in
    `trades` whose OWN summed R-multiple is positive -- "does this
    profile work broadly, not just on whichever one group happened to
    carry the whole set." 0.0 (not None) when there are no trades to
    group -- an honest "no consistency demonstrated," not a missing
    value."""
    if not trades:
        return 0.0
    groups: dict[Any, list[SimulatedTrade]] = {}
    for t in trades:
        groups.setdefault(key_fn(t), []).append(t)
    positive = sum(1 for g in groups.values() if sum(x.r_multiple for x in g) > 0)
    return round(positive / len(groups), 3)


def _confidence(
    test_trade_count: int, consistency_symbols: float, consistency_timeframes: float
) -> str:
    if test_trade_count >= 30 and consistency_symbols >= 0.6 and consistency_timeframes >= 0.6:
        return "HIGH"
    if test_trade_count >= MIN_TEST_TRADES:
        return "MEDIUM"
    return "LOW"


def _robustness_score(
    *,
    test_metrics: Payload,
    consistency_symbols: float,
    consistency_timeframes: float,
    overfit_gap: float | None,
    test_trade_count: int,
) -> float:
    """Real, disclosed weighting -- matches the approved architecture's
    own "Final Robustness Score" formula (profit factor + win rate +
    low drawdown + trade-count reliability + cross-symbol/timeframe
    consistency - overfitting penalty). Weights are this implementation's
    own calibration, same disclosed-not-hidden posture as EBIE's own
    FAMILY_WEIGHTS table elsewhere in this codebase -- a real, named,
    single place to retune, not scattered magic numbers."""
    profit_factor = min(test_metrics.get("profit_factor") or 0.0, 3.0)
    win_rate = test_metrics.get("win_rate_pct") or 0.0
    max_dd = test_metrics.get("max_drawdown_r") or 0.0
    score = (
        profit_factor * 20.0
        + win_rate * 0.5
        + (1.0 / (1.0 + max_dd)) * 20.0
        + min(test_trade_count, 100) * 0.3
        + consistency_symbols * 20.0
        + consistency_timeframes * 20.0
        - max(overfit_gap or 0.0, 0.0) * 15.0
    )
    return round(score, 3)


def _evaluate_combo(
    combo: ParamCombo,
    replay_trades: list[SimulatedTrade],
    *,
    requested_symbols: list[str],
    requested_timeframes: list[str],
    train_pct: float,
    embargo_sec: float,
) -> Payload:
    """One combo's full evaluation: refilter by trigger_source, purge/
    embargo split, train/test metrics, consistency, robustness score,
    and every real rejection rule the spec names."""
    filtered = [
        t for t in replay_trades if t.params_used.get("trigger_source") == combo.trigger_source
    ]
    train, test, purged_count, embargoed_count = _purge_and_embargo_trades(
        filtered, train_pct, embargo_sec
    )
    train_metrics = summarize_trades(train)
    test_metrics = summarize_trades(test)

    consistency_symbols = _consistency_fraction(test, lambda t: t.symbol)
    consistency_timeframes = _consistency_fraction(test, lambda t: t.timeframe)

    train_expectancy = train_metrics.get("expectancy_r")
    test_expectancy = test_metrics.get("expectancy_r")
    overfit_gap = (
        round(train_expectancy - test_expectancy, 4)
        if train_expectancy is not None and test_expectancy is not None
        else None
    )

    reasons: list[str] = []
    if len(test) < MIN_TEST_TRADES:
        reasons.append(f"Too few out-of-sample trades ({len(test)} < {MIN_TEST_TRADES})")
    unique_test_symbols = {t.symbol for t in test}
    if len(requested_symbols) >= 2 and len(unique_test_symbols) < 2:
        reasons.append(
            f"Out-of-sample trades came from only {len(unique_test_symbols)} symbol(s) "
            f"(requested {len(requested_symbols)})"
        )
    unique_test_timeframes = {t.timeframe for t in test}
    if len(requested_timeframes) >= 2 and len(unique_test_timeframes) < 2:
        reasons.append(
            f"Out-of-sample trades came from only {len(unique_test_timeframes)} timeframe(s) "
            f"(requested {len(requested_timeframes)})"
        )
    if overfit_gap is not None and overfit_gap > MAX_OVERFIT_GAP_R:
        reasons.append(
            f"Train/test expectancy gap too large ({overfit_gap:+.2f}R > {MAX_OVERFIT_GAP_R}R)"
        )
    if test_expectancy is not None and test_expectancy <= 0:
        reasons.append(f"Negative out-of-sample expectancy ({test_expectancy:+.2f}R)")
    elif test_expectancy is None:
        reasons.append("No out-of-sample trades to evaluate expectancy from")

    confidence = _confidence(len(test), consistency_symbols, consistency_timeframes)
    robustness = _robustness_score(
        test_metrics=test_metrics,
        consistency_symbols=consistency_symbols,
        consistency_timeframes=consistency_timeframes,
        overfit_gap=overfit_gap,
        test_trade_count=len(test),
    )

    return {
        "params": combo.as_dict(),
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "purged_train_count": purged_count,
        "embargoed_test_count": embargoed_count,
        "consistency_symbols": consistency_symbols,
        "consistency_timeframes": consistency_timeframes,
        "overfit_gap_r": overfit_gap,
        "confidence": confidence,
        "robustness_score": robustness,
        "rejected": bool(reasons),
        "rejection_reasons": reasons,
    }


def _compute_dsr(profiles: list[Payload], recommended: Payload | None) -> Payload:
    """Same real Deflated Sharpe Ratio correction as api.routes.
    backtest's own _compute_dsr() -- corrects for the selection bias
    this optimizer's own sampled grid search creates by trying many
    variants and picking a winner. Reuses statistics_utils.py's
    expected_max_sharpe()/probabilistic_sharpe_ratio() exactly as that
    function does; only the profile dict shape here differs."""
    trial_sharpes = [
        p["test_metrics"]["sharpe"]["sharpe"]
        for p in profiles
        if p.get("test_metrics", {}).get("sharpe", {}).get("sharpe") is not None
    ]
    n_trials = len(trial_sharpes)
    benchmark = expected_max_sharpe(trial_sharpes)

    if not recommended or benchmark is None:
        return {
            "available": False,
            "n_trials": n_trials,
            "reason": "Not enough profiles with a computable Sharpe (need std(R-multiples) > 0 across >=2 profiles).",
        }

    rec_sharpe = recommended.get("test_metrics", {}).get("sharpe") or {}
    sr_hat = rec_sharpe.get("sharpe")
    n = rec_sharpe.get("n")
    skew = rec_sharpe.get("skew")
    kurtosis = rec_sharpe.get("kurtosis")
    if sr_hat is None or not n or skew is None or kurtosis is None:
        return {
            "available": False,
            "n_trials": n_trials,
            "benchmark_sharpe": round(benchmark, 4),
            "reason": "Recommended profile's test set has no computable Sharpe (all wins, all losses, or too few decided trades).",
        }

    dsr = probabilistic_sharpe_ratio(sr_hat, benchmark, n, skew, kurtosis)
    return {
        "available": True,
        "n_trials": n_trials,
        "recommended_sharpe": round(sr_hat, 4),
        "benchmark_sharpe": round(benchmark, 4),
        "recommended_n_trades": n,
        "deflated_sharpe_ratio": round(dsr, 4) if dsr is not None else None,
        "note": (
            "Probability the recommended profile's real edge exceeds what pure chance "
            f"would produce as the best of {n_trials} tried variants -- not a precision "
            "number, a confidence-in-the-selection number."
        ),
    }


async def optimize_structure_backtest(
    redis: Any,
    *,
    symbols: list[str],
    timeframes: list[str],
    side: Side,
    start_ts: float,
    end_ts: float,
    costs: CostAssumptions = DEFAULT_COSTS,
    max_combinations: int = DEFAULT_MAX_COMBINATIONS,
    train_pct: float = DEFAULT_TRAIN_PCT,
    embargo_sec: float = DEFAULT_EMBARGO_SEC,
    seed: int = 42,
) -> Payload:
    """The actual search: samples the grid, replays each unique
    (non-trigger-source) parameter combination once against real
    history, refilters by trigger_source, evaluates every resulting
    profile out-of-sample, and ranks the survivors. Never picks the
    highest raw P&L -- ranks by the real robustness score, and a
    profile that fails ANY of the spec's own rejection rules is marked
    rejected and excluded from ever being "recommended," regardless of
    how good its raw numbers look."""
    combos, full_grid_size = sample_param_grid(max_combinations, seed)

    # Real history fetched ONCE per symbol and reused across every
    # sampled combination -- only the (cheap) pure replay computation
    # repeats per combo, not the Redis I/O.
    history: dict[str, tuple[list[Any], list[Any]]] = {}
    for symbol in symbols:
        history[symbol] = await _load_symbol_history(redis, symbol, start_ts, end_ts)

    replay_cache: dict[tuple[Any, ...], list[SimulatedTrade]] = {}
    profiles: list[Payload] = []
    replays_executed = 0

    for combo in combos:
        key = combo.replay_key()
        if key not in replay_cache:
            trades: list[SimulatedTrade] = []
            config = combo.to_config()
            for symbol in symbols:
                daily_full, intraday_full = history[symbol]
                for timeframe in timeframes:
                    trades.extend(
                        await _replay_symbol_timeframe(
                            symbol=symbol,
                            timeframe=timeframe,
                            daily_full=daily_full,
                            intraday_full=intraday_full,
                            side=side,
                            config=config,
                            costs=costs,
                        )
                    )
            replay_cache[key] = trades
            replays_executed += 1

        profile = _evaluate_combo(
            combo,
            replay_cache[key],
            requested_symbols=symbols,
            requested_timeframes=timeframes,
            train_pct=train_pct,
            embargo_sec=embargo_sec,
        )
        profiles.append(profile)

    survivors = [p for p in profiles if not p["rejected"]]
    survivors.sort(key=lambda p: p["robustness_score"], reverse=True)
    recommended = survivors[0] if survivors else None
    for i, p in enumerate(survivors):
        p["rank"] = i + 1

    dsr = _compute_dsr(profiles, recommended)

    note = (
        f"Recommended profile ranked #1 of {len(survivors)} candidate(s) that survived every "
        "rejection rule (too-few-trades, single-symbol, single-timeframe, overfit gap, "
        "negative out-of-sample expectancy)."
        if recommended
        else (
            f"No profile among the {len(combos)} tested survived the rejection rules -- "
            "none of them is a statistically stronger setup candidate yet. Do not treat any "
            "candidate below as safe to use; collect more real history or widen the search."
        )
    )

    return {
        "available": True,
        "full_grid_size": full_grid_size,
        "sampled_combinations": len(combos),
        "unique_replays_executed": replays_executed,
        "symbols": symbols,
        "timeframes": timeframes,
        "side": side,
        "train_pct": train_pct,
        "embargo_sec": embargo_sec,
        "recommended": recommended,
        "candidates": sorted(profiles, key=lambda p: p["robustness_score"], reverse=True)[:20],
        "rejected_count": sum(1 for p in profiles if p["rejected"]),
        "survivor_count": len(survivors),
        "dsr": dsr,
        "note": note,
        "disclaimer": DISCLAIMER,
    }


async def persist_optimized_profiles(pg_pool: Any, run_id: str, result: Payload) -> None:
    """Stores every candidate profile (not just the recommended one) so
    a run's own optimizer output can be audited later -- rank is set
    only for real survivors, per structure_optimized_profiles' own
    schema comment."""
    ranked_by_params = {
        json.dumps(p["params"], sort_keys=True): p["rank"]
        for p in result["candidates"]
        if not p["rejected"]
    }
    rows = []
    for p in result["candidates"]:
        params_key = json.dumps(p["params"], sort_keys=True)
        rows.append(
            (
                str(uuid.uuid4()),
                run_id,
                json.dumps(p["params"]),
                json.dumps(p["train_metrics"], default=str),
                json.dumps(p["test_metrics"], default=str),
                p["consistency_symbols"],
                p["consistency_timeframes"],
                p["overfit_gap_r"],
                p["robustness_score"],
                p["confidence"],
                p["rejected"],
                p["rejection_reasons"],
                ranked_by_params.get(params_key),
            )
        )
    if not rows:
        return
    async with pg_pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO structure_optimized_profiles
                (id, run_id, params, train_metrics, test_metrics, consistency_symbols,
                 consistency_timeframes, overfit_gap_r, robustness_score, confidence,
                 rejected, rejection_reasons, rank)
            VALUES ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)
            """,
            rows,
        )


async def get_cached_optimize_result(pg_pool: Any, run_id: str) -> Payload | None:
    """Reads back a previously-persisted optimize run for this run_id --
    avoids silently re-running the (real, non-trivial) search on every
    GET. None when this run_id has never been optimized yet."""
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT params, train_metrics, test_metrics, consistency_symbols,
                   consistency_timeframes, overfit_gap_r, robustness_score, confidence,
                   rejected, rejection_reasons, rank, created_at
            FROM structure_optimized_profiles
            WHERE run_id = $1
            ORDER BY robustness_score DESC
            """,
            run_id,
        )
    if not rows:
        return None

    candidates: list[Payload] = []
    for r in rows:
        d = dict(r)
        for key in ("params", "train_metrics", "test_metrics"):
            if isinstance(d.get(key), str):
                d[key] = json.loads(d[key])
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        # asyncpg returns Postgres NUMERIC columns as Decimal, not float
        # -- json.dumps/aiohttp's json_response can't serialize Decimal
        # (confirmed live: this exact call 500'd before this fix, per
        # this sprint's own real-verification pass, not a hypothetical).
        for key in (
            "consistency_symbols",
            "consistency_timeframes",
            "overfit_gap_r",
            "robustness_score",
        ):
            if d.get(key) is not None:
                d[key] = float(d[key])
        candidates.append(d)

    recommended = next((c for c in candidates if c.get("rank") == 1), None)
    survivor_count = sum(1 for c in candidates if not c["rejected"])
    return {
        "recommended": recommended,
        "candidates": candidates[:20],
        "rejected_count": sum(1 for c in candidates if c["rejected"]),
        "survivor_count": survivor_count,
        "note": (
            f"Recommended profile ranked #1 of {survivor_count} candidate(s) that survived "
            "every rejection rule."
            if recommended
            else "No profile among the tested candidates survived the rejection rules -- none "
            "of them is a statistically stronger setup candidate yet."
        ),
        "disclaimer": DISCLAIMER,
    }
