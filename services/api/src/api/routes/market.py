"""Market/options dashboard routes.

These endpoints are intentionally lightweight and Redis-backed so the
dashboard can show option-trading context without slowing the scanner.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from urllib.parse import urlencode

import aiohttp
import msgpack
from aiohttp import web

from api.cost_model import estimate_entry_costs_per_unit
from api.event_calendar import get_event_risk
from api.option_reality import breakeven_gate, delta_band_gate, derive_option_sl, iv_rank_gate
from api.options_analytics import compute_max_pain, compute_oi_support_resistance, compute_pcr

routes = web.RouteTableDef()

UPSTOX_API_BASE = "https://api.upstox.com/v3"
UPSTOX_API_V2_BASE = "https://api.upstox.com/v2"
UPSTOX_INDEX_MAP = {
    "NIFTY50": "NSE_INDEX|Nifty 50",
    "NIFTYBANK": "NSE_INDEX|Nifty Bank",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "GIFTNIFTY": os.getenv("INFUSION_GIFT_NIFTY_INSTRUMENT_KEY", ""),
}
INDEX_SYMBOLS = {"NIFTY", "NIFTY50", "BANKNIFTY", "NIFTYBANK", "FINNIFTY", "MIDCPNIFTY"}


def _liquidity_thresholds() -> dict:
    return {
        "min_oi": float(os.getenv("INFUSION_OPTION_MIN_OI", "100")),
        "min_volume": float(os.getenv("INFUSION_OPTION_MIN_VOLUME", "1")),
        "max_spread_pct": float(os.getenv("INFUSION_OPTION_MAX_SPREAD_PCT", "3.0")),
    }


def _decode_hash(data: dict) -> dict:
    result = {}
    for k, v in data.items():
      key = k.decode() if isinstance(k, bytes) else k
      val = v.decode() if isinstance(v, bytes) else v
      try:
          result[key] = float(val)
      except (ValueError, TypeError):
          result[key] = val
    return result


async def _tick(redis, symbol: str) -> dict | None:
    data = await redis.hgetall(f"infusion:tick:{symbol}")
    if not data:
        return None
    out = _decode_hash(data)
    out["symbol"] = symbol
    return out


async def _feature(redis, symbol: str) -> dict:
    data = await redis.hgetall(f"infusion:feature:{symbol}")
    return _decode_hash(data) if data else {}


async def _signal(redis, symbol: str) -> dict:
    data = await redis.hgetall(f"infusion:signal:{symbol}")
    return _decode_hash(data) if data else {}


async def _instrument_key_for_symbol(redis, symbol: str) -> str:
    raw = await redis.hgetall("infusion:symbols")
    for key, value in raw.items():
        instrument_key = key.decode() if isinstance(key, bytes) else key
        try:
            payload = msgpack.unpackb(value, raw=False) if isinstance(value, bytes) else value
        except Exception:
            continue
        if str(payload.get("symbol", "")).upper() == symbol.upper():
            return instrument_key
    return ""


async def _upstox_access_token(redis) -> str:
    auth_raw = await redis.get("infusion:auth:upstox")
    if auth_raw:
        try:
            auth_text = auth_raw.decode() if isinstance(auth_raw, bytes) else auth_raw
            token = json.loads(auth_text).get("access_token", "")
            if token:
                return token
        except Exception:
            pass
    # Fallback for the local Docker setup where ingestion gets the token via
    # .env and the OAuth service may not have refreshed Redis yet.
    import os
    return os.getenv("INFUSION_UPSTOX_ACCESS_TOKEN", "")


async def _upstox_index_quotes(request, symbols: list[str]) -> dict[str, dict]:
    """Fetch index quotes from Upstox REST as a fallback when WS index ticks are absent."""
    app = request.app
    now = time.time()
    cache = app.setdefault("_upstox_index_quote_cache", {"ts": 0.0, "data": {}})
    if now - float(cache.get("ts", 0) or 0) < 5:
        return cache.get("data", {})

    redis = app["redis"]
    access_token = await _upstox_access_token(redis)
    if not access_token:
        return {}

    instruments = [UPSTOX_INDEX_MAP[s] for s in symbols if s in UPSTOX_INDEX_MAP and UPSTOX_INDEX_MAP[s]]
    if not instruments:
        return {}

    query = urlencode({"instrument_key": ",".join(instruments)})
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{UPSTOX_API_BASE}/market-quote/ltp?{query}", headers=headers, timeout=5) as resp:
                if resp.status != 200:
                    return {}
                payload = await resp.json()
    except Exception:
        return {}

    out = {}
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    reverse = {v: k for k, v in UPSTOX_INDEX_MAP.items()}
    for _, quote in data.items():
        token = quote.get("instrument_token") or quote.get("instrument_key")
        symbol = reverse.get(token)
        if not symbol or not isinstance(quote, dict):
            continue
        ltp = float(quote.get("last_price") or quote.get("ltp") or 0)
        close = float(quote.get("cp") or 0)
        change_pct = ((ltp - close) / close * 100) if ltp and close else 0.0
        out[symbol] = {
            "symbol": symbol,
            "ltp": ltp,
            "change_pct": round(change_pct, 4),
            "volume": float(quote.get("volume") or 0),
            "exchange_ts": quote.get("last_trade_time") or quote.get("ltt") or "",
            "source": "upstox_quote_fallback",
        }

    cache["ts"] = now
    cache["data"] = out
    return out


def _underlying_score(features: dict) -> tuple[float | None, dict]:
    if not features:
        return None, {
            "trend": None, "volume": None, "vwap": None,
            "oi": None, "iv": None, "spread": None, "strike": None,
        }

    ltp = float(features.get("ltp", 0) or 0)
    vwap = float(features.get("vwap", 0) or 0)
    ema9 = float(features.get("ema_9", 0) or 0)
    ema20 = float(features.get("ema_20", 0) or 0)
    rsi = float(features.get("rsi_14", 50) or 50)
    rel_vol = float(features.get("rel_vol_20d", 0) or 0)
    bb_width = float(features.get("bb_width", 0) or 0)
    spread = float(features.get("spread_bps", 999) or 999)

    trend_ok = ltp > ema9 > ema20 > 0
    vwap_ok = ltp > vwap > 0
    volume_ok = rel_vol >= 1.5
    rsi_ok = 45 <= rsi <= 68
    bb_ok = bb_width > 0.002
    spread_ok = spread < 20

    score = 0.0
    score += 24 if trend_ok else 10 if ltp > ema9 > 0 else 0
    score += 18 if vwap_ok else 0
    score += 20 if rel_vol >= 3 else 15 if volume_ok else 5 if rel_vol > 0 else 0
    score += 14 if rsi_ok else 6 if 40 <= rsi <= 75 else 0
    score += 12 if bb_ok else 0
    score += 12 if spread_ok else 4 if spread < 50 else 0

    return min(score, 100.0), {
        "trend": trend_ok,
        "volume": volume_ok,
        "vwap": vwap_ok,
        "oi": None,
        "iv": None,
        "spread": None,
        "strike": None,
    }


def _days_to_expiry(expiry: str) -> int | None:
    try:
        return (date.fromisoformat(str(expiry)[:10]) - date.today()).days
    except Exception:
        return None


def _quality_grade(score: float, hard_blockers: list[str]) -> str:
    if hard_blockers:
        return "D"
    if score >= 85:
        return "A+"
    if score >= 75:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _execution_score_cap(
    raw_score: float,
    blockers: list[str],
    hard_blockers: list[str],
    *,
    has_pending_history: bool,
) -> tuple[float, str, str]:
    """Convert raw chain quality into realistic executable option score.

    Raw OI/spread/IV/strike strength can look strong even when a contract is
    not tradable.  This cap keeps dashboard conviction honest for option buying:
    a hard blocker means the user can watch the underlying, but should not treat
    the option contract as execution-ready.
    """
    score = float(raw_score or 0.0)
    if hard_blockers:
        return min(score, 49.0), "hard_blocker_cap", "; ".join(hard_blockers[:3])
    if has_pending_history:
        return min(score, 64.0), "iv_history_pending_cap", "Need 60-session IV history before full option conviction"
    if blockers:
        return min(score, 74.0), "soft_blocker_cap", "; ".join(blockers[:3])
    return score, "", ""


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _iv_rank(redis, contract_key: str, current_iv: float) -> tuple[float | None, int]:
    """Return rolling IV rank when at least 60 stored observations exist.

    The approval request requires no guessing.  Until history is available the
    option chain remains pending rather than pretending IV crush risk is known.
    """
    if not contract_key or current_iv <= 0:
        return None, 0
    key = f"infusion:option-iv-history:{contract_key}"
    seen_key = f"infusion:option-iv-history-seen:{contract_key}"
    raw = await redis.lrange(key, 0, 59)
    vals: list[float] = []
    for item in raw:
        try:
            vals.append(float(item.decode() if isinstance(item, bytes) else item))
        except (TypeError, ValueError):
            continue
    today_key = date.today().isoformat()
    if not await redis.sismember(seen_key, today_key):
        await redis.lpush(key, str(round(current_iv, 4)))
        await redis.ltrim(key, 0, 119)
        await redis.sadd(seen_key, today_key)
        await redis.expire(key, 86400 * 180)
        await redis.expire(seen_key, 86400 * 180)
    if len(vals) < 60:
        return None, len(vals)
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return 50.0, len(vals)
    rank = (current_iv - lo) / (hi - lo) * 100
    return round(max(0.0, min(100.0, rank)), 2), len(vals)


def _underlying_levels(features: dict, signal: dict, bias: str, spot: float) -> dict:
    atr = _num(features.get("atr_14"), max(spot * 0.006, 0.05))
    atr = atr if atr > 0 else max(spot * 0.006, 0.05)
    entry = _num(signal.get("entry_price") or signal.get("entry") or spot, spot)
    sl = _num(signal.get("invalidation_price") or signal.get("stop") or signal.get("sl"), 0)
    t1 = _num(signal.get("t1_price") or signal.get("target_price") or signal.get("target1"), 0)
    if not sl:
        sl = entry - atr if bias == "CE" else entry + atr
    if not t1:
        t1 = entry + atr * 1.6 if bias == "CE" else entry - atr * 1.6
    underlying_risk = abs(entry - sl)
    # Stock-option buying needs an underlying target large enough to clear
    # premium, spread, theta and breakeven.  Older signal targets can be only a
    # rupee or two away; that makes the option-chain breakeven gate reject every
    # contract even when the underlying is moving.  Floor T1 before scoring.
    target_floor = max(atr * 1.85, underlying_risk * 1.75, entry * 0.0075, 1.0)
    if bias == "CE" and t1 - entry < target_floor:
        t1 = entry + target_floor
    elif bias == "PE" and entry - t1 < target_floor:
        t1 = entry - target_floor
    daily_atr_pct = atr / max(spot, 0.01) * 100
    return {
        "entry_underlying": round(entry, 2),
        "sl_underlying": round(sl, 2),
        "target1_underlying": round(t1, 2),
        "target_floor": round(target_floor, 2),
        "atr": round(atr, 4),
        "daily_atr_pct": round(daily_atr_pct, 4),
    }


def _score_option_leg(
    row: dict,
    leg_name: str,
    spot: float,
    bias: str,
    expiry_days: int | None = None,
    levels: dict | None = None,
    iv_rank: float | None = None,
    iv_history_count: int = 0,
    symbol: str = "",
    event_risk: dict | None = None,
) -> tuple[float | None, dict, str, dict, list[str], list[str], list[str]]:
    leg = row.get(leg_name) or {}
    market = leg.get("market_data") or {}
    greeks = leg.get("option_greeks") or {}
    ltp = float(market.get("ltp") or 0)
    bid = float(market.get("bid_price") or 0)
    ask = float(market.get("ask_price") or 0)
    entry_ask = ask if ask > 0 else ltp
    oi = float(market.get("oi") or 0)
    prev_oi = float(market.get("prev_oi") or 0)
    iv = float(greeks.get("iv") or 0)
    delta = float(greeks.get("delta") or 0)
    volume = float(market.get("volume") or 0)

    spread_pct = ((ask - bid) / ltp * 100) if ask > 0 and bid > 0 and ltp > 0 else 999.0
    oi_change = ((oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0.0
    abs_delta = abs(delta)
    strike = float(row.get("strike_price") or 0)
    moneyness_pct = abs(strike - spot) / spot * 100 if spot > 0 and strike > 0 else 999.0
    is_ce = bias == "CE"
    # For intraday stock options, prefer ATM or one strike ITM. Deep OTM can
    # look cheap but decay/spread slippage makes signals unreliable.
    near_or_itm = (
        moneyness_pct <= 2.0
        or (is_ce and strike <= spot and moneyness_pct <= 4.0)
        or (not is_ce and strike >= spot and moneyness_pct <= 4.0)
    )
    thresholds = _liquidity_thresholds()
    liquidity_pass = (
        oi >= thresholds["min_oi"]
        and volume >= thresholds["min_volume"]
        and spread_pct <= thresholds["max_spread_pct"]
    )
    physical_settlement_block = (
        str(symbol or "").upper() not in INDEX_SYMBOLS
        and expiry_days is not None
        and expiry_days <= 3
    )
    event_risk = event_risk or {"entry_allowed": True}

    spread_per_unit = max(0.0, ask - bid) if ask > 0 and bid > 0 else 0.0
    est_costs_per_unit = estimate_entry_costs_per_unit(entry_ask, bid, 1) if entry_ask > 0 else 0.0
    levels = levels or {}
    sl_model = derive_option_sl(
        levels.get("entry_underlying") or spot,
        levels.get("sl_underlying") or spot,
        entry_ask,
        delta,
    )
    be_model = breakeven_gate(
        bias,
        spot,
        strike,
        entry_ask,
        spread_per_unit,
        est_costs_per_unit,
        levels.get("daily_atr_pct") or 0,
        1.0 if expiry_days is None else min(max(expiry_days, 1), 3),
        levels.get("target1_underlying") or spot,
    )
    delta_model = delta_band_gate(delta)
    iv_rank_model = iv_rank_gate(iv_rank)

    gates = {
        "premium": ltp > 0,
        "volume": volume >= 1,
        "oi": oi >= 100 and oi_change >= -20,
        "liquidity_whitelist": liquidity_pass,
        "iv": 5 <= iv <= 80,
        "iv_rank": iv_rank_model.get("iv_rank_pass"),
        "spread": spread_pct <= 3.0,
        "delta": delta_model["delta_band_pass"],
        "strike": near_or_itm and delta_model["delta_band_pass"],
        "premium_sl": not sl_model["hard_blockers"],
        "breakeven": not be_model["hard_blockers"],
        "expiry": expiry_days is None or 2 <= expiry_days <= 35,
        "physical_settlement": not physical_settlement_block,
        "event_calendar": bool(event_risk.get("entry_allowed", True)),
    }
    reasons: list[str] = []
    blockers: list[str] = []
    hard_blockers: list[str] = []
    if gates["premium"]:
        reasons.append(f"Premium live {ltp:.2f}")
    else:
        blockers.append("No live option premium")
        hard_blockers.append("No live option premium")
    if gates["spread"]:
        reasons.append(f"Spread {spread_pct:.1f}%")
    elif spread_pct <= 6.0:
        blockers.append(f"Spread acceptable but not ideal {spread_pct:.1f}%")
    else:
        blockers.append(f"Wide spread {spread_pct:.1f}%")
        hard_blockers.append(f"Wide spread {spread_pct:.1f}%")
    if gates["oi"]:
        reasons.append(f"OI {oi:,.0f} ({oi_change:+.1f}%)")
    else:
        blockers.append("Weak or falling OI")
    if gates["liquidity_whitelist"]:
        reasons.append("Liquidity whitelist pass")
    else:
        reason = "below liquidity whitelist"
        blockers.append(reason)
        hard_blockers.append(reason)
    if gates["iv"]:
        reasons.append(f"IV {iv:.1f}")
    elif iv > 0:
        blockers.append(f"IV outside comfort zone {iv:.1f}")
    else:
        blockers.append("IV missing")
    if iv_rank_model["status"] == "PASS":
        reasons.append(f"IV rank {iv_rank:.1f}")
    elif iv_rank_model["status"] == "CHAIN_PENDING":
        blockers.append(iv_rank_model["reason"])
    elif iv_rank_model["status"] == "WAIT_CONTRACT":
        blockers.append(iv_rank_model["reason"])
    elif iv_rank_model["status"] == "AVOID_CONTRACT":
        blockers.append(iv_rank_model["reason"])
        hard_blockers.append(iv_rank_model["reason"])
    if gates["strike"]:
        reasons.append(f"Strike near executable zone ({moneyness_pct:.1f}% from spot)")
    else:
        reason = delta_model["reason"] or "Strike too far / poor delta"
        blockers.append(reason)
        hard_blockers.append(reason)
    for reason in sl_model["hard_blockers"]:
        blockers.append(reason)
        hard_blockers.append(reason)
    if not sl_model["hard_blockers"]:
        reasons.append(f"Delta SL {sl_model['option_sl_price']:.2f} ({sl_model['premium_risk_pct']:.1f}% risk)")
    for reason in be_model["blockers"]:
        blockers.append(reason)
    for reason in be_model["hard_blockers"]:
        blockers.append(reason)
        hard_blockers.append(reason)
    if not be_model["hard_blockers"] and not be_model["blockers"]:
        reasons.append(f"Breakeven clears: req {be_model['required_move_pct']:.2f}% vs exp {be_model['expected_move_pct']:.2f}%")
    if expiry_days is not None:
        if gates["expiry"]:
            reasons.append(f"Expiry runway {expiry_days}d")
        elif expiry_days < 2:
            blockers.append(f"Expiry too close {expiry_days}d")
            hard_blockers.append(f"Expiry too close {expiry_days}d")
        else:
            blockers.append(f"Expiry too far {expiry_days}d")
    if physical_settlement_block:
        reason = "physical settlement block - stock option expiry <= 3 days"
        blockers.append(reason)
        hard_blockers.append(reason)
    if not event_risk.get("entry_allowed", True):
        reason = event_risk.get("block_reason") or "event calendar block"
        blockers.append(reason)
        hard_blockers.append(reason)

    score = 0.0
    score += 20 if gates["premium"] else 0
    score += 20 if gates["oi"] and oi_change > 5 else 14 if gates["oi"] else 4 if oi > 0 else 0
    score += 22 if spread_pct <= 2 else 18 if gates["spread"] else 8 if spread_pct <= 6 else 0
    score += 16 if gates["iv"] else 5 if iv > 0 else 0
    score += 16 if gates["strike"] else 4 if moneyness_pct <= 5 else 0
    score += 8 if gates["premium_sl"] else 0
    score += 8 if gates["breakeven"] else 0
    score += 6 if gates["iv_rank"] is True else -8 if gates["iv_rank"] is False else -12
    score += 10 if volume >= 1 else 0
    score += 10 if gates["expiry"] else 0
    score += 8 if gates["liquidity_whitelist"] else -18
    score -= 30 if physical_settlement_block else 0
    score -= 25 if not event_risk.get("entry_allowed", True) else 0
    score -= min(25, len(hard_blockers) * 10)
    score = min(max(score, 0.0), 100.0)
    contract = leg.get("instrument_key", "")
    metrics = {
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
        "entry_fill": entry_ask,
        "exit_fill_reference": bid,
        "spread_pct": round(spread_pct, 2),
        "spread_per_unit": round(spread_per_unit, 4),
        "est_costs_per_unit": round(est_costs_per_unit, 4),
        "oi": oi,
        "oi_change_pct": round(oi_change, 2),
        "iv": iv,
        "iv_rank": iv_rank,
        "iv_history_count": iv_history_count,
        "liquidity_whitelist_pass": liquidity_pass,
        "liquidity_thresholds": thresholds,
        "delta": delta,
        "volume": volume,
        "strike": strike,
        "lot_size": leg.get("lot_size") or row.get("lot_size") or row.get("minimum_lot") or 0,
        "moneyness_pct": round(moneyness_pct, 2),
        "expiry_days": expiry_days,
        "physical_settlement_block": physical_settlement_block,
        "event_calendar": event_risk,
        **sl_model,
        **be_model,
        "quality_grade": _quality_grade(score, hard_blockers),
        "hard_blockers": hard_blockers,
    }
    return score, gates, contract, metrics, reasons, blockers, hard_blockers


async def _resolve_upstox_expiry(session, headers: dict, instrument_key: str) -> tuple[str, str]:
    """Resolve relative expiry to a concrete YYYY-MM-DD date.

    Index options often have current-week contracts. Many stock options are
    monthly, so fallback to current_month before giving up.
    """
    last_error = ""
    for relative in ("current_week", "current_month"):
        try:
            async with session.get(
                f"{UPSTOX_API_V2_BASE}/option/contract",
                headers=headers,
                params={"instrument_key": instrument_key, "expiry_date": relative},
                timeout=8,
            ) as resp:
                payload = await resp.json()
                if resp.status != 200 or payload.get("status") != "success":
                    last_error = payload.get("message", f"Upstox option contract HTTP {resp.status}")
                    continue
        except Exception as exc:
            last_error = f"Upstox option contract error: {exc}"
            continue

        rows = payload.get("data") or []
        expiries = sorted({str(r.get("expiry") or r.get("expiry_date") or "") for r in rows if r.get("expiry") or r.get("expiry_date")})
        if expiries:
            return expiries[0], ""
        last_error = f"Upstox option contract returned no {relative} expiry."
    return "", last_error or "Unable to resolve Upstox option expiry."


async def _fetch_full_option_chain(redis, symbol: str) -> dict:
    """Fetch the FULL Upstox option chain (every strike, both call and put
    OI) for chain-wide analytics (PCR, OI-based S/R, Max Pain) -- separate
    from _upstox_option_context()'s near-ATM-only (+/-6%), bias-filtered
    fetch below, which exists for per-signal scoring and is left untouched.
    Cached under its own bias-independent key so a symbol with both a CE
    and PE signal live doesn't fetch the same full chain twice.
    """
    access_token = await _upstox_access_token(redis)
    if not access_token:
        return {"ready": False, "reason": "Upstox auth token missing; login at http://localhost:5100/auth/login."}

    instrument_key = await _instrument_key_for_symbol(redis, symbol)
    if not instrument_key:
        return {"ready": False, "reason": f"No Upstox instrument key found for {symbol}."}

    cache_key = f"infusion:option-chain-full:{symbol}"
    cached = await redis.get(cache_key)
    if cached:
        try:
            payload = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
            payload["cached"] = True
            return payload
        except Exception:
            pass

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with aiohttp.ClientSession() as session:
            expiry, expiry_error = await _resolve_upstox_expiry(session, headers, instrument_key)
            if not expiry:
                return {"ready": False, "reason": expiry_error or "Unable to resolve Upstox option expiry."}
            async with session.get(
                f"{UPSTOX_API_V2_BASE}/option/chain",
                headers=headers,
                params={"instrument_key": instrument_key, "expiry_date": expiry},
                timeout=8,
            ) as resp:
                data = await resp.json()
                if resp.status != 200 or data.get("status") != "success":
                    return {
                        "ready": False,
                        "reason": data.get("message", f"Upstox option chain HTTP {resp.status}"),
                    }
    except Exception as exc:
        return {"ready": False, "reason": f"Upstox option chain error: {exc}"}

    rows = data.get("data") or []
    if not rows:
        return {"ready": False, "reason": "Upstox option chain returned no rows."}

    spot = float(rows[0].get("underlying_spot_price") or 0)
    result = {"ready": True, "symbol": symbol, "expiry": expiry, "spot": spot, "rows": rows}
    try:
        await redis.setex(cache_key, 30, json.dumps(result, default=str))
    except Exception:
        pass
    return result


async def compute_options_chain_analytics(redis, symbol: str) -> dict:
    """PCR + OI-based support/resistance + Max Pain for one symbol, off
    the full option chain. See api/options_analytics.py for the sourced
    formulas (Upstox's own learning-center methodology)."""
    chain = await _fetch_full_option_chain(redis, symbol)
    if not chain.get("ready"):
        return {"ready": False, "reason": chain.get("reason", "Option chain unavailable.")}

    rows = chain["rows"]
    pcr = compute_pcr(rows)
    oi_sr = compute_oi_support_resistance(rows)
    max_pain = compute_max_pain(rows)

    return {
        "ready": True,
        "symbol": symbol,
        "expiry": chain.get("expiry"),
        "spot": chain.get("spot"),
        "strikes_in_chain": len(rows),
        "pcr": pcr,
        "oi_support_resistance": oi_sr,
        "max_pain": max_pain,
        "cached": bool(chain.get("cached")),
    }


async def _upstox_option_context(redis, symbol: str, bias: str, features: dict | None = None, signal: dict | None = None) -> dict:
    if bias not in {"CE", "PE"}:
        return {"ready": False, "reason": "Option chain waits until scanner has CE/PE bias."}

    access_token = await _upstox_access_token(redis)
    if not access_token:
        return {"ready": False, "reason": "Upstox auth token missing; login at http://localhost:5100/auth/login."}

    instrument_key = await _instrument_key_for_symbol(redis, symbol)
    if not instrument_key:
        return {"ready": False, "reason": f"No Upstox instrument key found for {symbol}."}

    cache_key = f"infusion:option-chain:{symbol}:{bias}"
    cached = await redis.get(cache_key)
    if cached:
        try:
            payload = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
            payload["cached"] = True
            return payload
        except Exception:
            pass

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    try:
        async with aiohttp.ClientSession() as session:
            expiry, expiry_error = await _resolve_upstox_expiry(session, headers, instrument_key)
            if not expiry:
                return {"ready": False, "reason": expiry_error or "Unable to resolve Upstox option expiry."}
            expiry_days = _days_to_expiry(expiry)
            async with session.get(
                f"{UPSTOX_API_V2_BASE}/option/chain",
                headers=headers,
                params={"instrument_key": instrument_key, "expiry_date": expiry},
                timeout=8,
            ) as resp:
                payload = await resp.json()
                if resp.status != 200 or payload.get("status") != "success":
                    return {
                        "ready": False,
                        "reason": payload.get("message", f"Upstox option chain HTTP {resp.status}"),
                    }
    except Exception as exc:
        return {"ready": False, "reason": f"Upstox option chain error: {exc}"}

    rows = payload.get("data") or []
    if not rows:
        return {"ready": False, "reason": "Upstox option chain returned no rows."}

    spot = float(rows[0].get("underlying_spot_price") or 0)
    if spot <= 0:
        return {"ready": False, "reason": "Upstox option chain missing underlying spot price."}

    leg_name = "call_options" if bias == "CE" else "put_options"
    levels = _underlying_levels(features or {}, signal or {}, bias, spot)
    event_risk = await get_event_risk(redis, symbol)
    scored_rows = []
    for row in rows:
        strike = float(row.get("strike_price") or 0)
        if strike <= 0 or abs(strike - spot) / spot * 100 > 6:
            continue
        row_expiry = str(row.get("expiry") or row.get("expiry_date") or expiry)
        leg = row.get(leg_name) or {}
        greeks = leg.get("option_greeks") or {}
        market = leg.get("market_data") or {}
        contract_key = str(leg.get("instrument_key") or "")
        current_iv = float(greeks.get("iv") or 0)
        iv_rank, iv_history_count = await _iv_rank(redis, contract_key, current_iv)
        score, gates, contract, metrics, reasons, blockers, hard_blockers = _score_option_leg(
            row,
            leg_name,
            spot,
            bias,
            _days_to_expiry(row_expiry) if row_expiry else expiry_days,
            levels,
            iv_rank,
            iv_history_count,
            symbol,
            event_risk,
        )
        scored_rows.append((score or 0, row, gates, contract, metrics, reasons, blockers, hard_blockers))
    if not scored_rows:
        return {"ready": False, "reason": "No near-ATM option strikes were scoreable from Upstox chain."}
    scored_rows.sort(key=lambda x: (x[0], -abs(float(x[1].get("strike_price") or 0) - spot)), reverse=True)
    raw_option_score, row, gates, contract, metrics, reasons, blockers, hard_blockers = scored_rows[0]
    has_pending_history = metrics.get("iv_rank") is None and int(metrics.get("iv_history_count") or 0) < 60
    option_score, cap_reason, cap_detail = _execution_score_cap(
        raw_option_score,
        blockers,
        hard_blockers,
        has_pending_history=has_pending_history,
    )
    metrics.update({
        "raw_option_score": round(float(raw_option_score or 0.0), 1),
        "execution_score": round(float(option_score or 0.0), 1),
        "score_cap_reason": cap_reason,
        "score_cap_detail": cap_detail,
        "quality_grade": _quality_grade(option_score, hard_blockers),
    })
    trade_ready = option_score >= 75 and not hard_blockers and not has_pending_history
    execution_status = (
        "TRADE_READY" if trade_ready
        else "CHAIN_PENDING" if has_pending_history and not hard_blockers
        else "AVOID_CONTRACT" if hard_blockers
        else "WAIT_CONTRACT" if option_score >= 60
        else "AVOID_CONTRACT"
    )
    result = {
        "ready": True,
        "cached": False,
        "underlying_key": instrument_key,
        "expiry": row.get("expiry", expiry),
        "expiry_days": metrics.get("expiry_days"),
        "spot": spot,
        "strike": row.get("strike_price"),
        "underlying_levels": levels,
        "event_calendar": event_risk,
        "contract": contract,
        "option_score": option_score,
        "raw_option_score": raw_option_score,
        "execution_score": option_score,
        "score_cap_reason": cap_reason,
        "score_cap_detail": cap_detail,
        "trade_ready": trade_ready,
        "execution_status": execution_status,
        "quality_grade": metrics.get("quality_grade"),
        "gates": gates,
        "metrics": metrics,
        "reasons": reasons,
        "blockers": blockers,
        "hard_blockers": hard_blockers,
        "pcr": row.get("pcr"),
    }
    await redis.set(cache_key, json.dumps(result, separators=(",", ":")), ex=30)
    await redis.set(f"infusion:option-chain:{symbol}", json.dumps(result, separators=(",", ":")), ex=30)
    return result


async def _default_symbol(redis) -> str:
    members = await redis.zrevrange("infusion:signals:active", 0, 0)
    if members:
        member = members[0].decode() if isinstance(members[0], bytes) else members[0]
        return member.split(":")[0]

    cursor = 0
    best_symbol = ""
    best_score = -1.0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="infusion:prebreak:*", count=200)
        for key in keys:
            data = await redis.hgetall(key)
            if not data:
                continue
            entry = _decode_hash(data)
            score = float(entry.get("readiness_score", 0) or 0)
            if score > best_score:
                kk = key.decode() if isinstance(key, bytes) else key
                best_symbol = kk.replace("infusion:prebreak:", "")
                best_score = score
        if cursor == 0:
            break
    return best_symbol


@routes.get("/api/market/indices")
async def market_indices(request):
    """Return top index ticks for the dashboard ticker strip."""
    redis = request.app["redis"]
    wanted = [
        ("NIFTY50", "NIFTY"),
        ("NIFTYBANK", "BANKNIFTY"),
        ("BANKNIFTY", "BANKNIFTY"),
        ("GIFTNIFTY", "GIFT NIFTY"),
    ]

    indices = []
    seen = set()
    missing_symbols = []
    for symbol, label in wanted:
        if label in seen:
            continue
        tick = await _tick(redis, symbol)
        if tick:
            tick["label"] = label
            indices.append(tick)
        else:
            missing_symbols.append(symbol)
            indices.append({"symbol": symbol, "label": label, "error": "no_tick_data"})
        seen.add(label)

    if missing_symbols:
        fallback = await _upstox_index_quotes(request, missing_symbols)
        for idx, item in enumerate(indices):
            symbol = item.get("symbol")
            if item.get("error") and symbol in fallback:
                fixed = fallback[symbol]
                fixed["label"] = item.get("label", symbol)
                indices[idx] = fixed

    return web.json_response({"count": len(indices), "indices": indices})


@routes.get("/api/options/chain-analytics")
async def options_chain_analytics(request):
    """PCR + OI-based support/resistance + Max Pain for one symbol.

    Sourced from Upstox's own learning-center methodology (the actual
    broker Infusion integrates with), not generic textbook convention --
    see api/options_analytics.py's module docstring for exact source URLs.
    """
    redis = request.app["redis"]
    symbol = request.query.get("symbol", "").upper().strip()
    if not symbol:
        symbol = await _default_symbol(redis)
    if not symbol:
        return web.json_response({"ready": False, "reason": "No symbol provided and no default symbol available."})

    result = await compute_options_chain_analytics(redis, symbol)
    return web.json_response(result)


@routes.get("/api/options/summary")
async def options_summary(request):
    """Option-trading readiness summary.

    Current phase uses underlying features and exposes option-chain gates as
    pending. Once option-chain Redis keys are available, this endpoint is the
    plug-in point for OI, IV, spread, strike and Greeks scoring.
    """
    redis = request.app["redis"]
    symbol = request.query.get("symbol", "").upper().strip()
    if not symbol:
        symbol = await _default_symbol(redis)

    if not symbol:
        return web.json_response({
            "symbol": "",
            "bias": "WAIT",
            "underlying_score": None,
            "option_score": None,
            "final_score": None,
            "option_chain_ready": False,
            "trade_ready": False,
            "execution_status": "NO_SYMBOL",
            "quality_grade": None,
            "blockers": [],
            "hard_blockers": [],
            "suggested_contract": "",
            "gates": {
                "trend": None, "volume": None, "vwap": None,
                "oi": None, "iv": None, "iv_rank": None, "spread": None,
                "delta": None, "premium_sl": None, "breakeven": None,
                "liquidity_whitelist": None, "physical_settlement": None,
                "event_calendar": None, "strike": None,
            },
            "reason": "No active signal or pre-breakout symbol is available yet.",
        })

    features = await _feature(redis, symbol)
    signal = await _signal(redis, symbol)
    score, gates = _underlying_score(features)
    change_pct = float(features.get("change_pct", 0) or 0)
    signal_bias = str(signal.get("option_bias") or "").upper()
    if "CE" in signal_bias:
        bias = "CE"
    elif "PE" in signal_bias:
        bias = "PE"
    else:
        bias = "CE" if change_pct > 0.15 and gates.get("trend") else "PE" if change_pct < -0.15 else "WAIT"

    # Future option-chain keys can be written here by an option-chain service.
    option_ctx = await _upstox_option_context(redis, symbol, bias, features, signal)
    chain_ready = bool(option_ctx.get("ready"))
    option_score = option_ctx.get("option_score") if chain_ready else None
    trade_ready = bool(option_ctx.get("trade_ready")) if chain_ready else False
    execution_status = option_ctx.get("execution_status") if chain_ready else "CHAIN_PENDING"
    final_score = (
        round((float(score or 0) * 0.60) + (float(option_score or 0) * 0.40), 1)
        if chain_ready and score is not None and option_score is not None
        else None
    )
    if chain_ready and final_score is not None and not trade_ready:
        final_score = min(final_score, 74.0)
    if chain_ready:
        gates.update(option_ctx.get("gates") or {})
    suggested = (
        f"{symbol} {option_ctx.get('strike')} {bias} · {option_ctx.get('expiry')} · {option_ctx.get('contract')}"
        if chain_ready
        else f"{symbol} ATM {'CE' if bias == 'CE' else 'PE' if bias == 'PE' else 'CE/PE'} · {option_ctx.get('reason', 'waiting for Upstox chain')}"
    )

    if chain_ready:
        reason = " | ".join((option_ctx.get("reasons") or [])[:4])
        blockers = option_ctx.get("blockers") or []
        if blockers:
            reason = f"{reason} | Blockers: {', '.join(blockers[:3])}"
        if not trade_ready:
            reason = f"WAIT: contract quality not execution-ready. {reason}"
    else:
        reason = (
            "Underlying scanner is live. Upstox chain confirmation is pending: "
            f"{option_ctx.get('reason', 'waiting for Upstox chain')}"
        )

    return web.json_response({
        "symbol": symbol,
        "bias": bias,
        "underlying_score": score,
        "option_score": option_score,
        "raw_option_score": option_ctx.get("raw_option_score") if chain_ready else None,
        "execution_score": option_ctx.get("execution_score") if chain_ready else None,
        "score_cap_reason": option_ctx.get("score_cap_reason") if chain_ready else "",
        "score_cap_detail": option_ctx.get("score_cap_detail") if chain_ready else "",
        "final_score": final_score,
        "option_chain_ready": chain_ready,
        "trade_ready": trade_ready,
        "execution_status": execution_status,
        "quality_grade": option_ctx.get("quality_grade") if chain_ready else None,
        "blockers": option_ctx.get("blockers") if chain_ready else [],
        "hard_blockers": option_ctx.get("hard_blockers") if chain_ready else [],
        "suggested_contract": suggested,
        "gates": gates,
        "upstox_option": option_ctx,
        "reason": reason,
    })


@routes.get("/api/options/queue/status")
async def options_queue_status(request):
    """Inspect the smart option-chain refresh queue."""
    redis = request.app["redis"]
    raw = await redis.get("infusion:option-chain-queue:status")
    if raw:
        try:
            text = raw.decode() if isinstance(raw, bytes) else raw
            status = json.loads(text)
        except Exception:
            status = {"enabled": True, "error": "status_decode_failed"}
    else:
        status = {"enabled": True, "state": "waiting_for_first_cycle"}

    cursor = 0
    recent = []
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="infusion:option-chain-last-refresh:*", count=100)
        if keys:
            pipe = redis.pipeline(transaction=False)
            for key in keys:
                pipe.get(key)
            rows = await pipe.execute()
            for raw_row in rows:
                try:
                    text = raw_row.decode() if isinstance(raw_row, bytes) else raw_row
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        recent.append(payload)
                except Exception:
                    continue
        if cursor == 0:
            break
    recent.sort(key=lambda x: str(x.get("refreshed_at") or ""), reverse=True)
    status["recent"] = recent[:30]
    return web.json_response(status)
