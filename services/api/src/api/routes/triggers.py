"""Manual spot-price trigger routes.

Triggers answer: "If this stock crosses this spot price, should I look green?"
They are evaluated from live tick + scanner intelligence, then can emit a
Telegram alert through the existing alerter stream.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from aiohttp import web
from infusion_models.events import EventType
from infusion_streams.codec import encode_event
from infusion_streams.constants import MAXLEN_SIGNALS, STREAM_SCAN_SIGNALS

routes = web.RouteTableDef()

KEY_TRIGGERS = "infusion:price-triggers"
KEY_TRIGGER_ALERT_PREFIX = "infusion:price-trigger-alert:"
Payload = dict[str, Any]


def _decode_hash(data: Payload) -> Payload:
    out: Payload = {}
    for k, v in data.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        try:
            out[kk] = json.loads(vv)
        except Exception:
            out[kk] = vv
    return out


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


async def _hash(redis: Any, key: str) -> Payload:
    data = await redis.hgetall(key)
    out: Payload = {}
    for k, v in data.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        try:
            out[kk] = float(vv)
        except Exception:
            out[kk] = vv
    return out


async def _scanner_snapshot(redis: Any, symbol: str) -> Payload:
    tick = await _hash(redis, f"infusion:tick:{symbol}")
    feature = await _hash(redis, f"infusion:feature:{symbol}")
    signal = await _hash(redis, f"infusion:signal:{symbol}")
    return {**feature, **tick, **signal}


def _trigger_state(trigger: Payload, snap: Payload) -> Payload:
    symbol = trigger["symbol"]
    trigger_price = _num(trigger.get("trigger_price"))
    direction = str(trigger.get("direction") or "above").lower()
    action = str(trigger.get("action") or ("BUY CE" if direction == "above" else "BUY PE")).upper()
    ltp = _num(snap.get("ltp"))
    if ltp <= 0 or trigger_price <= 0:
        return {**trigger, "state": "WAIT", "color": "yellow", "reason": "Waiting for live LTP"}

    distance_pct = (ltp - trigger_price) / trigger_price * 100
    crossed = ltp >= trigger_price if direction == "above" else ltp <= trigger_price
    near = abs(distance_pct) <= _num(trigger.get("near_pct"), 0.15)
    anti_ok = str(snap.get("anti_chase_ok", "1")) not in {"0", "false", "False"}
    vwap_state = str(snap.get("vwap_state") or "").upper()
    mtf_text = str(snap.get("mtf_text") or "")
    opt = _num(snap.get("option_readiness") or snap.get("conviction_score"))

    if crossed and anti_ok:
        state, color = "TRIGGERED", "green"
        reason = f"{symbol} crossed {direction} {trigger_price:.2f}; {action} watch"
    elif crossed:
        state, color = "CHASE_WAIT", "yellow"
        reason = "Crossed, but anti-chase filter says wait"
    elif near:
        state, color = "NEAR", "yellow"
        reason = f"Near trigger ({distance_pct:+.2f}%)"
    else:
        state, color = (
            "WAIT",
            "red"
            if (direction == "above" and distance_pct < -1.0)
            or (direction == "below" and distance_pct > 1.0)
            else "yellow",
        )
        reason = f"Waiting for {direction} {trigger_price:.2f}"

    return {
        **trigger,
        "ltp": round(ltp, 2),
        "distance_pct": round(distance_pct, 3),
        "crossed": crossed,
        "near": near,
        "state": state,
        "color": color,
        "reason": reason,
        "option_readiness": round(opt, 1),
        "anti_chase_ok": anti_ok,
        "vwap_state": vwap_state,
        "mtf_text": mtf_text,
        "evaluated_at": int(time.time()),
    }


async def _emit_trigger_alert(redis: Any, evaluated: Payload) -> bool:
    trigger_id = evaluated["trigger_id"]
    if evaluated.get("state") != "TRIGGERED" or not evaluated.get("telegram", True):
        return False
    cooldown_key = f"{KEY_TRIGGER_ALERT_PREFIX}{trigger_id}"
    if await redis.exists(cooldown_key):
        return False

    symbol = evaluated["symbol"]
    direction = str(evaluated.get("direction") or "above")
    action = str(evaluated.get("action") or "WATCH")
    ltp = _num(evaluated.get("ltp"))
    trigger_price = _num(evaluated.get("trigger_price"))
    stop = _num(evaluated.get("sl")) or (
        trigger_price * 0.995 if direction == "above" else trigger_price * 1.005
    )
    t1 = _num(evaluated.get("t1")) or (
        trigger_price * 1.006 if direction == "above" else trigger_price * 0.994
    )
    t2 = _num(evaluated.get("t2")) or (
        trigger_price * 1.012 if direction == "above" else trigger_price * 0.988
    )
    now_us = int(time.time() * 1_000_000)
    payload = {
        "signal_id": f"trigger-{trigger_id}-{int(time.time())}",
        "symbol": symbol,
        "strategy_id": "price_trigger",
        "signal_type": "price_trigger",
        "option_bias": action,
        "conviction_score": max(70, _num(evaluated.get("option_readiness"), 70)),
        "conviction_grade": "A",
        "risk_reward_ratio": 2.0,
        "sector_id": "",
        "sector_strength": 0,
        "market_regime": "",
        "entry_price": ltp,
        "invalidation_price": stop,
        "target_price": t1,
        "created_at_us": now_us,
        "features_snapshot": {
            "t1_price": t1,
            "t2_price": t2,
            "anti_chase_ok": evaluated.get("anti_chase_ok"),
            "mtf_text": evaluated.get("mtf_text"),
            "trigger_price": trigger_price,
            "trigger_direction": direction,
        },
        "explanation": [
            f"Manual trigger: {direction.upper()} {trigger_price:.2f}",
            evaluated.get("reason", ""),
            f"VWAP {evaluated.get('vwap_state') or 'NA'}",
            evaluated.get("mtf_text") or "MTF pending",
        ],
    }
    await redis.xadd(
        STREAM_SCAN_SIGNALS,
        {"data": encode_event(EventType.SCAN_SIGNAL, payload, now_us)},
        maxlen=MAXLEN_SIGNALS,
        approximate=True,
    )
    await redis.set(cooldown_key, "1", ex=int(_num(evaluated.get("cooldown_sec"), 900)))
    return True


@routes.get("/api/triggers")
async def list_triggers(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    raw = _decode_hash(await redis.hgetall(KEY_TRIGGERS))
    out: list[Payload] = []
    for trigger in raw.values():
        if isinstance(trigger, dict) and trigger.get("enabled", True):
            snap = await _scanner_snapshot(redis, trigger.get("symbol", ""))
            evaluated = _trigger_state(trigger, snap)
            evaluated["alert_emitted"] = await _emit_trigger_alert(redis, evaluated)
            out.append(evaluated)
    out.sort(key=lambda x: (x.get("color") != "green", x.get("symbol", "")))
    return web.json_response({"count": len(out), "triggers": out})


@routes.post("/api/triggers")
async def create_trigger(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    body: Payload = await request.json()
    symbol = str(body.get("symbol") or "").upper().strip()
    trigger_price = _num(body.get("trigger_price"))
    if not symbol or trigger_price <= 0:
        return web.json_response({"error": "symbol and trigger_price are required"}, status=400)
    direction = str(body.get("direction") or "above").lower()
    if direction not in {"above", "below"}:
        return web.json_response({"error": "direction must be above or below"}, status=400)
    trigger_id = body.get("trigger_id") or uuid.uuid4().hex[:10]
    payload: Payload = {
        "trigger_id": trigger_id,
        "symbol": symbol,
        "trigger_price": trigger_price,
        "direction": direction,
        "action": str(
            body.get("action") or ("BUY CE" if direction == "above" else "BUY PE")
        ).upper(),
        "near_pct": _num(body.get("near_pct"), 0.15),
        "sl": _num(body.get("sl")),
        "t1": _num(body.get("t1")),
        "t2": _num(body.get("t2")),
        "telegram": bool(body.get("telegram", True)),
        "enabled": True,
        "created_at": int(time.time()),
    }
    await redis.hset(KEY_TRIGGERS, trigger_id, json.dumps(payload, separators=(",", ":")))
    return web.json_response(payload)


@routes.delete("/api/triggers/{trigger_id}")
async def delete_trigger(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    trigger_id = request.match_info["trigger_id"]
    removed = await redis.hdel(KEY_TRIGGERS, trigger_id)
    return web.json_response({"deleted": bool(removed), "trigger_id": trigger_id})
