"""Historical multi-timeframe confidence engine.

This route converts cached Upstox OHLC candles into the MTF dots and confidence
story used by the options dashboard. It intentionally separates true
candle-based evidence from the older live-feature proxy logic.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time
from typing import Any, cast
from zoneinfo import ZoneInfo

from aiohttp import web

from api.anchored_vwap import compute_anchored_vwaps
from api.chart_patterns import detect_chart_patterns, fractal_pivots_indexed
from api.daily_trend_filter import compute_daily_trend_filter
from api.relative_strength import compute_multi_timeframe_rs
from api.vcp import compute_vcp
from api.wyckoff import detect_shortening_of_thrust, detect_sos_sow_bar, detect_structural_failure

routes = web.RouteTableDef()
Payload = dict[str, Any]
Bar = dict[str, Any]

# EBIE-KNOWN-GAPS.md §1.7 -- the main infusion:mtf:{symbol} cache below is
# only ever warm for a rolling ~50/208-symbol subset (mtf_queue.py's own
# priority-limited sweep), with a 300s TTL. Once a symbol drops out of that
# rotation, its cache entry simply expires and disappears -- there is then
# no way to tell "this symbol's relative-strength evidence has never been
# computed" apart from "it WAS computed recently but the short-TTL cache
# already expired." This separate, much-longer-lived marker (no evidence
# payload, just a timestamp) answers exactly that question for
# api/routes/ebie_candidates.py's cache_freshness enrichment, without
# changing the main cache's own short TTL (still correct for its own
# "is this reading fresh enough to score" purpose).
MTF_LAST_SEEN_PREFIX = "infusion:mtf-last-seen:"
MTF_LAST_SEEN_TTL_SEC = 7 * 24 * 3600

_IST = ZoneInfo("Asia/Kolkata")
_SESSION_OPEN = dt_time(9, 15)
_SESSION_CLOSE = dt_time(15, 30)

TIMEFRAMES = {
    "1M": ("intraday", 1),
    "5M": ("intraday", 5),
    "15M": ("intraday", 15),
    "1H": ("intraday", 60),
    "4H": ("intraday", 240),
    "1D": ("daily", 1),
}


@dataclass
class IndicatorPack:
    close: float
    ema20: float | None
    ema50: float | None
    ema200: float | None
    rsi14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    vwap: float | None
    atr: float | None
    supertrend: str
    candle: str


def _decode_ohlc(members: list[object]) -> list[Bar]:
    bars: list[Bar] = []
    for member in members:
        if isinstance(member, bytes):
            val = member.decode()
        elif isinstance(member, str):
            val = member
        else:
            continue
        try:
            raw = json.loads(val)
            ts = int(raw.get("t") or raw.get("time") or 0)
            if not ts:
                continue
            bars.append(
                {
                    "time": ts,
                    "open": float(raw.get("o", raw.get("open", 0))),
                    "high": float(raw.get("h", raw.get("high", 0))),
                    "low": float(raw.get("l", raw.get("low", 0))),
                    "close": float(raw.get("c", raw.get("close", 0))),
                    "volume": int(float(raw.get("v", raw.get("volume", 0)) or 0)),
                }
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return bars


def _merge_bars(*groups: list[Bar]) -> list[Bar]:
    merged: dict[int, Bar] = {}
    for group in groups:
        for bar in group:
            if bar.get("time"):
                merged[int(bar["time"])] = bar
    return [merged[key] for key in sorted(merged)]


def _aggregate(bars: list[Bar], minutes: int) -> list[Bar]:
    if minutes <= 1:
        return bars
    buckets: dict[int, Bar] = {}
    width = minutes * 60
    for bar in bars:
        bucket = int(bar["time"]) // width * width
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = {**bar, "time": bucket}
        else:
            current["high"] = max(current["high"], bar["high"])
            current["low"] = min(current["low"], bar["low"])
            current["close"] = bar["close"]
            current["volume"] += int(bar.get("volume") or 0)
    return [buckets[key] for key in sorted(buckets)]


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    ema = values[0]
    out = [ema]
    for value in values[1:]:
        ema = value * alpha + ema * (1 - alpha)
        out.append(ema)
    return out


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:], strict=False):
        change = cur - prev
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if len(values) < 26:
        return None, None, None
    ema12 = _ema_series(values, 12)
    ema26 = _ema_series(values, 26)
    line = [a - b for a, b in zip(ema12, ema26, strict=False)]
    if len(line) < 9:
        return line[-1], None, None
    signal = _ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def _atr(bars: list[Bar], period: int = 14) -> float | None:
    if len(bars) <= period:
        return None
    trs: list[float] = []
    for idx in range(1, len(bars)):
        high = float(bars[idx]["high"])
        low = float(bars[idx]["low"])
        prev_close = float(bars[idx - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    recent = trs[-period:]
    return sum(recent) / period if recent else None


def _vwap(bars: list[Bar]) -> float | None:
    total_volume = sum(max(int(b.get("volume") or 0), 0) for b in bars)
    if total_volume <= 0:
        return None
    pv = 0.0
    for bar in bars:
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        pv += typical * max(int(bar.get("volume") or 0), 0)
    return pv / total_volume


def _candle_pattern(bars: list[Bar]) -> str:
    if not bars:
        return "NA"
    cur = bars[-1]
    o, h, low, c = cur["open"], cur["high"], cur["low"], cur["close"]
    body = abs(c - o)
    rng = max(h - low, 0.01)
    upper = h - max(o, c)
    lower = min(o, c) - low
    if len(bars) >= 2:
        prev = bars[-2]
        po, pc = prev["open"], prev["close"]
        if c > o and pc < po and c >= po and o <= pc:
            return "Bullish Engulfing"
        if c < o and pc > po and c <= po and o >= pc:
            return "Bearish Engulfing"
        if h <= prev["high"] and low >= prev["low"]:
            return "Inside Bar"
    if body <= rng * 0.12:
        return "Doji"
    if lower >= body * 2.0 and upper <= body * 0.8:
        return "Hammer"
    if upper >= body * 2.0 and lower <= body * 0.8:
        return "Shooting Star"
    return "Bull Candle" if c > o else "Bear Candle" if c < o else "Flat Candle"


def _supertrend_state(bars: list[Bar], period: int = 10, multiplier: float = 3.0) -> str:
    """Compact Supertrend-style state using the latest ATR band.

    The full TradingView implementation is iterative. For scanner ranking, this
    approximation is deliberately stable: close above mid+ATR band is bullish,
    close below mid-ATR band is bearish, inside the band is mixed.
    """
    if len(bars) <= period:
        return "MIXED"
    atr = _atr(bars, period)
    if not atr:
        return "MIXED"
    cur = bars[-1]
    mid = (cur["high"] + cur["low"]) / 2
    close = cur["close"]
    if close > mid + atr * (multiplier * 0.35):
        return "BULL"
    if close < mid - atr * (multiplier * 0.35):
        return "BEAR"
    prev_close = bars[-2]["close"] if len(bars) >= 2 else close
    if close > prev_close and close > mid:
        return "BULL"
    if close < prev_close and close < mid:
        return "BEAR"
    return "MIXED"


def _indicators(bars: list[Bar], include_vwap: bool) -> IndicatorPack | None:
    if not bars:
        return None
    closes = [float(b["close"]) for b in bars]
    ema20 = _ema_series(closes, 20)[-1] if len(closes) >= 5 else None
    ema50 = _ema_series(closes, 50)[-1] if len(closes) >= 8 else None
    ema200 = _ema_series(closes, 200)[-1] if len(closes) >= 30 else None
    macd, macd_signal, macd_hist = _macd(closes)
    return IndicatorPack(
        close=closes[-1],
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi14=_rsi(closes, 14),
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        vwap=_vwap(bars[-120:]) if include_vwap else None,
        atr=_atr(bars, 14),
        supertrend=_supertrend_state(bars),
        candle=_candle_pattern(bars),
    )


def _fractal_pivots(
    bars: list[Bar], left: int = 2, right: int = 2
) -> tuple[list[float], list[float]]:
    """Confirmed swing pivot highs/lows over a bar series — the same
    left/right=2 fractal rule as
    simple_structure_pivot_ma_plan_v6.pine's ta.pivothigh/pivotlow and
    feature_engine/features/structure.py's live incremental version. This
    variant scans a full historical array at once (batch, not incremental).
    """
    highs: list[float] = []
    lows: list[float] = []
    window = left + right + 1
    if len(bars) < window:
        return highs, lows
    for i in range(left, len(bars) - right):
        segment = bars[i - left : i + right + 1]
        cand_high = bars[i]["high"]
        cand_low = bars[i]["low"]
        seg_highs = [b["high"] for b in segment]
        seg_lows = [b["low"] for b in segment]
        if cand_high == max(seg_highs) and seg_highs.count(cand_high) == 1:
            highs.append(cand_high)
        if cand_low == min(seg_lows) and seg_lows.count(cand_low) == 1:
            lows.append(cand_low)
    return highs, lows


def _classic_pivots(prev_high: float, prev_low: float, prev_close: float) -> Payload:
    """Standard floor-trader pivot points (John Person / Vikram Prabhu),
    computed from the prior COMPLETE trading day's H/L/C. This is distinct
    from ticks.py's "fibo_pivot" proxy, which uses the CURRENT session's
    still-forming day_high/day_low mid-session -- that's a live-tick
    approximation, not the classic day-ahead pivot Prabhu's methodology and
    the wider Indian intraday-trading convention are actually built on.

    CPR (Central Pivot Range) is the 3-line P/BCPR/TCPR variant his
    intraday setups use as the day's primary bias line and S/R width read.
    """
    if prev_high <= 0 or prev_low <= 0 or prev_close <= 0 or prev_high < prev_low:
        return {}
    pivot = (prev_high + prev_low + prev_close) / 3.0
    rng = prev_high - prev_low
    r1 = 2 * pivot - prev_low
    s1 = 2 * pivot - prev_high
    r2 = pivot + rng
    s2 = pivot - rng
    r3 = prev_high + 2 * (pivot - prev_low)
    s3 = prev_low - 2 * (prev_high - pivot)
    bcpr = (prev_high + prev_low) / 2.0
    tcpr = (pivot - bcpr) + pivot
    cpr_top = max(bcpr, tcpr)
    cpr_bottom = min(bcpr, tcpr)
    cpr_width_pct = (cpr_top - cpr_bottom) / pivot * 100.0 if pivot else 0.0

    # Narrow/wide cutoffs are Infusion's own calibration, not from the
    # source: Prabhu states the *effect* (narrow CPR -> higher odds of a
    # trend day; wide CPR -> range/mean-revert day, stronger S/R) without
    # giving exact numeric thresholds.
    if cpr_width_pct < 0.15:
        day_type = "narrow_trend_bias"
    elif cpr_width_pct > 0.50:
        day_type = "wide_range_bias"
    else:
        day_type = "neutral"

    return {
        "pivot": round(pivot, 2),
        "r1": round(r1, 2),
        "r2": round(r2, 2),
        "r3": round(r3, 2),
        "s1": round(s1, 2),
        "s2": round(s2, 2),
        "s3": round(s3, 2),
        "cpr_top": round(cpr_top, 2),
        "cpr_bottom": round(cpr_bottom, 2),
        "cpr_width_pct": round(cpr_width_pct, 3),
        "day_type": day_type,
        "prev_high": round(prev_high, 2),
        "prev_low": round(prev_low, 2),
        "prev_close": round(prev_close, 2),
    }


def _pivot_bias(ltp: float, pivots: Payload) -> str:
    """Price > CPR pivot -> bullish day bias; < -> bearish (Prabhu)."""
    pivot = pivots.get("pivot")
    if not pivot or ltp <= 0:
        return "neutral"
    if ltp > pivot:
        return "bullish"
    if ltp < pivot:
        return "bearish"
    return "neutral"


def _session_time_factor() -> float:
    """Virgin-CPR strength multiplier by time of day. Prabhu's book states
    virgin CPR is strongest near session open and weakens by late afternoon
    without giving an exact curve -- this is Infusion's own linear
    calibration (1.0 at 9:15 open, 0.5 by 15:30 close; 1.0 outside market
    hours where "time of day" doesn't meaningfully apply)."""
    now = datetime.now(tz=_IST).time()
    if now < _SESSION_OPEN or now > _SESSION_CLOSE:
        return 1.0
    open_s = _SESSION_OPEN.hour * 3600 + _SESSION_OPEN.minute * 60
    close_s = _SESSION_CLOSE.hour * 3600 + _SESSION_CLOSE.minute * 60
    now_s = now.hour * 3600 + now.minute * 60
    frac = (now_s - open_s) / max(close_s - open_s, 1)
    return round(1.0 - 0.5 * max(0.0, min(1.0, frac)), 3)


VIRGIN_CPR_MAX_AGE_DAYS = 5  # Prabhu: valid S/R for ~4-5 trading days after formation
VIRGIN_CPR_LOOKBACK_DAYS = 8  # how many recent days' CPRs are worth checking at all


def _virgin_cpr_zones(daily_bars: list[Bar], current_ltp: float) -> list[Payload]:
    """CPR zones from recent sessions that no candle body (a wick touching
    is not enough -- Prabhu's own definition) has closed back through since
    formation. A virgin zone stays valid S/R for ~4-5 trading days; the
    strength decay below is Infusion's own calibration since the source
    gives the shape (strong at formation, weak by expiry) without an exact
    formula.

    age=1 is TODAY's own CPR -- the same one returned in the `pivots` field
    (both use daily_bars[-1], the most recent complete day, as the prior
    H/L/C). age=2 is the CPR that was active for daily_bars[-1]'s own
    session (formed from daily_bars[-2]), and so on. This keeps the "age=1"
    entry consistent with `pivots` rather than silently one day behind it.
    """
    zones: list[Payload] = []
    n = len(daily_bars)
    if n < 2:
        return zones

    max_age = min(VIRGIN_CPR_MAX_AGE_DAYS, VIRGIN_CPR_LOOKBACK_DAYS, n)
    time_factor = _session_time_factor()
    for age in range(1, max_age + 1):
        prior_idx = n - age
        prior = daily_bars[prior_idx]
        pivots = _classic_pivots(prior["high"], prior["low"], prior["close"])
        if not pivots:
            continue
        top, bottom = pivots["cpr_top"], pivots["cpr_bottom"]

        touched = False
        for j in range(prior_idx + 1, n):
            body_lo = min(daily_bars[j]["open"], daily_bars[j]["close"])
            body_hi = max(daily_bars[j]["open"], daily_bars[j]["close"])
            if body_hi >= bottom and body_lo <= top:
                touched = True
                break
        # Today's still-forming session: price currently inside the zone
        # counts as a touch too, not just a completed day's body.
        if not touched and current_ltp > 0 and bottom <= current_ltp <= top:
            touched = True

        if touched:
            continue

        strength = max(0.0, 100.0 - (age - 1) * 20.0) * time_factor
        zones.append(
            {
                "cpr_top": top,
                "cpr_bottom": bottom,
                "formed_days_ago": age,
                "strength": round(strength, 0),
            }
        )

    zones.sort(key=lambda z: -z["strength"])
    return zones[:3]


CROSS_LOOKBACK_DAYS = (
    10  # "recent" cross window -- Infusion's own definition; sources don't specify one
)


def _sma_series(closes: list[float], period: int) -> list[float | None]:
    """Rolling SMA at each index; None until enough history exists."""
    out: list[float | None] = []
    window_sum = 0.0
    for i, c in enumerate(closes):
        window_sum += c
        if i >= period:
            window_sum -= closes[i - period]
        out.append(window_sum / period if i >= period - 1 else None)
    return out


def _regime_at(sma50: float | None, sma200: float | None) -> str:
    if sma50 is None or sma200 is None:
        return "unknown"
    if sma50 > sma200:
        return "golden_cross"
    if sma50 < sma200:
        return "death_cross"
    return "neutral"


def _ma_regime(daily_bars: list[Bar]) -> Payload:
    """Golden Cross / Death Cross regime + MA-stack alignment from daily
    closes. Three independent sources (Kratter, Moving Average 101, Farley)
    converge on the 50-SMA vs 200-SMA relationship as the primary
    structural bull/bear divider -- "bulls live above the 200-day, bears
    live below" (Farley). MA-stack additionally checks 20/50/200-SMA
    ordering against price for trend-strength confirmation.

    This is informational only -- NOT wired into the live conviction score
    or any suppression gate. Consistent with the self-improving-engine
    principle (new signals get tracked standalone before they're allowed to
    move the blended score): the existing score/precision-guard thresholds
    were calibrated without this input, and folding it in silently would
    shift what "score >= 80" means without re-validation.
    """
    closes = [float(b["close"]) for b in daily_bars if b.get("close")]
    if len(closes) < 20:
        return {
            "regime": "unknown",
            "sma20": None,
            "sma50": None,
            "sma200": None,
            "stack": "unknown",
            "cross_recent": False,
            "warning": "Not enough daily history (need 20+ days, 200+ for a real regime read)",
        }

    sma20_series = _sma_series(closes, 20)
    sma50_series = _sma_series(closes, 50)
    sma200_series = _sma_series(closes, 200)
    sma20, sma50, sma200 = sma20_series[-1], sma50_series[-1], sma200_series[-1]
    ltp = closes[-1]

    regime = _regime_at(sma50, sma200)

    cross_recent = False
    if regime in ("golden_cross", "death_cross"):
        for i in range(2, min(CROSS_LOOKBACK_DAYS, len(closes)) + 1):
            prior_regime = _regime_at(sma50_series[-i], sma200_series[-i])
            if prior_regime == "unknown":
                break
            # A real crossover typically passes through the two SMAs being
            # momentarily equal ("neutral") on the way -- that counts as a
            # differing prior state, not something to skip past. Only
            # "unknown" (insufficient history, handled above) is excluded.
            if prior_regime != regime:
                cross_recent = True
                break

    if sma20 is not None and sma50 is not None and sma200 is not None:
        if ltp > sma20 > sma50 > sma200:
            stack = "strong_bull_stack"
        elif ltp < sma20 < sma50 < sma200:
            stack = "strong_bear_stack"
        elif ltp > sma50 > sma200:
            stack = "bull_stack"
        elif ltp < sma50 < sma200:
            stack = "bear_stack"
        else:
            stack = "mixed"
    else:
        stack = "unknown"

    return {
        "regime": regime,
        "sma20": round(sma20, 2) if sma20 is not None else None,
        "sma50": round(sma50, 2) if sma50 is not None else None,
        "sma200": round(sma200, 2) if sma200 is not None else None,
        "stack": stack,
        "cross_recent": cross_recent,
    }


DONCHIAN_PERIOD = 20  # Donchian's own original 4-week channel convention, not Turtle's later 89-day/13-day variant


def _donchian_channel(daily_bars: list[Bar], period: int = DONCHIAN_PERIOD) -> Payload:
    """N-day high/low channel + fresh-breakout flag (Covel, Trend
    Following, Appendix F — the Turtle system). Deliberately using
    Donchian's original ~4-week (20 trading day) window here rather than
    the Turtle variant's 89-day entry / 13-day exit channels: those were
    calibrated for multi-month futures positions (Covel's cited backtest
    averaged a 305-day hold), a poor match for Infusion's weekly/monthly
    options. This is informational channel data, not a new live-firing
    entry strategy — see the Phase 7 commit for why.
    """
    if len(daily_bars) < period:
        return {
            "period": period,
            "high": None,
            "low": None,
            "fresh_high_breakout": False,
            "fresh_low_breakout": False,
        }

    window = daily_bars[-period:]
    channel_high = max(b["high"] for b in window)
    channel_low = min(b["low"] for b in window)
    latest = daily_bars[-1]

    return {
        "period": period,
        "high": round(channel_high, 2),
        "low": round(channel_low, 2),
        # "Fresh" = today's own bar is what set the extreme -- a breakout
        # happening now, not just price sitting at/above a channel it set
        # days ago.
        "fresh_high_breakout": latest["high"] >= channel_high,
        "fresh_low_breakout": latest["low"] <= channel_low,
    }


WEEK52_TRADING_DAYS = 252  # ~52 calendar weeks of NSE trading sessions
WEEK52_NEAR_PCT = 3.0  # within this % of the 52w extreme counts as "near"


def _week52_stats(daily_bars: list[Bar], ltp: float) -> Payload:
    """52-week high/low distance, from the same cached daily bars every other
    daily-bar feature here already uses (`bootstrap_historical` fetches a
    370-calendar-day window specifically to cover this — see scheduler/
    historical.py). Deliberately labeled "52-week", not "all-time-high": an
    honest ATH claim would need multi-year history Infusion doesn't fetch
    today, so this doesn't pretend to be one.
    """
    if not daily_bars or ltp <= 0:
        return {
            "week52_high": None,
            "week52_low": None,
            "week52_high_distance_pct": None,
            "week52_low_distance_pct": None,
            "week52_near_high": False,
            "week52_near_low": False,
            "week52_bars": 0,
        }

    window = daily_bars[-WEEK52_TRADING_DAYS:]
    high = max(b["high"] for b in window)
    low = min(b["low"] for b in window)
    high_dist = (ltp - high) / high * 100 if high > 0 else None
    low_dist = (ltp - low) / low * 100 if low > 0 else None

    return {
        "week52_high": round(high, 2),
        "week52_low": round(low, 2),
        # Negative = below the level (the common case for high_distance);
        # positive high_distance means price is making a fresh 52w high now.
        "week52_high_distance_pct": round(high_dist, 2) if high_dist is not None else None,
        "week52_low_distance_pct": round(low_dist, 2) if low_dist is not None else None,
        "week52_near_high": high_dist is not None and high_dist >= -WEEK52_NEAR_PCT,
        "week52_near_low": low_dist is not None and low_dist <= WEEK52_NEAR_PCT,
        "week52_bars": len(window),
    }


def _major_blocker(blocker_bars: dict[str, list[Bar]], ltp: float) -> Payload:
    """Nearest opposing swing pivot on the higher timeframes, between price
    and either direction's target — matches Pine's "Major Blocker" concept.

    Since this endpoint is computed per-symbol (not per-signal), it reports
    the nearest pivot HIGH above price (a blocker for a bullish/CE target)
    and nearest pivot LOW below price (a blocker for a bearish/PE target)
    across the configured timeframes; the caller picks whichever matches its
    trade direction.
    """
    best_up: tuple[float, str] | None = None
    best_down: tuple[float, str] | None = None
    for tf, bars in blocker_bars.items():
        highs, lows = _fractal_pivots(bars)
        candidates_up = [h for h in highs if h > ltp]
        candidates_down = [low for low in lows if low < ltp]
        if candidates_up:
            level = min(candidates_up)  # nearest above price
            if best_up is None or level < best_up[0]:
                best_up = (level, tf)
        if candidates_down:
            level = max(candidates_down)  # nearest below price
            if best_down is None or level > best_down[0]:
                best_down = (level, tf)
    return {
        "blocker_up_level": round(best_up[0], 2) if best_up else None,
        "blocker_up_source": best_up[1] if best_up else None,
        "blocker_down_level": round(best_down[0], 2) if best_down else None,
        "blocker_down_source": best_down[1] if best_down else None,
    }


def _state_from_score(score: float) -> str:
    if score >= 58:
        return "BULL"
    if score <= 42:
        return "BEAR"
    return "MIXED"


def _dot(state: str) -> str:
    return {"BULL": "G", "BEAR": "R", "MIXED": "Y"}.get(state, "Y")


def _score_timeframe(tf: str, bars: list[Bar], include_vwap: bool) -> Payload:
    needed = 40 if tf in {"1M", "5M", "15M"} else 30 if tf in {"1H", "4H"} else 50
    pack = _indicators(bars, include_vwap)
    if not pack:
        return {
            "state": "MIXED",
            "dot": "Y",
            "score": 50,
            "bars": 0,
            "quality": "missing",
            "reasons": ["No historical candles cached"],
            "warnings": ["History missing"],
        }

    score = 50.0
    reasons: list[str] = []
    warnings: list[str] = []

    if pack.ema20:
        if pack.close > pack.ema20:
            score += 12
            reasons.append("Close above EMA20")
        else:
            score -= 12
            reasons.append("Close below EMA20")
    else:
        warnings.append("EMA20 warming")

    if pack.ema20 and pack.ema50:
        if pack.ema20 > pack.ema50:
            score += 10
            reasons.append("EMA20 above EMA50")
        else:
            score -= 10
            reasons.append("EMA20 below EMA50")
    elif tf in {"1H", "4H", "1D"}:
        warnings.append("EMA50 limited")

    if pack.ema200 and pack.close > pack.ema200:
        score += 6
        reasons.append("Above EMA200")
    elif pack.ema200 and pack.close < pack.ema200:
        score -= 6
        reasons.append("Below EMA200")

    if pack.rsi14 is not None:
        if 52 <= pack.rsi14 <= 68:
            score += 10
            reasons.append(f"RSI healthy {pack.rsi14:.1f}")
        elif 32 <= pack.rsi14 <= 48:
            score -= 10
            reasons.append(f"RSI bearish {pack.rsi14:.1f}")
        elif pack.rsi14 > 74:
            score -= 4
            warnings.append(f"RSI chase risk {pack.rsi14:.1f}")
        elif pack.rsi14 < 26:
            score += 4
            warnings.append(f"RSI oversold bounce risk {pack.rsi14:.1f}")
    else:
        warnings.append("RSI warming")

    if pack.macd_hist is not None:
        if pack.macd_hist > 0:
            score += 8
            reasons.append("MACD positive")
        elif pack.macd_hist < 0:
            score -= 8
            reasons.append("MACD negative")
    else:
        warnings.append("MACD warming")

    if pack.supertrend == "BULL":
        score += 10
        reasons.append("Supertrend bullish")
    elif pack.supertrend == "BEAR":
        score -= 10
        reasons.append("Supertrend bearish")

    if include_vwap and pack.vwap:
        if pack.close > pack.vwap:
            score += 7
            reasons.append("Above session VWAP")
        else:
            score -= 7
            reasons.append("Below session VWAP")

    if "Bull" in pack.candle or pack.candle == "Hammer":
        score += 3
        reasons.append(pack.candle)
    elif "Bear" in pack.candle or pack.candle == "Shooting Star":
        score -= 3
        reasons.append(pack.candle)
    elif pack.candle in {"Doji", "Inside Bar"}:
        warnings.append(pack.candle)

    bars_count = len(bars)
    if bars_count < needed:
        warnings.append(f"Only {bars_count}/{needed} bars")

    bounded = round(min(max(score, 0), 100), 1)
    state = _state_from_score(bounded)
    return {
        "state": state,
        "dot": _dot(state),
        "score": bounded,
        "bars": bars_count,
        "quality": "ok" if bars_count >= needed else "limited",
        "close": round(pack.close, 2),
        "ema20": round(pack.ema20, 2) if pack.ema20 is not None else None,
        "ema50": round(pack.ema50, 2) if pack.ema50 is not None else None,
        "rsi14": round(pack.rsi14, 1) if pack.rsi14 is not None else None,
        "macd_hist": round(pack.macd_hist, 4) if pack.macd_hist is not None else None,
        "vwap": round(pack.vwap, 2) if pack.vwap is not None else None,
        "supertrend": pack.supertrend,
        "candle": pack.candle,
        "reasons": reasons[:5],
        "warnings": warnings[:4],
    }


NIFTY50_DAILY_KEY = (
    "infusion:ohlc:NIFTY50:daily"  # same bootstrap path as any equity, see scheduler/historical.py
)


async def _load_bars(redis: Any, symbol: str) -> tuple[list[Bar], list[Bar], list[Bar]]:
    now = int(time.time())
    # Enough 1m bars for recent 4H/1H/15M context without making the endpoint heavy.
    start_intraday = now - (10 * 86400)
    history, live, daily, nifty_daily = await asyncio.gather(
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:history:1m", start_intraday, "+inf"),
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:1m", start_intraday, "+inf"),
        redis.zrange(f"infusion:ohlc:{symbol}:daily", -260, -1),
        # Phase 13.12 (VCP relative strength) -- same daily zset shape as
        # any equity's, read once per call alongside the symbol's own bars
        # rather than a separate round trip.
        redis.zrange(NIFTY50_DAILY_KEY, -260, -1),
    )
    intraday = _merge_bars(_decode_ohlc(history), _decode_ohlc(live))
    daily_bars = _decode_ohlc(daily)
    nifty_bars = _decode_ohlc(nifty_daily)
    return intraday, daily_bars, nifty_bars


async def compute_mtf(redis: Any, symbol: str, store: bool = True) -> Payload:
    symbol = symbol.upper()
    intraday, daily, nifty_daily = await _load_bars(redis, symbol)
    timeframes: dict[str, Payload] = {}
    all_warnings: list[str] = []
    blocker_bars: dict[str, list[Bar]] = {}

    for tf, (kind, minutes) in TIMEFRAMES.items():
        bars = _aggregate(intraday, minutes) if kind == "intraday" else daily
        # Keep last 260 bars to prevent bloated JSON while preserving indicator context.
        recent_bars = bars[-260:]
        scored = _score_timeframe(tf, recent_bars, include_vwap=(kind == "intraday"))
        timeframes[tf] = scored
        all_warnings.extend([f"{tf}: {w}" for w in scored.get("warnings", [])])
        if tf in ("1H", "1D"):
            blocker_bars[tf] = recent_bars

    dots = {tf: row["dot"] for tf, row in timeframes.items()}
    states = {tf: row["state"] for tf, row in timeframes.items()}
    bull_count = sum(1 for state in states.values() if state == "BULL")
    bear_count = sum(1 for state in states.values() if state == "BEAR")
    mixed_count = len(states) - bull_count - bear_count
    fast_bull = sum(1 for tf in ("1M", "5M", "15M") if states.get(tf) == "BULL")
    fast_bear = sum(1 for tf in ("1M", "5M", "15M") if states.get(tf) == "BEAR")
    higher_bull = sum(1 for tf in ("1H", "4H", "1D") if states.get(tf) == "BULL")
    higher_bear = sum(1 for tf in ("1H", "4H", "1D") if states.get(tf) == "BEAR")

    if bull_count >= 5 and fast_bull >= 2:
        alignment = "Strong CE alignment"
        trade_bias = "BUY CE"
    elif bear_count >= 5 and fast_bear >= 2:
        alignment = "Strong PE alignment"
        trade_bias = "BUY PE"
    elif fast_bull >= 2 and higher_bear == 0:
        alignment = "CE focus; wait trigger"
        trade_bias = "BUY CE"
    elif fast_bear >= 2 and higher_bull == 0:
        alignment = "PE focus; wait trigger"
        trade_bias = "BUY PE"
    elif fast_bull >= 2 or fast_bear >= 2:
        alignment = "Fast scalp only"
        trade_bias = "HOLD"
    else:
        alignment = "Mixed alignment"
        trade_bias = "HOLD"

    weighted = (
        timeframes["1M"]["score"] * 0.10
        + timeframes["5M"]["score"] * 0.18
        + timeframes["15M"]["score"] * 0.24
        + timeframes["1H"]["score"] * 0.22
        + timeframes["4H"]["score"] * 0.13
        + timeframes["1D"]["score"] * 0.13
    )
    current_ltp = timeframes["1M"].get("close") or timeframes["1D"].get("close") or 0.0
    blocker = (
        _major_blocker(blocker_bars, current_ltp)
        if current_ltp
        else {
            "blocker_up_level": None,
            "blocker_up_source": None,
            "blocker_down_level": None,
            "blocker_down_source": None,
        }
    )

    # Classic floor pivots + CPR, from the prior complete day's H/L/C
    # (daily[-1]) -- and virgin-CPR zones from the last several sessions.
    pivots = (
        _classic_pivots(daily[-1]["high"], daily[-1]["low"], daily[-1]["close"]) if daily else {}
    )
    pivot_bias = _pivot_bias(current_ltp, pivots) if pivots else "neutral"
    virgin_cpr_zones = _virgin_cpr_zones(daily, current_ltp) if daily else []
    ma_regime = _ma_regime(daily)
    daily_pivots = fractal_pivots_indexed(daily) if daily else []
    chart_patterns = detect_chart_patterns(daily_pivots, current_ltp) if current_ltp else []
    donchian = (
        _donchian_channel(daily)
        if daily
        else {
            "period": DONCHIAN_PERIOD,
            "high": None,
            "low": None,
            "fresh_high_breakout": False,
            "fresh_low_breakout": False,
        }
    )
    wyckoff_structural_failure = detect_structural_failure(daily_pivots)
    wyckoff_sot = detect_shortening_of_thrust(daily_pivots)
    wyckoff_sos_sow = detect_sos_sow_bar(daily) if daily else None
    week52 = _week52_stats(daily, current_ltp)
    vcp = (
        compute_vcp(daily, nifty_daily, current_ltp)
        if daily
        else {"available": False, "score": None, "reason": "No daily bar history"}
    )
    # User asked why a real Chartink daily-trend screener surfaces different
    # names than our live Radar -- answer: different questions (daily trend
    # regime vs. live intraday evidence), see api/daily_trend_filter.py's own
    # header. This gives that comparison a real surface instead of leaving
    # it as two disconnected tools.
    daily_trend = (
        compute_daily_trend_filter(daily)
        if daily
        else {
            "available": False,
            "reason": "No daily bar history",
            "pass": False,
            "pass_count": 0,
            "total": 14,
            "conditions": {},
        }
    )
    # EBIE EB-2: multi-anchor AVWAP -- batch-computed from the same
    # `intraday` 1m bar series already fetched above, no new I/O. See
    # api/anchored_vwap.py's own header for why this is a batch pass over
    # persisted history rather than a new live streaming accumulator.
    anchored_vwaps = (
        compute_anchored_vwaps(intraday, current_ltp)
        if current_ltp
        else {
            "prev_close": None,
            "prev_high": None,
            "prev_low": None,
            "week_open": None,
            "swing_high": None,
            "swing_low": None,
        }
    )
    # EBIE EB-3: multi-timeframe relative strength -- see
    # api/relative_strength.py. Batch-computed from the same daily/
    # nifty_daily bars already fetched above, no new I/O.
    multi_rs = compute_multi_timeframe_rs(daily, nifty_daily)
    quality = "historical" if any(row["bars"] for row in timeframes.values()) else "missing"
    if any(row["quality"] == "limited" for row in timeframes.values()) and quality == "historical":
        quality = "limited"

    rejection_reasons: list[str] = []
    if trade_bias == "BUY CE" and higher_bear:
        rejection_reasons.append("Higher timeframe is resisting CE")
    if trade_bias == "BUY PE" and higher_bull:
        rejection_reasons.append("Higher timeframe is resisting PE")
    if (
        trade_bias == "BUY CE"
        and timeframes["5M"].get("rsi14", 50)
        and timeframes["5M"]["rsi14"] > 74
    ):
        rejection_reasons.append("5M RSI chase risk")
    if (
        trade_bias == "BUY PE"
        and timeframes["5M"].get("rsi14", 50)
        and timeframes["5M"]["rsi14"] < 26
    ):
        rejection_reasons.append("5M RSI oversold chase risk")

    payload = {
        "symbol": symbol,
        "source": quality,
        "engine_version": "mtf-v2.0",
        "updated_at": int(time.time()),
        "timeframes": timeframes,
        "mtf": states,
        "dots": dots,
        "mtf_dots": dots,
        "mtf_text": alignment,
        "alignment": alignment,
        "trade_bias": trade_bias,
        "score": round(weighted, 1) if math.isfinite(weighted) else 50.0,
        **blocker,
        "pivots": pivots,
        "pivot_bias": pivot_bias,
        "virgin_cpr_zones": virgin_cpr_zones,
        "ma_regime": ma_regime,
        "chart_patterns": chart_patterns,
        "donchian": donchian,
        "week52": week52,
        "vcp": vcp,
        "daily_trend": daily_trend,
        "anchored_vwaps": anchored_vwaps,
        "multi_timeframe_rs": multi_rs,
        "wyckoff_structural_failure": wyckoff_structural_failure,
        "wyckoff_sot": wyckoff_sot,
        "wyckoff_sos_sow": wyckoff_sos_sow,
        "bull_count": bull_count,
        "bear_count": bear_count,
        "mixed_count": mixed_count,
        "rejection_reasons": rejection_reasons,
        "warnings": all_warnings[:10],
    }

    if store:
        await redis.setex(f"infusion:mtf:{symbol}", 300, json.dumps(payload, separators=(",", ":")))
        # See MTF_LAST_SEEN_PREFIX's own comment above -- a long-lived
        # "was this symbol ever actually computed" marker, independent of
        # the short-TTL cache's own freshness window.
        await redis.setex(
            f"{MTF_LAST_SEEN_PREFIX}{symbol}", MTF_LAST_SEEN_TTL_SEC, str(int(time.time()))
        )
    return payload


async def _cached_or_compute(redis: Any, symbol: str) -> Payload:
    raw = await redis.get(f"infusion:mtf:{symbol.upper()}")
    if raw:
        try:
            value = raw.decode() if isinstance(raw, bytes) else raw
            payload = cast(Payload, json.loads(value))
            payload["cached"] = True
            return payload
        except (json.JSONDecodeError, TypeError):
            pass
    payload = await compute_mtf(redis, symbol, store=True)
    payload["cached"] = False
    return payload


@routes.get("/api/mtf/refresh")
async def refresh_mtf(request: web.Request) -> web.Response:
    """Warm MTF cache for scanner rows.

    Query params:
      ?limit=50  number of symbols to refresh, capped to 250
    """
    redis = request.app["redis"]
    try:
        limit = min(max(int(request.query.get("limit", "75")), 1), 250)
    except ValueError:
        limit = 75
    all_symbols = await redis.hgetall("infusion:symbols")
    symbols: list[str] = []
    for _, meta_raw in all_symbols.items():
        try:
            import msgpack

            meta = msgpack.unpackb(meta_raw, raw=False) if isinstance(meta_raw, bytes) else meta_raw
            sym = str(meta.get("symbol") or "").upper()
            if sym and meta.get("segment") != "INDEX":
                symbols.append(sym)
        except Exception:
            continue
    refreshed = 0
    errors: list[Payload] = []
    for symbol in symbols[:limit]:
        try:
            await compute_mtf(redis, symbol, store=True)
            refreshed += 1
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:120]})
    return web.json_response(
        {"requested": min(limit, len(symbols)), "refreshed": refreshed, "errors": errors[:10]}
    )


@routes.get("/api/mtf/queue/status")
async def mtf_queue_status(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    raw = await redis.get("infusion:mtf-queue:status")
    if not raw:
        return web.json_response({"enabled": True, "state": "waiting_for_first_cycle"})
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        payload = cast(Payload, json.loads(text))
        return web.json_response(payload)
    except Exception:
        return web.json_response({"enabled": True, "error": "status_decode_failed"})


@routes.get("/api/mtf/{symbol}")
async def get_symbol_mtf(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    symbol = request.match_info["symbol"].upper()
    payload = await _cached_or_compute(redis, symbol)
    return web.json_response(payload)
