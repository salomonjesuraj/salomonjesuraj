"""Safety cockpit for paper-first execution readiness."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import msgpack
from aiohttp import web

routes = web.RouteTableDef()

IST = timezone(timedelta(hours=5, minutes=30))
KILL_SWITCH_KEY = "infusion:safety:kill_switch"
RISK_KEY = "infusion:risk:settings"
JOURNAL_KEY = "infusion:journal:paper_trades"
STAGED_KEY = "infusion:execution:staged_tickets"
AUTH_KEY = "infusion:auth:upstox"
Payload = dict[str, Any]


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _market_session(now: datetime) -> str:
    if now.weekday() >= 5:
        return "closed_weekend"
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 15:
        return "pre_market"
    if minutes <= 15 * 60 + 30:
        return "open"
    return "post_market"


def _decode_json(raw: Any, default: Any = None) -> Any:
    if not raw:
        return default if default is not None else {}
    try:
        text = raw.decode() if isinstance(raw, bytes) else raw
        return json.loads(text)
    except Exception:
        return default if default is not None else {}


async def _load_list(redis: Any, key: str, limit: int = 200) -> list[Payload]:
    rows: list[Payload] = []
    raw_rows = await redis.lrange(key, 0, max(0, limit - 1))
    for raw in raw_rows:
        row = _decode_json(raw, {})
        if isinstance(row, dict):
            rows.append(row)
    return rows


async def _health(redis: Any, service: str) -> Payload:
    raw = await redis.get(f"infusion:health:{service}")
    if not raw:
        return {"status": "unhealthy", "reason": "no heartbeat"}
    try:
        payload = msgpack.unpackb(raw, raw=False)
        return payload if isinstance(payload, dict) else {"status": "unknown"}
    except Exception:
        return {"status": "unknown"}


async def _kill_state(redis: Any) -> Payload:
    raw = await redis.get(KILL_SWITCH_KEY)
    data = _decode_json(raw, {})
    return {
        "enabled": bool(data.get("enabled")),
        "reason": data.get("reason", ""),
        "updated_at_ist": data.get("updated_at_ist", ""),
    }


@routes.get("/api/safety/status")
async def safety_status(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    now = _now_ist()
    today = _today()
    session = _market_session(now)

    auth = cast(Payload, _decode_json(await redis.get(AUTH_KEY), {}))
    expiry_ts = int(auth.get("expiry_ts") or 0)
    token_valid = bool(expiry_ts and expiry_ts > int(time.time()))

    risk = cast(Payload, _decode_json(await redis.get(RISK_KEY), {}))
    risk_updated_today = str(risk.get("updated_at_ist", "")).startswith(today)
    auto_orders_enabled = bool(risk.get("auto_orders_enabled"))
    paper_first = (
        str(risk.get("execution_mode") or "paper_first") == "paper_first"
        and not auto_orders_enabled
    )

    ingestion = await _health(redis, "ingestion")
    normalizer = await _health(redis, "normalizer")
    feature_engine = await _health(redis, "feature-engine")
    api_health = await _health(redis, "api")
    last_tick_age_ms = int(ingestion.get("last_tick_age_ms", -1) or -1)
    tick_count = int(ingestion.get("tick_count", 0) or 0)
    data_fresh = tick_count > 0 and (session != "open" or 0 <= last_tick_age_ms < 20000)

    symbols_loaded = await redis.hlen("infusion:symbols")
    active_signals = await redis.zcard("infusion:signals:active")
    journal = await _load_list(redis, JOURNAL_KEY, 200)
    staged = await _load_list(redis, STAGED_KEY, 200)
    today_journal = [r for r in journal if str(r.get("created_at_ist", "")).startswith(today)]
    today_staged = [r for r in staged if str(r.get("created_at_ist", "")).startswith(today)]
    ready_tickets = [r for r in today_staged if r.get("status") == "READY_TO_STAGE"]
    blocked_tickets = [r for r in today_staged if r.get("status") == "BLOCKED"]
    physical_risk_rows = []
    for row in today_journal + today_staged:
        option_raw = row.get("option")
        option = option_raw if isinstance(option_raw, dict) else row
        if row.get("status") == "CLOSED":
            continue
        expiry_days = option.get("expiry_days")
        try:
            near_expiry = expiry_days is not None and float(expiry_days) <= 3
        except (TypeError, ValueError):
            near_expiry = False
        if bool(option.get("physical_settlement_block")) or near_expiry:
            physical_risk_rows.append(row)
    kill = await _kill_state(redis)

    gates = [
        {
            "key": "paper_first",
            "label": "Paper-first lock",
            "state": "pass" if paper_first else "block",
            "detail": "Live orders disabled" if paper_first else "Auto orders setting is unsafe",
        },
        {
            "key": "kill_switch",
            "label": "Kill switch",
            "state": "block" if kill["enabled"] else "pass",
            "detail": kill["reason"]
            or ("Manual block is ON" if kill["enabled"] else "Manual block is OFF"),
        },
        {
            "key": "token",
            "label": "Upstox token",
            "state": "pass" if token_valid else "block",
            "detail": f"Expires {auth.get('expiry_ist', '-')}"
            if token_valid
            else "Token missing/expired",
        },
        {
            "key": "data",
            "label": "Market data",
            "state": "pass"
            if data_fresh
            else "warn"
            if session != "open" and tick_count > 0
            else "block",
            "detail": f"{tick_count} ticks | age {last_tick_age_ms} ms | {session}",
        },
        {
            "key": "risk",
            "label": "Morning risk",
            "state": "pass" if risk_updated_today else "warn",
            "detail": f"Capital {risk.get('capital', '-')} | Risk/trade {risk.get('risk_per_trade_amount', '-')}",
        },
        {
            "key": "services",
            "label": "Services",
            "state": "pass"
            if all(
                s.get("status") == "healthy"
                for s in [ingestion, normalizer, feature_engine, api_health]
            )
            else "warn",
            "detail": f"Ingestion {ingestion.get('status')} | Feature {feature_engine.get('status')}",
        },
        {
            "key": "universe",
            "label": "F&O universe",
            "state": "pass" if symbols_loaded > 0 else "block",
            "detail": f"{symbols_loaded} symbols loaded",
        },
        {
            "key": "physical_settlement",
            "label": "Physical settlement",
            "state": "block" if physical_risk_rows else "pass",
            "detail": (
                f"{len(physical_risk_rows)} stock-option expiry-week risk flagged"
                if physical_risk_rows
                else "No expiry-week stock option risk flagged"
            ),
        },
    ]
    block_count = len([g for g in gates if g["state"] == "block"])
    warn_count = len([g for g in gates if g["state"] == "warn"])
    verdict = "PAPER_READY" if block_count == 0 else "BLOCKED"
    if verdict == "PAPER_READY" and warn_count:
        verdict = "WATCH_READY"

    return web.json_response(
        {
            "ok": True,
            "verdict": verdict,
            "session": session,
            "timestamp_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "paper_first": paper_first,
            "kill_switch": kill,
            "gates": gates,
            "counts": {
                "active_signals": active_signals,
                "journal_today": len(today_journal),
                "staged_today": len(today_staged),
                "ready_tickets": len(ready_tickets),
                "blocked_tickets": len(blocked_tickets),
            },
            "next_action": (
                "Do not trade. Clear blockers first."
                if block_count
                else "Paper trade only. Stage ticket, visually confirm in TradingView, then journal outcome."
            ),
        }
    )


@routes.post("/api/safety/kill-switch")
async def set_kill_switch(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    enabled = bool(payload.get("enabled"))
    data = {
        "enabled": enabled,
        "reason": str(payload.get("reason") or ("Manual safety block" if enabled else "")),
        "updated_at_ist": _now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
    }
    await redis.set(KILL_SWITCH_KEY, json.dumps(data, separators=(",", ":")), ex=60 * 60 * 24 * 30)
    return web.json_response({"ok": True, "kill_switch": data})
