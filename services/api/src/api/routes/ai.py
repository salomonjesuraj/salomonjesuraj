"""OpenAI advisory endpoints.

AI is an optional explanation layer. All market facts and decisions originate
from deterministic Redis-backed Infusion data.
"""

from __future__ import annotations

import json

from aiohttp import web

from api.ai_advisor import snapshot_digest

routes = web.RouteTableDef()


def _decode_hash(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        key = key.decode() if isinstance(key, bytes) else key
        value = value.decode() if isinstance(value, bytes) else value
        if value in {"True", "False"}:
            out[key] = value == "True"
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            try:
                out[key] = json.loads(value) if value and value[0] in "[{" else value
            except (json.JSONDecodeError, IndexError):
                out[key] = value
    return out


async def _first_signal(redis, symbol: str) -> dict:
    direct = await redis.hgetall(f"infusion:signal:{symbol}")
    if direct:
        return _decode_hash(direct)
    members = await redis.zrevrange("infusion:signals:active", 0, -1)
    for raw in members:
        member = raw.decode() if isinstance(raw, bytes) else raw
        if member.split(":", 1)[0] == symbol:
            data = await redis.hgetall(f"infusion:signal:{member}")
            if data:
                return _decode_hash(data)
    return {}


async def _snapshot(request: web.Request, symbol: str) -> dict:
    redis = request.app["redis"]
    tick_raw, feature_raw, prebreak_raw, regime_raw, signal = await _snapshot_reads(redis, symbol)
    tick = _decode_hash(tick_raw)
    features = _decode_hash(feature_raw)
    prebreak = _decode_hash(prebreak_raw)
    regime = _decode_hash(regime_raw)

    sector_id = str(tick.get("sector_id") or "")
    sector = {}
    if sector_id:
        sector = _decode_hash(await redis.hgetall(f"infusion:sector:{sector_id}"))

    bias = (
        signal.get("option_bias")
        or ("BUY CE" if features.get("change_pct", 0) > 0 else "BUY PE")
    )
    option_chain_ready = bool(
        await redis.exists(f"infusion:option-chain:{symbol}")
        or await redis.exists(f"infusion:options:{symbol}")
    )

    return {
        "symbol": symbol,
        "scanner": {
            "decision": bias,
            "signal_type": signal.get("signal_type", ""),
            "conviction_score": signal.get("conviction_score", 0),
            "conviction_grade": signal.get("conviction_grade", ""),
            "entry_price": signal.get("entry_price", 0),
            "invalidation_price": signal.get("invalidation_price", 0),
            "target_price": signal.get("target_price", 0),
            "conditions_met": signal.get("conditions_met", {}),
            "explanation": signal.get("explanation", ""),
        },
        "market": {
            "ltp": tick.get("ltp", features.get("ltp", 0)),
            "change_pct": tick.get("change_pct", features.get("change_pct", 0)),
            "sector": sector_id,
            "regime": regime.get("regime", "neutral"),
        },
        "technical": {
            key: features.get(key)
            for key in (
                "vwap", "ema_5", "ema_9", "ema_20", "ema_50",
                "rsi_14", "macd", "macd_signal", "macd_hist",
                "rel_vol_20d", "bb_width", "squeeze_state", "nr_pattern",
                "candle_pattern", "atr_14", "atr_trail_stop", "atr_trend",
                "spread_bps", "order_imbalance",
            )
        },
        "setup": {
            "state": prebreak.get("state", ""),
            "readiness_score": prebreak.get("readiness_score", 0),
            "sector_strength": sector.get("strength_score", 0),
            "sector_trend": sector.get("trend", ""),
        },
        "option_execution": {
            "chain_ready": option_chain_ready,
            "oi_available": False,
            "iv_available": False,
            "contract_spread_available": False,
            "selected_strike_available": False,
        },
    }


async def _snapshot_reads(redis, symbol: str):
    pipe = redis.pipeline()
    pipe.hgetall(f"infusion:tick:{symbol}")
    pipe.hgetall(f"infusion:feature:{symbol}")
    pipe.hgetall(f"infusion:prebreak:{symbol}")
    pipe.hgetall("infusion:regime")
    tick, feature, prebreak, regime = await pipe.execute()
    signal = await _first_signal(redis, symbol)
    return tick, feature, prebreak, regime, signal


def _fallback(snapshot: dict, reason: str = "") -> dict:
    scanner = snapshot["scanner"]
    technical = snapshot["technical"]
    execution = snapshot["option_execution"]
    decision = str(scanner.get("decision") or "HOLD")
    why = []
    blockers = []

    if technical.get("atr_trend") in {"BULL", "BEAR"}:
        why.append(f"ATR trend is {str(technical['atr_trend']).lower()}")
    if float(technical.get("rel_vol_20d") or 0) >= 1.5:
        why.append(f"Relative volume is {float(technical['rel_vol_20d']):.1f}x")
    if technical.get("squeeze_state") in {"EXTREME", "COILED", "BUILDING"}:
        why.append(f"Price compression is {str(technical['squeeze_state']).lower()}")
    if not execution["chain_ready"]:
        blockers.append("Option chain is pending")
    if not execution["oi_available"]:
        blockers.append("OI and OI-change confirmation are unavailable")
    if not execution["contract_spread_available"]:
        blockers.append("Contract bid/ask liquidity is not confirmed")

    entry = float(scanner.get("entry_price") or 0)
    invalidation = float(scanner.get("invalidation_price") or 0)
    target = float(scanner.get("target_price") or 0)
    verdict = "WATCH" if decision in {"BUY CE", "BUY PE"} else "AVOID"
    return {
        "verdict": verdict,
        "summary": (
            f"{decision} is the deterministic underlying bias. "
            "Wait for contract-level option confirmation before entry."
        ),
        "why_trade": why[:4],
        "blockers": blockers[:4],
        "trigger": f"Underlying trigger: ₹{entry:,.2f}" if entry else "Use the deterministic scanner trigger.",
        "invalidation": (
            f"Underlying invalidation: ₹{invalidation:,.2f}"
            if invalidation else "Use the scanner invalidation level."
        ),
        "option_view": (
            "Do not select a contract until strike, IV, OI and spread are live."
            if not execution["chain_ready"] else "Confirm contract liquidity before entry."
        ),
        "risk_note": (
            f"Underlying target reference is ₹{target:,.2f}; manage the actual trade by option premium."
            if target else "Manage risk using actual option premium, not estimated values."
        ),
        "source": "deterministic_fallback",
        "model": "",
        "fallback_reason": reason,
    }


@routes.get("/api/ai/status")
async def ai_status(request):
    advisor = request.app["openai_advisor"]
    return web.json_response({
        "enabled": advisor.enabled,
        "model": advisor.model if advisor.enabled else "",
        "role": "advisory_only",
        "scanner_authority": "deterministic",
    })


@routes.post("/api/ai/analyze/{symbol}")
async def analyze_symbol(request):
    symbol = request.match_info["symbol"].upper().strip()
    if not symbol:
        return web.json_response({"error": "symbol_required"}, status=400)
    try:
        body = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        body = {}
    mode = str(body.get("mode") or "explain").lower()
    if mode not in {"explain", "risk"}:
        return web.json_response({"error": "mode_must_be_explain_or_risk"}, status=400)

    snapshot = await _snapshot(request, symbol)
    if not snapshot["market"]["ltp"]:
        return web.json_response({"error": f"No live data for {symbol}"}, status=404)

    advisor = request.app["openai_advisor"]
    digest = snapshot_digest(snapshot, mode)
    # Stable five-minute cache per action. Live scanner data remains fresh;
    # repeated button clicks do not create unnecessary model calls.
    cache_key = f"infusion:ai:{symbol}:{mode}"
    cached = await request.app["redis"].get(cache_key)
    if cached:
        data = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
        data["cached"] = True
        return web.json_response(data)

    if not advisor.enabled:
        result = _fallback(snapshot, "OpenAI API key is not configured")
    else:
        try:
            result = await advisor.analyze(snapshot, mode)
        except Exception as exc:
            result = _fallback(snapshot, str(exc)[:160])

    result.update({
        "symbol": symbol,
        "mode": mode,
        "cached": False,
        "advisory_only": True,
        "snapshot_digest": digest,
    })
    await request.app["redis"].set(
        cache_key,
        json.dumps(result),
        ex=request.app["config"].openai_cache_ttl_sec,
    )
    return web.json_response(result)
