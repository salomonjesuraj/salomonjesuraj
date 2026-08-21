"""ICT / Smart Money concepts — Fair Value Gaps, liquidity sweeps, and
proper Order Blocks. Matches the definitions in "The ICT Bible V1" (Ali
Khan) — the mainstream-ICT source, not the fringe PO3/Goldbach numerology
variant flagged separately in the corpus report as low-confidence.

Scope: this operates on the SAME 1-minute intraday bars as
features/structure.py and features/zones.py, not the daily timeframe
Phase 5's chart patterns needed. Unlike Bulkowski's weeks-long geometric
patterns, ICT concepts are explicitly designed for and commonly applied to
fast intraday charts — there's no timeframe mismatch to correct for here.

Infusion's existing supply/demand zone rule (features/zones.py — a single
candle's body >= 1.8x the prior opposite candle's body) is a real, live
feature but is NOT the same thing as an ICT Order Block, despite looking
similar. It's missing the two things that actually make an Order Block an
Order Block rather than just a big candle:
  1. The liquidity-sweep precondition (the candle must run stops beyond a
     confirmed prior swing, not just be large).
  2. Close-based validation (a return that only WICKS through doesn't
     validate it — needs a full-bar CLOSE beyond the candle's own extreme).
This module adds both; features/zones.py is left untouched.

NOT implemented this pass — deferred, not silently skipped:
  - Breaker Block / Mitigation Block: both require tracking the FULL
    swing-high -> swing-low -> (higher-high-or-not) -> reversal sequence
    as a multi-step state machine, a meaningfully bigger and riskier piece
    of state than the single-candle-plus-sweep Order Block below. Doing
    it properly deserves its own pass rather than a rushed version bolted
    onto this one.
  - Killzone session filter: the source gives only London/NY/FX session
    times with no IST conversion, and fabricating "NSE killzones" without
    a real source would be inventing data, not porting it. Infusion's
    existing precision_guard_sessions mechanism already serves the same
    functional role (a time-of-day gate on signal delivery) using actual
    IST session boundaries — there's no missing mechanism here, just a
    source that doesn't transfer to this market.
  - HTF-support context for Order Block quality (the ICT Bible's step 2,
    "sits at/near an HTF support level"): feature-engine computes per-tick
    from OHLCV alone and has no reach into the MTF blocker/pivot data
    computed at the API layer (api/routes/mtf.py). The two well-defined,
    OHLC-only criteria (sweep + close validation) are implemented here;
    HTF confluence is left as a scoring-layer enhancement, not built here.
"""

from __future__ import annotations

FVG_REBALANCE_TOUCHES = 3  # ICT: 3rd touch/pass-through = fully rebalanced


def update_ict(state) -> None:
    """Advance FVG / liquidity-sweep / Order-Block state by one completed
    bar. Reads state.recent_1m_bars (last 3, for the FVG's 3-candle check)
    and state.swing_high_1 / state.swing_low_1 (the liquidity-sweep
    precondition — already maintained by features/structure.py, run before
    this in the engine's per-bar sequence).
    """
    items = list(state.recent_1m_bars)
    state.last_liquidity_sweep = None
    if len(items) < 3:
        return

    cur = items[-1]

    # ── Liquidity sweep: wick/close beyond a confirmed swing, closing back
    #    inside the range. This is both a standalone event flag and the
    #    precondition an Order Block candidate must satisfy below. ──
    sellside_swept = (
        state.swing_low_1 is not None
        and cur["l"] < state.swing_low_1
        and cur["c"] > state.swing_low_1
    )
    buyside_swept = (
        state.swing_high_1 is not None
        and cur["h"] > state.swing_high_1
        and cur["c"] < state.swing_high_1
    )
    if sellside_swept:
        state.last_liquidity_sweep = "sellside"
    elif buyside_swept:
        state.last_liquidity_sweep = "buyside"

    # ── Fair Value Gap (3-candle imbalance): BISI (bullish) when candle1's
    #    high sits below candle3's low; SIBI (bearish) the mirror. ──
    c1, c3 = items[-3], items[-1]
    if c1["h"] < c3["l"]:
        state.fvg_bullish = (c1["h"], c3["l"], state.completed_1m_bars)
        state.fvg_bullish_touches = 0
    if c1["l"] > c3["h"]:
        state.fvg_bearish = (c3["h"], c1["l"], state.completed_1m_bars)
        state.fvg_bearish_touches = 0

    if state.fvg_bullish is not None:
        bottom, top, _ = state.fvg_bullish
        if bottom <= cur["c"] <= top:
            state.fvg_bullish_touches += 1
            if state.fvg_bullish_touches >= FVG_REBALANCE_TOUCHES:
                state.fvg_bullish = None  # fully rebalanced, per ICT's own rule
        elif cur["c"] < bottom:
            state.fvg_bullish = None  # traded clean through, no longer relevant

    if state.fvg_bearish is not None:
        bottom, top, _ = state.fvg_bearish
        if bottom <= cur["c"] <= top:
            state.fvg_bearish_touches += 1
            if state.fvg_bearish_touches >= FVG_REBALANCE_TOUCHES:
                state.fvg_bearish = None
        elif cur["c"] > top:
            state.fvg_bearish = None

    # ── Order Block: a down-close candle that just swept sellside liquidity
    #    is a bullish OB *candidate* — not yet real until price later
    #    CLOSES above its high (the ICT Bible's validity trigger; a wick
    #    poking through doesn't count). Once validated, a close back below
    #    its 50% mean threshold invalidates it. Mirror for bearish. Only
    #    forms a new candidate if the current one isn't already validated,
    #    so a fresh sweep can't casually overwrite a real, active zone. ──
    is_down_candle = cur["c"] < cur["o"]
    is_up_candle = cur["c"] > cur["o"]

    if (
        sellside_swept
        and is_down_candle
        and (state.order_block_bullish is None or not state.order_block_bullish[3])
    ):
        state.order_block_bullish = (cur["l"], cur["h"], state.completed_1m_bars, False)
    if (
        buyside_swept
        and is_up_candle
        and (state.order_block_bearish is None or not state.order_block_bearish[3])
    ):
        state.order_block_bearish = (cur["l"], cur["h"], state.completed_1m_bars, False)

    if state.order_block_bullish is not None:
        ob_low, ob_high, ob_bar, validated = state.order_block_bullish
        if not validated:
            if cur["c"] > ob_high:
                state.order_block_bullish = (ob_low, ob_high, ob_bar, True)
            elif cur["c"] < ob_low:
                state.order_block_bullish = None  # failed before ever validating
        else:
            mean_threshold = (ob_low + ob_high) / 2.0
            if cur["c"] < mean_threshold:
                state.order_block_bullish = None  # invalidated post-validation

    if state.order_block_bearish is not None:
        ob_low, ob_high, ob_bar, validated = state.order_block_bearish
        if not validated:
            if cur["c"] < ob_low:
                state.order_block_bearish = (ob_low, ob_high, ob_bar, True)
            elif cur["c"] > ob_high:
                state.order_block_bearish = None
        else:
            mean_threshold = (ob_low + ob_high) / 2.0
            if cur["c"] > mean_threshold:
                state.order_block_bearish = None


def ict_snapshot(state) -> dict:
    """Compact dict for FeatureVectorV1.ml_features."""
    fb, fr = state.fvg_bullish, state.fvg_bearish
    obb, obr = state.order_block_bullish, state.order_block_bearish
    return {
        "fvg_bullish_bottom": fb[0] if fb else None,
        "fvg_bullish_top": fb[1] if fb else None,
        "fvg_bullish_ce": round((fb[0] + fb[1]) / 2.0, 4) if fb else None,
        "fvg_bearish_bottom": fr[0] if fr else None,
        "fvg_bearish_top": fr[1] if fr else None,
        "fvg_bearish_ce": round((fr[0] + fr[1]) / 2.0, 4) if fr else None,
        "last_liquidity_sweep": state.last_liquidity_sweep,
        "order_block_bullish_low": obb[0] if obb else None,
        "order_block_bullish_high": obb[1] if obb else None,
        "order_block_bullish_validated": obb[3] if obb else False,
        "order_block_bearish_low": obr[0] if obr else None,
        "order_block_bearish_high": obr[1] if obr else None,
        "order_block_bearish_validated": obr[3] if obr else False,
    }
