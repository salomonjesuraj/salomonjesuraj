"""Scanner routes — signals, pre-breakout watchlist, sectors, regime, alerts."""

import time
import uuid
import json as _json
from aiohttp import web
from infusion_models.events import EventType
from infusion_streams.codec import decode_event, encode_event
from infusion_streams.constants import STREAM_SCAN_SIGNALS, STREAM_SCAN_SUPPRESSED, MAXLEN_SIGNALS

routes = web.RouteTableDef()

PREBREAK_PREFIX = "infusion:prebreak:"
SIGNAL_PREFIX = "infusion:signal:"
SIGNAL_ACTIVE_KEY = "infusion:signals:active"
SECTOR_PREFIX = "infusion:sector:"
REGIME_KEY = "infusion:regime"
ALERT_LOG_KEY = "infusion:alert:log"
ALERT_MUTE_SYMBOLS_KEY = "infusion:alert:mute:symbols"
ALERT_MUTE_STRATEGIES_KEY = "infusion:alert:mute:strat"
ALERT_RATE_KEY = "infusion:alert:rate"
ALERT_BURST_KEY = "infusion:alert:burst"
RISK_KEY = "infusion:risk:settings"

# Fields that are stored as JSON strings and must be parsed
_JSON_FIELDS = {
    "conditions_met", "sub_scores", "features_snapshot",
    "mtf", "mtf_dots", "anti_chase_reasons", "rejection_reasons",
}
# Fields that should stay as strings (not coerced to float)
_STRING_FIELDS = {
    "signal_id", "symbol", "strategy_id", "signal_type", "lifecycle",
    "conviction_grade", "option_bias", "sector_id", "market_regime", "pre_breakout_state",
    "explanation", "conditions_met", "sub_scores", "state", "prev_state",
    "transition_reason", "trend", "regime", "reason", "mtf_text",
}


def _test_alert_payload(body: dict | None = None) -> tuple[dict, int]:
    body = body if isinstance(body, dict) else {}
    symbol = str(body.get("symbol") or "INFUSION_TEST").upper().strip()[:24] or "INFUSION_TEST"
    now_us = int(time.time() * 1_000_000)
    entry = float(body.get("entry") or 1234.50)
    stop = float(body.get("stop") or entry * 0.988)
    t1 = float(body.get("target1") or entry * 1.018)
    t2 = float(body.get("target2") or entry * 1.034)
    side = str(body.get("side") or "BUY CE").upper()
    if side not in {"BUY CE", "BUY PE"}:
        side = "BUY CE"

    return {
        "signal_id": f"test_{uuid.uuid4()}",
        "symbol": symbol,
        "strategy_id": "telegram_test",
        "signal_type": "test_alert",
        "option_bias": side,
        "conviction_score": 88,
        "conviction_grade": "A+",
        "risk_reward_ratio": 1.8,
        "sector_id": "TEST",
        "sector_strength": 70,
        "market_regime": "paper_test",
        "entry_price": entry,
        "invalidation_price": stop,
        "target_price": t1,
        "created_at_us": now_us,
        "features_snapshot": {
            "t1_price": t1,
            "t2_price": t2,
            "bull_confidence": 82 if side != "BUY PE" else 34,
            "bear_confidence": 78 if side == "BUY PE" else 36,
            "trade_horizon": "TEST_ONLY",
            "chase_quality": "FORMAT_TEST",
            "sustain_rule": "No trade - Telegram delivery test only",
            "mtf_dots": {"1M": "G", "5M": "G", "15M": "Y", "1H": "Y", "4H": "Y", "1D": "G"},
            "mtf_text": "Telegram format test - not a trading signal",
            "primary_trade_map": {
                "side": side,
                "entry": entry,
                "stop_loss": stop,
                "target_1": t1,
                "target_2": t2,
                "rr1": 1.8,
                "sustain_rule": "No trade - test only",
            },
            "breakout_explanation": "This is a controlled Telegram formatting/delivery test. Do not trade.",
            "anti_chase_ok": False,
            "anti_chase_reasons": ["Test alert only"],
            "rejection_reasons": ["Not a live market setup", "No order execution allowed"],
        },
        "explanation": [
            "Telegram bot delivery test",
            "Verifies entry/SL/T1/T2, MTF, trade-map, option-chain, and risk text",
            "Not archived into precision statistics",
        ],
    }, now_us


def _test_alert_preview(payload: dict) -> dict:
    fs = payload.get("features_snapshot") or {}
    trade_map = fs.get("primary_trade_map") or {}
    return {
        "symbol": payload.get("symbol"),
        "side": payload.get("option_bias"),
        "grade": payload.get("conviction_grade"),
        "score": payload.get("conviction_score"),
        "entry": payload.get("entry_price"),
        "sl": payload.get("invalidation_price"),
        "t1": fs.get("t1_price"),
        "t2": fs.get("t2_price"),
        "rr": trade_map.get("rr1") or payload.get("risk_reward_ratio"),
        "horizon": fs.get("trade_horizon"),
        "chase": fs.get("chase_quality"),
        "mtf_text": fs.get("mtf_text"),
        "sustain_rule": fs.get("sustain_rule"),
        "safety": "TEST ONLY - no trade/order, skipped from precision stats",
    }


@routes.get("/api/signal-diagnostics/{symbol}")
async def get_signal_diagnostics(request):
    symbol = request.match_info["symbol"].upper()
    data = await request.app["redis"].hgetall(f"infusion:signal-diagnostics:{symbol}")
    if not data:
        return web.json_response({"error": "No completed-candle evaluation yet"}, status=404)
    decoded = {}
    for key, value in data.items():
        key = key.decode() if isinstance(key, bytes) else key
        value = value.decode() if isinstance(value, bytes) else value
        if key == "gates":
            decoded[key] = _json.loads(value)
        elif key in {"candidate", "evaluated_at_us"}:
            decoded[key] = int(value)
        else:
            decoded[key] = value
    return web.json_response(decoded)


def _decode_hash(data: dict) -> dict:
    """Decode Redis hash bytes to typed Python dict.

    - JSON fields → parsed dict/list
    - Numeric strings → float
    - Everything else → str
    """
    result = {}
    for k, v in data.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        if kk in _JSON_FIELDS:
            try:
                result[kk] = _json.loads(vv)
            except (_json.JSONDecodeError, TypeError):
                result[kk] = {}
        elif kk not in _STRING_FIELDS:
            try:
                result[kk] = float(vv)
            except (ValueError, TypeError):
                result[kk] = vv
        else:
            result[kk] = vv
    return result


@routes.get("/api/signals")
async def get_signals(request):
    """List active signals from the active ZSET, ordered by conviction."""
    redis = request.app["redis"]

    # Get all active signal members (symbol:strategy) scored by conviction
    members = await redis.zrevrange(SIGNAL_ACTIVE_KEY, 0, -1, withscores=True)
    signals = []
    for member, score in members:
        m = member.decode() if isinstance(member, bytes) else member
        symbol = m.split(":")[0] if ":" in m else m
        key = f"{SIGNAL_PREFIX}{m}" if ":" in m else f"{SIGNAL_PREFIX}{symbol}"
        data = await redis.hgetall(key)
        if not data and ":" in m:
            data = await redis.hgetall(f"{SIGNAL_PREFIX}{symbol}")
        if data:
            entry = _decode_hash(data)
            entry["symbol"] = symbol
            entry["active_score"] = score
            signals.append(entry)

    return web.json_response({"count": len(signals), "signals": signals})


def _compact_suppressed(payload: dict, stream_id: str = "") -> dict:
    """Compact suppressed signal payload for dashboard diagnostics."""
    fs = payload.get("features_snapshot") or {}
    if not isinstance(fs, dict):
        fs = {}
    explanation = payload.get("explanation") or []
    if isinstance(explanation, str):
        explanation = [x.strip() for x in explanation.split("|") if x.strip()]
    if not isinstance(explanation, list):
        explanation = []

    side = fs.get("option_bias") or ("BUY PE" if payload.get("signal_type") == "bearish" else "BUY CE")
    return {
        "id": stream_id,
        "signal_id": payload.get("signal_id", ""),
        "symbol": payload.get("symbol", ""),
        "strategy_id": payload.get("strategy_id", ""),
        "signal_type": payload.get("signal_type", ""),
        "side": side,
        "grade": payload.get("conviction_grade", ""),
        "score": payload.get("conviction_score", 0),
        "reason": payload.get("suppression_reason", ""),
        "entry": payload.get("entry_price", 0),
        "stop": payload.get("invalidation_price", 0),
        "target": payload.get("target_price", 0),
        "rr": payload.get("risk_reward_ratio", 0),
        "sector": payload.get("sector_id", ""),
        "sector_strength": payload.get("sector_strength", 0),
        "regime": payload.get("market_regime", ""),
        "pre_breakout_state": payload.get("pre_breakout_state", ""),
        "created_at_us": payload.get("created_at_us", 0),
        "ltp": payload.get("price_at_signal", fs.get("ltp", 0)),
        "positive_above": fs.get("positive_above", payload.get("entry_price", 0)),
        "negative_below": fs.get("negative_below", payload.get("invalidation_price", 0)),
        "bull_confidence": fs.get("bull_confidence", 0),
        "bear_confidence": fs.get("bear_confidence", 0),
        "mtf_score": fs.get("mtf_score", fs.get("pine_confidence", 0)),
        "mtf_text": fs.get("mtf_text", ""),
        "anti_chase_ok": fs.get("anti_chase_ok", True),
        "anti_chase_reasons": fs.get("anti_chase_reasons", []),
        "rejection_reasons": fs.get("rejection_reasons", []),
        "explanation": explanation[:6],
    }


def _trading_mode_profile(mode: str) -> dict:
    mode = str(mode or "precision")
    profiles = {
        "precision": {
            "label": "Precision Mode",
            "watch_score": 70,
            "paper_score": 80,
            "min_rr": 1.6,
            "note": "Strict alert-quality view. Fewer candidates, highest caution.",
        },
        "opportunity": {
            "label": "Opportunity Mode",
            "watch_score": 55,
            "paper_score": 72,
            "min_rr": 1.4,
            "note": "Shows more watch ideas. Telegram/live alerts remain strict.",
        },
        "aggressive_paper": {
            "label": "Aggressive Paper Mode",
            "watch_score": 45,
            "paper_score": 62,
            "min_rr": 1.2,
            "note": "Learning mode. Use paper journal only; no live execution assumption.",
        },
    }
    return profiles.get(mode, profiles["precision"])


def _candidate_action(row: dict, profile: dict) -> dict:
    score = float(row.get("score") or 0)
    rr = float(row.get("rr") or 0)
    reason = str(row.get("reason") or "")
    anti_reasons = row.get("anti_chase_reasons") or []
    rejection = row.get("rejection_reasons") or []
    blockers = len(anti_reasons) + len(rejection)
    hard_chase = any(
        any(term in str(x).lower() for term in ["large signal candle", "vwap stretch", "stop too wide"])
        for x in [*anti_reasons, *rejection]
    )

    if reason in {"cooldown_active", "duplicate_active"} and score >= profile["paper_score"]:
        return {"action": "DUPLICATE_WAIT", "tone": "warn", "why": "Good candidate, but already in cooldown/duplicate gate."}
    if score >= profile["paper_score"] and rr >= profile["min_rr"] and not hard_chase:
        return {"action": "PAPER_READY", "tone": "good", "why": "Meets paper-ready threshold for selected mode."}
    if score >= profile["watch_score"] and rr >= profile["min_rr"]:
        return {"action": "WATCH_ONLY", "tone": "watch", "why": "Worth watching, but still blocked for live alert discipline."}
    if hard_chase:
        return {"action": "NO_CHASE", "tone": "block", "why": "Rejected by anti-chase location/risk rules."}
    if blockers >= 4:
        return {"action": "AVOID", "tone": "block", "why": "Too many blockers at current location."}
    return {"action": "AVOID", "tone": "block", "why": "Below selected-mode opportunity threshold."}


async def _load_trading_mode(redis) -> str:
    raw = await redis.get(RISK_KEY)
    if not raw:
        return "precision"
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        data = _json.loads(text)
        mode = str(data.get("trading_signal_mode") or "precision")
        return mode if mode in {"precision", "opportunity", "aggressive_paper"} else "precision"
    except Exception:
        return "precision"


@routes.get("/api/signals/suppressed")
async def get_suppressed_signals(request):
    """Recent suppressed scanner candidates for zero-signal investigation."""
    redis = request.app["redis"]
    try:
        limit = min(max(int(request.query.get("limit", "80")), 1), 200)
    except ValueError:
        limit = 80

    mode = await _load_trading_mode(redis)
    profile = _trading_mode_profile(mode)
    raw_entries = await redis.xrevrange(STREAM_SCAN_SUPPRESSED, count=limit)
    rows = []
    reason_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    for stream_id, fields in raw_entries:
        sid = stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)
        data = fields.get(b"data") or fields.get("data")
        if not data:
            continue
        try:
            _, _, _, _, payload = decode_event(data)
        except Exception:
            continue
        row = _compact_suppressed(payload, sid)
        row.update(_candidate_action(row, profile))
        rows.append(row)
        reason = str(row.get("reason") or "unknown")
        strategy = str(row.get("strategy_id") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

    return web.json_response({
        "mode": mode,
        "profile": profile,
        "count": len(rows),
        "suppressed": rows,
        "reason_counts": reason_counts,
        "strategy_counts": strategy_counts,
    })


@routes.get("/api/signals/{symbol}")
async def get_signal(request):
    """Get latest signal for a specific symbol."""
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]

    data = await redis.hgetall(f"{SIGNAL_PREFIX}{symbol}")
    if not data:
        return web.json_response(
            {"error": f"No active signal for {symbol}"}, status=404
        )

    result = _decode_hash(data)
    result["symbol"] = symbol
    return web.json_response(result)


@routes.get("/api/prebreakout")
async def get_prebreakout(request):
    """List symbols in pre-breakout states (watchlist).

    Returns only COMPRESSING, ACCUMULATING, COILED symbols.
    Ordered by readiness_score descending.
    """
    redis = request.app["redis"]

    # SCAN for prebreak keys
    cursor = 0
    watchlist = []
    while True:
        cursor, keys = await redis.scan(
            cursor=cursor, match=f"{PREBREAK_PREFIX}*", count=100
        )
        for key in keys:
            data = await redis.hgetall(key)
            if not data:
                continue
            kk = key.decode() if isinstance(key, bytes) else key
            symbol = kk.replace(PREBREAK_PREFIX, "")
            entry = _decode_hash(data)
            entry["symbol"] = symbol

            # Enrich with signal intelligence if available
            sig_data = await redis.hgetall(f"{SIGNAL_PREFIX}{symbol}")
            if sig_data:
                sig = _decode_hash(sig_data)
                entry["conviction_score"] = sig.get("conviction_score", 0)
                entry["conviction_grade"] = sig.get("conviction_grade", "")
                entry["entry_price"] = sig.get("entry_price", 0)
                entry["invalidation_price"] = sig.get("invalidation_price", 0)
                entry["target_price"] = sig.get("target_price", 0)
                entry["signal_type"] = sig.get("signal_type", "")
                entry["has_signal"] = True
            else:
                entry["has_signal"] = False

            watchlist.append(entry)

        if cursor == 0:
            break

    # Sort by readiness_score descending
    watchlist.sort(key=lambda x: x.get("readiness_score", 0), reverse=True)

    return web.json_response({
        "count": len(watchlist),
        "watchlist": watchlist,
    })


def _decode_hash(data: dict) -> dict:
    """Decode Redis hash bytes to typed dict with JSON and numeric conversion."""
    result = {}
    for k, v in data.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        if kk in _JSON_FIELDS:
            try:
                result[kk] = _json.loads(vv)
            except (_json.JSONDecodeError, TypeError):
                result[kk] = [] if kk.endswith("_reasons") else {}
        elif kk not in _STRING_FIELDS:
            try:
                result[kk] = float(vv)
            except (ValueError, TypeError):
                result[kk] = vv
        else:
            result[kk] = vv
    return result


@routes.get("/api/sectors")
async def get_sectors(request):
    """List sector rankings with breadth, strength, and trend.

    Returns sectors ordered by rank (1 = strongest).
    """
    redis = request.app["redis"]

    cursor = 0
    sectors = []
    while True:
        cursor, keys = await redis.scan(
            cursor=cursor, match=f"{SECTOR_PREFIX}*", count=100
        )
        for key in keys:
            data = await redis.hgetall(key)
            if not data:
                continue
            entry = _decode_hash(data)
            sectors.append(entry)

        if cursor == 0:
            break

    # Sort by rank ascending (1 = strongest)
    sectors.sort(key=lambda x: x.get("rank", 999))

    return web.json_response({
        "count": len(sectors),
        "sectors": sectors,
    })


@routes.get("/api/regime")
async def get_regime(request):
    """Get current market regime state."""
    redis = request.app["redis"]

    data = await redis.hgetall(REGIME_KEY)
    if not data:
        return web.json_response({
            "regime": "neutral",
            "reason": "no_data",
        })

    return web.json_response(_decode_hash(data))


@routes.get("/api/alerts/log")
async def get_alert_log(request):
    """Get recent delivery log entries (last 50)."""
    redis = request.app["redis"]
    import json

    raw = await redis.lrange(ALERT_LOG_KEY, 0, 49)
    entries = []
    for item in raw:
        val = item.decode() if isinstance(item, bytes) else item
        try:
            entries.append(json.loads(val))
        except (json.JSONDecodeError, TypeError):
            entries.append({"raw": val})

    return web.json_response({"count": len(entries), "log": entries})


@routes.post("/api/alerts/test")
async def send_test_alert(request):
    """Publish a safe Telegram test alert.

    This deliberately uses signal_type=test_alert so the alerter can bypass
    cooldown/quality gates while the archiver skips it from precision stats.
    """
    redis = request.app["redis"]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    payload, now_us = _test_alert_payload(body)

    await redis.xadd(
        STREAM_SCAN_SIGNALS,
        {"data": encode_event(EventType.SCAN_SIGNAL, payload, now_us)},
        maxlen=MAXLEN_SIGNALS,
        approximate=True,
    )
    return web.json_response({
        "ok": True,
        "queued": True,
        "symbol": payload["symbol"],
        "signal_id": payload["signal_id"],
        "preview": _test_alert_preview(payload),
        "message": "Telegram test alert queued. Check Telegram and Alert Log.",
    })


@routes.get("/api/alerts/test/preview")
async def preview_test_alert(request):
    """Preview the safe Telegram test alert without sending anything."""
    payload, _ = _test_alert_payload({})
    return web.json_response({
        "ok": True,
        "send_required": False,
        "preview": _test_alert_preview(payload),
        "note": "Preview only. Use POST /api/alerts/test or the dashboard button to send.",
    })


@routes.get("/api/alerts/stats")
async def get_alert_stats(request):
    """Get delivery stats: rate, burst counters."""
    redis = request.app["redis"]

    rate_raw = await redis.get(ALERT_RATE_KEY)
    burst_raw = await redis.get(ALERT_BURST_KEY)
    rate_ttl = await redis.ttl(ALERT_RATE_KEY)
    burst_ttl = await redis.ttl(ALERT_BURST_KEY)
    log_len = await redis.llen(ALERT_LOG_KEY)

    rate = int(rate_raw) if rate_raw else 0
    burst = int(burst_raw) if burst_raw else 0

    return web.json_response({
        "hourly_sent": rate,
        "hourly_ttl_sec": rate_ttl,
        "burst_5min_sent": burst,
        "burst_5min_ttl_sec": burst_ttl,
        "delivery_log_entries": log_len,
    })


@routes.get("/api/alerts/mute")
async def get_muted(request):
    """List muted symbols and strategies."""
    redis = request.app["redis"]

    symbols = await redis.smembers(ALERT_MUTE_SYMBOLS_KEY)
    strategies = await redis.smembers(ALERT_MUTE_STRATEGIES_KEY)

    decode = lambda s: {(x.decode() if isinstance(x, bytes) else x) for x in s}
    return web.json_response({
        "muted_symbols": sorted(decode(symbols)),
        "muted_strategies": sorted(decode(strategies)),
    })


@routes.post("/api/alerts/mute/{symbol}")
async def mute_symbol(request):
    """Mute a symbol from receiving Telegram alerts."""
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]
    await redis.sadd(ALERT_MUTE_SYMBOLS_KEY, symbol)
    return web.json_response({"muted": symbol})


@routes.delete("/api/alerts/mute/{symbol}")
async def unmute_symbol(request):
    """Unmute a symbol to resume receiving Telegram alerts."""
    symbol = request.match_info["symbol"].upper()
    redis = request.app["redis"]
    await redis.srem(ALERT_MUTE_SYMBOLS_KEY, symbol)
    return web.json_response({"unmuted": symbol})
