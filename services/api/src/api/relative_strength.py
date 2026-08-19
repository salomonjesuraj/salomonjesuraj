"""Multi-timeframe relative strength — EBIE EB-3.

Upgrades the existing single-snapshot RS reads into a genuine
multi-window reading per docs/EBIE-BLUEPRINT.md Section 4.5: "Calculate
stock performance relative to NIFTY... across 5m/15m/1h/1D/5D/20D/60D."

What already existed, checked before writing this (not assumed):
  - R8's sector_relative_strength/index_relative_strength (api/routes/
    ticks.py) -- today's-session-so-far intraday change_pct only, a
    single snapshot, not a window.
  - VCP's own _relative_strength (api/vcp.py) -- a single 63-day window,
    folded directly into VCP's composite score as one weighted component,
    not exposed as a standalone RS reading.

This module is genuinely new ground: multiple daily windows (5D/20D/60D
-- the ones this codebase's own already-fetched daily bar history can
actually support) plus a slope reading (is outperformance accelerating
or fading), computed from the SAME daily bar series api/routes/mtf.py's
_load_bars() already fetches for the symbol and NIFTY50 (the same series
VCP/week52/anchored_vwap already reuse) -- zero new I/O, same "batch-
recompute-from-persisted-history" pattern as the rest of EB-2/EB-3.

The intraday windows (5m/15m/1h) are already covered by R8's own live
sector/index RS (refreshed every /api/ticks poll from real-time
change_pct) -- deliberately not rebuilt here.

Percentile rank (blueprint Section 4.5) needs the whole symbol universe
to rank against, the same shape as rvol_rank/sector_leader -- that's a
second-pass computation over the full /api/ticks response, not something
a single symbol's function can do alone, so it lives in ticks.py itself
rather than here.
"""

from __future__ import annotations

RS_WINDOWS = {"rs_5d": 5, "rs_20d": 20, "rs_60d": 60}
RS_SLOPE_WINDOW = 20
RS_SLOPE_SHIFT_DAYS = 10


def _pct_return(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    base = closes[-1 - lookback]
    if base <= 0:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def compute_multi_timeframe_rs(daily_bars: list[dict], nifty_bars: list[dict]) -> dict:
    """daily_bars/nifty_bars: the same oldest-first daily bar lists
    (keys open/high/low/close/volume) api/routes/mtf.py's _load_bars()
    already produces. Every rs_* value is (stock_return - nifty_return)
    in percentage points over that window; None (never a fabricated 0.0)
    when there isn't enough overlapping history for that window yet.
    """
    result: dict = {key: None for key in RS_WINDOWS}
    result["rs_slope_20d"] = None
    result["rs_available"] = False

    stock_closes = [float(b["close"]) for b in daily_bars if b.get("close")]
    nifty_closes = [float(b["close"]) for b in nifty_bars if b.get("close")]
    if not stock_closes or not nifty_closes:
        return result

    result["rs_available"] = True
    for key, window in RS_WINDOWS.items():
        stock_ret = _pct_return(stock_closes, window)
        nifty_ret = _pct_return(nifty_closes, window)
        result[key] = (
            round(stock_ret - nifty_ret, 2)
            if stock_ret is not None and nifty_ret is not None
            else None
        )

    # RS slope: is the 20D relative-strength read improving or fading?
    # Compares the CURRENT 20D-RS against the same 20D-RS window measured
    # RS_SLOPE_SHIFT_DAYS sessions ago (shifting both series back by that
    # many bars) -- a genuine "accelerating vs. fading" read per the
    # blueprint's own "RS acceleration" ask, not just a repeated snapshot.
    shift = RS_SLOPE_SHIFT_DAYS
    window = RS_SLOPE_WINDOW
    if len(stock_closes) > window + shift and len(nifty_closes) > window + shift:
        cur_stock_ret = _pct_return(stock_closes, window)
        cur_nifty_ret = _pct_return(nifty_closes, window)
        past_stock_ret = _pct_return(stock_closes[:-shift], window)
        past_nifty_ret = _pct_return(nifty_closes[:-shift], window)
        if None not in (cur_stock_ret, cur_nifty_ret, past_stock_ret, past_nifty_ret):
            cur_rs = cur_stock_ret - cur_nifty_ret
            past_rs = past_stock_ret - past_nifty_ret
            result["rs_slope_20d"] = round(cur_rs - past_rs, 2)

    return result
