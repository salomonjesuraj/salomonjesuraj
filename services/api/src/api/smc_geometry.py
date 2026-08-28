"""Institutional Chart Overlay sprint (2026-08-28): real Smart Money
Concepts (SMC) geometry -- BOS/CHOCH events, liquidity sweeps, Order
Blocks, and Fibonacci target zones -- computed in one BATCH pass over a
symbol's historical 1-minute OHLC bars, for the chart overlay's own
`GET /api/chart/smc` route (api/routes/charts.py).

This is not a new SMC algorithm: it mirrors feature-engine's own real,
already-shipped definitions bar-for-bar --
feature_engine.features.structure.update_structure (fractal swing
pivots + BOS/CHOCH) and feature_engine.features.ict.update_ict
(liquidity sweeps, Fair Value Gaps, Order Blocks), in the exact call
order engine.py's own per-bar sequence uses (ATR, then structure, then
ICT -- ICT's liquidity-sweep precondition reads THIS bar's
swing_high_1/swing_low_1, which must already reflect any pivot
structure.py just confirmed on this same bar).

Why a second implementation instead of importing the real one: the
`api` service has no dependency on `feature-engine` (separate
deployable images, no shared import path except libs/ -- see
infusion_models.smc's own docstring for this codebase's established
precedent of accepting cross-service duplication rather than adding a
service-to-service package dependency for one function). More
fundamentally, feature-engine's real functions are INCREMENTAL --
SymbolState only ever keeps the CURRENT trend/swing/OB/FVG state and
the MOST RECENT break event, by design, for a live streaming engine.
A chart overlay needs the FULL HISTORY of every BOS/CHOCH and
liquidity-sweep event to place as markers, which the live hot-state
hash structurally cannot provide -- so this module replays the same
real rules as a single deterministic pass over already-closed bars,
recording every event instead of only the latest.

Kept in sync with feature-engine by convention (matching comment,
matching constants), not by import -- if structure.py's or ict.py's
real rules ever change, this module needs the same edit made twice.
Disclosed here rather than silently risked.
"""

from __future__ import annotations

from typing import Any

Bar = dict[str, Any]
Payload = dict[str, Any]

# Same values as feature_engine.features.structure's own module
# constants -- see that module's docstring for why left=right=2
# (mirrors `simple_structure_pivot_ma_plan_v6.pine`'s
# `ta.pivothigh(left, right)` / `ta.pivotlow(left, right)`).
DEFAULT_LEFT = 2
DEFAULT_RIGHT = 2
BREAK_BUFFER_ATR = 0.10

# Same as feature_engine.features.ict's own FVG_REBALANCE_TOUCHES.
FVG_REBALANCE_TOUCHES = 3

# Same Wilder-smoothed ATR period feature_engine.features.volatility's
# own update_atr() and engine.py's FeatureEngineSettings.atr_period both
# default to.
ATR_PERIOD = 14

# Same golden-ratio convention api/broker_sync.py's own
# FIB_EXTENSION_T2/T3 use for the Position Intelligence Card's T2/T3 --
# duplicated here (not imported) so this module stays a pure,
# dependency-free function; broker_sync.py pulls in real Redis/httpx
# position-tracking machinery this chart-only geometry has no reason to
# depend on. Only the measured swing differs by design: broker_sync.py
# projects from a Donchian channel bound, this module projects from the
# real fractal swing this same pass just confirmed -- the literal
# "current swing" the sprint asked for.
FIB_EXTENSION_T2 = 1.618
FIB_EXTENSION_T3 = 2.618

# A chart-payload/marker-clutter cap, not a real data limit -- only the
# most recent N events of each kind are returned. "UI Cleanup, Symbol
# Sync & SMC Clutter Filtering" sprint (2026-08-28): lowered from 50 --
# a live chart with 3000+ real bars of history genuinely can produce
# that many real BOS/CHOCH/sweep events, and rendering all 50 as
# overlapping markers on one candlestick pane was unreadable, not
# incorrect. 8 keeps only the handful of MOST RECENT structural events
# actually relevant to current price action -- still real, chronological
# events (never re-ranked by a fabricated "importance" score this
# codebase has no real data to back), just fewer of them. Matches this
# codebase's own established "cap for payload size" precedent (e.g.
# api/routes/mtf.py keeping the last 260 bars per timeframe).
MAX_EVENTS = 8


def _trend_text(trend_state: int) -> str:
    """Same labels as feature_engine.features.structure.trend_text."""
    if trend_state == 1:
        return "UPTREND (HH/HL)"
    if trend_state == -1:
        return "DOWNTREND (LH/LL)"
    return "RANGE / UNDEFINED"


def _ob_payload(ob: tuple[float, float, int, bool] | None) -> Payload | None:
    if ob is None:
        return None
    low, high, _bar_idx, validated = ob
    return {"low": low, "high": high, "validated": validated}


def _fvg_payload(fvg: tuple[float, float, int] | None) -> Payload | None:
    if fvg is None:
        return None
    bottom, top, _bar_idx = fvg
    return {"bottom": bottom, "top": top}


def _target_zones(
    trend_state: int, swing_high_1: float | None, swing_low_1: float | None
) -> Payload:
    """1.618 / 2.618 Fibonacci extension of the current confirmed swing
    (swing_low_1 -> swing_high_1 projected further up for an uptrend,
    the mirror down for a downtrend). None/None -- never a fabricated
    number -- when the trend is RANGE (no directional swing to extend
    from) or either swing point isn't confirmed yet."""
    if trend_state == 0 or swing_high_1 is None or swing_low_1 is None:
        return {"t2": None, "t3": None, "direction": None}
    swing_range = swing_high_1 - swing_low_1
    if swing_range <= 0:
        return {"t2": None, "t3": None, "direction": None}
    if trend_state == 1:
        return {
            "t2": round(swing_low_1 + swing_range * FIB_EXTENSION_T2, 2),
            "t3": round(swing_low_1 + swing_range * FIB_EXTENSION_T3, 2),
            "direction": "bullish",
        }
    return {
        "t2": round(swing_high_1 - swing_range * FIB_EXTENSION_T2, 2),
        "t3": round(swing_high_1 - swing_range * FIB_EXTENSION_T3, 2),
        "direction": "bearish",
    }


def compute_smc_geometry(
    bars: list[Bar], *, left: int = DEFAULT_LEFT, right: int = DEFAULT_RIGHT
) -> Payload:
    """Pure function, no I/O -- same design rule
    feature_engine.features.structure's own module docstring states for
    its real incremental counterpart. `bars` must be ascending by time,
    each a dict with open/high/low/close/volume/time (the exact shape
    api/routes/mtf.py's own `_decode_ohlc` already produces from
    infusion:ohlc:{symbol}:*'s real stored bars -- api/routes/charts.py
    passes that same real data in, no synthetic bars accepted here)."""
    window = left + right + 1
    if len(bars) < window:
        return {
            "ready": False,
            "reason": f"Need at least {window} closed bars for pivot confirmation, have {len(bars)}.",
        }

    atr = 0.0
    atr_prev_close = 0.0
    atr_values: list[float] = []

    swing_high_1: float | None = None
    swing_high_2: float | None = None
    swing_low_1: float | None = None
    swing_low_2: float | None = None
    trend_state = 0
    last_break_high: float | None = None
    last_break_low: float | None = None

    fvg_bullish: tuple[float, float, int] | None = None
    fvg_bearish: tuple[float, float, int] | None = None
    fvg_bullish_touches = 0
    fvg_bearish_touches = 0

    order_block_bullish: tuple[float, float, int, bool] | None = None
    order_block_bearish: tuple[float, float, int, bool] | None = None

    bos_choch_events: list[Payload] = []
    liquidity_sweeps: list[Payload] = []

    for j, bar in enumerate(bars):
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        opn = float(bar["open"])
        ts = int(bar["time"])

        # ── ATR (Wilder-smoothed) -- mirrors update_atr() exactly ──
        if atr_prev_close == 0:
            atr_prev_close = close
        else:
            tr = max(high - low, abs(high - atr_prev_close), abs(low - atr_prev_close))
            atr_prev_close = close
            atr_values.append(tr)
            if len(atr_values) >= ATR_PERIOD:
                atr = (
                    sum(atr_values) / len(atr_values)
                    if atr == 0
                    else (atr * (ATR_PERIOD - 1) + tr) / ATR_PERIOD
                )

        # ── Structure: fractal pivot confirmation + BOS/CHOCH ──
        # mirrors update_structure() exactly, replayed as a static
        # window check per bar rather than a sliding deque -- see this
        # module's own docstring for why the two are equivalent.
        if j >= window - 1:
            candidate_idx = j - right
            win = bars[candidate_idx - left : j + 1]
            cand_high = float(bars[candidate_idx]["high"])
            cand_low = float(bars[candidate_idx]["low"])
            highs = [float(b["high"]) for b in win]
            lows = [float(b["low"]) for b in win]

            if cand_high == max(highs) and highs.count(cand_high) == 1:
                swing_high_2 = swing_high_1
                swing_high_1 = cand_high
            if cand_low == min(lows) and lows.count(cand_low) == 1:
                swing_low_2 = swing_low_1
                swing_low_1 = cand_low

            buf = max(atr, 0.0) * BREAK_BUFFER_ATR
            bullish_break = swing_high_1 is not None and close > swing_high_1 + buf
            bearish_break = swing_low_1 is not None and close < swing_low_1 - buf

            if bullish_break and last_break_high != swing_high_1:
                label = "Bullish CHOCH" if trend_state == -1 else "Bullish BOS"
                trend_state = 1
                last_break_high = swing_high_1
                bos_choch_events.append(
                    {"time": ts, "price": swing_high_1, "label": label, "direction": "bullish"}
                )
            elif bearish_break and last_break_low != swing_low_1:
                label = "Bearish CHOCH" if trend_state == 1 else "Bearish BOS"
                trend_state = -1
                last_break_low = swing_low_1
                bos_choch_events.append(
                    {"time": ts, "price": swing_low_1, "label": label, "direction": "bearish"}
                )

        # ── ICT: liquidity sweep / FVG / Order Block -- mirrors
        # update_ict() exactly, run after the structure block above
        # (same real per-bar order engine.py's own docstring specifies:
        # ICT reads THIS bar's just-updated swing_high_1/swing_low_1). ──
        if j >= 2:
            c1 = bars[j - 2]
            c1_high = float(c1["high"])
            c1_low = float(c1["low"])

            sellside_swept = swing_low_1 is not None and low < swing_low_1 and close > swing_low_1
            buyside_swept = (
                swing_high_1 is not None and high > swing_high_1 and close < swing_high_1
            )
            if sellside_swept:
                liquidity_sweeps.append({"time": ts, "side": "sellside", "price": swing_low_1})
            elif buyside_swept:
                liquidity_sweeps.append({"time": ts, "side": "buyside", "price": swing_high_1})

            if c1_high < low:
                fvg_bullish = (c1_high, low, j)
                fvg_bullish_touches = 0
            if c1_low > high:
                fvg_bearish = (high, c1_low, j)
                fvg_bearish_touches = 0

            if fvg_bullish is not None:
                bottom, top, _ = fvg_bullish
                if bottom <= close <= top:
                    fvg_bullish_touches += 1
                    if fvg_bullish_touches >= FVG_REBALANCE_TOUCHES:
                        fvg_bullish = None
                elif close < bottom:
                    fvg_bullish = None

            if fvg_bearish is not None:
                bottom, top, _ = fvg_bearish
                if bottom <= close <= top:
                    fvg_bearish_touches += 1
                    if fvg_bearish_touches >= FVG_REBALANCE_TOUCHES:
                        fvg_bearish = None
                elif close > top:
                    fvg_bearish = None

            is_down_candle = close < opn
            is_up_candle = close > opn

            if (
                sellside_swept
                and is_down_candle
                and (order_block_bullish is None or not order_block_bullish[3])
            ):
                order_block_bullish = (low, high, j, False)
            if (
                buyside_swept
                and is_up_candle
                and (order_block_bearish is None or not order_block_bearish[3])
            ):
                order_block_bearish = (low, high, j, False)

            if order_block_bullish is not None:
                ob_low, ob_high, ob_bar, validated = order_block_bullish
                if not validated:
                    if close > ob_high:
                        order_block_bullish = (ob_low, ob_high, ob_bar, True)
                    elif close < ob_low:
                        order_block_bullish = None
                else:
                    mean_threshold = (ob_low + ob_high) / 2.0
                    if close < mean_threshold:
                        order_block_bullish = None

            if order_block_bearish is not None:
                ob_low, ob_high, ob_bar, validated = order_block_bearish
                if not validated:
                    if close < ob_low:
                        order_block_bearish = (ob_low, ob_high, ob_bar, True)
                    elif close > ob_high:
                        order_block_bearish = None
                else:
                    mean_threshold = (ob_low + ob_high) / 2.0
                    if close > mean_threshold:
                        order_block_bearish = None

    return {
        "ready": True,
        "bar_count": len(bars),
        "trend_state": trend_state,
        "trend_text": _trend_text(trend_state),
        "swing_high_1": swing_high_1,
        "swing_high_2": swing_high_2,
        "swing_low_1": swing_low_1,
        "swing_low_2": swing_low_2,
        "bos_choch_events": bos_choch_events[-MAX_EVENTS:],
        "liquidity_sweeps": liquidity_sweeps[-MAX_EVENTS:],
        "order_block_bullish": _ob_payload(order_block_bullish),
        "order_block_bearish": _ob_payload(order_block_bearish),
        "fvg_bullish": _fvg_payload(fvg_bullish),
        "fvg_bearish": _fvg_payload(fvg_bearish),
        "target_zones": _target_zones(trend_state, swing_high_1, swing_low_1),
    }
