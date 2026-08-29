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
actual cost, not assumed): most of this module's own parameters
(min_setup_quality/min_bias_edge/fast_trigger_lookback/
atr_breakout_buffer/strict_stop_max_atr/tp_ratios/trade_mode/
trigger_source) change what the SIGNAL ENGINE ITSELF decides -- a
different lookback produces different trigger prices and therefore
entirely different trades, not a filterable subset of one baseline
replay's trades. A true evaluation of one parameter combination
requires a full bar-by-bar decision replay, and the spec's full named
grid is 4x3x5x5x5x2x2x3 = 18,000 combinations -- literally exhaustive-
replaying all 18,000 against even a few symbols of real daily history
would take on the order of hours in pure Python, not something a
request (even a backgrounded one meant to finish before a human loses
interest) should attempt. This module therefore uses SEEDED RANDOM
SEARCH over the full grid (Bergstra & Bengio 2012 -- a well-established,
real alternative to exhaustive grid search when the full grid is
computationally prohibitive, not an invented shortcut), capped at
`max_combinations` (default below, tighter for intraday runs -- see
DEFAULT_MAX_COMBINATIONS_INTRADAY), with the full grid size and the
actual sampled count both reported honestly in the response -- never
silently presented as if the whole grid were searched.

REAL FIX, not a redesign (review, 2026-08-29): `trigger_source` used to
be treated as a cheap post-hoc filter -- `replay_key()` deliberately
excluded it so combos differing only by trigger_source shared ONE
"hybrid" (closest-wins) replay, refiltered afterward. Because swing_
zone's own pivot levels are systematically closer to price than a
12-bar rolling range or a trendline projection in real data, hybrid
mode almost always won with swing_zone -- so a "fast_range" or
"trendline" combo silently tested hybrid mode and then discarded every
trade that wasn't swing_zone (confirmed live: 0 trades for almost every
non-swing_zone combo). `trigger_source` is now real, per api.
structure_signal.StructureSignalConfig's own `trigger_source_mode`
field -- each combo's OWN replay only ever considers its own trigger
source (see ParamCombo.to_config() and select_breakout_trigger()).

REAL PERFORMANCE FIX (review, 2026-08-29), not a redesign: every one of
the parameters above changes only the CHEAP decision layer (a bias-
threshold comparison, a trigger price calc, candle confirmation, a risk
calc) -- the EXPENSIVE layer underneath it (indicators, SMC geometry,
MFI, squeeze/RVOL, the seven-condition bull/bear subscore reads) is the
same for every combo tested against the same (symbol, timeframe) bars.
This module now calls api.structure_backtest.precompute_replay_features()
ONCE per (symbol, timeframe) pair in a run and replays every sampled
combo against that one precomputed feature set via _replay_with_
precomputed() -- turning an O(N_combos * n^2) replay cost into
O(n^2 + N_combos * n), where n is bar count. See api.structure_signal.
compute_bar_features()'s own docstring for exactly what's cacheable and
why (nothing swept by this module's own grid affects it).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from api.statistics_utils import expected_max_sharpe, probabilistic_sharpe_ratio
from api.structure_backtest import (
    DEFAULT_COSTS,
    CostAssumptions,
    PrecomputedReplay,
    ReplayDiagnostics,
    Side,
    SimulatedTrade,
    _load_symbol_history,
    _replay_with_precomputed,
    precompute_replay_features,
    summarize_trades,
)
from api.structure_signal import DEFAULT_CONFIG, StructureSignalConfig, _is_intraday_timeframe

logger = structlog.get_logger()
Payload = dict[str, Any]

DISCLAIMER = (
    "Statistically stronger setup candidate, evaluated out-of-sample -- "
    "never a perfect or guaranteed setup."
)

# In-memory, process-local progress store -- Task 3's own "return
# progress information so the UI does not feel stuck" ask. Deliberately
# not persisted (an optimizer run is re-runnable from scratch; losing
# progress on a process restart is an honest "start over," not silent
# data loss of anything the run itself produced). Polled by GET
# /api/structure/optimize/{run_id}/progress while the main (blocking)
# GET /api/structure/optimize/{run_id} call is still in flight -- the
# event loop stays responsive to that poll because of the same
# `asyncio.sleep(0)` yields the replay loop already uses (Phase 4 fix).
_progress_store: dict[str, Payload] = {}


def get_optimize_progress(run_id: str) -> Payload | None:
    """Reads back the current progress snapshot for an in-flight (or
    just-finished) optimizer run. None when no run with this id has
    ever reported progress in this process -- an honest "nothing to
    show," not a fabricated 0%."""
    snapshot = _progress_store.get(run_id)
    return dict(snapshot) if snapshot is not None else None


def _set_progress(run_id: str | None, **fields: Any) -> None:
    if not run_id:
        return
    current = _progress_store.setdefault(run_id, {})
    current.update(fields)
    current["updated_at"] = time.time()


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
# Real, hard guard (Task 3's own "add a hard runtime guard or max-
# combinations guard for intraday runs" ask): an intraday replay bar
# count can run into the thousands (a month of 1-minute history
# aggregated to 3m/5m/15m) vs. a daily replay's ~250 bars -- even after
# the precompute fix above, the per-combo decision pass is still O(n),
# and n is an order of magnitude larger for intraday. Both guards are
# enforced together, whichever trips first wins (see
# optimize_structure_backtest's own use of them).
DEFAULT_MAX_COMBINATIONS_INTRADAY = 20
DEFAULT_MAX_RUNTIME_SEC_INTRADAY = 90.0
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
            # Real fix (review, 2026-08-29): this used to be omitted
            # entirely -- trigger_source was ONLY ever applied as a
            # post-hoc filter on a shared "hybrid" replay (see this
            # module's own header for the swing_zone-dominance bug that
            # caused). Now threaded straight into the signal engine's
            # own trigger_source_mode so a "fast_range"/"trendline"
            # combo's replay never even considers the other sources.
            trigger_source_mode=self.trigger_source,
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
    """One combo's full evaluation: purge/embargo split, train/test
    metrics, consistency, robustness score, and every real rejection
    rule the spec names. The trigger_source filter below is now a
    defensive safety net, not the primary mechanism (review, 2026-08-29)
    -- `replay_trades` already comes from a replay whose OWN signal
    engine was restricted to combo.trigger_source (see ParamCombo.
    to_config()'s own trigger_source_mode), so every trade here should
    already match; this guards against a future regression re-
    introducing a shared/hybrid replay rather than doing any real
    filtering work today."""
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


def _trigger_source_breakdown(profiles: list[Payload]) -> Payload:
    """Task 4's own "trigger-source breakdown" / "trade count by trigger
    source" UI ask -- real per-source tallies from the profiles actually
    produced this run, not a derived guess. Reads combos_tested/
    total_test_trades/survivors per trigger_source so the UI can show,
    at a glance, whether fast_range/trendline are really being tested
    (and producing trades) independently of swing_zone."""
    breakdown: Payload = {}
    for p in profiles:
        source = p["params"]["trigger_source"]
        row = breakdown.setdefault(
            source, {"combos_tested": 0, "total_test_trades": 0, "survivors": 0}
        )
        row["combos_tested"] += 1
        row["total_test_trades"] += p["test_metrics"].get("trade_count", 0) or 0
        if not p["rejected"]:
            row["survivors"] += 1
    return breakdown


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
    run_id: str | None = None,
) -> Payload:
    """The actual search: samples the grid, precomputes the expensive
    (combo-invariant) feature set once per (symbol, timeframe), replays
    every sampled combo against it, evaluates every resulting profile
    out-of-sample, and ranks the survivors. Never picks the highest raw
    P&L -- ranks by the real robustness score, and a profile that fails
    ANY of the spec's own rejection rules is marked rejected and
    excluded from ever being "recommended," regardless of how good its
    raw numbers look. `run_id`, when given, publishes live progress via
    get_optimize_progress() -- see this module's own header for the
    real O(N_combos * n^2) -> O(n^2 + N_combos * n) cost this restructure
    achieves, and DEFAULT_MAX_COMBINATIONS_INTRADAY/
    DEFAULT_MAX_RUNTIME_SEC_INTRADAY for the hard guard applied below."""
    is_intraday = any(_is_intraday_timeframe(tf) for tf in timeframes)
    effective_max_combinations = (
        min(max_combinations, DEFAULT_MAX_COMBINATIONS_INTRADAY)
        if is_intraday
        else max_combinations
    )
    combos, full_grid_size = sample_param_grid(effective_max_combinations, seed)
    started_at = time.monotonic()
    _set_progress(
        run_id,
        phase="loading_history",
        combos_done=0,
        combos_total=len(combos),
        is_intraday=is_intraday,
        elapsed_sec=0.0,
    )

    # Real history fetched ONCE per symbol and reused across every
    # sampled combination and every (symbol, timeframe) precompute.
    history: dict[str, tuple[list[Any], list[Any]]] = {}
    for symbol in symbols:
        history[symbol] = await _load_symbol_history(redis, symbol, start_ts, end_ts)

    # Precompute the expensive, combo-invariant feature set ONCE per
    # (symbol, timeframe) pair -- see this module's own header. Any
    # combo's config would produce identical features here (none of the
    # swept fields affect compute_bar_features()); DEFAULT_CONFIG is used
    # directly so this doesn't even depend on iteration order over combos.
    pairs = [(s, tf) for s in symbols for tf in timeframes]
    precomputed: dict[tuple[str, str], PrecomputedReplay | None] = {}
    _set_progress(run_id, phase="precomputing_features", pairs_done=0, pairs_total=len(pairs))
    for pair_idx, (symbol, timeframe) in enumerate(pairs):
        daily_full, intraday_full = history[symbol]
        precomputed[(symbol, timeframe)] = await precompute_replay_features(
            symbol=symbol,
            timeframe=timeframe,
            daily_full=daily_full,
            intraday_full=intraday_full,
            config=DEFAULT_CONFIG,
        )
        _set_progress(
            run_id,
            pairs_done=pair_idx + 1,
            elapsed_sec=round(time.monotonic() - started_at, 1),
        )

    profiles: list[Payload] = []
    combos_evaluated = 0
    runtime_guard_triggered = False
    _set_progress(run_id, phase="evaluating_combos", combos_done=0)

    for combo_idx, combo in enumerate(combos):
        elapsed = time.monotonic() - started_at
        if is_intraday and elapsed > DEFAULT_MAX_RUNTIME_SEC_INTRADAY:
            runtime_guard_triggered = True
            logger.warning(
                "structure_optimize_runtime_guard_triggered",
                run_id=run_id,
                elapsed_sec=round(elapsed, 1),
                combos_evaluated=combos_evaluated,
                combos_sampled=len(combos),
            )
            break

        config = combo.to_config()
        trades: list[SimulatedTrade] = []
        diagnostics: list[ReplayDiagnostics] = []
        for symbol, timeframe in pairs:
            pre = precomputed.get((symbol, timeframe))
            if pre is None:
                continue
            combo_trades, diag = await _replay_with_precomputed(
                pre, side=side, config=config, costs=costs
            )
            trades.extend(combo_trades)
            diagnostics.append(diag)

        profile = _evaluate_combo(
            combo,
            trades,
            requested_symbols=symbols,
            requested_timeframes=timeframes,
            train_pct=train_pct,
            embargo_sec=embargo_sec,
        )
        profile["trigger_diagnostics"] = ReplayDiagnostics.merge(diagnostics)
        profiles.append(profile)
        combos_evaluated += 1

        if combo_idx % 5 == 0:
            await asyncio.sleep(0)  # same responsiveness fix as the replay loop itself
        _set_progress(
            run_id,
            combos_done=combos_evaluated,
            elapsed_sec=round(time.monotonic() - started_at, 1),
        )

    survivors = [p for p in profiles if not p["rejected"]]
    survivors.sort(key=lambda p: p["robustness_score"], reverse=True)
    recommended = survivors[0] if survivors else None
    for i, p in enumerate(survivors):
        p["rank"] = i + 1

    dsr = _compute_dsr(profiles, recommended)
    total_elapsed = round(time.monotonic() - started_at, 1)

    note = (
        f"Recommended profile ranked #1 of {len(survivors)} candidate(s) that survived every "
        "rejection rule (too-few-trades, single-symbol, single-timeframe, overfit gap, "
        "negative out-of-sample expectancy)."
        if recommended
        else (
            f"No profile among the {len(profiles)} tested survived the rejection rules -- "
            "none of them is a statistically stronger setup candidate yet. Do not treat any "
            "candidate below as safe to use; collect more real history or widen the search."
        )
    )
    if runtime_guard_triggered:
        note += (
            f" Runtime guard stopped this run early after {total_elapsed}s "
            f"({combos_evaluated} of {len(combos)} sampled combos evaluated) -- an intraday "
            "run's own bar count makes the full sample too slow to finish inline; results "
            "above reflect only the combos actually evaluated."
        )

    _set_progress(
        run_id,
        phase="DONE",
        combos_done=combos_evaluated,
        elapsed_sec=total_elapsed,
        runtime_guard_triggered=runtime_guard_triggered,
    )

    return {
        "available": True,
        "full_grid_size": full_grid_size,
        "sampled_combinations": len(combos),
        "combos_evaluated": combos_evaluated,
        "feature_precompute_pairs": len(pairs),
        "symbols": symbols,
        "timeframes": timeframes,
        "side": side,
        "train_pct": train_pct,
        "embargo_sec": embargo_sec,
        "recommended": recommended,
        "candidates": sorted(profiles, key=lambda p: p["robustness_score"], reverse=True)[:20],
        "rejected_count": sum(1 for p in profiles if p["rejected"]),
        "survivor_count": len(survivors),
        "trigger_source_breakdown": _trigger_source_breakdown(profiles),
        "dsr": dsr,
        "runtime": {
            "elapsed_sec": total_elapsed,
            "is_intraday": is_intraday,
            "max_combinations_applied": effective_max_combinations,
            "max_runtime_sec_guard": (DEFAULT_MAX_RUNTIME_SEC_INTRADAY if is_intraday else None),
            "runtime_guard_triggered": runtime_guard_triggered,
        },
        "note": note,
        "disclaimer": DISCLAIMER,
    }


async def persist_optimized_profiles(pg_pool: Any, run_id: str, result: Payload) -> None:
    """Stores every candidate profile (not just the recommended one) so
    a run's own optimizer output can be audited later -- rank is set
    only for real survivors, per structure_optimized_profiles' own
    schema comment. Also persists the run-LEVEL summary (grid size,
    trigger-source breakdown, runtime guard info, DSR) into
    structure_optimize_runs_meta -- real gap found this same review: the
    ORIGINAL Phase 4 get_cached_optimize_result() already dropped these
    on a cache-hit read, so a page revisit after the first run would
    silently lose them; fixed here rather than repeated for the new
    Task 2/4 fields (see migrations/014_structure_optimize_diagnostics.sql)."""
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
                json.dumps(p.get("trigger_diagnostics"), default=str),
            )
        )
    async with pg_pool.acquire() as conn:
        if rows:
            await conn.executemany(
                """
                INSERT INTO structure_optimized_profiles
                    (id, run_id, params, train_metrics, test_metrics, consistency_symbols,
                     consistency_timeframes, overfit_gap_r, robustness_score, confidence,
                     rejected, rejection_reasons, rank, trigger_diagnostics)
                VALUES ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb)
                """,
                rows,
            )
        await conn.execute(
            """
            INSERT INTO structure_optimize_runs_meta
                (run_id, full_grid_size, sampled_combinations, combos_evaluated,
                 feature_precompute_pairs, trigger_source_breakdown, runtime, dsr)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                full_grid_size = EXCLUDED.full_grid_size,
                sampled_combinations = EXCLUDED.sampled_combinations,
                combos_evaluated = EXCLUDED.combos_evaluated,
                feature_precompute_pairs = EXCLUDED.feature_precompute_pairs,
                trigger_source_breakdown = EXCLUDED.trigger_source_breakdown,
                runtime = EXCLUDED.runtime,
                dsr = EXCLUDED.dsr,
                created_at = now()
            """,
            run_id,
            result.get("full_grid_size"),
            result.get("sampled_combinations"),
            result.get("combos_evaluated"),
            result.get("feature_precompute_pairs"),
            json.dumps(result.get("trigger_source_breakdown"), default=str),
            json.dumps(result.get("runtime"), default=str),
            json.dumps(result.get("dsr"), default=str),
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
                   rejected, rejection_reasons, rank, created_at, trigger_diagnostics
            FROM structure_optimized_profiles
            WHERE run_id = $1
            ORDER BY robustness_score DESC
            """,
            run_id,
        )
        meta = await conn.fetchrow(
            """
            SELECT full_grid_size, sampled_combinations, combos_evaluated,
                   feature_precompute_pairs, trigger_source_breakdown, runtime, dsr
            FROM structure_optimize_runs_meta
            WHERE run_id = $1
            """,
            run_id,
        )
    if not rows:
        return None

    candidates: list[Payload] = []
    for r in rows:
        d = dict(r)
        for key in ("params", "train_metrics", "test_metrics", "trigger_diagnostics"):
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
    meta_dict = dict(meta) if meta else {}
    for key in ("trigger_source_breakdown", "runtime", "dsr"):
        if isinstance(meta_dict.get(key), str):
            meta_dict[key] = json.loads(meta_dict[key])
    return {
        "full_grid_size": meta_dict.get("full_grid_size"),
        "sampled_combinations": meta_dict.get("sampled_combinations"),
        "combos_evaluated": meta_dict.get("combos_evaluated"),
        "feature_precompute_pairs": meta_dict.get("feature_precompute_pairs"),
        "trigger_source_breakdown": meta_dict.get("trigger_source_breakdown"),
        "runtime": meta_dict.get("runtime"),
        "dsr": meta_dict.get("dsr"),
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
