"""Daily Trend Filter -- a literal replica of a real, popular Chartink
screener ("FNO STOCKS BULLISH TREND SCANNER (MOVING AVERAGE + ADX + MACD)",
2,240+ saves) the user pointed at, asking why our live Breakout Radar
surfaces different names than that screen does.

Answer, confirmed by reading both side by side: they are not competing --
they answer different questions. Chartink's formula is a DAILY-bar trend-
REGIME filter (14 conditions, hard AND, refreshed once a day) that asks
"has this stock been in a clean uptrend structure lately?". Our Radar
(services/api/src/api/routes/ticks.py's stock_breakout_score/breakout_type)
is a LIVE intraday evidence engine, re-scored every 5s off real-time ticks,
that deliberately suppresses a stock that's *just* been trending quietly
for weeks with nothing fresh happening today (the whole point of the
NO_CHASE tier / anti-chase gate). A stock can pass every single one of
Chartink's 14 conditions and correctly show NO_CHASE on our Radar if it's
already extended -- that's not a bug in either tool.

This module closes the gap: it replicates Chartink's exact 14 conditions
verbatim (same indicators, same periods, same thresholds) so a Daily Trend
badge/filter can sit next to the live Radar, letting a user see both reads
on the same row instead of having to cross-reference two different tools.

Deliberately self-contained (own SMA/EMA/WMA/RSI/MACD/ADX implementations),
same reasoning as api/vcp.py's own header: this belongs next to
api/routes/mtf.py's daily-bar domain (same `infusion:ohlc:{symbol}:daily`
zset _ma_regime/_donchian_channel/_week52_stats/vcp already read) without
importing from mtf.py itself, which already imports compute_vcp from this
package's sibling module -- importing back the other way would be circular.

Informational only -- NOT wired into stock_breakout_score or any
suppression gate. Same governance as every other Phase 13+ field: emitted
into the mtf payload, evaluated later via /api/backtest/feature-ablation
before it's ever allowed to move a live score.
"""

from __future__ import annotations

# The 14 conditions, verbatim from the Chartink screen (each entry's own
# comment is the exact filter text as shown on the screener):
#  1. Daily Ema(close,5)              Greater than  Daily Sma(close,20)
#  2. Daily Wma(close,10)             Greater than  Daily Sma(close,20)
#  3. Daily ADX DI Positive(14)       Greater than  Number 20
#  4. Daily ADX(14)                   Greater than  Number 20
#  5. Daily Volume                    Greater than  Number 100000
#  6. Daily Macd Line(26,12,9)        Greater than  Number 0
#  7. Daily Close                     Greater than  1 day ago Close
#  8. Daily Close                     Greater than  Daily Sma(close,50)
#  9. Daily Close                     Greater than  Number 150
# 10. Daily ADX DI Positive(14)       Greater than  Daily ADX DI Negative(14)
# 11. Daily Rsi(14)                   Greater than  Number 50
# 12. Daily Macd Line(26,12,9)        Greater than  Daily Macd Signal(26,12,9)
# 13. Daily Close                     Greater than  2 days ago Close
# 14. Daily Sma(close,20)             Greater than  Daily Sma(close,40)

MIN_DAILY_BARS = 51  # SMA50 (the longest lookback here) + 1 bar of headroom
ADX_DI_THRESHOLD = 20.0
CLOSE_FLOOR = 150.0
VOLUME_FLOOR = 100_000
SOURCE_LABEL = "Chartink: FNO stocks bullish trend scanner (moving average + ADX + MACD)"


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


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


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return _ema_series(values, period)[-1]


def _wma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    window = values[-period:]
    weights = list(range(1, period + 1))
    return sum(v * w for v, w in zip(window, weights, strict=False)) / sum(weights)


def _rsi_wilder(values: list[float], period: int = 14) -> float | None:
    """Standard Wilder-smoothed RSI -- matches Chartink's own convention
    (feature-engine's live update_rsi/get_rsi is the same math, incremental
    tick-by-tick instead of a batch pass over daily closes)."""
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
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


def _adx_di(bars: list[dict], period: int = 14) -> tuple[float | None, float | None, float | None]:
    """Wilder DI+/DI-/ADX, batch equivalent of feature-engine's incremental
    update_adx/get_adx (services/feature-engine/.../features/momentum.py) --
    same Wilder smoothing, just computed fresh over the full daily-bar
    array instead of maintained tick-by-tick in live state."""
    if len(bars) <= period + 1:
        return None, None, None
    trs: list[float] = []
    plus_dms: list[float] = []
    minus_dms: list[float] = []
    for i in range(1, len(bars)):
        high, low = bars[i]["high"], bars[i]["low"]
        prev_high, prev_low, prev_close = (
            bars[i - 1]["high"],
            bars[i - 1]["low"],
            bars[i - 1]["close"],
        )
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
    if len(trs) < period:
        return None, None, None
    tr_smooth = sum(trs[:period])
    plus_smooth = sum(plus_dms[:period])
    minus_smooth = sum(minus_dms[:period])
    dx_values: list[float] = []
    for i in range(period, len(trs)):
        tr_smooth = tr_smooth - (tr_smooth / period) + trs[i]
        plus_smooth = plus_smooth - (plus_smooth / period) + plus_dms[i]
        minus_smooth = minus_smooth - (minus_smooth / period) + minus_dms[i]
        if tr_smooth <= 0:
            continue
        di_plus_i = 100 * plus_smooth / tr_smooth
        di_minus_i = 100 * minus_smooth / tr_smooth
        denom = di_plus_i + di_minus_i
        dx_values.append(100 * abs(di_plus_i - di_minus_i) / denom if denom > 0 else 0.0)
    if tr_smooth <= 0:
        return None, None, None
    di_plus = 100 * plus_smooth / tr_smooth
    di_minus = 100 * minus_smooth / tr_smooth
    if not dx_values:
        return di_plus, di_minus, None
    adx = sum(dx_values[-period:]) / min(period, len(dx_values))
    return di_plus, di_minus, adx


def compute_daily_trend_filter(daily_bars: list[dict]) -> dict:
    """Pure function: real daily OHLCV bars in, Chartink-replica verdict out.

    Returns pass/fail per condition (not just one collapsed boolean) so a
    row that's close but not quite there is legible, not just "no" --
    same "surface real granularity" discipline as VCP's component
    breakdown and the Feature-IC reliability gate.
    """
    bars = [b for b in daily_bars if b.get("close") and b.get("high") and b.get("low")]
    closes = [float(b["close"]) for b in bars]
    if len(closes) < MIN_DAILY_BARS:
        return {
            "available": False,
            "reason": f"Need {MIN_DAILY_BARS}+ daily bars, have {len(closes)}",
            "pass": False,
            "pass_count": 0,
            "total": 14,
            "conditions": {},
            "source": SOURCE_LABEL,
        }

    close = closes[-1]
    volume = int(bars[-1].get("volume") or 0)
    ema5 = _ema(closes, 5)
    wma10 = _wma(closes, 10)
    sma20 = _sma(closes, 20)
    sma40 = _sma(closes, 40)
    sma50 = _sma(closes, 50)
    rsi14 = _rsi_wilder(closes, 14)
    macd_line, macd_signal, _ = _macd(closes)
    di_plus, di_minus, adx = _adx_di(bars, 14)

    conditions = {
        "ema5_above_sma20": ema5 is not None and sma20 is not None and ema5 > sma20,
        "wma10_above_sma20": wma10 is not None and sma20 is not None and wma10 > sma20,
        "di_plus_above_20": di_plus is not None and di_plus > ADX_DI_THRESHOLD,
        "adx_above_20": adx is not None and adx > ADX_DI_THRESHOLD,
        "volume_above_1l": volume > VOLUME_FLOOR,
        "macd_line_above_0": macd_line is not None and macd_line > 0,
        "close_above_1d_ago": len(closes) >= 2 and close > closes[-2],
        "close_above_sma50": sma50 is not None and close > sma50,
        "close_above_150": close > CLOSE_FLOOR,
        "di_plus_above_di_minus": di_plus is not None
        and di_minus is not None
        and di_plus > di_minus,
        "rsi_above_50": rsi14 is not None and rsi14 > 50,
        "macd_line_above_signal": macd_line is not None
        and macd_signal is not None
        and macd_line > macd_signal,
        "close_above_2d_ago": len(closes) >= 3 and close > closes[-3],
        "sma20_above_sma40": sma20 is not None and sma40 is not None and sma20 > sma40,
    }
    pass_count = sum(1 for v in conditions.values() if v)

    return {
        "available": True,
        "pass": pass_count == len(conditions),
        "pass_count": pass_count,
        "total": len(conditions),
        "conditions": conditions,
        "metrics": {
            "close": round(close, 2),
            "ema5": round(ema5, 2) if ema5 is not None else None,
            "wma10": round(wma10, 2) if wma10 is not None else None,
            "sma20": round(sma20, 2) if sma20 is not None else None,
            "sma40": round(sma40, 2) if sma40 is not None else None,
            "sma50": round(sma50, 2) if sma50 is not None else None,
            "rsi14": round(rsi14, 1) if rsi14 is not None else None,
            "adx14": round(adx, 1) if adx is not None else None,
            "di_plus": round(di_plus, 1) if di_plus is not None else None,
            "di_minus": round(di_minus, 1) if di_minus is not None else None,
            "macd_line": round(macd_line, 3) if macd_line is not None else None,
            "macd_signal": round(macd_signal, 3) if macd_signal is not None else None,
            "volume": volume,
        },
        "source": SOURCE_LABEL,
    }
