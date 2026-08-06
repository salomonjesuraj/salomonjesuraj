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

from aiohttp import web

routes = web.RouteTableDef()

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


def _decode_ohlc(members: list) -> list[dict]:
    bars: list[dict] = []
    for member in members:
        val = member.decode() if isinstance(member, bytes) else member
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


def _merge_bars(*groups: list[dict]) -> list[dict]:
    merged = {}
    for group in groups:
        for bar in group:
            if bar.get("time"):
                merged[int(bar["time"])] = bar
    return [merged[key] for key in sorted(merged)]


def _aggregate(bars: list[dict], minutes: int) -> list[dict]:
    if minutes <= 1:
        return bars
    buckets: dict[int, dict] = {}
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
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
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
    line = [a - b for a, b in zip(ema12, ema26)]
    if len(line) < 9:
        return line[-1], None, None
    signal = _ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def _atr(bars: list[dict], period: int = 14) -> float | None:
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


def _vwap(bars: list[dict]) -> float | None:
    total_volume = sum(max(int(b.get("volume") or 0), 0) for b in bars)
    if total_volume <= 0:
        return None
    pv = 0.0
    for bar in bars:
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        pv += typical * max(int(bar.get("volume") or 0), 0)
    return pv / total_volume


def _candle_pattern(bars: list[dict]) -> str:
    if not bars:
        return "NA"
    cur = bars[-1]
    o, h, l, c = cur["open"], cur["high"], cur["low"], cur["close"]
    body = abs(c - o)
    rng = max(h - l, 0.01)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if len(bars) >= 2:
        prev = bars[-2]
        po, pc = prev["open"], prev["close"]
        if c > o and pc < po and c >= po and o <= pc:
            return "Bullish Engulfing"
        if c < o and pc > po and c <= po and o >= pc:
            return "Bearish Engulfing"
        if h <= prev["high"] and l >= prev["low"]:
            return "Inside Bar"
    if body <= rng * 0.12:
        return "Doji"
    if lower >= body * 2.0 and upper <= body * 0.8:
        return "Hammer"
    if upper >= body * 2.0 and lower <= body * 0.8:
        return "Shooting Star"
    return "Bull Candle" if c > o else "Bear Candle" if c < o else "Flat Candle"


def _supertrend_state(bars: list[dict], period: int = 10, multiplier: float = 3.0) -> str:
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


def _indicators(bars: list[dict], include_vwap: bool) -> IndicatorPack | None:
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


def _state_from_score(score: float) -> str:
    if score >= 58:
        return "BULL"
    if score <= 42:
        return "BEAR"
    return "MIXED"


def _dot(state: str) -> str:
    return {"BULL": "G", "BEAR": "R", "MIXED": "Y"}.get(state, "Y")


def _score_timeframe(tf: str, bars: list[dict], include_vwap: bool) -> dict:
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


async def _load_bars(redis, symbol: str) -> tuple[list[dict], list[dict]]:
    now = int(time.time())
    # Enough 1m bars for recent 4H/1H/15M context without making the endpoint heavy.
    start_intraday = now - (10 * 86400)
    history, live, daily = await asyncio.gather(
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:history:1m", start_intraday, "+inf"),
        redis.zrangebyscore(f"infusion:ohlc:{symbol}:1m", start_intraday, "+inf"),
        redis.zrange(f"infusion:ohlc:{symbol}:daily", -260, -1),
    )
    intraday = _merge_bars(_decode_ohlc(history), _decode_ohlc(live))
    daily_bars = _decode_ohlc(daily)
    return intraday, daily_bars


async def compute_mtf(redis, symbol: str, store: bool = True) -> dict:
    symbol = symbol.upper()
    intraday, daily = await _load_bars(redis, symbol)
    timeframes: dict[str, dict] = {}
    all_warnings: list[str] = []

    for tf, (kind, minutes) in TIMEFRAMES.items():
        bars = _aggregate(intraday, minutes) if kind == "intraday" else daily
        # Keep last 260 bars to prevent bloated JSON while preserving indicator context.
        scored = _score_timeframe(tf, bars[-260:], include_vwap=(kind == "intraday"))
        timeframes[tf] = scored
        all_warnings.extend([f"{tf}: {w}" for w in scored.get("warnings", [])])

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
    quality = "historical" if any(row["bars"] for row in timeframes.values()) else "missing"
    if any(row["quality"] == "limited" for row in timeframes.values()) and quality == "historical":
        quality = "limited"

    rejection_reasons: list[str] = []
    if trade_bias == "BUY CE" and higher_bear:
        rejection_reasons.append("Higher timeframe is resisting CE")
    if trade_bias == "BUY PE" and higher_bull:
        rejection_reasons.append("Higher timeframe is resisting PE")
    if trade_bias == "BUY CE" and timeframes["5M"].get("rsi14", 50) and timeframes["5M"]["rsi14"] > 74:
        rejection_reasons.append("5M RSI chase risk")
    if trade_bias == "BUY PE" and timeframes["5M"].get("rsi14", 50) and timeframes["5M"]["rsi14"] < 26:
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
        "bull_count": bull_count,
        "bear_count": bear_count,
        "mixed_count": mixed_count,
        "rejection_reasons": rejection_reasons,
        "warnings": all_warnings[:10],
    }

    if store:
        await redis.setex(f"infusion:mtf:{symbol}", 300, json.dumps(payload, separators=(",", ":")))
    return payload


async def _cached_or_compute(redis, symbol: str) -> dict:
    raw = await redis.get(f"infusion:mtf:{symbol.upper()}")
    if raw:
        try:
            value = raw.decode() if isinstance(raw, bytes) else raw
            payload = json.loads(value)
            payload["cached"] = True
            return payload
        except (json.JSONDecodeError, TypeError):
            pass
    payload = await compute_mtf(redis, symbol, store=True)
    payload["cached"] = False
    return payload


@routes.get("/api/mtf/refresh")
async def refresh_mtf(request):
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
    errors: list[dict] = []
    for symbol in symbols[:limit]:
        try:
            await compute_mtf(redis, symbol, store=True)
            refreshed += 1
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:120]})
    return web.json_response({"requested": min(limit, len(symbols)), "refreshed": refreshed, "errors": errors[:10]})


@routes.get("/api/mtf/queue/status")
async def mtf_queue_status(request):
    redis = request.app["redis"]
    raw = await redis.get("infusion:mtf-queue:status")
    if not raw:
        return web.json_response({"enabled": True, "state": "waiting_for_first_cycle"})
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        payload = json.loads(text)
        return web.json_response(payload)
    except Exception:
        return web.json_response({"enabled": True, "error": "status_decode_failed"})


@routes.get("/api/mtf/{symbol}")
async def get_symbol_mtf(request):
    redis = request.app["redis"]
    symbol = request.match_info["symbol"].upper()
    payload = await _cached_or_compute(redis, symbol)
    return web.json_response(payload)
