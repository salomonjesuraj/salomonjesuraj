"""Unified TradeBlueprint — bundles every quantitative read this session's
mathematical audit covered (entry/SL/targets, retest state, accumulation
base, Volume Profile POC/VAH/VAL, OI buildup, OI walls) into one contract,
per the audit follow-up's own Task 4 spec.

This is a presentation-layer bundle, not a new scoring/decision engine --
every field is read from an existing, already-computed source (scanner
strategies, feature-engine's retest/volume-profile modules, futures_queue's
OI buildup cache, market.py's option-chain OI reads). Nothing here
recomputes or overrides those sources; api/routes/trade_blueprint.py's own
docstring lists exactly which field comes from where.

`trade_horizon` (Sniper HUD Phase 1, 2026-08-26) is the one exception to
"pure passthrough" above -- it's a genuine new classification, derived
from a combination of the fields already in this model plus a few extra
reads (MTF timeframe states, a 20-day daily Volume Profile). See
api/trade_blueprint.py's classify_trade_horizon() for the full rule set.

`setup_name`/`direction` describe the underlying strategy candidate this
blueprint bundles context around -- not a new signal generator.
"""

from __future__ import annotations

from pydantic import BaseModel


class TradeBlueprint(BaseModel):
    symbol: str
    direction: str  # "BULL" | "BEAR"
    setup_name: str

    entry_price: float
    invalidation_sl: float
    target_1_fib: float
    target_2_fib: float
    target_3_fib: float
    target_method: str  # "fibonacci_confluence" | "atr_practical" | "unavailable"

    retest_status: str  # "NO_BREAKOUT" | "PENDING_RETEST" | "RETEST_HELD" | "RETEST_FAILED"
    retest_level: float | None = None

    accumulation_base: bool = False
    poc_level: float | None = None
    vah_level: float | None = None
    val_level: float | None = None

    oi_buildup: str = "NEUTRAL"  # OIBuildupType value
    # See this module's own docstring / api/routes/trade_blueprint.py for
    # the exact definitions used: attraction = Max Pain strike (price is
    # statistically drawn toward it into expiry); hurdle = the OI wall
    # sitting in the way of THIS blueprint's own trade direction (call
    # wall for BULL, put wall for BEAR).
    oi_attraction_strike: float | None = None
    oi_hurdle_strike: float | None = None

    # "Sniper HUD" Phase 1 -- TradeHorizon value. See
    # api/trade_blueprint.py's classify_trade_horizon() for the rule
    # set; UNCLASSIFIED (not a fifth silently-wrong guess) whenever
    # there's no active signal or the evidence doesn't cleanly clear
    # one horizon's specific bar.
    trade_horizon: str = "UNCLASSIFIED"

    # SMC Inception Conviction Model (2026-08-27) -- the same three
    # reads scanner/scoring.py's compute_conviction() scores against,
    # surfaced here so the frontend can show the exact institutional
    # footprint a signal was scored on. ob_fvg_distance_pct is None
    # when there's no unmitigated 1m Order Block/FVG right now (see
    # api/trade_blueprint.py's own note on why this is 1-minute
    # structure, not the 15m/1H originally asked for).
    ob_fvg_distance_pct: float | None = None
    liquidity_sweep: str | None = None  # "sellside" | "buyside" | None
    # Honest substitute for CVD -- real order-BOOK imbalance pressure
    # (book_imbalance vs. its own EMA), not executed-trade volume delta.
    # See scanner/scoring.py's module docstring for why true CVD isn't
    # available from this pipeline's real feed.
    order_flow_divergence: bool = False

    # Honesty fields -- which of the above actually had real data behind
    # them right now, so a dashboard never has to guess why a field is
    # null versus genuinely absent from the response.
    available_fields: list[str] = []
    unavailable_fields: list[str] = []
