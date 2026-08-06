"""Broker auth helper routes for local dashboard recovery.

These endpoints intentionally support the local Infusion dashboard workflow:
when Upstox expires the access token, the user can paste a fresh token in the
dashboard and force ingestion to re-authenticate without editing .env files.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone, timedelta

from aiohttp import web

routes = web.RouteTableDef()

KEY_AUTH_UPSTOX = "infusion:auth:upstox"
KEY_AUTH_STATUS = "infusion:auth:upstox:status"
KEY_FORCE_RECHECK = "infusion:auth:upstox:force_recheck"
IST = timezone(timedelta(hours=5, minutes=30))


def _jwt_expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return int(data.get("exp") or 0)
    except Exception:
        return 0


def _iso(ts: int, tz=timezone.utc) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz).isoformat() if ts else ""


@routes.get("/api/auth/upstox/status")
async def upstox_auth_status(request):
    redis = request.app["redis"]
    health_raw = await redis.get("infusion:health:ingestion")
    health = {}
    if health_raw:
        try:
            import msgpack

            health = msgpack.unpackb(health_raw, raw=False)
        except Exception:
            health = {}

    auth_raw = await redis.get(KEY_AUTH_UPSTOX)
    auth_payload = {}
    if auth_raw:
        try:
            auth_payload = json.loads(auth_raw.decode() if isinstance(auth_raw, bytes) else auth_raw)
        except Exception:
            auth_payload = {}

    status_raw = await redis.hgetall(KEY_AUTH_STATUS)
    status = {}
    for k, v in status_raw.items():
        kk = k.decode() if isinstance(k, bytes) else k
        vv = v.decode() if isinstance(v, bytes) else v
        status[kk] = vv

    expiry_ts = int(auth_payload.get("expiry_ts") or health.get("token_expiry_ts") or status.get("token_expiry_ts") or 0)
    now = int(time.time())
    auth_error = health.get("auth_error") or status.get("state") or ""
    token_state = "missing"
    if expiry_ts:
        token_state = "expired" if expiry_ts <= now else "valid"
    if auth_error in {"expired_token", "invalid_token", "missing_token"}:
        token_state = auth_error.replace("_token", "")

    return web.json_response({
        "broker": "upstox",
        "token_state": token_state,
        "needs_token": token_state in {"expired", "invalid", "missing"},
        "auth_error": auth_error,
        "source": auth_payload.get("source") or health.get("token_source") or status.get("token_source") or "",
        "expiry_ts": expiry_ts,
        "expiry_utc": _iso(expiry_ts),
        "expiry_ist": _iso(expiry_ts, IST),
        "ingestion_state": health.get("state", ""),
        "last_tick_age_ms": health.get("last_tick_age_ms", -1),
        "tick_count": health.get("tick_count", 0),
    })


@routes.post("/api/auth/upstox/token")
async def save_upstox_token(request):
    redis = request.app["redis"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body."}, status=400)

    token = str(body.get("access_token") or "").strip()
    if not token:
        return web.json_response({"ok": False, "error": "Access token is required."}, status=400)

    expiry_ts = _jwt_expiry(token)
    if not expiry_ts:
        return web.json_response({
            "ok": False,
            "error": "Could not read expiry from this token. Please paste the full Upstox access token.",
        }, status=400)
    now = int(time.time())
    if expiry_ts and expiry_ts <= now:
        return web.json_response({
            "ok": False,
            "error": "This Upstox token is already expired.",
            "expiry_ist": _iso(expiry_ts, IST),
        }, status=400)

    ttl = max((expiry_ts - now - 60), 300) if expiry_ts else 20 * 3600
    auth_data = {
        "access_token": token,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        "broker": "upstox",
        "source": "dashboard",
        "expiry_ts": expiry_ts,
        "expiry_utc": _iso(expiry_ts),
        "expiry_ist": _iso(expiry_ts, IST),
    }
    await redis.set(KEY_AUTH_UPSTOX, json.dumps(auth_data, separators=(",", ":")), ex=ttl)
    await redis.hset(KEY_AUTH_STATUS, mapping={
        "state": "token_saved",
        "token_source": "dashboard",
        "token_expiry_ts": str(expiry_ts),
        "updated_at": str(now),
    })
    await redis.set(KEY_FORCE_RECHECK, str(now), ex=120)

    return web.json_response({
        "ok": True,
        "broker": "upstox",
        "source": "dashboard",
        "expiry_ts": expiry_ts,
        "expiry_ist": _iso(expiry_ts, IST),
        "recheck_triggered": True,
        "message": "Token saved. Ingestion recheck has been requested.",
    })


@routes.post("/api/auth/upstox/recheck")
async def force_upstox_recheck(request):
    redis = request.app["redis"]
    now = int(time.time())
    await redis.set(KEY_FORCE_RECHECK, str(now), ex=120)
    await redis.hset(KEY_AUTH_STATUS, mapping={
        "state": "recheck_requested",
        "updated_at": str(now),
    })
    return web.json_response({"ok": True, "broker": "upstox", "recheck_triggered": True})
