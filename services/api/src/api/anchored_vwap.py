"""Multi-anchor VWAP — EBIE EB-2.

Session VWAP (feature-engine's own streaming accumulator, reset every
session) already covers the "session open" anchor from docs/EBIE-
BLUEPRINT.md Section 4.4. This module adds the anchors that need
MULTI-DAY bar history — previous-day close/high/low, week-open, and the
latest swing-pivot — which feature-engine's in-process, session-reset
SymbolState can never durably hold (confirmed during EB-0's own
architecture survey: that state lives only in memory and resets on
every restart and every session boundary).

Rather than inventing a new cross-session accumulator, this computes
each anchor as a batch pass over the SAME 10-day merged 1-minute bar
series `api/routes/mtf.py`'s own `_load_bars()` already fetches for
every `compute_mtf()` call (`infusion:ohlc:{symbol}:history:1m` +
`infusion:ohlc:{symbol}:1m`, merged). No new I/O, no new durable state
anywhere — the same "batch-recompute-from-already-persisted-history"
pattern this codebase already uses for VCP/week52 (`api/vcp.py`,
`_week52_stats()`), applied to AVWAP instead of a live streaming
accumulator.

Per the blueprint's own Section 3.6, base/consolidation-origin and
event/gap-candle anchors are explicitly phase-dependent ("add with
EB-7", "add when gap classifier is stable") — deliberately not built
here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
Bar = dict[str, Any]
AnchorResult = dict[str, Any]

# ~2 trading sessions of 1m bars — enough window for a "latest significant
# swing" to be genuinely recent, not an anchor from a week ago.
SWING_LOOKBACK_BARS = 800
SWING_FRACTAL_WIDTH = 5


def _session_date_ist(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).astimezone(IST).date().isoformat()


def _anchored_vwap(bars: list[Bar], anchor_ts: int, current_price: float) -> AnchorResult | None:
    """Volume-weighted average close from anchor_ts (inclusive) to the
    latest bar. None if no bars fall on/after the anchor -- never a
    fabricated 0.0 for "we don't actually have this anchor's history"."""
    num = 0.0
    den = 0
    for bar in bars:
        if int(bar.get("time") or 0) < anchor_ts:
            continue
        v = int(bar.get("volume") or 0)
        c = float(bar.get("close") or 0.0)
        if v <= 0 or c <= 0:
            continue
        num += c * v
        den += v
    if den <= 0:
        return None
    vwap = num / den
    return {
        "vwap": round(vwap, 2),
        "above": current_price > vwap,
        "distance_pct": round((current_price - vwap) / vwap * 100, 3) if vwap > 0 else None,
    }


def _latest_swing_anchors(
    bars: list[Bar], current_price: float
) -> tuple[AnchorResult | None, AnchorResult | None]:
    """Simple fractal pivot (N bars either side) over the most recent
    ~2 sessions, searched newest-first so "latest" really means latest.
    This is deliberately independent of feature-engine's own in-memory
    swing-pivot tracker (state.py's swing_points) -- that state lives in
    a different process and isn't reachable from `api`, so this
    recomputes pivots directly from the same persisted bar history
    everything else here already uses, rather than adding a new
    cross-service dependency for one anchor."""
    window = bars[-SWING_LOOKBACK_BARS:]
    n = len(window)
    w = SWING_FRACTAL_WIDTH
    if n < (2 * w + 1):
        return None, None

    swing_high_ts: int | None = None
    swing_low_ts: int | None = None
    for i in range(n - w - 1, w - 1, -1):
        bar = window[i]
        high = float(bar.get("high") or 0.0)
        low = float(bar.get("low") or 0.0)
        left = window[i - w : i]
        right = window[i + 1 : i + 1 + w]
        if (
            swing_high_ts is None
            and high > 0
            and all(float(b.get("high") or 0.0) <= high for b in left)
            and all(float(b.get("high") or 0.0) <= high for b in right)
        ):
            swing_high_ts = int(bar.get("time") or 0)
        if (
            swing_low_ts is None
            and low > 0
            and all(float(b.get("low") or float("inf")) >= low for b in left)
            and all(float(b.get("low") or float("inf")) >= low for b in right)
        ):
            swing_low_ts = int(bar.get("time") or 0)
        if swing_high_ts is not None and swing_low_ts is not None:
            break

    swing_high = _anchored_vwap(bars, swing_high_ts, current_price) if swing_high_ts else None
    swing_low = _anchored_vwap(bars, swing_low_ts, current_price) if swing_low_ts else None
    return swing_high, swing_low


def compute_anchored_vwaps(bars: list[Bar], current_price: float) -> dict[str, AnchorResult | None]:
    """bars: merged multi-day 1m intraday bars (oldest-first, the exact
    shape `api/routes/mtf.py`'s `_load_bars()` already produces -- keys
    time/open/high/low/close/volume). Returns one sub-dict per anchor,
    or None for an anchor that couldn't be resolved (e.g. a genuinely
    fresh symbol with no prior-day history cached yet) -- never a
    fabricated number standing in for missing history.
    """
    empty: dict[str, AnchorResult | None] = {
        "prev_close": None,
        "prev_high": None,
        "prev_low": None,
        "week_open": None,
        "swing_high": None,
        "swing_low": None,
    }
    if not bars or current_price <= 0:
        return empty

    sessions: dict[str, list[Bar]] = {}
    for bar in bars:
        ts = int(bar.get("time") or 0)
        if ts <= 0:
            continue
        sessions.setdefault(_session_date_ist(ts), []).append(bar)
    if not sessions:
        return empty

    session_dates = sorted(sessions.keys())
    today = session_dates[-1]
    prev_sessions = [d for d in session_dates if d != today]

    result: dict[str, AnchorResult | None] = dict(empty)
    if prev_sessions:
        prev_bars = sessions[prev_sessions[-1]]
        prev_close_ts = int(prev_bars[-1].get("time") or 0)
        result["prev_close"] = _anchored_vwap(bars, prev_close_ts, current_price)

        high_bar = max(prev_bars, key=lambda b: float(b.get("high") or 0.0))
        result["prev_high"] = _anchored_vwap(bars, int(high_bar.get("time") or 0), current_price)
        low_bar = min(prev_bars, key=lambda b: float(b.get("low") or float("inf")))
        result["prev_low"] = _anchored_vwap(bars, int(low_bar.get("time") or 0), current_price)

    latest_dt = datetime.fromisoformat(today)
    week_start_date = (latest_dt - timedelta(days=latest_dt.weekday())).date().isoformat()
    week_sessions = [d for d in session_dates if d >= week_start_date]
    if week_sessions:
        week_open_bar = sessions[week_sessions[0]][0]
        result["week_open"] = _anchored_vwap(
            bars, int(week_open_bar.get("time") or 0), current_price
        )

    result["swing_high"], result["swing_low"] = _latest_swing_anchors(bars, current_price)

    return result
