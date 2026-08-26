"""Trade horizon taxonomy — classifies an active TradeBlueprint's
expected holding period, per the "Sniper HUD" rebuild's own Phase 1
spec (2026-08-26).

    SCALP     (15m-1H)   -- a fast, momentum-driven move expected to
                            resolve before the next OI wall, typically
                            fueled by short-covering rather than fresh
                            conviction.
    INTRADAY  (same day)  -- a same-day breakout with higher-timeframe
                            support, expected to run out its ATR-sized
                            move within the session.
    BTST      (overnight) -- a late-session breakout closing strong,
                            with enough room to the next OI wall to
                            carry overnight.
    SWING     (2-5 days)  -- a structural move clearing a multi-day
                            value area, backed by sustained OI buildup
                            and daily-timeframe alignment.
    UNCLASSIFIED           -- no active signal, or an active signal
                            whose evidence doesn't cleanly match any of
                            the four horizons above. Never forced into
                            a bucket on partial/ambiguous evidence.

See api/trade_blueprint.py's classify_trade_horizon() for the exact
rule thresholds and, importantly, which inputs are real pipeline
fields versus a disclosed proxy for something this pipeline doesn't
track yet (multi-day OI history, timeframe-tagged breakout origin).
"""

from enum import StrEnum


class TradeHorizon(StrEnum):
    SCALP = "SCALP"
    INTRADAY = "INTRADAY"
    BTST = "BTST"
    SWING = "SWING"
    UNCLASSIFIED = "UNCLASSIFIED"
