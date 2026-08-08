"""OpenAI advisory endpoints.

AI is an optional explanation layer. All market facts and decisions originate
from deterministic Redis-backed Infusion data.
"""

from __future__ import annotations

import hashlib
import json

from aiohttp import web

from api import ai_query
from api.ai_advisor import snapshot_digest
from api.signal_snapshot import build_symbol_snapshot, decode_hash

routes = web.RouteTableDef()

# Kept as thin aliases -- this module's own route handlers below were
# written against these names before the Phase 12 extraction into
# api/signal_snapshot.py (shared with ai_query.py). Not removed to avoid
# touching every call site for a pure rename.
_decode_hash = decode_hash


async def _snapshot(request: web.Request, symbol: str) -> dict:
    return await build_symbol_snapshot(request.app["redis"], symbol)


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


@routes.post("/api/ai/query")
async def ai_query_route(request):
    """Phase 12: free-text question -> grounded answer over live Infusion
    state. See api/ai_query.py for the intent router + fact gathering and
    api/ai_advisor.py's answer_query() for the optional LLM phrasing pass.
    Works with no OpenAI key configured -- returns the deterministic answer
    directly (source: "deterministic"), same degrade path as /api/ai/analyze.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, web.HTTPBadRequest):
        body = {}
    question = str(body.get("question") or "").strip()
    if not question:
        return web.json_response({"error": "question_required"}, status=400)
    if len(question) > 500:
        return web.json_response({"error": "question_too_long_max_500_chars"}, status=400)

    redis = request.app["redis"]
    pool = request.app.get("pg_pool")

    digest = hashlib.sha256(question.strip().lower().encode()).hexdigest()[:20]
    cache_key = f"infusion:ai:query:{digest}"
    cached = await redis.get(cache_key)
    if cached:
        data = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
        data["cached"] = True
        return web.json_response(data)

    known_symbols = await ai_query.load_known_symbols(redis)
    intents = ai_query.classify_intents(question, known_symbols)[:6]

    # Local import: routes/market.py pulls in the heavier Upstox-auth
    # import chain (aiohttp session helpers, instrument-key resolution)
    # that only matters when a question actually needs options analytics.
    from api.routes.market import compute_options_chain_analytics

    facts = [
        await ai_query.gather_facts(intent, redis=redis, pool=pool, options_chain_fn=compute_options_chain_analytics)
        for intent in intents
    ]
    deterministic_answer = ai_query.format_facts_as_text(question, intents, facts)
    intent_types = [i["type"] for i in intents]

    advisor = request.app["openai_advisor"]
    if not intents or not advisor.enabled:
        result = {
            "answer": deterministic_answer,
            "data_sources_used": intent_types,
            "caveats": [] if intents else ["No matching data source found for this question."],
            "source": "deterministic",
            "model": "",
        }
    else:
        try:
            result = await advisor.answer_query(question, facts, deterministic_answer)
        except Exception as exc:
            result = {
                "answer": deterministic_answer,
                "data_sources_used": intent_types,
                "caveats": [f"AI phrasing unavailable, showing the deterministic answer: {str(exc)[:120]}"],
                "source": "deterministic_fallback",
                "model": "",
            }

    result.update({
        "question": question,
        "intents_matched": intent_types,
        "deterministic_answer": deterministic_answer,
        "facts": facts,
        "cached": False,
        "advisory_only": True,
    })
    # Short TTL -- unlike the per-symbol advisory's 5-minute cache, this
    # covers live-changing aggregate state (sectors, regime, active
    # signals) that shouldn't go stale for long if asked again soon.
    await redis.set(cache_key, json.dumps(result, default=str), ex=45)
    return web.json_response(result)
