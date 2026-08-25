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

    # Honesty fields -- which of the above actually had real data behind
    # them right now, so a dashboard never has to guess why a field is
    # null versus genuinely absent from the response.
    available_fields: list[str] = []
    unavailable_fields: list[str] = []
